from collections.abc import Callable
from dataclasses import dataclass

from data_profile.bigquery_profile import SUPPORTED_TYPES, ProfilingError, dry_run, generate_profile_sql
from data_profile.models import ModelProfile


@dataclass(frozen=True)
class ProfilePlanItem:
    model: ModelProfile
    dimension: str
    sql: str
    estimated_bytes: int
    max_bytes_billed: int
    skipped_columns: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return self.estimated_bytes <= self.max_bytes_billed


Estimator = Callable[[str, str, str], int]


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
        if not model.profiling.dimensions:
            raise ProfilingError(f"no profiling dimensions configured for {model.name}")
        skipped = tuple(column.name for column in model.columns if column.data_type not in SUPPORTED_TYPES)
        query_project = project or model.database
        for dimension in model.profiling.dimensions:
            sql = generate_profile_sql(model, dimension)
            estimated = estimator(sql, query_project, location)
            plan.append(ProfilePlanItem(
                model=model,
                dimension=dimension,
                sql=sql,
                estimated_bytes=estimated,
                max_bytes_billed=model.profiling.max_bytes_billed,
                skipped_columns=skipped,
            ))
    return plan
