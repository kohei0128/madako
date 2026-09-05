from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from data_profile.exceptions import DataProfileError, PlanningError, ResultValidationError, WarehouseError
from data_profile.models import ModelProfile
from data_profile.warehouse import WarehouseAdapter, complete_adapter


class ProfilePlanItem(BaseModel):
    """Plan item for a single relation and dimension profile query.

    Contains the SQL query, cost estimation, and configuration for profiling
    a specific model or source with an optional dimension breakdown.
    """

    model: ModelProfile = Field(description="Model or source to profile")
    dimension: str | None = Field(default=None, description="Dimension column for breakdown, or None for overall")
    sql: str = Field(description="Generated SQL query for profiling")
    project: str = Field(description="BigQuery project ID to execute the query")
    location: str = Field(description="BigQuery location/region for query execution")
    estimated_bytes: Annotated[int, Field(ge=0)] = Field(description="Estimated bytes to be processed")
    max_bytes_billed: Annotated[int, Field(gt=0)] = Field(description="Maximum allowed bytes to process")
    skipped_columns: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Column names skipped due to unsupported data types"
    )
    model_signature: str = Field(default="", description="Hash of model config for staleness detection")

    @property
    def executable(self) -> bool:
        """Query is within the configured max_bytes_billed limit."""
        return self.estimated_bytes <= self.max_bytes_billed

    model_config = {"frozen": True}


Estimator = Callable[[str, str, str], int]
Runner = Callable[[str, str, str, int], list[dict]]


class ProfileItemResult(BaseModel):
    """Execution result for a single profile plan item.

    Indicates whether the query succeeded, failed, or was skipped,
    along with row counts and error details if applicable.
    """

    item: ProfilePlanItem = Field(description="The plan item that was executed")
    status: Literal["succeeded", "failed", "skipped"] = Field(description="Execution status")
    row_count: Annotated[int, Field(ge=0)] = Field(default=0, description="Number of rows returned")
    error: str | None = Field(default=None, description="Error message if status is 'failed'")

    model_config = {"frozen": True}


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

    plan: list[ProfilePlanItem] = []
    for model in enabled:
        skipped = tuple(column.name for column in model.columns if column.data_type not in supported_types)
        query_project = project or model.database
        dimensions: list[str | None] = model.profiling.dimensions or [None]
        for dimension in dimensions:
            try:
                sql = warehouse.build_profile_query(model, dimension)
                estimated = estimate(sql, query_project, location)
            except DataProfileError:
                raise
            except Exception as error:
                raise WarehouseError(
                    f"warehouse could not plan {model.unique_id} / {dimension or 'Overall'}"
                ) from error
            plan.append(ProfilePlanItem(
                model=model.model_copy(deep=True),
                dimension=dimension,
                sql=sql,
                project=query_project,
                location=location,
                estimated_bytes=estimated,
                max_bytes_billed=model.profiling.max_bytes_billed,
                skipped_columns=skipped,
                model_signature=model.profiling_signature(),
            ))
    return plan


def execute_profile_plan(
    models: list[ModelProfile],
    plan: list[ProfilePlanItem],
    *,
    adapter: WarehouseAdapter | None = None,
    runner: Runner | None = None,
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
        return ProfilePlanExecution(models=models, results=results, complete=False)

    profiles_by_model: dict[str, list] = {}
    results: list[ProfileItemResult] = []
    for index, item in enumerate(plan):
        try:
            rows = execute(item.sql, item.project, item.location, item.max_bytes_billed)
            profiles = warehouse.parse_profile_rows(item.model, rows, item.dimension)
            overall = [profile for profile in profiles if profile.dimension_name is None]
            dimensions = [profile for profile in profiles if profile.dimension_name is not None]
            if len(overall) != 1:
                raise ResultValidationError("adapter result must contain exactly one Overall profile")
            if any(profile.dimension_name != item.dimension for profile in dimensions):
                raise ResultValidationError("adapter result contains an unexpected dimension")
        except Exception as error:
            results.append(ProfileItemResult(item=item, status="failed", error=str(error)))
            results.extend(ProfileItemResult(
                item=remaining,
                status="skipped",
                error="not executed because an earlier item failed",
            ) for remaining in plan[index + 1:])
            return ProfilePlanExecution(models=models, results=tuple(results), complete=False)
        collected = profiles_by_model.setdefault(item.model.unique_id, [])
        if not collected:
            collected.extend(profile for profile in profiles if profile.dimension_name is None)
        collected.extend(profile for profile in profiles if profile.dimension_name is not None)
        results.append(ProfileItemResult(item=item, status="succeeded", row_count=len(rows)))

    profiled_at = datetime.now(UTC)
    updated_models = [
        model.model_copy(update={"profiles": profiles_by_model[model.unique_id], "profiled_at": profiled_at})
        if model.unique_id in profiles_by_model
        else model
        for model in models
    ]
    return ProfilePlanExecution(models=updated_models, results=tuple(results), complete=True)
