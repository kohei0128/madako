"""CLI presentation of profile events; warehouse execution stays independent."""

from __future__ import annotations

import os
import sys
from collections import Counter
from contextlib import AbstractContextManager
from pathlib import Path
from shutil import get_terminal_size
from threading import Event, Lock, Thread
from time import monotonic
from typing import TextIO

from madako.api import ProfilePlan, ProfileResult
from madako.planning import ProfileProgress


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{value:,} B" if unit == "B" else f"{size:,.1f} {unit}"


def profile_label(progress: ProfileProgress) -> str:
    model = progress.model or (progress.item.model if progress.item else None)
    dimension = progress.dimension or (progress.item.dimension if progress.item else None)
    return f"{model.unique_id if model else 'profile'} / {dimension or 'Overall'}"


class ProfileRenderer(AbstractContextManager):
    """Own one animation thread for the command's lifetime, only on a TTY.

    Execution callbacks are serialized by the profile API. The output lock also
    protects them from the timer; the timer never reads execution state.
    No colors are emitted, including when NO_COLOR is set.
    """

    SPINNER_INTERVAL = 0.08
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, storage_dir: Path, *, verbose: bool = False, stream: TextIO | None = None):
        self.storage_dir = storage_dir
        self.verbose = verbose
        self.stream = stream if stream is not None else sys.stdout
        self.tty = self.stream.isatty() and os.environ.get("TERM") != "dumb" and not verbose
        self._message = ""
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._started = monotonic()
        self._total = 0
        self._states: dict[int, str] = {}
        self._plain_step = 0
        self._error_reported = False
        self.phase = "Import"

    def __enter__(self) -> ProfileRenderer:
        if self.tty:
            self._thread = Thread(target=self._animate, name="madako-progress", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            if self._message:
                self.stream.write("\r\x1b[2K")
                self.stream.flush()
                self._message = ""

    def _animate(self) -> None:
        while not self._stop.wait(self.SPINNER_INTERVAL):
            with self._lock:
                if self._message:
                    self._draw()

    def _draw(self) -> None:
        # Time, not callback frequency, determines the frame. Keep the live
        # ASCII status on one terminal row so clearing it cannot leave debris.
        frame = int((monotonic() - self._started) / self.SPINNER_INTERVAL)
        width = max(1, get_terminal_size().columns - 1)
        text = f"{self.FRAMES[frame % len(self.FRAMES)]} {self._message}"
        self.stream.write(f"\r\x1b[2K{text[:width]}")
        self.stream.flush()

    def _line(self, message: str, *, keep_progress: bool = False) -> None:
        with self._lock:
            if self._message:
                self.stream.write("\r\x1b[2K")
            self.stream.write(f"{message}\n")
            if keep_progress and self._message:
                self._draw()
            else:
                self._message = ""
            self.stream.flush()

    def _update(self, message: str) -> None:
        if self.tty:
            with self._lock:
                self._message = message
                self._draw()

    def phase_started(self, message: str) -> None:
        if self.tty:
            self._update(message)
        else:
            self._line(message)

    def phase_completed(self, message: str) -> None:
        self._line(f"✓ {message}" if self.tty else message)

    def warning(self, message: str) -> None:
        self._line(f"Warning: {message}", keep_progress=True)

    def error(self, message: str) -> None:
        self._error_reported = True
        self._line(f"Error: {message}")

    def phase_failed(self, error: BaseException) -> None:
        if not self._error_reported:
            self.error(f"{self.phase} failed · {error}")

    def plan_completed(self, plan: ProfilePlan) -> None:
        relations = len({item.model.unique_id for item in plan.items})
        self.phase_completed(f"Planned {len(plan.items):,} queries for {relations:,} relations")
        for item in plan.items:
            label = f"{item.model.unique_id} / {item.dimension or 'Overall'}"
            if not item.executable:
                assert item.estimated_bytes is not None and item.max_bytes_billed is not None
                self.error(
                    f"{label}: {format_bytes(item.estimated_bytes)} estimated exceeds "
                    f"{format_bytes(item.max_bytes_billed)} limit; no queries will run"
                )
            if self.verbose:
                if item.estimated_bytes is None:
                    self._line(f"[PLAN] {label} · Query cost check: disabled")
                if item.skipped_columns:
                    self._line(f"[PLAN] {label} · Unsupported columns omitted: {', '.join(item.skipped_columns)}")

    def profiling_started(self, total: int) -> None:
        self._total = total
        self._states.clear()
        self._plain_step = 0
        self.phase_started(f"Profiling 0/{total:,}")

    def _counts(self) -> str:
        counts = Counter(self._states.values())
        return ", ".join(
            f"{counts[status]:,} {status}" for status in ("succeeded", "failed", "skipped")
            if counts[status]
        ) or "0 succeeded"

    def event(self, progress: ProfileProgress) -> None:
        event = progress.event
        label = profile_label(progress)
        position = f"{progress.current}/{progress.total}"
        if event == "estimate_started" and self.verbose:
            self._line(f"[PLAN {position}] Checking query cost: {label}")
        elif event == "estimate_completed" and self.verbose:
            item = progress.item
            assert item is not None and item.estimated_bytes is not None
            assert item.max_bytes_billed is not None
            self._line(
                f"[PLAN {position}] {label} · {format_bytes(item.estimated_bytes)} "
                f"estimated / {format_bytes(item.max_bytes_billed)} maximum"
            )
        elif event == "estimate_failed":
            self.error(f"Could not plan {label} · {progress.error}")
        elif event == "execute_started":
            self._states[progress.current] = "running"
            if self.verbose:
                self._line(f"[RUN {position}] Querying: {label}")
            self._update_profile()
        elif event in {"execute_completed", "execute_skipped"}:
            result = progress.result
            assert result is not None
            self._states[progress.current] = result.status
            if result.status == "failed":
                self.error(f"Failed {label} · {result.error}")
            elif result.skip_reason == "max_dimension_values":
                self.warning(
                    f"Skipped {label} · {result.distinct_values:,} distinct values exceeds "
                    f"the {result.maximum_allowed:,} limit"
                )
            elif self.verbose:
                if result.status == "skipped":
                    self._line(f"[SKIPPED {position}] {label} · {result.error}")
                else:
                    usage = f" · {format_bytes(result.bytes_processed)} processed" if result.bytes_processed is not None else ""
                    self._line(f"[DONE {position}] {label} · {result.row_count:,} metric rows{usage}")
            self._update_profile()
        elif event == "storage_started":
            self.phase_completed(f"Profiling finished: {self._counts()}")
            self.phase = "Save"
            self.phase_started("Updating profile storage")
        elif event == "storage_completed":
            self.phase_completed(f"Updated {self.storage_dir}")
        elif event == "storage_discarded":
            self._line(
                f"Profile failed: {self._counts()}. Profile results not saved; "
                f"discarded results from {progress.current:,} completed queries."
            )
        elif event == "storage_failed":
            self.error(f"Could not update storage · {progress.error}")

    def _update_profile(self) -> None:
        running = sum(state == "running" for state in self._states.values())
        done = len(self._states) - running
        message = f"Profiling {done:,}/{self._total:,} complete · {running:,} running"
        self._update(message)
        # At most ten aggregate updates in redirected logs, independent of
        # query completion order. Verbose already reports each query.
        step = done * 10 // max(1, self._total)
        if not self.tty and not self.verbose and step > self._plain_step:
            self._line(message)
            self._plain_step = step

    def complete(self, result: ProfileResult) -> None:
        succeeded = [item for item in result.items if item.status == "succeeded"]
        relations = len({item.item.model.unique_id for item in succeeded})
        skipped = sum(item.status == "skipped" for item in result.items)
        summary = f"Profile complete: {relations:,} relations, {len(succeeded):,} queries"
        if skipped:
            summary += f", {skipped:,} skipped"
        measured = [item.bytes_processed for item in succeeded if item.bytes_processed is not None]
        if measured:
            qualifier = " (reported queries only)" if len(measured) != len(succeeded) else ""
            summary += f", {format_bytes(sum(measured))} processed{qualifier}"
        self._line(summary)
