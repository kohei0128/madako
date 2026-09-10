"""CLI ownership of progress, warning routing, and error/interrupt cleanup."""

import sys
import warnings
from io import StringIO
from types import SimpleNamespace

import pytest

from data_profile.api import DataProfile, ProfilePlan
from data_profile.cli import main
from data_profile.exceptions import PlanningError
from data_profile.planning import ProfileProgress
from data_profile.progress import ProfileRenderer


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
    monkeypatch.setattr("data_profile.cli.ProfileRenderer", renderer)
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
