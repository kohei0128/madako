from dataclasses import dataclass
from pathlib import Path

from data_profile.bigquery_profile import dry_run, execute_profile
from data_profile.models import ModelProfile
from data_profile.planning import Estimator, ProfilePlanItem, Runner, create_profile_plan, execute_profile_plan
from data_profile.repository import DuckDBProfileRepository
from data_profile.storage import MODELS_FILENAME, PROFILES_FILENAME, build_dbt_artifact_storage, write_profile_storage


@dataclass(frozen=True)
class ProfilePlan:
    items: tuple[ProfilePlanItem, ...]

    @property
    def executable(self) -> bool:
        return all(item.executable for item in self.items)

    @property
    def estimated_bytes(self) -> int:
        return sum(item.estimated_bytes for item in self.items)


@dataclass(frozen=True)
class ProfileResult:
    plan: ProfilePlan
    profiled_models: tuple[str, ...]
    models_path: Path
    profiles_path: Path


class DataProfile:
    """Public entry point for planning and running dbt data profiles."""

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        estimator: Estimator = dry_run,
        runner: Runner = execute_profile,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self._estimator = estimator
        self._runner = runner

    @classmethod
    def from_storage(
        cls,
        storage_dir: str | Path,
        *,
        estimator: Estimator = dry_run,
        runner: Runner = execute_profile,
    ) -> "DataProfile":
        return cls(storage_dir, estimator=estimator, runner=runner)

    @classmethod
    def from_dbt_project(
        cls,
        project_dir: str | Path,
        storage_dir: str | Path,
        *,
        estimator: Estimator = dry_run,
        runner: Runner = execute_profile,
    ) -> "DataProfile":
        instance = cls(storage_dir, estimator=estimator, runner=runner)
        build_dbt_artifact_storage(Path(project_dir), instance.storage_dir)
        return instance

    def models(self) -> list[ModelProfile]:
        return self._repository().list_models()

    def plan(
        self,
        *,
        select: str | None = None,
        project: str | None = None,
        location: str = "asia-northeast1",
    ) -> ProfilePlan:
        items = create_profile_plan(
            self.models(),
            selector=select,
            project=project,
            location=location,
            estimator=self._estimator,
        )
        return ProfilePlan(tuple(items))

    def run(self, plan: ProfilePlan) -> ProfileResult:
        models = self.models()
        updated_models = execute_profile_plan(models, list(plan.items), runner=self._runner)
        models_path, profiles_path = write_profile_storage(updated_models, self.storage_dir)
        profiled_models = tuple(dict.fromkeys(item.model.name for item in plan.items))
        return ProfileResult(
            plan=plan,
            profiled_models=profiled_models,
            models_path=models_path,
            profiles_path=profiles_path,
        )

    def profile(
        self,
        *,
        select: str | None = None,
        project: str | None = None,
        location: str = "asia-northeast1",
    ) -> ProfileResult:
        return self.run(self.plan(select=select, project=project, location=location))

    def _repository(self) -> DuckDBProfileRepository:
        return DuckDBProfileRepository(
            self.storage_dir / MODELS_FILENAME,
            self.storage_dir / PROFILES_FILENAME,
        )
