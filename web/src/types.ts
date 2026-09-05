export type ColumnType = "STRING" | "INT64" | "FLOAT64" | "NUMERIC" | "BIGNUMERIC" | "BOOL" | "DATE";

export interface ColumnProfile {
  name: string;
  data_type: ColumnType;
  description: string;
  null_count: number;
  null_rate: number;
  empty_string_count: number;
  missing_count: number;
  missing_rate: number;
  distinct_count: number | null;
  min_value: string | number | boolean | null;
  max_value: string | number | boolean | null;
  true_count: number | null;
}

export interface ProfileSlice {
  dimension_name: string | null;
  dimension_value: string | null;
  record_count: number;
  columns: ColumnProfile[];
}

export interface ModelProfile {
  unique_id: string;
  resource_type: "model" | "source";
  name: string;
  database: string;
  schema: string;
  relation_name: string;
  description: string;
  materialization: string;
  tags: string[];
  tests: string[];
  profiling: {
    enabled: boolean;
    dimensions: string[];
    max_bytes_billed: number;
    treat_empty_string_as_null: boolean;
  };
  columns: { name: string; data_type: string; description: string }[];
  profiled_at: string | null;
  profiles: ProfileSlice[];
}
