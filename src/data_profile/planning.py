from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from data_profile.bigquery_profile import SUPPORTED_TYPES, ProfilingError, dry_run, execute_profile, generate_profile_sql, rows_to_profiles
from data_profile.models import ModelProfile


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
    estimator: Estimator = dry_run,
) -> list[ProfilePlanItem]:
    enabled = [model for model in models if model.profiling.enabled]
    if selector:
        enabled = [model for model in enabled if selector in {model.name, model.unique_id}]
        if len(enabled) > 1:
            raise ProfilingError("ambiguous selector; use a unique_id")
        if not enabled:
            raise ProfilingError(f"selector did not match an enabled relation: {selector}")
    if not enabled:
        raise ProfilingError("no relations have meta.profiling.enabled=true")

    plan: list[ProfilePlanItem] = []
    for model in enabled:
        skipped = tuple(column.name for column in model.columns if column.data_type not in SUPPORTED_TYPES)
        query_project = project or model.database
        dimensions: list[str | None] = model.profiling.dimensions or [None]
        for dimension in dimensions:
            sql = generate_profile_sql(model, dimension)
            estimated = estimator(sql, query_project, location)
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
    runner: Runner = execute_profile,
) -> ProfilePlanExecution:
    current = {model.unique_id: model for model in models}
    if len(current) != len(models):
        raise ProfilingError("duplicate relation unique_id")
    for item in plan:
        model = current.get(item.model.unique_id)
        if (model is None or model.profiling_signature() != item.model_signature
                or item.model.profiling_signature() != item.model_signature):
            raise ProfilingError("stale or modified plan; create a new plan before running")
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
            rows = runner(item.sql, item.project, item.location, item.max_bytes_billed)
            profiles = rows_to_profiles(item.model, rows, dimension=item.dimension, validate=True)
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
