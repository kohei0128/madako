from io import StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from data_profile.api import ProfilePlan
from data_profile.models import ModelProfile, ProfilingConfig
from data_profile.planning import ProfileItemResult, ProfilePlanItem, ProfileProgress
from data_profile.progress import ProfileRenderer


class TtyBuffer(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.animated = Event()
        self._frames = 0

    def isatty(self) -> bool:
        return True

    def write(self, value: str) -> int:
        written = super().write(value)
        if value.startswith("\r\x1b[2K") and len(value) > len("\r\x1b[2K"):
            self._frames += 1
            if self._frames >= 3:
                self.animated.set()
        return written


@pytest.fixture(autouse=True)
def terminal_environment(monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLUMNS", "80")


def plan_item(unique_id: str, dimension: str | None = None) -> ProfilePlanItem:
    model = ModelProfile(
        unique_id=unique_id,
        name=unique_id.rsplit(".", 1)[-1],
        database="project",
        schema_name="dataset",
        materialization="table",
        profiling=ProfilingConfig(enabled=True),
    )
    return ProfilePlanItem(
        model=model,
        dimension=dimension,
        sql="SELECT 1",
        project="project",
        location="US",
        model_signature=model.profiling_signature(),
    )


def progress(event: str, current: int, item: ProfilePlanItem) -> ProfileProgress:
    result = None
    if event == "execute_completed":
        result = ProfileItemResult(
            item=item,
            status="succeeded",
            row_count=1,
            bytes_processed=1024,
        )
    return ProfileProgress(
        event=event,
        current=current,
        total=2,
        model=item.model,
        dimension=item.dimension,
        item=item,
        result=result,
    )


def test_plain_renderer_is_append_only_and_quiet_for_successful_queries() -> None:
    output = StringIO()
    first = plan_item("model.demo.first")
    second = plan_item("model.demo.second")
    renderer = ProfileRenderer(Path(".madako"), stream=output)

    renderer.phase_started("Importing dbt artifacts")
    renderer.phase_completed("Imported dbt artifacts")
    renderer.phase_started("Planning queries")
    renderer.plan_completed(ProfilePlan(items=(first, second)))
    renderer.profiling_started(2)
    renderer.event(progress("execute_started", 1, first))
    renderer.event(progress("execute_started", 2, second))
    renderer.event(progress("execute_completed", 2, second))
    renderer.event(progress("execute_completed", 1, first))
    renderer.event(ProfileProgress(event="storage_started", current=2, total=2))
    renderer.event(ProfileProgress(event="storage_completed", current=2, total=2))
    renderer.complete(SimpleNamespace(
        profiled_models=("first", "second"),
        items=(
            ProfileItemResult(item=first, status="succeeded", bytes_processed=1024),
            ProfileItemResult(item=second, status="succeeded", bytes_processed=2048),
        ),
    ))

    rendered = output.getvalue()
    assert "\x1b" not in rendered
    assert "[RUN" not in rendered
    assert "model.demo.first" not in rendered
    assert "Profiling finished: 2 succeeded" in rendered
    assert "Profile complete: 2 relations, 2 queries, 3.0 KiB processed" in rendered


def test_tty_renderer_rewrites_one_aggregate_progress_line() -> None:
    output = TtyBuffer()
    first = plan_item("model.demo.first")
    second = plan_item("model.demo.second")
    renderer = ProfileRenderer(Path(".madako"), stream=output)

    renderer.profiling_started(2)
    renderer.event(progress("execute_started", 1, first))
    renderer.event(progress("execute_started", 2, second))
    renderer.event(progress("execute_completed", 2, second))
    renderer.event(progress("execute_completed", 1, first))

    rendered = output.getvalue()
    assert "\r\x1b[2K" in rendered
    assert "Profiling 1/2 complete · 1 running" in rendered
    assert "Profiling 2/2" in rendered
    assert "model.demo" not in rendered


def test_tty_spinner_animates_while_no_progress_events_arrive() -> None:
    output = TtyBuffer()
    with ProfileRenderer(Path(".madako"), stream=output) as renderer:
        renderer.phase_started("Profiling 0/2")
        assert output.animated.wait(timeout=2)

    rendered = output.getvalue()
    frames = {character for character in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if character in rendered}
    assert len(frames) >= 3


def test_verbose_renderer_keeps_query_level_details_without_cursor_control() -> None:
    output = TtyBuffer()
    item = plan_item("model.demo.events", "event_date")
    renderer = ProfileRenderer(Path(".madako"), verbose=True, stream=output)

    renderer.profiling_started(1)
    renderer.event(ProfileProgress(
        event="execute_started", current=1, total=1, item=item,
    ))
    renderer.event(ProfileProgress(
        event="execute_completed",
        current=1,
        total=1,
        item=item,
        result=ProfileItemResult(
            item=item, status="succeeded", row_count=3, bytes_processed=12_345,
        ),
    ))

    rendered = output.getvalue()
    assert "\x1b" not in rendered
    assert "[RUN 1/1] Querying: model.demo.events / event_date" in rendered
    assert "[DONE 1/1]" in rendered
    assert "12.1 KiB processed" in rendered


def test_progress_events_do_not_accelerate_spinner(monkeypatch) -> None:
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr("data_profile.progress.monotonic", lambda: 0)
    output = TtyBuffer()
    renderer = ProfileRenderer(Path(".madako"), stream=output)
    item = plan_item("model.demo.events")
    renderer.profiling_started(100)
    for index in range(1, 101):
        renderer.event(progress("execute_started", index, item))
    assert set(output.getvalue()) & set(renderer.FRAMES) == {"⠋"}
    monkeypatch.setattr("data_profile.progress.monotonic", lambda: 0.16)
    renderer.event(progress("execute_completed", 1, item))
    assert output.getvalue().endswith("⠹ Profiling 1/100 complete · 99 running")


@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_renderer_lifetime_includes_warnings_phases_and_exception_cleanup(failure, monkeypatch) -> None:
    monkeypatch.setenv("TERM", "xterm")
    output = TtyBuffer()
    renderer = ProfileRenderer(Path(".madako"), stream=output)
    try:
        with renderer:
            worker = renderer._thread
            renderer.phase_started("Importing dbt artifacts")
            renderer.warning("stale catalog")
            renderer.phase_completed("Imported dbt artifacts")
            renderer.phase_started("Planning queries")
            assert renderer._thread is worker
            assert worker.is_alive()
            if failure:
                raise failure()
    except (RuntimeError, KeyboardInterrupt):
        assert failure is not None
    assert not worker.is_alive()
    assert output.getvalue().count("Warning: stale catalog\n") == 1
    assert output.getvalue().endswith("\r\x1b[2K")


@pytest.mark.parametrize("tty,verbose,term", [(False, False, "xterm"), (True, True, "xterm"), (True, False, "dumb")])
def test_plain_modes_never_start_animation(tty, verbose, term, monkeypatch) -> None:
    monkeypatch.setenv("TERM", term)
    output = TtyBuffer() if tty else StringIO()
    with ProfileRenderer(Path(".madako"), stream=output, verbose=verbose) as renderer:
        renderer.phase_started("Planning queries")
        assert renderer._thread is None
    assert output.getvalue() == "Planning queries\n"


def test_no_color_and_narrow_terminal(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLUMNS", "20")
    output = TtyBuffer()
    renderer = ProfileRenderer(Path(".madako"), stream=output)
    renderer.profiling_started(1000)
    live = output.getvalue().removeprefix("\r\x1b[2K")
    assert len(live) <= 19
    assert "\x1b" not in live


def test_default_logs_stay_bounded_for_many_queries_and_cascade_skips() -> None:
    output = StringIO()
    item = plan_item("model.demo.events")
    renderer = ProfileRenderer(Path(".madako"), stream=output)
    renderer.profiling_started(100)
    for index in reversed(range(1, 101)):
        renderer.event(ProfileProgress(
            event="execute_skipped", current=index, total=100, item=item,
            result=ProfileItemResult(item=item, status="skipped", error="another query failed"),
        ))
    renderer.event(ProfileProgress(event="storage_discarded", total=100))
    lines = output.getvalue().splitlines()
    assert len(lines) == 12  # start, ten milestones, failure summary
    assert "100 skipped" in lines[-1]
    assert "Profile results not saved" in lines[-1]
    assert "Profile complete" not in output.getvalue()


def test_dimension_skip_is_visible_but_never_counted_as_success() -> None:
    output = StringIO()
    item = plan_item("model.demo.events", "user_id")
    renderer = ProfileRenderer(Path(".madako"), stream=output)
    renderer.profiling_started(1)
    renderer.event(ProfileProgress(
        event="execute_skipped", current=1, total=1, item=item,
        result=ProfileItemResult(item=item, status="skipped", skip_reason="max_dimension_values",
                                 distinct_values=100, maximum_allowed=10),
    ))
    renderer.event(ProfileProgress(event="storage_started", current=1, total=1))
    assert "Warning: Skipped model.demo.events / user_id" in output.getvalue()
    assert "Profiling finished: 1 skipped" in output.getvalue()
    assert "1 succeeded" not in output.getvalue()


def test_summary_counts_unique_relations_and_labels_partial_byte_usage() -> None:
    output = StringIO()
    first = plan_item("model.demo.events")
    second = plan_item("source.demo.events")
    result = SimpleNamespace(items=(
        ProfileItemResult(item=first, status="succeeded", bytes_processed=1024),
        ProfileItemResult(item=second, status="succeeded"),
    ))
    ProfileRenderer(Path(".madako"), stream=output).complete(result)
    assert output.getvalue() == (
        "Profile complete: 2 relations, 2 queries, 1.0 KiB processed (reported queries only)\n"
    )


@pytest.mark.parametrize("cost_check", [False, True])
def test_verbose_includes_omitted_columns_with_or_without_cost_check(cost_check) -> None:
    output = StringIO()
    item = plan_item("model.demo.events").model_copy(update={
        "skipped_columns": ("payload",),
        "estimated_bytes": 1024 if cost_check else None,
        "max_bytes_billed": 2048 if cost_check else None,
    })
    renderer = ProfileRenderer(Path(".madako"), verbose=True, stream=output)
    if cost_check:
        renderer.event(ProfileProgress(event="estimate_completed", current=1, total=1, item=item))
    renderer.plan_completed(ProfilePlan(items=(item,)))
    rendered = output.getvalue()
    assert rendered.count("Unsupported columns omitted: payload") == 1
    assert ("1.0 KiB estimated / 2.0 KiB maximum" if cost_check else "Query cost check: disabled") in rendered


def test_cost_limit_error_is_visible_without_per_query_cascade_warnings() -> None:
    output = StringIO()
    blocked = plan_item("model.demo.large").model_copy(update={
        "estimated_bytes": 2048, "max_bytes_billed": 1024,
    })
    other = plan_item("model.demo.other")
    renderer = ProfileRenderer(Path(".madako"), stream=output)
    renderer.plan_completed(ProfilePlan(items=(blocked, other)))
    renderer.profiling_started(2)
    for index, item in enumerate((blocked, other), start=1):
        renderer.event(ProfileProgress(
            event="execute_skipped", current=index, total=2, item=item,
            result=ProfileItemResult(item=item, status="skipped", error="plan over budget"),
        ))
    renderer.event(ProfileProgress(event="storage_discarded", total=2))
    rendered = output.getvalue()
    assert "Error: model.demo.large / Overall: 2.0 KiB estimated exceeds 1.0 KiB limit" in rendered
    assert "no queries will run" in rendered
    assert "Profile failed: 2 skipped" in rendered
    assert "model.demo.other" not in rendered
    assert "Profile complete" not in rendered
