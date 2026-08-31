import json
from pathlib import Path
from typing import Protocol

import duckdb

from data_profile.models import ColumnProfile, ModelProfile, ProfileSlice


class ProfileNotFoundError(Exception):
    pass


class ProfileRepository(Protocol):
    def list_models(self) -> list[ModelProfile]: ...

    def get_model(self, model_name: str) -> ModelProfile: ...


class JsonProfileRepository:
    def __init__(self, fixture_path: Path):
        self.fixture_path = fixture_path

    def list_models(self) -> list[ModelProfile]:
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return [ModelProfile.model_validate(item) for item in payload]

    def get_model(self, model_name: str) -> ModelProfile:
        try:
            return next(model for model in self.list_models() if model.name == model_name)
        except StopIteration as error:
            raise ProfileNotFoundError(model_name) from error


class DuckDBProfileRepository:
    def __init__(self, models_path: Path, profiles_path: Path):
        self.models_path = models_path
        self.profiles_path = profiles_path

    def list_models(self) -> list[ModelProfile]:
        with duckdb.connect() as connection:
            rows = connection.execute(
                """
                SELECT model_name, database_name, schema_name, description,
                       materialization, tags_json, tests_json, profiled_at
                FROM read_parquet(?)
                ORDER BY model_name
                """,
                [str(self.models_path)],
            ).fetchall()
        return [self._build_model(row) for row in rows]

    def get_model(self, model_name: str) -> ModelProfile:
        with duckdb.connect() as connection:
            row = connection.execute(
                """
                SELECT model_name, database_name, schema_name, description,
                       materialization, tags_json, tests_json, profiled_at
                FROM read_parquet(?)
                WHERE model_name = ?
                """,
                [str(self.models_path), model_name],
            ).fetchone()
        if row is None:
            raise ProfileNotFoundError(model_name)
        return self._build_model(row)

    def _build_model(self, row: tuple) -> ModelProfile:
        model_name = row[0]
        with duckdb.connect() as connection:
            profile_rows = connection.execute(
                """
                SELECT profile_order, dimension_name, dimension_value, record_count,
                       column_name, column_type, column_description, null_count,
                       null_rate, distinct_count, min_value, max_value, true_count
                FROM read_parquet(?)
                WHERE model_name = ?
                ORDER BY profile_order, column_order
                """,
                [str(self.profiles_path), model_name],
            ).fetchall()

        slices: list[ProfileSlice] = []
        current_order: int | None = None
        current_columns: list[ColumnProfile] = []
        current_metadata: tuple[str | None, str | None, int] | None = None
        for profile_row in profile_rows:
            profile_order = profile_row[0]
            if current_order is not None and profile_order != current_order:
                assert current_metadata is not None
                slices.append(ProfileSlice(
                    dimension_name=current_metadata[0],
                    dimension_value=current_metadata[1],
                    record_count=current_metadata[2],
                    columns=current_columns,
                ))
                current_columns = []
            current_order = profile_order
            current_metadata = (profile_row[1], profile_row[2], profile_row[3])
            current_columns.append(ColumnProfile(
                name=profile_row[4],
                data_type=profile_row[5],
                description=profile_row[6],
                null_count=profile_row[7],
                null_rate=profile_row[8],
                distinct_count=profile_row[9],
                min_value=self._decode_value(profile_row[10], profile_row[5]),
                max_value=self._decode_value(profile_row[11], profile_row[5]),
                true_count=profile_row[12],
            ))
        if current_metadata is not None:
            slices.append(ProfileSlice(
                dimension_name=current_metadata[0],
                dimension_value=current_metadata[1],
                record_count=current_metadata[2],
                columns=current_columns,
            ))

        return ModelProfile(
            name=model_name,
            database=row[1],
            schema_name=row[2],
            description=row[3],
            materialization=row[4],
            tags=json.loads(row[5]),
            tests=json.loads(row[6]),
            profiled_at=row[7],
            profiles=slices,
        )

    @staticmethod
    def _decode_value(value: str | None, column_type: str) -> str | int | float | None:
        if value is None:
            return None
        if column_type == "INT64":
            return int(value)
        if column_type == "FLOAT64":
            return float(value)
        return value
