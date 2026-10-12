from threading import Barrier, Lock

import pytest

from madako import BigQueryAdapter, ProfilePlan, QueryExecution
from madako.bigquery_profile import ProfilingError
from madako.models import ColumnMetadata, ModelProfile, ProfileSlice, ProfilingConfig
from madako.planning import create_profile_plan, execute_profile_plan


def configured_model() -> ModelProfile:
    return ModelProfile(
        unique_id="model.test.events",
        name="events",
        database="project",
        schema_name="dataset",
        relation_name="`project.dataset.events`",
        materialization="table",
        columns=[
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="amount", data_type="INT64"),
            ColumnMetadata(name="payload", data_type="JSON"),
        ],
        profiling=ProfilingConfig(
            enabled=True,
            dimensions=["event_date"],
            max_bytes_billed=10_000,
        ),
    )


def test_plan_resolves_config_and_estimates_cost() -> None:
    calls: list[tuple[str, str, str]] = []

    def estimate(sql: str, project: str, location: str) -> int:
        calls.append((sql, project, location))
        return 4_000

    plan = create_profile_plan([configured_model()], estimator=estimate)

    assert [item.dimension for item in plan] == [None, "event_date"]
    assert all(item.estimated_bytes == 4_000 for item in plan)
    assert ProfilePlan(items=tuple(plan)).executable
    assert all(item.skipped_columns == ("payload",) for item in plan)
    assert all(item.project == "project" for item in plan)
    assert all(item.location == "asia-northeast1" for item in plan)
    assert len(calls) == 2
    assert calls[0][1:] == ("project", "asia-northeast1")


def test_cost_estimation_uses_filtered_queries() -> None:
    model = configured_model()
    model.profiling.where = "event_date >= DATE '2026-09-01'"
    estimated_sql = []

    def estimate(sql, *_):
        estimated_sql.append(sql)
        assert "WHERE (\nevent_date >= DATE '2026-09-01'\n  )" in sql
        return 4_000

    plan = create_profile_plan([model], estimator=estimate)

    assert estimated_sql == [item.sql for item in plan]
    assert len(plan) == 2
    assert ProfilePlan(items=tuple(plan)).executable


def test_plan_skips_cost_estimation_by_default() -> None:
    model = configured_model().model_copy(update={
        "profiling": ProfilingConfig(enabled=True),
    })

    plan = create_profile_plan(
        [model],
        estimator=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected dry run")),
    )

    assert len(plan) == 1
    assert plan[0].estimated_bytes is None
    assert plan[0].max_bytes_billed is None
    assert ProfilePlan(items=tuple(plan)).executable


def test_plan_supports_numeric_and_bignumeric_columns() -> None:
    model = configured_model().model_copy(update={"columns": [
        ColumnMetadata(name="amount", data_type="NUMERIC"),
        ColumnMetadata(name="large_amount", data_type="BIGNUMERIC"),
        ColumnMetadata(name="payload", data_type="JSON"),
    ], "profiling": ProfilingConfig(enabled=True)})

    plan = create_profile_plan([model], estimator=lambda *_: 1)

    assert plan[0].skipped_columns == ("payload",)
    assert "MIN(`amount`)" in plan[0].sql
    assert "MAX(`large_amount`)" in plan[0].sql


def test_plan_marks_estimate_over_limit_as_blocked() -> None:
    plan = create_profile_plan([configured_model()], estimator=lambda *_: 10_001)

    assert ProfilePlan(items=tuple(plan)).executable is False


@pytest.mark.parametrize("estimate, executable", [(0, True), (5_000, True), (5_001, False), (6_000, False)])
def test_budget_applies_to_sum_of_overall_and_dimensions(estimate, executable) -> None:
    model = configured_model()
    items = create_profile_plan([model], estimator=lambda *_: estimate)
    plan = ProfilePlan(items=tuple(items))

    assert plan.estimated_bytes == 2 * estimate
    assert plan.relation_costs[model.unique_id].estimated_bytes == 2 * estimate
    assert plan.executable is executable
    if not executable:
        result = execute_profile_plan(
            [model], items,
            runner=lambda *_: pytest.fail("No query should run when the relation total exceeds its limit"),
        )
        assert not result.complete
        assert all(item.status == "skipped" for item in result.results)
        assert all(item.error == "relation estimated bytes exceed max_bytes_billed" for item in result.results)


