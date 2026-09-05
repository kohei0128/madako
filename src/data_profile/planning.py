from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from data_profile.exceptions import DataProfileError, PlanningError, ResultValidationError, WarehouseError
from data_profile.models import ModelProfile
from data_profile.warehouse import WarehouseAdapter, complete_adapter


@dataclass(frozen=True)
class ProfilePlanItem:
    model: ModelProfile
    dimension: str | None
    sql: str
    project: str
    location: str
    estimated_bytes: int
    max_bytes_billed: int
    skipped_columns: tuple[str, ...]
    model_signature: str = ""

    @property
    def executable(self) -> bool:
        return self.estimated_bytes <= self.max_bytes_billed


Estimator = Callable[[str, str, str], int]
Runner = Callable[[str, str, str, int], list[dict]]


@dataclass(frozen=True)
class ProfileItemResult:
    item: ProfilePlanItem
    status: Literal["succeeded", "failed", "skipped"]
    row_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ProfilePlanExecution:
    models: list[ModelProfile]
    results: tuple[ProfileItemResult, ...]
    complete: bool


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
