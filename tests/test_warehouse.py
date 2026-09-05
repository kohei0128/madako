from data_profile import BigQueryAdapter


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
