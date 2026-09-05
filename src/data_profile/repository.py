import json
from pathlib import Path
from typing import Protocol

import duckdb

from data_profile.models import ColumnMetadata, ColumnProfile, ModelProfile, ProfileSlice


class ProfileNotFoundError(Exception):
    pass


class AmbiguousProfileError(ValueError):
    pass


def _select_model(models: list[ModelProfile], identifier: str) -> ModelProfile:
    matches = [model for model in models if model.unique_id == identifier]
    matches = matches or [model for model in models if model.name == identifier]
    if not matches:
        raise ProfileNotFoundError(identifier)
    if len(matches) > 1:
        raise AmbiguousProfileError("ambiguous model name; use unique_id")
    return matches[0]


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
        return _select_model(self.list_models(), model_name)


class DuckDBProfileRepository:
    def __init__(self, models_path: Path, profiles_path: Path):
        self.models_path = models_path
        self.profiles_path = profiles_path

    def list_models(self) -> list[ModelProfile]:
        return self._load()

    def list_metadata(self) -> list[ModelProfile]:
        return self._load(include_profiles=False)

    def get_model(self, model_name: str) -> ModelProfile:
        return self._load(identifier=model_name)[0]

    @staticmethod
    def _schema(connection: duckdb.DuckDBPyConnection, path: Path) -> set[str]:
        columns = {row[0] for row in connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)],
        ).fetchall()}
        if "schema_version" in columns:
            versions = connection.execute(
                "SELECT DISTINCT schema_version FROM read_parquet(?)", [str(path)],
            ).fetchall()
            if any(version != (1,) for version in versions):
                raise ValueError("unsupported profile storage schema version")
        return columns

    def _load(self, identifier: str | None = None, *, include_profiles: bool = True) -> list[ModelProfile]:
        with duckdb.connect() as connection:
            model_columns = self._schema(connection, self.models_path)
            profile_columns = self._schema(connection, self.profiles_path)
            if ("schema_version" in model_columns) != ("schema_version" in profile_columns):
                raise ValueError("inconsistent profile storage schema versions")
            current = "schema_version" in profile_columns
            if current and "unique_id" not in profile_columns:
                raise ValueError("profile storage is missing unique_id")
            profiling = "profiling_json" if "profiling_json" in model_columns else "'{}'"
            rows = connection.execute(
                f"""
                SELECT unique_id, resource_type, model_name, database_name, schema_name,
                       relation_name, description, materialization, tags_json, tests_json,
                       columns_json, {profiling}, profiled_at
                FROM read_parquet(?) ORDER BY model_name, unique_id
                """, [str(self.models_path)],
            ).fetchall()
            ids = [row[0] for row in rows]
            names = [row[2] for row in rows]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate relation unique_id in storage")
            if not current and len(names) != len(set(names)):
                raise ValueError("legacy storage has ambiguous model names; reimport into a new directory and reprofile")
            if identifier is not None:
                matches = [row for row in rows if row[0] == identifier]
                matches = matches or [row for row in rows if row[2] == identifier]
                if not matches:
                    raise ProfileNotFoundError(identifier)
                if len(matches) > 1:
                    raise AmbiguousProfileError("ambiguous model name; use unique_id")
                rows = matches
            grouped: dict[str, list[tuple]] = {}
            if include_profiles:
                missing = ("empty_string_count, missing_count, missing_rate"
                           if {"empty_string_count", "missing_count", "missing_rate"} <= profile_columns
                           else "0, null_count, null_rate")
                key = "unique_id" if current else "model_name"
                keys = [row[0] if current else row[2] for row in rows]
                profile_rows = connection.execute(
                    f"""
                    SELECT {key}, profile_order, dimension_name, dimension_value, record_count,
                           column_name, column_type, column_description, null_count,
                           null_rate, {missing}, distinct_count, min_value, max_value, true_count
                    FROM read_parquet(?)
                    WHERE {key} IN (SELECT unnest(?))
                    ORDER BY {key}, profile_order, column_order
                    """, [str(self.profiles_path), keys],
                ).fetchall()
                for profile_row in profile_rows:
                    grouped.setdefault(profile_row[0], []).append(profile_row[1:])
        return [self._build_model(row, grouped.get(row[0] if current else row[2], [])) for row in rows]

    def _build_model(self, row: tuple, profile_rows: list[tuple]) -> ModelProfile:
        model_name = row[2]
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
                empty_string_count=profile_row[9],
                missing_count=profile_row[10],
                missing_rate=profile_row[11],
                distinct_count=profile_row[12],
                min_value=self._decode_value(profile_row[13], profile_row[5]),
                max_value=self._decode_value(profile_row[14], profile_row[5]),
                true_count=profile_row[15],
            ))
        if current_metadata is not None:
            slices.append(ProfileSlice(
                dimension_name=current_metadata[0],
                dimension_value=current_metadata[1],
                record_count=current_metadata[2],
                columns=current_columns,
            ))

        return ModelProfile(
            unique_id=row[0],
            resource_type=row[1],
            name=model_name,
            database=row[3],
            schema_name=row[4],
            relation_name=row[5],
            description=row[6],
            materialization=row[7],
            tags=json.loads(row[8]),
            tests=json.loads(row[9]),
            columns=[ColumnMetadata.model_validate(column) for column in json.loads(row[10])],
            profiling=json.loads(row[11]),
            profiled_at=row[12],
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