def test_same_named_model_and_source_have_separate_budgets() -> None:
    first = configured_model()
    second = first.model_copy(update={"unique_id": "source.test.raw.events", "resource_type": "source"})
    plan = ProfilePlan(items=tuple(create_profile_plan([first, second], estimator=lambda *_: 4_000)))

    assert plan.estimated_bytes == 16_000
    assert plan.executable
    assert len(plan.relation_costs) == 2
    assert all(cost.estimated_bytes == 8_000 for cost in plan.relation_costs.values())


def test_over_budget_relation_blocks_entire_mixed_plan() -> None:
    first = configured_model()
    second = first.model_copy(deep=True, update={"unique_id": "source.test.raw.events"})
    second.profiling.max_bytes_billed = None
    plan = create_profile_plan([first, second], estimator=lambda *_: 6_000)

    assert ProfilePlan(items=tuple(plan)).estimated_bytes is None
    assert not ProfilePlan(items=tuple(plan)).executable
    execution = execute_profile_plan(
        [first, second], plan, threads=2,
        runner=lambda *_: pytest.fail("An over-budget relation must block the entire plan"),
    )
    assert not execution.complete
    assert all(result.status == "skipped" for result in execution.results)
    assert execution.results[-1].error == "plan contains another relation over max_bytes_billed"


@pytest.mark.parametrize("threads", [1, 2])
@pytest.mark.parametrize("billed", [0, 4_000, 5_000])
def test_execution_shares_remaining_billed_budget_only_within_each_relation(threads, billed) -> None:
    first = configured_model()
    second = first.model_copy(update={"unique_id": "source.test.raw.events", "relation_name": "`raw.events`"})
    calls = []

    class Adapter(BigQueryAdapter):
        def execute(self, sql, project, location, max_bytes_billed):
            calls.append(("raw" if "`raw.events`" in sql else "model", max_bytes_billed))
            # Processed bytes intentionally differ: only billed bytes spend the budget.
            return QueryExecution(rows=[], bytes_processed=8_000, bytes_billed=billed)

        def parse_profile_rows(self, model, rows, dimension):
            return [ProfileSlice(dimension_name=dimension, record_count=1, columns=[])]

    plan = create_profile_plan([first, second], estimator=lambda *_: 1_000)
    execution = execute_profile_plan([first, second], plan, adapter=Adapter(), threads=threads)

    assert execution.complete
    for relation in ["model", "raw"]:
        assert [maximum for name, maximum in calls if name == relation] == [10_000, 10_000 - billed]


@pytest.mark.parametrize("billed", [None, 10_000, 10_001, -1])
def test_exhausted_or_invalid_billed_usage_stops_later_queries(billed) -> None:
    model = configured_model()
    calls = []

    class Adapter(BigQueryAdapter):
        def execute(self, sql, project, location, max_bytes_billed):
            calls.append(max_bytes_billed)
            return QueryExecution(rows=[], bytes_billed=billed)

        def parse_profile_rows(self, model, rows, dimension):
            return [ProfileSlice(record_count=1, columns=[])]

    plan = create_profile_plan([model], estimator=lambda *_: 1_000)
    execution = execute_profile_plan([model], plan, adapter=Adapter())

    assert calls == [10_000]
    assert not execution.complete
    assert not execution.models[0].profiles
    assert any(result.status == "failed" for result in execution.results)


def test_modified_plan_cannot_increase_relation_budget() -> None:
    model = configured_model()
    plan = create_profile_plan([model], estimator=lambda *_: 1_000)
    plan = [item.model_copy(update={"max_bytes_billed": 20_000}) for item in plan]
    with pytest.raises(ProfilingError, match="stale or modified plan"):
        execute_profile_plan([model], plan, runner=lambda *_: pytest.fail("Modified budget must be rejected"))


def test_plan_requires_enabled_selection() -> None:
    with pytest.raises(ProfilingError, match="selector did not match"):
        create_profile_plan([configured_model()], selector="missing", estimator=lambda *_: 0)


def test_plan_supports_overall_only_profile() -> None:
    model = configured_model().model_copy(update={
        "profiling": ProfilingConfig(enabled=True, dimensions=[]),
    })

    plan = create_profile_plan([model], estimator=lambda *_: 1)

    assert plan[0].dimension is None
    assert "dimension_agg" not in plan[0].sql


