from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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

    @property
    def executable(self) -> bool:
        return self.estimated_bytes <= self.max_bytes_billed


Estimator = Callable[[str, str, str], int]
Runner = Callable[[str, str, str, int], list[dict]]


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
                model=model,
                dimension=dimension,
                sql=sql,
                project=query_project,
                location=location,
                estimated_bytes=estimated,
                max_bytes_billed=model.profiling.max_bytes_billed,
                skipped_columns=skipped,
            ))
    return plan


def execute_profile_plan(
    models: list[ModelProfile],
    plan: list[ProfilePlanItem],
    *,
    runner: Runner = execute_profile,
) -> list[ModelProfile]:
    blocked = [item for item in plan if not item.executable]
    if blocked:
        names = ", ".join(f"{item.model.name}:{item.dimension or 'Overall'}" for item in blocked)
        raise ProfilingError(f"profile plan contains items over max_bytes_billed: {names}")

    profiles_by_model: dict[str, list] = {}
    for item in plan:
        rows = runner(item.sql, item.project, item.location, item.max_bytes_billed)
        profiles = rows_to_profiles(item.model, rows)
        collected = profiles_by_model.setdefault(item.model.unique_id, [])
        if not collected:
            collected.extend(profile for profile in profiles if profile.dimension_name is None)
        collected.extend(profile for profile in profiles if profile.dimension_name is not None)

    profiled_at = datetime.now(UTC)
    return [
        model.model_copy(update={"profiles": profiles_by_model[model.unique_id], "profiled_at": profiled_at})
        if model.unique_id in profiles_by_model
        else model
        for model in models
    ]
