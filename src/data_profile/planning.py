from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from data_profile.exceptions import DataProfileError, PlanningError, ResultValidationError, WarehouseError
from data_profile.models import PROFILE_COMPUTATION_VERSION, ModelProfile
from data_profile.warehouse import QueryExecution, WarehouseAdapter, complete_adapter


class ProfilePlanItem(BaseModel):
    """Plan item for a single relation and dimension profile query.

    Contains the SQL query, optional cost estimation, and configuration for profiling
    a specific model or source with an optional dimension breakdown.
    """

    model: ModelProfile = Field(description="Model or source to profile")
    dimension: str | None = Field(default=None, description="Dimension column for breakdown, or None for overall")
    sql: str = Field(description="Generated SQL query for profiling")
    project: str = Field(description="BigQuery project ID to execute the query")
    location: str = Field(description="BigQuery location/region for query execution")
    estimated_bytes: Annotated[int, Field(ge=0)] | None = Field(
        default=None, description="Estimated bytes, or None when cost checking is disabled"
    )
    max_bytes_billed: Annotated[int, Field(gt=0)] | None = Field(
        default=None, description="Maximum bytes to process, or None when cost checking is disabled"
    )
    skipped_columns: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Column names skipped due to unsupported data types"
    )
    model_signature: str = Field(default="", description="Hash of model config for staleness detection")

    @model_validator(mode="after")
    def validate_cost_check(self) -> "ProfilePlanItem":
        if (self.estimated_bytes is None) != (self.max_bytes_billed is None):
            raise ValueError("estimated_bytes and max_bytes_billed must both be set or both be None")
        return self

    @property
    def executable(self) -> bool:
        """Query is within the configured max_bytes_billed limit."""
        if self.estimated_bytes is None:
            return True
        assert self.max_bytes_billed is not None
        return self.estimated_bytes <= self.max_bytes_billed

    model_config = {"frozen": True}


Estimator = Callable[[str, str, str], int]
Runner = Callable[[str, str, str, int | None], QueryExecution | list[dict]]


class ProfileItemResult(BaseModel):
    """Execution result for a single profile plan item.

    Indicates whether the query succeeded, failed, or was skipped,
    along with row counts and error details if applicable.
    """

    item: ProfilePlanItem = Field(description="The plan item that was executed")
    status: Literal["succeeded", "failed", "skipped"] = Field(description="Execution status")
    row_count: Annotated[int, Field(ge=0)] = Field(default=0, description="Number of rows returned")
    error: str | None = Field(default=None, description="Error message if status is 'failed'")
    skip_reason: Literal["max_dimension_values"] | None = Field(
        default=None, description="Reason for an intentional dimension skip"
    )
    distinct_values: Annotated[int | None, Field(ge=0)] = None
    maximum_allowed: Annotated[int | None, Field(gt=0)] = None
    bytes_processed: Annotated[int | None, Field(ge=0)] = None
    bytes_billed: Annotated[int | None, Field(ge=0)] = None

    model_config = {"frozen": True}


@dataclass(frozen=True)
class ProfileProgress:
    """A synchronous progress event emitted while planning and running profiles."""

    event: Literal[
        "estimate_started", "estimate_completed", "estimate_failed",
        "execute_started", "execute_completed", "execute_skipped",
        "storage_started", "storage_completed", "storage_discarded", "storage_failed",
    ]
    current: int = 0
    total: int = 0
    model: ModelProfile | None = None
    dimension: str | None = None
    item: ProfilePlanItem | None = None
    result: ProfileItemResult | None = None
    error: str | None = None


ProgressCallback = Callable[[ProfileProgress], None]


def _notify(progress: ProgressCallback | None, event: ProfileProgress) -> None:
    if progress is not None:
        progress(event)


class ProfilePlanExecution(BaseModel):
    """Internal execution state tracking all item results.

    Used internally during profile execution to accumulate results
    and determine whether all items succeeded.
    """

    models: list[ModelProfile] = Field(description="Updated models with profile results")
    results: tuple[ProfileItemResult, ...] = Field(description="Results for each plan item")
    complete: bool = Field(description="True if all items succeeded")

    model_config = {"frozen": True}


