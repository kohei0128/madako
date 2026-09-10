import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MetricValue = str | int | float | bool | date | None
PROFILE_COMPUTATION_VERSION = 4
_BYTE_SIZE_PATTERN = re.compile(
    r"^(?P<amount>(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>[KMGTPE]?i?B)$",
    re.IGNORECASE,
)
_BYTE_UNIT_MULTIPLIERS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
    "EB": 1000**6,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "PIB": 1024**5,
    "EIB": 1024**6,
}


class ColumnProfile(BaseModel):
    name: str
    data_type: Literal[
        "STRING", "INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC", "BOOL",
        "DATE", "DATETIME", "TIMESTAMP",
    ]
    description: str = ""
    null_count: Annotated[int, Field(ge=0)]
    null_rate: Annotated[float, Field(ge=0, le=1)]
    empty_string_count: Annotated[int, Field(ge=0)] = 0
    missing_count: Annotated[int, Field(ge=0)] = 0
    missing_rate: Annotated[float, Field(ge=0, le=1)] = 0
    distinct_count: Annotated[int | None, Field(ge=0)] = None
    distinct_ratio: Annotated[float | None, Field(ge=0, le=1)] = None
    min_value: MetricValue = None
    max_value: MetricValue = None
    true_count: Annotated[int | None, Field(ge=0)] = None

    @model_validator(mode="before")
    @classmethod
    def default_missing_metrics_to_null_metrics(cls, data: object) -> object:
        if isinstance(data, dict):
            data = data.copy()
            data.setdefault("empty_string_count", 0)
            data.setdefault("missing_count", data.get("null_count", 0))
            data.setdefault("missing_rate", data.get("null_rate", 0))
        return data


class ProfileSlice(BaseModel):
    dimension_name: str | None = None
    dimension_value: str | None = None
    record_count: Annotated[int, Field(ge=0)]
    columns: list[ColumnProfile]

    @model_validator(mode="after")
    def validate_dimension_pair(self) -> "ProfileSlice":
        if self.dimension_name is None and self.dimension_value is not None:
            raise ValueError("Overall must have a null dimension_value")
        for column in self.columns:
            if column.distinct_count is not None and column.distinct_ratio is None:
                column.distinct_ratio = column.distinct_count / self.record_count if self.record_count else 0
        return self


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    description: str = ""

    @field_validator("data_type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        value = value.upper()
        return {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL"}.get(value, value)


class ProfilingConfig(BaseModel):
    """Configuration for profiling a dbt model or source.

    Stored in dbt YAML under `meta.profiling`. Fields affecting computation
    (like treat_empty_string_as_null) are included in profiling_signature()
    and trigger profile invalidation when changed.

    See config-versioning.md for versioning details.
    """

    enabled: bool = False
    dimensions: list[str] = Field(default_factory=list)
    max_dimension_values: Annotated[int, Field(gt=0)] = 10_000
    max_bytes_billed: Annotated[int, Field(gt=0)] | None = None
    treat_empty_string_as_null: bool = False

    @field_validator("max_bytes_billed", mode="before")
    @classmethod
    def parse_max_bytes_billed(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.isdigit():
            return stripped
        match = _BYTE_SIZE_PATTERN.fullmatch(stripped)
        if match is None:
            raise ValueError(
                "max_bytes_billed must be a byte count or a size such as '10 GB' or '8 GiB'"
            )
        try:
            byte_count = (
                Decimal(match.group("amount"))
                * _BYTE_UNIT_MULTIPLIERS[match.group("unit").upper()]
            )
        except InvalidOperation as error:
            raise ValueError("max_bytes_billed contains an invalid number") from error
        if byte_count != byte_count.to_integral_value():
            raise ValueError("max_bytes_billed must resolve to a whole number of bytes")
        return int(byte_count)


class ModelProfile(BaseModel):
    unique_id: str = ""
    resource_type: Literal["model", "source"] = "model"
    name: str
    database: str
    schema_name: str = Field(serialization_alias="schema")
    relation_name: str = ""
    description: str = ""
    materialization: str
    tags: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    upstream_ids: list[str] = Field(default_factory=list)
    columns: list[ColumnMetadata] = Field(default_factory=list)
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)
    profiled_at: datetime | None = None
    profile_version: int | None = Field(default=None, exclude=True)
    profiles: list[ProfileSlice] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_unique_id(self) -> "ModelProfile":
        if not self.unique_id:
            self.unique_id = f"{self.resource_type}.{self.database}.{self.schema_name}.{self.name}"
        return self

    def profiling_signature(self) -> str:
        """Compute signature of inputs that invalidate an execution plan.

        Returns JSON string of fields that require a plan to be rebuilt when changed:
        unique_id, resource_type, name, database, schema_name, relation_name,
        materialization, columns, and profiling config.

        Execution controls such as ``enabled`` and ``max_bytes_billed`` are
        included so a plan cannot run after its constraints have changed.

        See config-versioning.md for details on what triggers invalidation.
        """
        payload = self.model_dump_json(include={
            "unique_id", "resource_type", "name", "database", "schema_name",
            "relation_name", "materialization", "columns", "profiling",
        })
        return f'{PROFILE_COMPUTATION_VERSION}:{payload}'

    def profile_result_signature(self) -> str:
        """Compute signature of inputs that affect persisted profile results.

        Execution-only controls are excluded so toggling profiling or changing
        its cost limit does not discard compatible results during dbt import.
        """
        payload = self.model_dump_json(
            include={
                "unique_id", "resource_type", "name", "database", "schema_name",
                "relation_name", "materialization", "columns", "profiling",
            },
            exclude={
                "profiling": {"enabled", "max_bytes_billed"},
            },
        )
        return f'{PROFILE_COMPUTATION_VERSION}:{payload}'