def test_execute_plan_combines_multiple_dimensions_and_updates_once() -> None:
    model = configured_model()
    model = model.model_copy(update={
        "columns": [
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="processed_date", data_type="DATE"),
        ],
        "profiling": ProfilingConfig(
            enabled=True,
            dimensions=["event_date", "processed_date"],
            max_bytes_billed=10_000,
        ),
    })
    plan = create_profile_plan([model], estimator=lambda *_: 1)
    calls: list[tuple[str, str, int]] = []

    def run(sql: str, project: str, location: str, maximum: int) -> QueryExecution:
        calls.append((project, location, maximum))
        dimension = (
            "processed_date" if "'processed_date' AS dimension_name" in sql
            else "event_date" if "'event_date' AS dimension_name" in sql
            else None
        )
        rows = []
        slices = [(None, None)] if dimension is None else [(dimension, "2026-09-01")]
        for dimension_name, dimension_value in slices:
            for order, column in enumerate(model.columns):
                rows.append({
                    "dimension_name": dimension_name,
                    "dimension_value": dimension_value,
                    "record_count": "10",
                    "column_order": str(order),
                    "column_name": column.name,
                    "column_type": column.data_type,
                    "null_count": "0",
                    "null_rate": "0",
                    "distinct_count": None,
                    "min_value": "2026-09-01",
                    "max_value": "2026-09-01",
                    "true_count": None,
                })
        return QueryExecution(rows=rows, bytes_billed=1)

    execution = execute_profile_plan([model], plan, runner=run)

    assert len(calls) == 3
    assert execution.complete is True
    assert [result.status for result in execution.results] == ["succeeded", "succeeded", "succeeded"]
    assert [profile.dimension_name for profile in execution.models[0].profiles] == [
        None,
        "event_date",
        "processed_date",
    ]
    assert execution.models[0].profiled_at is not None


def test_execute_plan_runs_different_relations_in_parallel_and_preserves_order() -> None:
    first = configured_model().model_copy(update={
        "profiling": ProfilingConfig(enabled=True),
    })
    second = first.model_copy(update={
        "unique_id": "model.test.orders",
        "name": "orders",
        "relation_name": "`project.dataset.orders`",
    })
    plan = create_profile_plan([first, second], estimator=lambda *_: 1)
    barrier = Barrier(2)
    lock = Lock()
    active = 0
    maximum_active = 0

    def run(sql: str, *_args) -> list[dict]:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        model = second if "orders" in sql else first
        return [{
            "dimension_name": None,
            "dimension_value": None,
            "record_count": "10",
            "column_order": str(order),
            "column_name": column.name,
            "column_type": column.data_type,
            "null_count": "0",
            "null_rate": "0",
            "distinct_count": None,
            "min_value": "2026-09-01" if column.data_type == "DATE" else "1",
            "max_value": "2026-09-01" if column.data_type == "DATE" else "10",
            "true_count": None,
        } for order, column in enumerate(model.columns) if column.data_type != "JSON"]

    execution = execute_profile_plan([first, second], plan, runner=run, threads=2)

    assert execution.complete is True
    assert maximum_active == 2
    assert [result.item.model.unique_id for result in execution.results] == [
        first.unique_id,
        second.unique_id,
    ]
    assert [model.unique_id for model in execution.models if model.profiles] == [
        first.unique_id,
        second.unique_id,
    ]


def test_execute_plan_rejects_non_positive_thread_count() -> None:
    plan = create_profile_plan([configured_model()], estimator=lambda *_: 1)

    with pytest.raises(ProfilingError, match="threads must be at least 1"):
        execute_profile_plan([configured_model()], plan, runner=lambda *_: [], threads=0)