def create_profile_plan(
    models: list[ModelProfile],
    *,
    selector: str | None = None,
    project: str | None = None,
    location: str = "asia-northeast1",
    adapter: WarehouseAdapter | None = None,
    estimator: Estimator | None = None,
    progress: ProgressCallback | None = None,
) -> list[ProfilePlanItem]:
    warehouse = complete_adapter(adapter)
    estimate = estimator or warehouse.estimate
    supported_types = warehouse.supported_types
    enabled = [model for model in models if model.profiling.enabled]
    if selector:
        enabled = [model for model in enabled if selector in {model.name, model.unique_id}]
        if len(enabled) > 1:
            raise PlanningError("ambiguous selector; use a unique_id")
        if not enabled:
            raise PlanningError(f"selector did not match an enabled relation: {selector}")
    if not enabled:
        raise PlanningError("no relations have meta.profiling.enabled=true")

    targets = [
        (model, dimension)
        for model in enabled
        for dimension in [None, *model.profiling.dimensions]
    ]
    plan: list[ProfilePlanItem] = []
    for index, (model, dimension) in enumerate(targets, start=1):
        skipped = tuple(column.name for column in model.columns if column.data_type not in supported_types)
        query_project = project or model.database
        try:
            sql = warehouse.build_profile_query(model, dimension)
            estimated = None
            if model.profiling.max_bytes_billed is not None:
                _notify(progress, ProfileProgress(
                    event="estimate_started", current=index, total=len(targets),
                    model=model, dimension=dimension,
                ))
                estimated = estimate(sql, query_project, location)
        except DataProfileError as error:
            _notify(progress, ProfileProgress(
                event="estimate_failed", current=index, total=len(targets),
                model=model, dimension=dimension, error=str(error),
            ))
            raise
        except Exception as error:
            wrapped = WarehouseError(
                f"warehouse could not plan {model.unique_id} / {dimension or 'Overall'}"
            )
            _notify(progress, ProfileProgress(
                event="estimate_failed", current=index, total=len(targets),
                model=model, dimension=dimension, error=str(wrapped),
            ))
            raise wrapped from error
        item = ProfilePlanItem(
            model=model.model_copy(deep=True),
            dimension=dimension,
            sql=sql,
            project=query_project,
            location=location,
            estimated_bytes=estimated,
            max_bytes_billed=model.profiling.max_bytes_billed,
            skipped_columns=skipped,
            model_signature=model.profiling_signature(),
        )
        plan.append(item)
        if model.profiling.max_bytes_billed is not None:
            _notify(progress, ProfileProgress(
                event="estimate_completed", current=index, total=len(targets),
                model=model, dimension=dimension, item=item,
            ))
    return plan


