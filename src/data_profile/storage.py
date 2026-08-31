import json
from datetime import date
from pathlib import Path

import duckdb

from data_profile.repository import JsonProfileRepository


MODELS_FILENAME = "models.parquet"
PROFILES_FILENAME = "column_profiles.parquet"


def build_parquet_fixture(source_path: Path, output_dir: Path) -> tuple[Path, Path]:
    models = JsonProfileRepository(source_path).list_models()
    output_dir.mkdir(parents=True, exist_ok=True)
    models_path = output_dir / MODELS_FILENAME
    profiles_path = output_dir / PROFILES_FILENAME

    model_rows: list[tuple] = []
    profile_rows: list[tuple] = []
    for model in models:
        model_rows.append((
            model.name,
            model.database,
            model.schema_name,
            model.description,
            model.materialization,
            json.dumps(model.tags),
            json.dumps(model.tests),
            model.profiled_at.isoformat(),
        ))
        for profile_order, profile in enumerate(model.profiles):
            for column_order, column in enumerate(profile.columns):
                profile_rows.append((
                    model.name,
                    profile_order,
                    profile.dimension_name,
                    profile.dimension_value,
                    profile.record_count,
                    column_order,
                    column.name,
                    column.data_type,
                    column.description,
                    column.null_count,
                    column.null_rate,
                    column.distinct_count,
                    _encode_value(column.min_value),
                    _encode_value(column.max_value),
                    column.true_count,
                ))

    with duckdb.connect() as connection:
        connection.execute("""
            CREATE TABLE models (
                model_name VARCHAR NOT NULL,
                database_name VARCHAR NOT NULL,
                schema_name VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                materialization VARCHAR NOT NULL,
                tags_json VARCHAR NOT NULL,
                tests_json VARCHAR NOT NULL,
                profiled_at VARCHAR NOT NULL
            )
        """)
        connection.executemany("INSERT INTO models VALUES (?, ?, ?, ?, ?, ?, ?, ?)", model_rows)
        connection.execute("""
            CREATE TABLE column_profiles (
                model_name VARCHAR NOT NULL,
                profile_order INTEGER NOT NULL,
                dimension_name VARCHAR,
                dimension_value VARCHAR,
                record_count BIGINT NOT NULL,
                column_order INTEGER NOT NULL,
                column_name VARCHAR NOT NULL,
                column_type VARCHAR NOT NULL,
                column_description VARCHAR NOT NULL,
                null_count BIGINT NOT NULL,
                null_rate DOUBLE NOT NULL,
                distinct_count BIGINT,
                min_value VARCHAR,
                max_value VARCHAR,
                true_count BIGINT
            )
        """)
        connection.executemany(
            "INSERT INTO column_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            profile_rows,
        )
        connection.execute("COPY models TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(models_path)])
        connection.execute("COPY column_profiles TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(profiles_path)])

    return models_path, profiles_path


def _encode_value(value: str | int | float | bool | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