def test_high_cardinality_string_dimension_is_skipped_without_stopping_date_dimension() -> None:
    model = configured_model().model_copy(update={
        "columns": [
            ColumnMetadata(name="event_date", data_type="DATE"),
            ColumnMetadata(name="user_id", data_type="STRING"),
        ],
        "profiling": ProfilingConfig(
            enabled=True,
            dimensions=["user_id", "event_date"],
            max_dimension_values=10_000,
            max_bytes_billed=10_000,
        ),
    })
    plan = create_profile_plan([model], estimator=lambda *_: 1)
    calls: list[str] = []

    def run(sql: str, *_args) -> QueryExecution:
        calls.append(sql)
        dimension = "event_date" if "'event_date' AS dimension_name" in sql else None
        rows = []
        for order, column in enumerate(model.columns):
            rows.append({
                "dimension_name": dimension,
                "dimension_value": "2026-09-01" if dimension else None,
                "record_count": "1000000",
                "column_order": str(order),
                "column_name": column.name,
                "column_type": column.data_type,
                "null_count": "0",
                "null_rate": "0",
                "distinct_count": "50000" if column.name == "user_id" else None,
                "min_value": "2026-09-01" if column.data_type == "DATE" else None,
                "max_value": "2026-09-01" if column.data_type == "DATE" else None,
                "true_count": None,
            })
        return QueryExecution(rows=rows, bytes_billed=1)

    execution = execute_profile_plan([model], plan, runner=run)

    assert len(calls) == 2
    assert execution.complete is True
    assert [result.status for result in execution.results] == ["succeeded", "skipped", "succeeded"]
    skipped = execution.results[1]
    assert skipped.skip_reason == "max_dimension_values"
    assert skipped.distinct_values == 50_000
    assert skipped.maximum_allowed == 10_000
    assert [profile.dimension_name for profile in execution.models[0].profiles] == [None, "event_date"]


def test_small_high_ratio_string_dimension_is_executed() -> None:
    model = configured_model().model_copy(update={
        "columns": [ColumnMetadata(name="user_id", data_type="STRING")],
        "profiling": ProfilingConfig(
            enabled=True,
            dimensions=["user_id"],
            max_dimension_values=90,
        ),
    })
    plan = create_profile_plan([model], estimator=lambda *_: 1)
    calls: list[str] = []

    def row(dimension_value: str | None, count: int, distinct: int) -> dict:
        return {
            "dimension_name": "user_id" if dimension_value is not None else None,
            "dimension_value": dimension_value,
            "record_count": str(count),
            "column_order": "0",
            "column_name": "user_id",
            "column_type": "STRING",
            "null_count": "0",
            "null_rate": "0",
            "distinct_count": str(distinct),
            "min_value": None,
            "max_value": None,
            "true_count": None,
        }

    def run(sql: str, *_args) -> list[dict]:
        calls.append(sql)
        if "'user_id' AS dimension_name" not in sql:
            return [row(None, 100, 90)]
        return [row(f"user-{index}", 2 if index < 10 else 1, 1) for index in range(90)]

    execution = execute_profile_plan([model], plan, runner=run)

    assert len(calls) == 2
    assert execution.complete is True
    assert [result.status for result in execution.results] == ["succeeded", "succeeded"]
    assert execution.models[0].profiles[0].columns[0].distinct_ratio == 0.9


def test_execute_plan_rejects_all_work_before_running_blocked_item() -> None:
    plan = create_profile_plan([configured_model()], estimator=lambda *_: 10_001)
    calls: list[str] = []

    execution = execute_profile_plan(
        [configured_model()],
        plan,
        runner=lambda sql, *_: calls.append(sql) or [],
    )

    assert calls == []
    assert execution.complete is False
    assert execution.results[0].status == "skipped"
    assert execution.results[0].error == "relation estimated bytes exceed max_bytes_billed"


def test_execute_plan_marks_failure_and_skips_remaining_items() -> None:
    model = configured_model().model_copy(update={
        "profiling": ProfilingConfig(enabled=True, dimensions=["event_date", "event_date"]),
    })
    plan = create_profile_plan([model], estimator=lambda *_: 1)

    execution = execute_profile_plan(
        [model],
        plan,
        runner=lambda *_: (_ for _ in ()).throw(ProfilingError("query failed")),
    )

    assert execution.complete is False
    assert [result.status for result in execution.results] == ["failed", "skipped", "skipped"]
    assert execution.results[0].error == "query failed"


def test_ambiguous_selector_requires_unique_id_before_estimation() -> None:
    first = configured_model()
    second = first.model_copy(update={"unique_id": "source.test.raw.events"})
    calls = []
    with pytest.raises(ProfilingError, match="ambiguous selector"):
        create_profile_plan([first, second], selector="events", estimator=lambda *_: calls.append(True) or 1)
    assert not calls
    assert create_profile_plan([first, second], selector=second.unique_id, estimator=lambda *_: 1)[0].model.unique_id == second.unique_id


