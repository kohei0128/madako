from pathlib import Path

from pydantic import BaseModel, Field

from data_profile.exceptions import DataProfileError, PlanningError, StorageOperationError
from data_profile.warehouse import WarehouseAdapter, complete_adapter
from data_profile.models import ModelProfile
from data_profile.planning import (
    Estimator,
    ProfileItemResult,
    ProfilePlanItem,
    ProfileProgress,
    ProgressCallback,
    Runner,
    create_profile_plan,
    execute_profile_plan,
)
from data_profile.storage import ParquetProfileStorage, ProfileStorage, import_dbt_profiles


class ProfilePlan(BaseModel):
    """Profile execution plan containing SQL and optional cost estimates.

    A plan contains one or more items, each representing a profiling query
    for a specific model/source and optional dimension. The plan can be
    inspected before execution. Cost estimates are present only when the
    relation config sets max_bytes_billed.
    """

    items: tuple[ProfilePlanItem, ...] = Field(
        description="Plan items for each relation and dimension combination"
    )

    @property
    def executable(self) -> bool:
        """All items are within their configured max_bytes_billed limit."""
        return bool(self.items) and all(item.executable for item in self.items)

    @property
    def estimated_bytes(self) -> int | None:
        """Total estimated bytes, or None if any item skipped cost checking."""
        if any(item.estimated_bytes is None for item in self.items):
            return None
        return sum(item.estimated_bytes or 0 for item in self.items)

    model_config = {"frozen": True}


class ProfileResult(BaseModel):
    """Result of profile execution including success/failure status.

    Contains the executed plan, per-item results, and paths to the saved
    storage files. The storage_updated flag indicates whether all items
    succeeded and results were persisted.
    """

    plan: ProfilePlan = Field(description="The plan that was executed")
    items: tuple[ProfileItemResult, ...] = Field(description="Per-item execution results")
    profiled_models: tuple[str, ...] = Field(
        description="Names of models successfully profiled (empty if storage not updated)"
    )
    models_path: Path = Field(description="Path to models.parquet")
    profiles_path: Path = Field(description="Path to column_profiles.parquet")
    storage_updated: bool = Field(
        description="True if all items succeeded and storage was saved"
    )

    @property
    def successful(self) -> bool:
        """No items failed and completed results were saved."""
        return self.storage_updated and not self.failed

    @property
    def failed(self) -> tuple[ProfileItemResult, ...]:
        """Items that failed during execution."""
        return tuple(item for item in self.items if item.status == "failed")

    @property
    def skipped(self) -> tuple[ProfileItemResult, ...]:
        """Items that were skipped (due to size limit or earlier failure)."""
        return tuple(item for item in self.items if item.status == "skipped")

    model_config = {"frozen": True}


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
        progress: ProgressCallback | None = None,
    ) -> ProfilePlan:
        items = create_profile_plan(
            self.models(),
            selector=select,
            project=project,
            location=location,
            adapter=self._adapter,
            estimator=self._estimator,
            progress=progress,
        )
        return ProfilePlan(items=tuple(items))

    def run(
        self,
        plan: ProfilePlan,
        *,
        progress: ProgressCallback | None = None,
    ) -> ProfileResult:
        if not plan.items:
            raise PlanningError("cannot run an empty profile plan")
        models = self.models()
        execution = execute_profile_plan(
            models,
            list(plan.items),
            adapter=self._adapter,
            runner=self._runner,
            progress=progress,
        )
        models_path, profiles_path = self._storage.paths
        if execution.complete:
            if progress is not None:
                progress(ProfileProgress(
                    event="storage_started",
                    current=len(execution.results), total=len(execution.results),
                ))
            try:
                models_path, profiles_path = self._save(execution.models)
            except Exception as error:
                if progress is not None:
                    progress(ProfileProgress(
                        event="storage_failed",
                        current=len(execution.results), total=len(execution.results),
                        error=str(error),
                    ))
                raise
            if progress is not None:
                progress(ProfileProgress(
                    event="storage_completed",
                    current=len(execution.results), total=len(execution.results),
                ))
        elif progress is not None:
            progress(ProfileProgress(
                event="storage_discarded",
                current=sum(item.status == "succeeded" for item in execution.results),
                total=len(execution.results),
            ))
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
        progress: ProgressCallback | None = None,
    ) -> ProfileResult:
        return self.run(
            self.plan(select=select, project=project, location=location, progress=progress),
            progress=progress,
        )
