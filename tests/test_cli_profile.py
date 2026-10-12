"""CLI ownership of progress, warning routing, and error/interrupt cleanup."""

import sys
import warnings
from io import StringIO
from types import SimpleNamespace

import pytest

from madako.api import DataProfile, ProfilePlan
from madako.cli import main
from madako.exceptions import PlanningError
from madako.planning import ProfileProgress
from madako.progress import ProfileRenderer


def test_plan_prints_row_filter_and_filtered_cost(capsys):
    from madako.cli import _print_plan
    from madako.models import ColumnMetadata, ModelProfile, ProfilingConfig
    from madako.planning import create_profile_plan

    model = ModelProfile(
        name="events", database="project", schema_name="dataset", materialization="table",
        columns=[ColumnMetadata(name="amount", data_type="INT64")],
        profiling=ProfilingConfig(enabled=True, where="amount > 0", max_bytes_billed=1000),
    )
    plan = ProfilePlan(items=tuple(create_profile_plan([model], estimator=lambda *_: 100)))

    _print_plan(plan)

    output = capsys.readouterr().out
    assert "WHERE: amount > 0" in output
    assert "Query estimated: 100 B" in output
    assert "[READY]" in output


def test_plan_and_progress_show_combined_relation_budget(capsys, tmp_path):
    from madako.cli import _print_plan
    from madako.models import ColumnMetadata, ModelProfile, ProfilingConfig
    from madako.planning import create_profile_plan

    model = ModelProfile(
        unique_id="model.test.events", name="events", database="project", schema_name="dataset",
        materialization="table", columns=[ColumnMetadata(name="day", data_type="DATE")],
        profiling=ProfilingConfig(enabled=True, dimensions=["day"], max_bytes_billed=1000),
    )
    plan = ProfilePlan(items=tuple(create_profile_plan([model], estimator=lambda *_: 600)))
    _print_plan(plan)
    output = capsys.readouterr().out
    assert output.count("[BLOCKED]") == 2
    assert "Query estimated: 600 B" in output
    assert output.count("Relation estimated total: 1.2 KiB") == 1
    assert "Relation maximum billed: 1,000 B" in output

    output = StringIO()
    ProfileRenderer(tmp_path, stream=output).plan_completed(plan)
    assert output.getvalue().count("Error:") == 1
    assert "1.2 KiB relation total estimated exceeds 1,000 B relation limit" in output.getvalue()


class Terminal(StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("phase", ["Import", "Plan", "Profile", "Save"])
@pytest.mark.parametrize("interrupt", [False, True])
@pytest.mark.parametrize("verbose", [False, True])
def test_cli_cleans_up_every_phase_and_reports_errors_once(
    phase, interrupt, verbose, monkeypatch, tmp_path,
):
    output = Terminal()
    instances = []
    failure = KeyboardInterrupt() if interrupt else PlanningError("test failure")

    def renderer(*args, **kwargs):
        instance = ProfileRenderer(*args, **kwargs)
        instances.append(instance)
        return instance

    def plan(*, progress, **kwargs):
        if phase == "Plan":
            if not interrupt:
                progress(ProfileProgress(event="estimate_failed", error=str(failure)))
            raise failure
        return ProfilePlan(items=())

    def run(*args, progress, **kwargs):
        if phase == "Save":
            progress(ProfileProgress(event="storage_started"))
            if not interrupt:
                progress(ProfileProgress(event="storage_failed", error=str(failure)))
        raise failure

    def import_dbt(*args, **kwargs):
        warnings.warn("catalog.json is older than manifest.json", UserWarning)
        if phase == "Import":
            raise failure
        return SimpleNamespace(plan=plan, run=run)

    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "argv", ["madako", "profile"] + (["-v"] if verbose else []))
    monkeypatch.setattr("madako.cli.ProfileRenderer", renderer)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(DataProfile, "from_dbt_project", import_dbt)

    expected = PlanningError if verbose and not interrupt else SystemExit
    with pytest.raises(expected) as raised:
        main()

    if expected is SystemExit:
        assert raised.value.code == (130 if interrupt else 1)
    instance = instances[0]
    assert instance._thread is None or not instance._thread.is_alive()
    assert not instance._message
    rendered = output.getvalue()
    assert rendered.count("Warning: catalog.json is older than manifest.json\n") == 1
    assert rendered.count("Error:") == 1
    assert "Profile complete" not in rendered
    if interrupt:
        assert f"{phase} interrupted" in rendered
    else:
        assert rendered.count("test failure") == 1


def test_cli_unexpected_error_is_not_hidden(monkeypatch, tmp_path, capsys):
    def import_dbt(*args, **kwargs):
        raise RuntimeError("unexpected bug")

    monkeypatch.setattr(sys, "argv", ["madako", "profile"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(DataProfile, "from_dbt_project", import_dbt)
    with pytest.raises(RuntimeError, match="unexpected bug"):
        main()
    assert "Error: Import failed · unexpected bug" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["plan", "profile"])
@pytest.mark.parametrize("local_budget, expected", [(None, 1_000), (800, 800), (2_000, 1_000)])
def test_cli_applies_toml_budget_and_blocks_entire_plan(
    command, local_budget, expected, tmp_path, monkeypatch, capsys,
):
    from madako import BigQueryAdapter
    from madako.models import ColumnMetadata, ModelProfile, ProfilingConfig
    from madako.storage import write_profile_storage

    model = ModelProfile(
        unique_id="model.test.events", name="events", database="project", schema_name="dataset",
        relation_name="`project.dataset.events`", materialization="table",
        columns=[ColumnMetadata(name="day", data_type="DATE")],
        profiling=ProfilingConfig(enabled=True, dimensions=["day"], max_bytes_billed=local_budget),
    )
    source = model.model_copy(deep=True, update={
        "unique_id": "source.test.raw.events", "resource_type": "source", "relation_name": "`raw.events`",
    })
    source.profiling.max_bytes_billed = None
    storage_dir = tmp_path / ".madako"
    write_profile_storage([model, source], storage_dir)
    (tmp_path / "madako.toml").write_text(
        '[profiling]\nmax_bytes_billed = "1 KB"\n', encoding="utf-8",
    )
    estimates = []

    def estimate(self, sql, project, location):
        estimates.append(sql)
        return 600 if "`project.dataset.events`" in sql else 100

    monkeypatch.setattr(BigQueryAdapter, "estimate", estimate)
    monkeypatch.setattr(
        BigQueryAdapter, "execute",
        lambda *_: pytest.fail("Over-budget plan must not execute any queries"),
    )
    monkeypatch.setattr("madako.storage.read_dbt_artifacts", lambda _: [model, source])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["madako", command])

    if command == "profile":
        with pytest.raises(SystemExit) as raised:
            main()
        assert raised.value.code == 1
    else:
        main()

    output = capsys.readouterr().out
    assert len(estimates) == 4
    assert "model.test.events" in output
    if command == "plan":
        assert "Relation estimated total: 1.2 KiB" in output
        assert f"Relation maximum billed: {expected:,} B" in output
        assert "[BLOCKED] model.test.events" in output
        assert "[READY] source.test.raw.events" in output
    else:
        assert f"1.2 KiB relation total estimated exceeds {expected:,} B relation limit" in output
        assert "no queries will run" in output
