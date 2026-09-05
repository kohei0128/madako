from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MetricValue = str | int | float | bool | date | None


class ColumnProfile(BaseModel):
    name: str
    data_type: Literal["STRING", "INT64", "FLOAT64", "BOOL", "DATE"]
    description: str = ""
    null_count: Annotated[int, Field(ge=0)]
    null_rate: Annotated[float, Field(ge=0, le=1)]
    empty_string_count: Annotated[int, Field(ge=0)] = 0
    missing_count: Annotated[int, Field(ge=0)] = 0
    missing_rate: Annotated[float, Field(ge=0, le=1)] = 0
    distinct_count: Annotated[int | None, Field(ge=0)] = None
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
    enabled: bool = False
    dimensions: list[str] = Field(default_factory=list)
    max_bytes_billed: Annotated[int, Field(gt=0)] = 1_000_000_000
    treat_empty_string_as_null: bool = False


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
    columns: list[ColumnMetadata] = Field(default_factory=list)
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)
    profiled_at: datetime | None = None
    profiles: list[ProfileSlice] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_unique_id(self) -> "ModelProfile":
        if not self.unique_id:
            self.unique_id = f"{self.resource_type}.{self.database}.{self.schema_name}.{self.name}"
        return self

    def profiling_signature(self) -> str:
        """Inputs whose changes invalidate a plan or previously calculated metrics."""
        return self.model_dump_json(include={
            "unique_id", "resource_type", "name", "database", "schema_name",
            "relation_name", "materialization", "columns", "profiling",
        })
