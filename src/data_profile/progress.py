"""Console renderers for the ``madako profile`` command."""

from __future__ import annotations

import sys
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from typing import TextIO

from data_profile.api import ProfilePlan, ProfileResult
from data_profile.planning import ProfileProgress


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    if unit == "B":
        return f"{value:,} B"
    return f"{size:,.1f} {unit}"


def profile_label(progress: ProfileProgress) -> str:
    model = progress.model or (progress.item.model if progress.item else None)
    relation = model.unique_id if model else "profile"
    dimension = progress.dimension
    if dimension is None and progress.item is not None:
        dimension = progress.item.dimension
    return f"{relation} / {dimension or 'Overall'}"


class ProfileRenderer:
    """Render execution state without coupling it to profile execution."""

    SPINNER_INTERVAL = 0.08

    def __init__(
        self,
        storage_dir: Path,
        *,
        verbose: bool = False,
        stream: TextIO | None = None,
    ) -> None:
        self.storage_dir = storage_dir
        self.verbose = verbose
        self.stream = stream or sys.stdout
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)()) and not verbose
        self._live = False
        self._total = 0
        self._states: dict[int, str] = {}
        self._spinner = 0
        self._live_message: str | None = None
        self._output_lock = Lock()
        self._spinner_stop: Event | None = None
        self._spinner_thread: Thread | None = None

    def _line(self, message: str = "") -> None:
        self._stop_spinner()
        with self._output_lock:
            if self._live:
                self.stream.write("\r\x1b[2K")
                self._live = False
            self._live_message = None
            self.stream.write(f"{message}\n")
            self.stream.flush()

    def _update(self, message: str) -> None:
        if not self.tty:
            return
        if self._spinner_thread is None:
            self._start_spinner(message)
            return
        with self._output_lock:
            self._live_message = message
        self._render_spinner()

    def _start_spinner(self, message: str) -> None:
        self._stop_spinner()
        with self._output_lock:
            self._live_message = message
        stop = Event()
        self._spinner_stop = stop
        self._render_spinner()
        thread = Thread(
            target=self._animate_spinner,
            args=(stop,),
            name="madako-progress",
            daemon=True,
        )
        self._spinner_thread = thread
        thread.start()

    def _animate_spinner(self, stop: Event) -> None:
        while not stop.wait(self.SPINNER_INTERVAL):
            self._render_spinner()

    def _render_spinner(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        with self._output_lock:
            if self._live_message is None:
                return
            marker = frames[self._spinner % len(frames)]
            self._spinner += 1
            self.stream.write(f"\r\x1b[2K{marker} {self._live_message}")
            self.stream.flush()
            self._live = True

    def _stop_spinner(self) -> None:
        stop = self._spinner_stop
        thread = self._spinner_thread
        self._spinner_stop = None
        self._spinner_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not current_thread():
            thread.join()

    def close(self) -> None:
        """Stop background animation and remove an unfinished progress line."""
        self._stop_spinner()
        with self._output_lock:
            if self._live:
                self.stream.write("\r\x1b[2K")
                self.stream.flush()
                self._live = False
            self._live_message = None

    def phase_started(self, message: str) -> None:
        if self.tty:
            self._update(message)
        else:
            self._line(message)

    def phase_completed(self, message: str) -> None:
        self._line(f"✓ {message}" if self.tty else message)

    def phase_failed(self, message: str, error: BaseException) -> None:
        prefix = "×" if self.tty else "Error:"
        self._line(f"{prefix} {message} · {error}")

    def plan_completed(self, plan: ProfilePlan) -> None:
        relation_count = len({item.model.unique_id for item in plan.items})
        self.phase_completed(
            f"Planned {len(plan.items):,} queries for {relation_count:,} relations"
        )
        for item in plan.items:
            if not item.executable:
                assert item.estimated_bytes is not None
                assert item.max_bytes_billed is not None
                self._warning(
                    f"{item.model.unique_id} / {item.dimension or 'Overall'} requires "
                    f"{format_bytes(item.estimated_bytes)}; limit is "
                    f"{format_bytes(item.max_bytes_billed)}"
                )

    def profiling_started(self, total: int) -> None:
        self._total = total
        self._states.clear()
        self.phase_started(f"Profiling 0/{total:,}")

    def event(self, progress: ProfileProgress) -> None:
        if self.verbose:
            self._verbose_event(progress)

        if progress.event == "execute_started":
            self._states[progress.current] = "running"
            self._update_profile()
        elif progress.event in {"execute_completed", "execute_skipped"}:
            self._states[progress.current] = "complete"
            result = progress.result
            if result is not None and result.status == "failed":
                self._warning(f"Failed {profile_label(progress)} · {result.error}")
            elif progress.event == "execute_skipped":
                self._warning(self._skip_message(progress))
            self._update_profile()
        elif progress.event == "estimate_failed":
            self._warning(f"Could not plan {profile_label(progress)} · {progress.error}")
        elif progress.event == "storage_started":
            done = sum(state == "complete" for state in self._states.values())
            self.phase_completed(f"Profiled {done:,}/{self._total:,} queries")
            self.phase_started(f"Updating {self.storage_dir}")
        elif progress.event == "storage_completed":
            self.phase_completed(f"Updated {self.storage_dir}")
        elif progress.event == "storage_discarded":
            self._line(
                f"{'×' if self.tty else 'Error:'} Storage unchanged; discarded results "
                f"from {progress.current:,} completed queries"
            )
            if progress.error:
                self._line(f"  Failed query: {profile_label(progress)}")
                self._line(f"  Error details: {progress.error}")
        elif progress.event == "storage_failed":
            self._line(
                f"{'×' if self.tty else 'Error:'} Could not update storage · {progress.error}"
            )

    def complete(self, result: ProfileResult) -> None:
        relation_count = len(result.profiled_models)
        succeeded = sum(item.status == "succeeded" for item in result.items)
        skipped = sum(item.status == "skipped" for item in result.items)
        processed = sum(getattr(item, "bytes_processed", None) or 0 for item in result.items)
        measured_bytes = any(
            getattr(item, "bytes_processed", None) is not None for item in result.items
        )

        if self.tty:
            self._line()
            self._line("Profile complete")
            self._line(f"  {relation_count:,} relations")
            self._line(f"  {succeeded:,} queries")
            if skipped:
                self._line(f"  {skipped:,} skipped")
            if measured_bytes:
                self._line(f"  {format_bytes(processed)} processed")
        else:
            summary = f"Profile complete: {relation_count:,} relations, {succeeded:,} queries"
            if skipped:
                summary += f", {skipped:,} skipped"
            if measured_bytes:
                summary += f", {format_bytes(processed)} processed"
            self._line(summary)

    def _update_profile(self) -> None:
        done = sum(state == "complete" for state in self._states.values())
        running = sum(state == "running" for state in self._states.values())
        message = f"Profiling {done:,}/{self._total:,}"
        if running:
            message += f" · {running:,} running"
        self._update(message)

    def _warning(self, message: str) -> None:
        self._line(f"! {message}" if self.tty else f"Warning: {message}")

    @staticmethod
    def _skip_message(progress: ProfileProgress) -> str:
        result = progress.result
        assert result is not None
        label = profile_label(progress)
        if result.skip_reason == "max_dimension_values":
            return (
                f"Skipped {label} · {result.distinct_values:,} distinct values exceeds "
                f"the {result.maximum_allowed:,} limit"
            )
        return f"Skipped {label} · {result.error}"

    def _verbose_event(self, progress: ProfileProgress) -> None:
        position = f"{progress.current}/{progress.total}"
        label = profile_label(progress)
        if progress.event == "estimate_started":
            self._line(f"[PLAN {position}] Checking query cost: {label}")
        elif progress.event == "estimate_completed":
            item = progress.item
            assert item is not None and item.estimated_bytes is not None
            assert item.max_bytes_billed is not None
            status = "READY" if item.executable else "BLOCKED"
            self._line(
                f"[{status} {position}] {label} · {format_bytes(item.estimated_bytes)} "
                f"estimated / {format_bytes(item.max_bytes_billed)} maximum"
            )
            if item.skipped_columns:
                self._line(f"  Unsupported columns omitted: {', '.join(item.skipped_columns)}")
        elif progress.event == "execute_started":
            self._line(f"[RUN {position}] Querying: {label}")
        elif progress.event == "execute_completed":
            result = progress.result
            assert result is not None
            if result.status == "succeeded":
                usage = (
                    f" · {format_bytes(result.bytes_processed)} processed"
                    if result.bytes_processed is not None else ""
                )
                self._line(
                    f"[DONE {position}] {label} · {result.row_count:,} metric rows{usage}"
                )


def make_profile_renderer(
    storage_dir: Path,
    *,
    verbose: bool = False,
    stream: TextIO | None = None,
) -> ProfileRenderer:
    """Create the appropriate renderer for the current output stream."""
    return ProfileRenderer(storage_dir, verbose=verbose, stream=stream)