def execute_profile_plan(
    models: list[ModelProfile],
    plan: list[ProfilePlanItem],
    *,
    adapter: WarehouseAdapter | None = None,
    runner: Runner | None = None,
    progress: ProgressCallback | None = None,
) -> ProfilePlanExecution:
    warehouse = complete_adapter(adapter)
    execute = runner or warehouse.execute
    current = {model.unique_id: model for model in models}
    if len(current) != len(models):
        raise PlanningError("duplicate relation unique_id")
    for item in plan:
        model = current.get(item.model.unique_id)
        if (model is None or model.profiling_signature() != item.model_signature
                or item.model.profiling_signature() != item.model_signature):
            raise PlanningError("stale or modified plan; create a new plan before running")
    blocked = [item for item in plan if not item.executable]
    if blocked:
        results = tuple(ProfileItemResult(
            item=item,
            status="skipped",
            error=(
                "estimated bytes exceed max_bytes_billed"
                if not item.executable
                else "plan contains another item over max_bytes_billed"
            ),
        ) for item in plan)
        for index, result in enumerate(results, start=1):
            _notify(progress, ProfileProgress(
                event="execute_skipped", current=index, total=len(plan),
                model=result.item.model, dimension=result.item.dimension,
                item=result.item, result=result,
            ))
        return ProfilePlanExecution(models=models, results=results, complete=False)

    profiles_by_model: dict[str, list] = {}
    results: list[ProfileItemResult] = []
    for index, item in enumerate(plan):
        current_index = index + 1
        collected = profiles_by_model.setdefault(item.model.unique_id, [])
        if item.dimension is not None:
            dimension_column = next(
                column for column in item.model.columns if column.name == item.dimension
            )
            if dimension_column.data_type == "STRING":
                overall = next(
                    (profile for profile in collected if profile.dimension_name is None), None
                )
                if overall is None:
                    raise PlanningError(
                        f"Overall profile must run before dimension {item.dimension}"
                    )
                dimension_metric = next(
                    (column for column in overall.columns if column.name == item.dimension), None
                )
                if dimension_metric is None or dimension_metric.distinct_count is None:
                    raise PlanningError(
                        f"Overall profile did not produce distinct count for {item.dimension}"
                    )
                distinct_values = dimension_metric.distinct_count
                maximum_allowed = item.model.profiling.max_dimension_values
                if distinct_values > maximum_allowed:
                    result = ProfileItemResult(
                        item=item,
                        status="skipped",
                        error=(
                            f"dimension has {distinct_values:,} distinct values; "
                            f"maximum allowed is {maximum_allowed:,}"
                        ),
                        skip_reason="max_dimension_values",
                        distinct_values=distinct_values,
                        maximum_allowed=maximum_allowed,
                    )
                    results.append(result)
                    _notify(progress, ProfileProgress(
                        event="execute_skipped", current=current_index, total=len(plan),
                        model=item.model, dimension=item.dimension, item=item, result=result,
                    ))
                    continue
        _notify(progress, ProfileProgress(
            event="execute_started", current=current_index, total=len(plan),
            model=item.model, dimension=item.dimension, item=item,
        ))
        try:
            execution = execute(item.sql, item.project, item.location, item.max_bytes_billed)
            if isinstance(execution, QueryExecution):
                rows = execution.rows
                bytes_processed = execution.bytes_processed
                bytes_billed = execution.bytes_billed
            else:
                rows = execution
                bytes_processed = None
                bytes_billed = None
            profiles = warehouse.parse_profile_rows(item.model, rows, item.dimension)
            overall = [profile for profile in profiles if profile.dimension_name is None]
            dimensions = [profile for profile in profiles if profile.dimension_name is not None]
            if item.dimension is None and (len(overall) != 1 or dimensions):
                raise ResultValidationError("adapter result must contain exactly one Overall profile")
            if item.dimension is not None and (
                overall or any(profile.dimension_name != item.dimension for profile in dimensions)
            ):
                raise ResultValidationError("adapter result contains an unexpected dimension")
            if item.dimension is not None:
                model_overall = next(
                    profile for profile in collected if profile.dimension_name is None
                )
                if sum(profile.record_count for profile in dimensions) != model_overall.record_count:
                    raise ResultValidationError(
                        "dimension record counts do not match Overall; result may be incomplete"
                    )
        except Exception as error:
            failed = ProfileItemResult(item=item, status="failed", error=str(error))
            results.append(failed)
            _notify(progress, ProfileProgress(
                event="execute_completed", current=current_index, total=len(plan),
                model=item.model, dimension=item.dimension, item=item, result=failed,
            ))
            remaining_results = [ProfileItemResult(
                item=remaining,
                status="skipped",
                error="not executed because an earlier item failed",
            ) for remaining in plan[index + 1:]]
            results.extend(remaining_results)
            for offset, skipped in enumerate(remaining_results, start=current_index + 1):
                _notify(progress, ProfileProgress(
                    event="execute_skipped", current=offset, total=len(plan),
                    model=skipped.item.model, dimension=skipped.item.dimension,
                    item=skipped.item, result=skipped,
                ))
            return ProfilePlanExecution(models=models, results=tuple(results), complete=False)
        collected.extend(profiles)
        result = ProfileItemResult(
            item=item,
            status="succeeded",
            row_count=len(rows),
            bytes_processed=bytes_processed,
            bytes_billed=bytes_billed,
        )
        results.append(result)
        _notify(progress, ProfileProgress(
            event="execute_completed", current=current_index, total=len(plan),
            model=item.model, dimension=item.dimension, item=item, result=result,
        ))

    profiled_at = datetime.now(UTC)
    updated_models = [
        model.model_copy(update={
            "profiles": profiles_by_model[model.unique_id],
            "profiled_at": profiled_at,
            "profile_version": PROFILE_COMPUTATION_VERSION,
        })
        if model.unique_id in profiles_by_model
        else model
        for model in models
    ]
    return ProfilePlanExecution(models=updated_models, results=tuple(results), complete=True)
