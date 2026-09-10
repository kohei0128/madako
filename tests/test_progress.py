from io import StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from data_profile.api import ProfilePlan
from data_profile.models import ModelProfile, ProfilingConfig
from data_profile.planning import ProfileItemResult, ProfilePlanItem, ProfileProgress
from data_profile.progress import make_profile_renderer


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
    renderer = make_profile_renderer(Path(".madako"), stream=output)

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
    assert "Profiled 2/2 queries" in rendered
    assert "Profile complete: 2 relations, 2 queries, 3.0 KiB processed" in rendered


def test_tty_renderer_rewrites_one_aggregate_progress_line() -> None:
    output = TtyBuffer()
    first = plan_item("model.demo.first")
    second = plan_item("model.demo.second")
    renderer = make_profile_renderer(Path(".madako"), stream=output)

    renderer.profiling_started(2)
    renderer.event(progress("execute_started", 1, first))
    renderer.event(progress("execute_started", 2, second))
    renderer.event(progress("execute_completed", 2, second))
    renderer.event(progress("execute_completed", 1, first))

    renderer.close()
    rendered = output.getvalue()
    assert "\r\x1b[2K" in rendered
    assert "Profiling 1/2 · 1 running" in rendered
    assert "Profiling 2/2" in rendered
    assert "model.demo" not in rendered


def test_tty_spinner_animates_while_no_progress_events_arrive() -> None:
    output = TtyBuffer()
    renderer = make_profile_renderer(Path(".madako"), stream=output)

    renderer.phase_started("Profiling 0/2")
    assert output.animated.wait(timeout=1)
    renderer.close()

    rendered = output.getvalue()
    frames = {character for character in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if character in rendered}
    assert len(frames) >= 3


def test_verbose_renderer_keeps_query_level_details_without_cursor_control() -> None:
    output = TtyBuffer()
    item = plan_item("model.demo.events", "event_date")
    renderer = make_profile_renderer(Path(".madako"), verbose=True, stream=output)

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
    renderer.close()
    assert "\x1b" not in rendered
    assert "[RUN 1/1] Querying: model.demo.events / event_date" in rendered
    assert "[DONE 1/1]" in rendered
    assert "12.1 KiB processed" in rendered
