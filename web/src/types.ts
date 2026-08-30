export type ColumnType = "STRING" | "INT64" | "FLOAT64" | "BOOL" | "DATE";

export interface ColumnProfile {
  name: string;
  data_type: ColumnType;
  description: string;
  null_count: number;
  null_rate: number;
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
  name: string;
  database: string;
  schema: string;
  description: string;
  materialization: string;
  tags: string[];
  tests: string[];
  profiled_at: string;
  profiles: ProfileSlice[];
}

