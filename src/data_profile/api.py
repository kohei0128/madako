from dataclasses import dataclass
from pathlib import Path

from data_profile.exceptions import DataProfileError, PlanningError, StorageOperationError
from data_profile.warehouse import WarehouseAdapter, complete_adapter
from data_profile.models import ModelProfile
from data_profile.planning import Estimator, ProfileItemResult, ProfilePlanItem, Runner, create_profile_plan, execute_profile_plan
from data_profile.storage import ParquetProfileStorage, ProfileStorage, import_dbt_profiles


@dataclass(frozen=True)
class ProfilePlan:
    items: tuple[ProfilePlanItem, ...]

    @property
    def executable(self) -> bool:
        return bool(self.items) and all(item.executable for item in self.items)

    @property
    def estimated_bytes(self) -> int:
        return sum(item.estimated_bytes for item in self.items)


@dataclass(frozen=True)
class ProfileResult:
    plan: ProfilePlan
    items: tuple[ProfileItemResult, ...]
    profiled_models: tuple[str, ...]
    models_path: Path
    profiles_path: Path
    storage_updated: bool

    @property
    def successful(self) -> bool:
        return self.storage_updated and all(item.status == "succeeded" for item in self.items)

    @property
    def failed(self) -> tuple[ProfileItemResult, ...]:
        return tuple(item for item in self.items if item.status == "failed")

    @property
    def skipped(self) -> tuple[ProfileItemResult, ...]:
        return tuple(item for item in self.items if item.status == "skipped")


class DataProfile:
    """Public entry point for planning and running dbt data profiles."""

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        estimator: Estimator | None = None,
        runner: Runner | None = None,
        adapter: WarehouseAdapter | None = None,
        storage: ProfileStorage | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self._storage = storage if storage is not None else ParquetProfileStorage(self.storage_dir)
        warehouse = complete_adapter(adapter)
        self._adapter = warehouse
        self._estimator = estimator if estimator is not None else warehouse.estimate
        self._runner = runner if runner is not None else warehouse.execute

    @classmethod
    def from_storage(
        cls,
        storage_dir: str | Path,
        *,
        estimator: Estimator | None = None,
        runner: Runner | None = None,
        adapter: WarehouseAdapter | None = None,
        storage: ProfileStorage | None = None,
    ) -> "DataProfile":
        return cls(storage_dir, estimator=estimator, runner=runner, adapter=adapter, storage=storage)

    @classmethod
    def from_dbt_project(
        cls,
        project_dir: str | Path,
        storage_dir: str | Path,
        *,
        estimator: Estimator | None = None,
        runner: Runner | None = None,
        adapter: WarehouseAdapter | None = None,
        storage: ProfileStorage | None = None,
    ) -> "DataProfile":
        instance = cls(storage_dir, estimator=estimator, runner=runner, adapter=adapter, storage=storage)
        try:
            import_dbt_profiles(Path(project_dir), instance._storage)
        except DataProfileError:
            raise
        except Exception as error:
            raise StorageOperationError("could not import dbt metadata into profile storage") from error
        return instance

    def models(self) -> list[ModelProfile]:
        try:
            return self._storage.load()
        except DataProfileError:
            raise
        except Exception as error:
            raise StorageOperationError("could not load profile storage") from error

    def _save(self, models: list[ModelProfile]) -> tuple[Path, Path]:
        try:
            return self._storage.save(models)
        except DataProfileError:
            raise
        except Exception as error:
            raise StorageOperationError("could not save profile storage") from error

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
            adapter=self._adapter,
            estimator=self._estimator,
        )
        return ProfilePlan(tuple(items))

    def run(self, plan: ProfilePlan) -> ProfileResult:
        if not plan.items:
            raise PlanningError("cannot run an empty profile plan")
        models = self.models()
        execution = execute_profile_plan(
            models,
            list(plan.items),
            adapter=self._adapter,
            runner=self._runner,
        )
        models_path, profiles_path = self._storage.paths
        if execution.complete:
            models_path, profiles_path = self._save(execution.models)
        profiled_models = (
            tuple(dict.fromkeys(item.model.name for item in plan.items))
            if execution.complete else ()
        )
        return ProfileResult(
            plan=plan,
            items=execution.results,
            profiled_models=profiled_models,
            models_path=models_path,
            profiles_path=profiles_path,
            storage_updated=execution.complete,
        )

    def profile(
        self,
        *,
        select: str | None = None,
        project: str | None = None,
        location: str = "asia-northeast1",
    ) -> ProfileResult:
        return self.run(self.plan(select=select, project=project, location=location))