@pytest.mark.parametrize("local_budget, expected", [(None, 8_000), (5_000, 5_000), (10_000, 8_000)])
@pytest.mark.parametrize("resource_type", ["model", "source"])
def test_shared_budget_uses_smaller_limit_without_changing_yaml(local_budget, expected, resource_type):
    model = configured_model()
    model.resource_type = resource_type
    model.profiling.max_bytes_billed = local_budget
    signature = model.profiling_signature()
    events = []

    items = create_profile_plan(
        [model], max_bytes_billed=8_000, estimator=lambda *_: 3_000, progress=events.append,
    )
    cost = ProfilePlan(items=tuple(items)).relation_costs[model.unique_id]

    assert cost.estimated_bytes == 6_000
    assert cost.max_bytes_billed == expected
    assert cost.executable is (expected >= 6_000)
    assert all(item.max_bytes_billed == expected for item in items)
    assert all(item.model.profiling.max_bytes_billed == local_budget for item in items)
    assert model.profiling_signature() == signature
    assert [event.event for event in events] == [
        "estimate_started", "estimate_completed", "estimate_started", "estimate_completed",
    ]


@pytest.mark.parametrize("threads", [1, 2])
def test_shared_budget_is_per_relation_and_spent_across_its_queries(threads):
    first = configured_model()
    first.profiling.max_bytes_billed = None
    second = first.model_copy(update={
        "unique_id": "source.test.raw.events", "resource_type": "source", "relation_name": "`raw.events`",
    })
    calls = []

    class Adapter(BigQueryAdapter):
        def execute(self, sql, project, location, max_bytes_billed):
            calls.append(("raw" if "`raw.events`" in sql else "model", max_bytes_billed))
            return QueryExecution(rows=[], bytes_billed=1_000)

        def parse_profile_rows(self, model, rows, dimension):
            return [ProfileSlice(dimension_name=dimension, record_count=1, columns=[])]

    items = create_profile_plan([first, second], max_bytes_billed=4_000, estimator=lambda *_: 2_000)
    plan = ProfilePlan(items=tuple(items))
    assert plan.estimated_bytes == 8_000
    assert plan.executable

    execution = execute_profile_plan(
        [first, second], items, max_bytes_billed=4_000, adapter=Adapter(), threads=threads,
    )

    assert execution.complete
    for relation in ["model", "raw"]:
        assert [limit for name, limit in calls if name == relation] == [4_000, 3_000]


@pytest.mark.parametrize("threads", [1, 2])
def test_shared_budget_overage_blocks_other_relations_before_execution(threads):
    first = configured_model()
    first.profiling.max_bytes_billed = None
    second = first.model_copy(deep=True, update={
        "unique_id": "source.test.raw.events", "resource_type": "source", "relation_name": "`raw.events`",
    })
    items = create_profile_plan(
        [first, second], max_bytes_billed=4_000,
        estimator=lambda sql, *_: 1_000 if "`raw.events`" in sql else 2_001,
    )

    execution = execute_profile_plan(
        [first, second], items, max_bytes_billed=4_000, threads=threads,
        runner=lambda *_: pytest.fail("No query should run if any relation exceeds the shared limit"),
    )

    assert not execution.complete
    assert all(result.status == "skipped" for result in execution.results)
    assert execution.results[-1].error == "plan contains another relation over max_bytes_billed"


@pytest.mark.parametrize("maximum", [None, 0, -1, 3_000, 10_000])
def test_execution_rejects_plan_with_different_shared_budget(maximum):
    model = configured_model()
    items = create_profile_plan([model], max_bytes_billed=4_000, estimator=lambda *_: 1_000)

    with pytest.raises(ProfilingError, match="stale or modified plan|max_bytes_billed must be positive"):
        execute_profile_plan(
            [model], items, max_bytes_billed=maximum,
            runner=lambda *_: pytest.fail("Mismatched shared policy must be rejected"),
        )


def test_modified_plan_cannot_relax_shared_budget():
    model = configured_model()
    items = create_profile_plan([model], max_bytes_billed=4_000, estimator=lambda *_: 1_000)
    items = [item.model_copy(update={"max_bytes_billed": 10_000}) for item in items]

    with pytest.raises(ProfilingError, match="stale or modified plan"):
        execute_profile_plan(
            [model], items, max_bytes_billed=4_000,
            runner=lambda *_: pytest.fail("Modified shared budget must be rejected"),
        )
