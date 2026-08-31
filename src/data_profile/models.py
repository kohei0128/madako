from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


MetricValue = str | int | float | bool | date | None


class ColumnProfile(BaseModel):
    name: str
    data_type: Literal["STRING", "INT64", "FLOAT64", "BOOL", "DATE"]
    description: str = ""
    null_count: Annotated[int, Field(ge=0)]
    null_rate: Annotated[float, Field(ge=0, le=1)]
    distinct_count: Annotated[int | None, Field(ge=0)] = None
    min_value: MetricValue = None
    max_value: MetricValue = None
    true_count: Annotated[int | None, Field(ge=0)] = None


class ProfileSlice(BaseModel):
    dimension_name: str | None = None
    dimension_value: str | None = None
    record_count: Annotated[int, Field(ge=0)]
    columns: list[ColumnProfile]

    @model_validator(mode="after")
    def validate_dimension_pair(self) -> "ProfileSlice":
        if (self.dimension_name is None) != (self.dimension_value is None):
            raise ValueError("dimension_name and dimension_value must both be set or both be null")
        return self


class ColumnMetadata(BaseModel):
    name: str
    data_type: str
    description: str = ""


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
    profiled_at: datetime | None = None
    profiles: list[ProfileSlice] = Field(default_factory=list)
