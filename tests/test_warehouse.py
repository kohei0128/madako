import pytest

from data_profile import BigQueryAdapter, DataProfile, WarehouseError
from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice, ProfilingConfig
from data_profile.storage import write_profile_storage
from data_profile.warehouse import complete_adapter


def test_bigquery_adapter_forwards_execution_settings(monkeypatch):
    calls = []

    def estimate(*args):
        calls.append(args)
        return 123

    def execute(*args):
        calls.append(args)
        return [{"record_count": "1"}]

    monkeypatch.setattr("data_profile.bigquery_profile.dry_run", estimate)
    monkeypatch.setattr("data_profile.bigquery_profile.execute_profile", execute)
    adapter = BigQueryAdapter()
    assert adapter.estimate("sql", "billing", "US") == 123
    assert adapter.execute("sql", "billing", "US", 456) == [{"record_count": "1"}]
    assert calls == [("sql", "billing", "US"), ("sql", "billing", "US", 456)]


def test_complete_adapter_owns_query_and_result_contract(tmp_path, monkeypatch):
    model = ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        materialization="table",
        columns=[ColumnMetadata(name="payload", data_type="CUSTOM")],
        profiling=ProfilingConfig(enabled=True),
    )
    write_profile_storage([model], tmp_path)
    calls = []

    class CustomAdapter:
        supported_types = frozenset({"CUSTOM"})

        def build_profile_query(self, selected, dimension):
            calls.append(("build", selected.unique_id, dimension))
            return "custom profile query"

        def estimate(self, sql, project, location):
            calls.append(("estimate", sql, project, location))
            return 12

        def execute(self, sql, project, location, max_bytes_billed):
            calls.append(("execute", sql, project, location, max_bytes_billed))
            return [{"custom": "row"}]

        def parse_profile_rows(self, selected, rows, dimension):
            calls.append(("parse", selected.unique_id, rows, dimension))
            return [ProfileSlice(
                record_count=1,
                columns=[ColumnProfile(
                    name="payload",
                    data_type="STRING",
                    null_count=0,
                    null_rate=0,
                    distinct_count=1,
                )],
            )]

    monkeypatch.setattr(
        "data_profile.bigquery_profile.generate_profile_sql",
        lambda *_: (_ for _ in ()).throw(AssertionError("BigQuery compiler was used")),
    )
    app = DataProfile(tmp_path, adapter=CustomAdapter())
    result = app.profile()

    assert result.successful
    assert [call[0] for call in calls] == ["build", "execute", "parse"]
    assert calls[1][-1] is None
    assert app.models()[0].profiles[0].columns[0].name == "payload"


def test_legacy_adapter_keeps_bigquery_profile_contract(tmp_path):
    class LegacyAdapter:
        def estimate(self, *_):
            return 1

        def execute(self, *_):
            return []

    adapter = LegacyAdapter()
    completed = complete_adapter(adapter)
    assert completed.estimate("sql", "project", "US") == 1
    assert "COUNT(*) AS record_count" in completed.build_profile_query(
        ModelProfile(
            name="events",
            database="project",
            schema_name="dataset",
            materialization="table",
            columns=[ColumnMetadata(name="id", data_type="INT64")],
        ),
        None,
    )


def test_custom_adapter_planning_error_uses_public_exception(tmp_path):
    model = ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        materialization="table",
        columns=[ColumnMetadata(name="id", data_type="INT64")],
        profiling=ProfilingConfig(enabled=True, max_bytes_billed=10_000),
    )
    write_profile_storage([model], tmp_path)

    class BrokenAdapter(BigQueryAdapter):
        def estimate(self, *_):
            raise RuntimeError("adapter bug")

    with pytest.raises(WarehouseError, match="could not plan") as error:
        DataProfile(tmp_path, adapter=BrokenAdapter()).plan()
    assert isinstance(error.value.__cause__, RuntimeError)
