import { useEffect, useMemo, useState } from "react";

import type { ColumnProfile, ModelProfile, ProfileSlice } from "./types";

type TypeFilter = "all" | "string" | "numeric" | "boolean" | "date";
const typeFilters: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "All" }, { value: "string", label: "String" },
  { value: "numeric", label: "Numeric" }, { value: "boolean", label: "Boolean" },
  { value: "date", label: "Date/Time" },
];
const relationPathPrefix = "/relations/";

function relationIdFromLocation(): string | null {
  if (!window.location.pathname.startsWith(relationPathPrefix)) return null;
  try {
    return decodeURIComponent(window.location.pathname.slice(relationPathPrefix.length)) || null;
  } catch {
    return null;
  }
}

function relationPath(id: string): string {
  return `${relationPathPrefix}${encodeURIComponent(id)}`;
}

function isTemporalType(dataType: string): boolean {
  return ["DATE", "DATETIME", "TIMESTAMP"].includes(dataType);
}

function matchesType(column: ColumnProfile, filter: TypeFilter): boolean {
  if (filter === "all") return true;
  if (filter === "string") return column.data_type === "STRING";
  if (filter === "numeric") return ["INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC"].includes(column.data_type);
  if (filter === "boolean") return column.data_type === "BOOL";
  return isTemporalType(column.data_type);
}

function compareDecimalStrings(left: string, right: string): number {
  const parse = (value: string) => {
    const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(value);
    if (!match) return null;
    const integer = match[2].replace(/^0+(?=\d)/, "");
    const fraction = (match[3] ?? "").replace(/0+$/, "");
    const zero = integer === "0" && fraction === "";
    return { sign: match[1] === "-" && !zero ? -1 : 1, integer, fraction };
  };
  const a = parse(left);
  const b = parse(right);
  if (!a || !b) return left.localeCompare(right);
  if (a.sign !== b.sign) return a.sign - b.sign;
  let magnitude = a.integer.length - b.integer.length;
  if (magnitude === 0) magnitude = a.integer.localeCompare(b.integer);
  if (magnitude === 0) {
    const scale = Math.max(a.fraction.length, b.fraction.length);
    magnitude = a.fraction.padEnd(scale, "0").localeCompare(b.fraction.padEnd(scale, "0"));
  }
  return a.sign * magnitude;
}

function missingMetric(column: ColumnProfile, includeEmpty: boolean) {
  return includeEmpty
    ? { count: column.missing_count, rate: column.missing_rate }
    : { count: column.null_count, rate: column.null_rate };
}

function missingDetail(column: ColumnProfile, includeEmpty: boolean): string {
  if (!includeEmpty) return `${column.null_count.toLocaleString()} nulls`;
  return `${column.missing_count.toLocaleString()} missing (${column.null_count.toLocaleString()} null, ${column.empty_string_count.toLocaleString()} empty)`;
}

function NullMetric({ column, includeEmpty }: { column: ColumnProfile; includeEmpty: boolean }) {
  const metric = missingMetric(column, includeEmpty);
  return <div className="null-metric">
    <div className="bar" title={missingDetail(column, includeEmpty)} style={{ "--rate": `${metric.rate * 100}%` } as React.CSSProperties}>
      <span>{(metric.rate * 100).toFixed(1)}%</span>
    </div>
    <small>{metric.count.toLocaleString()}</small>
  </div>;
}

function PairMetric({ leftLabel, leftValue, rightLabel, rightValue }: {
  leftLabel: string; leftValue: string; rightLabel: string; rightValue: string;
}) {
  return <div className="pair-metric">
    <span><small>{leftLabel}</small><strong>{leftValue}</strong></span>
    <span><small>{rightLabel}</small><strong>{rightValue}</strong></span>
  </div>;
}

function BooleanMetric({ column, slice }: { column: ColumnProfile; slice: ProfileSlice }) {
  const trueCount = column.true_count ?? 0;
  const falseCount = Math.max(0, slice.record_count - column.null_count - trueCount);
  const denominator = slice.record_count || 1;
  const trueRate = (trueCount / denominator) * 100;
  const falseRate = (falseCount / denominator) * 100;
  const nullRate = column.null_rate * 100;
  return <div className="boolean-metric">
    <div className="boolean-value"><small>True</small><strong>{trueRate.toFixed(1)}%</strong></div>
    <div className="boolean-bar" aria-label={`True ${trueRate.toFixed(1)}%, False ${falseRate.toFixed(1)}%, Null ${nullRate.toFixed(1)}%`}>
      <span className="true" style={{ width: `${trueRate}%` }} />
      <span className="false" style={{ width: `${falseRate}%` }} />
      <span className="null" style={{ width: `${nullRate}%` }} />
    </div>
  </div>;
}

function CompactMetrics({ column, slice }: { column: ColumnProfile; slice: ProfileSlice }) {
  if (column.data_type === "STRING") {
    return <PairMetric
      leftLabel="Distinct"
      leftValue={column.distinct_count?.toLocaleString() ?? "—"}
      rightLabel="Ratio"
      rightValue={column.distinct_ratio === null ? "—" : `${(column.distinct_ratio * 100).toFixed(1)}%`}
    />;
  }
  if (column.data_type === "BOOL") return <BooleanMetric column={column} slice={slice} />;
  return <PairMetric leftLabel="Min" leftValue={String(column.min_value ?? "—")} rightLabel="Max" rightValue={String(column.max_value ?? "—")} />;
}

function isTemporal(profiles: ProfileSlice[]): boolean {
  const values = profiles.filter((profile) => profile.dimension_value !== null);
  return values.length > 0 && values.every((profile) => /^\d{4}-\d{2}-\d{2}$/.test(profile.dimension_value ?? ""));
}

function heatIntensity(rate: number): number {
  if (rate <= 0) return 0;
  return Math.min(1, 0.18 + Math.sqrt(rate) * 1.4);
}

function DimensionMetricSummary({ profiles, columnName, dimensionName }: { profiles: ProfileSlice[]; columnName: string; dimensionName: string }) {
  const points = profiles.flatMap((profile) => {
    const column = profile.columns.find((item) => item.name === columnName);
    return column ? [{ column, profile }] : [];
  });
  const first = points[0]?.column;
  if (!first) return <>—</>;
  if (first.data_type === "STRING") {
    const values = points.flatMap(({ column }) => column.distinct_count === null ? [] : [column.distinct_count]);
    const range = values.length ? `${Math.min(...values).toLocaleString()}–${Math.max(...values).toLocaleString()}` : "—";
    return <div className="dimension-summary"><strong>Distinct {range}</strong><small>{profiles.length.toLocaleString()} {dimensionName} values</small></div>;
  }
  if (first.data_type === "BOOL") {
    const rates = points.map(({ column, profile }) => profile.record_count ? ((column.true_count ?? 0) / profile.record_count) * 100 : 0);
    return <div className="dimension-summary"><strong>True {Math.min(...rates).toFixed(1)}–{Math.max(...rates).toFixed(1)}%</strong><small>{profiles.length.toLocaleString()} {dimensionName} values</small></div>;
  }
  const minimums = points.flatMap(({ column }) => column.min_value === null ? [] : [column.min_value]);
  const maximums = points.flatMap(({ column }) => column.max_value === null ? [] : [column.max_value]);
  const exactDecimal = first.data_type === "NUMERIC" || first.data_type === "BIGNUMERIC";
  const min = minimums.length === 0 ? null
    : isTemporalType(first.data_type) ? minimums.map(String).sort()[0]
    : exactDecimal ? minimums.map(String).sort(compareDecimalStrings)[0]
    : Math.min(...minimums.map(Number));
  const max = maximums.length === 0 ? null
    : isTemporalType(first.data_type) ? maximums.map(String).sort().at(-1)
    : exactDecimal ? maximums.map(String).sort(compareDecimalStrings).at(-1)
    : Math.max(...maximums.map(Number));
  return <div className="dimension-summary"><strong>{String(min ?? "—")} → {String(max ?? "—")}</strong><small>Min / Max</small></div>;
}

function dimensionMetricKind(column: ColumnProfile): "string" | "boolean" | "range" {
  if (column.data_type === "STRING") return "string";
  if (column.data_type === "BOOL") return "boolean";
  return "range";
}

type DimensionSortKey = "dimension" | "rowBar" | "rows" | "missing" | "distinct" | "true" | "min" | "max";
type SortDirection = "asc" | "desc";

function DimensionDetail({ profiles, columnName, dimensionName, includeEmpty, temporal }: {
  profiles: ProfileSlice[];
  columnName: string;
  dimensionName: string;
  includeEmpty: boolean;
  temporal: boolean;
}) {
  const first = profiles.flatMap((profile) => profile.columns.filter((column) => column.name === columnName))[0];
  const [sort, setSort] = useState<{ key: DimensionSortKey; direction: SortDirection } | null>(
    temporal ? { key: "dimension", direction: "desc" } : null,
  );
  if (!first) return null;
  const kind = dimensionMetricKind(first);
  const maxRows = Math.max(1, ...profiles.map((profile) => profile.record_count));
  const missingLabel = includeEmpty ? "Missing" : "Null";
  const valueForSort = (profile: ProfileSlice, key: DimensionSortKey): string | number | boolean | null => {
    const column = profile.columns.find((item) => item.name === columnName);
    if (key === "dimension") return profile.dimension_value;
    if (key === "rowBar" || key === "rows") return profile.record_count;
    if (!column) return null;
    if (key === "missing") return missingMetric(column, includeEmpty).rate;
    if (key === "distinct") return column.distinct_count;
    if (key === "true") return profile.record_count ? (column.true_count ?? 0) / profile.record_count : 0;
    if (key === "min") return column.min_value;
    return column.max_value;
  };
  const compareValues = (left: string | number | boolean, right: string | number | boolean, key: DimensionSortKey): number => {
    if (key === "dimension") return String(left).localeCompare(String(right));
    if ((key === "min" || key === "max") && isTemporalType(first.data_type)) return String(left).localeCompare(String(right));
    if ((key === "min" || key === "max") && (first.data_type === "NUMERIC" || first.data_type === "BIGNUMERIC")) return compareDecimalStrings(String(left), String(right));
    if (typeof left === "number" && typeof right === "number") return left - right;
    if (typeof left === "boolean" && typeof right === "boolean") return Number(left) - Number(right);
    const numericDifference = Number(left) - Number(right);
    return Number.isNaN(numericDifference) ? String(left).localeCompare(String(right)) : numericDifference;
  };
  const orderedProfiles = sort ? [...profiles].sort((left, right) => {
    const leftValue = valueForSort(left, sort.key);
    const rightValue = valueForSort(right, sort.key);
    if (leftValue === null && rightValue === null) return 0;
    if (leftValue === null) return 1;
    if (rightValue === null) return -1;
    const result = compareValues(leftValue, rightValue, sort.key);
    return sort.direction === "asc" ? result : -result;
  }) : profiles;
  const toggleSort = (key: DimensionSortKey) => setSort((current) => current?.key === key
    ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
    : { key, direction: key === "dimension" ? "asc" : "desc" });
  const sortHeader = (key: DimensionSortKey, label: string) => <button type="button" className={sort?.key === key ? "active" : ""}
    aria-label={`Sort by ${label}`} onClick={() => toggleSort(key)}>
    <span>{label}</span><i aria-hidden="true">{sort?.key === key ? (sort.direction === "asc" ? "↑" : "↓") : "⇅"}</i>
  </button>;

  return <div className={`dimension-detail-grid ${kind}`}>
    <div className="dimension-detail-title">{columnName} by {dimensionName}</div>
    <div className="dimension-detail-scroll">
      <div className="dimension-detail-header">
        {sortHeader("dimension", dimensionName)}{sortHeader("rowBar", "Row count")}{sortHeader("rows", "Rows")}{sortHeader("missing", missingLabel)}
        {kind === "string" && sortHeader("distinct", "Distinct")}
        {kind === "boolean" && sortHeader("true", "True")}
        {kind === "range" && <>{sortHeader("min", "Min")}{sortHeader("max", "Max")}</>}
      </div>
      {orderedProfiles.map((profile, index) => {
        const column = profile.columns.find((item) => item.name === columnName);
        const metric = column ? missingMetric(column, includeEmpty) : { count: 0, rate: 0 };
        const trueRate = column && profile.record_count ? ((column.true_count ?? 0) / profile.record_count) * 100 : 0;
        return <div className="dimension-detail-row" key={`${profile.dimension_value ?? "NULL"}-${index}`}>
          <span className={profile.dimension_value === null ? "missing-value" : ""}>{profile.dimension_value ?? "NULL"}</span>
          <span className="dimension-row-track"><i style={{ width: `${(profile.record_count / maxRows) * 100}%`, "--heat": String(Math.max(0.12, heatIntensity(metric.rate))) } as React.CSSProperties} /></span>
          <span className="dimension-number">{profile.record_count.toLocaleString()}</span>
          <span className="dimension-number" title={column ? missingDetail(column, includeEmpty) : undefined}>{column ? `${(metric.rate * 100).toFixed(1)}%` : "—"}</span>
          {kind === "string" && <span className="dimension-number">{column?.distinct_count?.toLocaleString() ?? "—"}</span>}
          {kind === "boolean" && <span className="dimension-number">{column ? `${trueRate.toFixed(1)}%` : "—"}</span>}
          {kind === "range" && <><span className="dimension-number">{String(column?.min_value ?? "—")}</span><span className="dimension-number">{String(column?.max_value ?? "—")}</span></>}
        </div>;
      })}
    </div>
  </div>;
}

function DimensionTable({ profiles, filter, dimensionName, includeEmpty, temporal }: {
  profiles: ProfileSlice[];
  filter: TypeFilter;
  dimensionName: string;
  includeEmpty: boolean;
  temporal: boolean;
}) {
  const orderedProfiles = useMemo(() => {
    if (!temporal) return profiles;
    return [...profiles].sort((left, right) => {
      if (left.dimension_value === null) return 1;
      if (right.dimension_value === null) return -1;
      return right.dimension_value.localeCompare(left.dimension_value);
    });
  }, [profiles, temporal]);
  const columns = orderedProfiles[0]?.columns.filter((column) => matchesType(column, filter)) ?? [];
  const [expandedColumn, setExpandedColumn] = useState<string | null>(null);

  useEffect(() => {
    setExpandedColumn(null);
  }, [dimensionName, filter]);

  return <div className="dimension-table">
    <div className="dimension-table-header"><span>Column</span><span>Type</span><span>{includeEmpty ? "Missing" : "Null"} by {dimensionName}</span><span>Metrics</span><span /></div>
    {columns.map((baseColumn) => {
      const expanded = expandedColumn === baseColumn.name;
      const toggle = () => setExpandedColumn((current) => current === baseColumn.name ? null : baseColumn.name);
      return <div className="dimension-column" key={baseColumn.name}>
        <div className="dimension-column-row" role="button" tabIndex={0} aria-expanded={expanded} onClick={toggle}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            toggle();
          }}>
          <div className="column-name"><strong>{baseColumn.name}</strong><small>{baseColumn.description}</small></div>
          <div><code>{baseColumn.data_type}</code></div>
          <div className="heatmap categorical-heatmap" style={{ "--point-count": orderedProfiles.length } as React.CSSProperties}>
            {orderedProfiles.map((profile, index) => {
              const column = profile.columns.find((item) => item.name === baseColumn.name);
              const rate = column ? missingMetric(column, includeEmpty).rate : 0;
              return <span key={`${profile.dimension_value ?? "NULL"}-${index}`} className={column ? `heat-cell ${rate === 0 ? "zero" : ""}` : "heat-cell missing"}
                style={{ "--heat": String(heatIntensity(rate)) } as React.CSSProperties}
                title={`${dimensionName} = ${profile.dimension_value ?? "NULL"}: ${column ? `${(rate * 100).toFixed(1)}% ${includeEmpty ? "MISSING" : "NULL"} · ${missingDetail(column, includeEmpty)}` : "No data"}`} />;
            })}
          </div>
          <DimensionMetricSummary profiles={orderedProfiles} columnName={baseColumn.name} dimensionName={dimensionName} />
          <button className="dimension-expand" aria-label={`${expanded ? "Collapse" : "Expand"} ${baseColumn.name} details`} aria-expanded={expanded}
            onClick={(event) => { event.stopPropagation(); toggle(); }}>{expanded ? "⌄" : "›"}</button>
        </div>
        {expanded && <DimensionDetail profiles={orderedProfiles} columnName={baseColumn.name} dimensionName={dimensionName} includeEmpty={includeEmpty} temporal={temporal} />}
      </div>;
    })}
    {columns.length === 0 && <div className="empty-state">No columns match this type.</div>}
  </div>;
}

function LineageCard({ id, relation, current = false, onSelect }: {
  id: string;
  relation?: ModelProfile;
  current?: boolean;
  onSelect: (id: string) => void;
}) {
  const name = relation?.name ?? id.split(".").at(-1) ?? id;
  const context = relation
    ? `${relation.database} / ${relation.schema}`
    : id;
  const content = <>
    <span className="lineage-kind">{relation?.resource_type ?? "Unavailable"}</span>
    <strong>{name}</strong>
    <small>{context}</small>
  </>;
  if (current) return <div className="lineage-card current">{content}</div>;
  return <button
    className="lineage-card"
    disabled={!relation}
    onClick={() => relation && onSelect(relation.unique_id)}
    aria-label={relation ? `Open ${relation.resource_type} ${relation.name}` : undefined}
  >{content}</button>;
}

function LineagePanel({ model, models, onSelect }: {
  model: ModelProfile;
  models: ModelProfile[];
  onSelect: (id: string) => void;
}) {
  const byId = new Map(models.map((item) => [item.unique_id, item]));
  const upstream = model.upstream_ids.map((id) => ({ id, relation: byId.get(id) }));
  const downstream = models
    .filter((item) => item.upstream_ids.includes(model.unique_id))
    .map((relation) => ({ id: relation.unique_id, relation }));

  const group = (items: { id: string; relation?: ModelProfile }[]) => (
    <div className="lineage-nodes">
      {items.map((item) => <LineageCard key={item.id} {...item} onSelect={onSelect} />)}
    </div>
  );

  return <section className="lineage-section" aria-label="Direct lineage">
    <div className="lineage-heading">
      <div><h2>Lineage</h2><span>Direct relationships only</span></div>
      <small>{upstream.length} upstream · {downstream.length} downstream</small>
    </div>
    <div className="lineage-flow">
      <div className="lineage-column upstream">{upstream.length > 0 && <><h3>Upstream</h3>{group(upstream)}</>}</div>
      <div className="lineage-column focus">
        <h3>Selected</h3>
        <div className="lineage-selected">
          {upstream.length > 0 && <span className="lineage-arrow incoming" aria-hidden="true">→</span>}
          <LineageCard id={model.unique_id} relation={model} current onSelect={onSelect} />
          {downstream.length > 0 && <span className="lineage-arrow outgoing" aria-hidden="true">→</span>}
        </div>
      </div>
      <div className="lineage-column downstream">{downstream.length > 0 && <><h3>Downstream</h3>{group(downstream)}</>}</div>
    </div>
  </section>;
}

function App() {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [selectedModel, setSelectedModel] = useState(() => relationIdFromLocation() ?? "");
  const [sliceIndex, setSliceIndex] = useState(0);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [expandedDatasets, setExpandedDatasets] = useState<Set<string>>(new Set());

  const [detail, setDetail] = useState<ModelProfile | null>(null);
  const [revision, setRevision] = useState(0);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    fetch("/api/models?include_profiles=false", { signal: controller.signal }).then((response) => {
      if (!response.ok) throw new Error("Could not load profiles");
      return response.json() as Promise<ModelProfile[]>;
    }).then((data) => {
      setModels(data);
      setSelectedModel((current) => {
        const selected = data.find((item) => item.unique_id === current) ?? data[0];
        const next = selected?.unique_id ?? "";
        if (selected) {
          setExpandedDatasets((expanded) => new Set(expanded).add(`${selected.database}/${selected.schema}`));
          if (window.location.pathname !== relationPath(next)) {
            window.history.replaceState({ relationId: next }, "", relationPath(next));
          }
        }
        return next;
      });
      setLoaded(true);
    })
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    if (!selectedModel) return;
    const controller = new AbortController();
    setDetail(null);
    setError("");
    fetch(`/api/models/${encodeURIComponent(selectedModel)}/profile`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Could not load model profile");
        return response.json() as Promise<ModelProfile>;
      })
      .then((data) => { setDetail(data); setSliceIndex(0); })
      .catch((reason: Error) => { if (reason.name !== "AbortError") setError(reason.message); });
    return () => controller.abort();
  }, [selectedModel, revision]);

  useEffect(() => {
    const restoreSelection = () => {
      const id = relationIdFromLocation();
      const selected = models.find((item) => item.unique_id === id);
      if (!selected) return;
      setSelectedModel(selected.unique_id);
      setSliceIndex(0);
      setTypeFilter("all");
      setExpandedDatasets((current) => new Set(current).add(`${selected.database}/${selected.schema}`));
    };
    window.addEventListener("popstate", restoreSelection);
    return () => window.removeEventListener("popstate", restoreSelection);
  }, [models]);

  const model = detail?.unique_id === selectedModel ? detail : null;
  const includeEmpty = model?.profiling.treat_empty_string_as_null ?? false;
  const slice = model?.profiles[sliceIndex];
  const overallSlice = model?.profiles.find((profile) => profile.dimension_name === null);
  const dimensionNames = useMemo(() => Array.from(new Set(
    model?.profiles.flatMap((profile) => profile.dimension_name ? [profile.dimension_name] : []) ?? [],
  )), [model]);
  const activeDimension = slice?.dimension_name ?? null;
  const dimensionSlices = model?.profiles.filter((profile) => profile.dimension_name === activeDimension) ?? [];
  const temporalDimension = activeDimension !== null && (isTemporal(dimensionSlices) || Boolean(model?.columns.some((column) => column.name === activeDimension && isTemporalType(column.data_type))));
  const latestDimensionSlice = temporalDimension
    ? dimensionSlices.filter((profile) => profile.dimension_value !== null).sort((a, b) => (a.dimension_value ?? "").localeCompare(b.dimension_value ?? "")).at(-1)
    : undefined;
  const summarySlice = latestDimensionSlice ?? slice;
  const visibleColumns = summarySlice?.columns.filter((column) => matchesType(column, typeFilter)) ?? [];
  const filteredModels = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return models;
    return models.filter((item) => [item.name, item.schema, item.database, item.resource_type]
      .some((value) => value.toLowerCase().includes(normalizedQuery)));
  }, [models, query]);
  const explorerGroups = useMemo(() => {
    const projects = new Map<string, Map<string, ModelProfile[]>>();
    for (const item of filteredModels) {
      const datasets = projects.get(item.database) ?? new Map<string, ModelProfile[]>();
      const relations = datasets.get(item.schema) ?? [];
      relations.push(item);
      datasets.set(item.schema, relations);
      projects.set(item.database, datasets);
    }
    return projects;
  }, [filteredModels]);

  function selectDimension(dimensionName: string | null) {
    const index = model?.profiles.findIndex((profile) => profile.dimension_name === dimensionName) ?? -1;
    if (index >= 0) setSliceIndex(index);
  }

  function toggleDataset(key: string) {
    setExpandedDatasets((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function selectModel(id: string) {
    const selected = models.find((item) => item.unique_id === id);
    setSelectedModel(id);
    setSliceIndex(0);
    setTypeFilter("all");
    if (selected) {
      const path = relationPath(id);
      if (window.location.pathname !== path) window.history.pushState({ relationId: id }, "", path);
      setExpandedDatasets((current) => new Set(current).add(`${selected.database}/${selected.schema}`));
    }
  }

  function renderHeaders() {
    const missingLabel = includeEmpty ? "MISSING" : "NULL";
    if (typeFilter === "string") return <tr><th>Column</th><th>{missingLabel}</th><th>Distinct</th><th>Distinct ratio</th></tr>;
    if (typeFilter === "numeric") return <tr><th>Column</th><th>Type</th><th>{missingLabel}</th><th>Min</th><th>Max</th></tr>;
    if (typeFilter === "boolean") return <tr><th>Column</th><th>{missingLabel}</th><th>TRUE</th></tr>;
    if (typeFilter === "date") return <tr><th>Column</th><th>{missingLabel}</th><th>Min</th><th>Max</th></tr>;
    return <tr><th>Column</th><th>Type</th><th>{missingLabel}</th><th>Metrics</th></tr>;
  }

  function renderCells(column: ColumnProfile) {
    const columnCell = <td className="column-name"><strong>{column.name}</strong><small>{column.description}</small></td>;
    if (typeFilter === "string") return <>{columnCell}<td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td className="value-cell">{column.distinct_count?.toLocaleString() ?? "—"}</td><td className="value-cell">{column.distinct_ratio === null ? "—" : `${(column.distinct_ratio * 100).toFixed(1)}%`}</td></>;
    if (typeFilter === "numeric") return <>{columnCell}<td><code>{column.data_type}</code></td><td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td className="value-cell">{String(column.min_value ?? "—")}</td><td className="value-cell">{String(column.max_value ?? "—")}</td></>;
    if (typeFilter === "boolean") return <>{columnCell}<td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td><BooleanMetric column={column} slice={slice!} /></td></>;
    if (typeFilter === "date") return <>{columnCell}<td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td className="value-cell">{String(column.min_value ?? "—")}</td><td className="value-cell">{String(column.max_value ?? "—")}</td></>;
    return <>{columnCell}<td><code>{column.data_type}</code></td><td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td><CompactMetrics column={column} slice={slice!} /></td></>;
  }

  return <main className="shell">
    <aside className="explorer">
      <div className="brand">madako <span>alpha</span></div>
      <label className="search"><span>Search models</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Model name" /></label>
      <div className="tree-label">Explorer <button onClick={() => setRevision((value) => value + 1)}>Refresh</button></div>
      {[...explorerGroups].sort(([left], [right]) => left.localeCompare(right)).map(([database, datasets]) => <div className="project-group" key={database}>
        <div className="project-name"><span>◆</span>{database}</div>
        {[...datasets].sort(([left], [right]) => left.localeCompare(right)).map(([schema, relations]) => {
          const datasetKey = `${database}/${schema}`;
          const isOpen = query.trim().length > 0 || expandedDatasets.has(datasetKey);
          return <div className="dataset-group" key={datasetKey}>
            <button className={`dataset-item ${isOpen ? "open" : ""}`} onClick={() => toggleDataset(datasetKey)} aria-expanded={isOpen}>
              <span className="chevron">›</span><span className="dataset-icon">▤</span><span>{schema}</span><small>{relations.length}</small>
            </button>
            {isOpen && <div className="dataset-relations">{[...relations].sort((left, right) => left.name.localeCompare(right.name)).map((item) => <button className={`model-item ${item.unique_id === selectedModel ? "selected" : ""}`} key={item.unique_id || item.name} onClick={() => selectModel(item.unique_id)}>
              <span className="table-icon">{item.resource_type === "source" ? "◇" : "▦"}</span><span><small>{item.resource_type}</small>{item.name}</span>
            </button>)}</div>}
          </div>;
        })}
      </div>)}
    </aside>

    <section className="detail">
      {error && <div className="notice error">{error}. Is the API running?</div>}
      {!model && !error && <div className="notice">{loaded && models.length === 0 ? "No models imported" : "Loading profile…"}</div>}
      {model && !slice && <>
        <header>
          <div className="title-row"><h1>{model.name}</h1><span className="pill">{model.materialization}</span></div>
          <p>{model.description || "No description"}</p>
          <div className="eyebrow">{model.database} / {model.schema}</div>
          <div className="metadata"><span>{model.columns.length} columns</span><span>{model.tests.length} dbt tests</span><span>Profile not generated</span></div>
        </header>
        <div className="profile-status"><strong>Metadata available</strong><span>Profiling metrics have not been generated for this relation yet.</span></div>
        <div className="columns-heading"><div><h2>Columns</h2><span>{model.columns.length}</span></div></div>
        <div className="table-wrap metadata-table"><table>
          <thead><tr><th>Column</th><th>Type</th><th>Description</th></tr></thead>
          <tbody>{model.columns.map((column) => <tr key={column.name}>
            <td className="column-name"><strong>{column.name}</strong></td><td><code>{column.data_type}</code></td><td>{column.description || "—"}</td>
          </tr>)}</tbody>
        </table></div>
      </>}
      {model && slice && <>
        <header>
          <div className="title-row"><h1>{model.name}</h1><span className="pill">{model.materialization}</span></div>
          <p>{model.description}</p>
          <div className="eyebrow">{model.database} / {model.schema}</div>
          <div className="metadata">
            <span><b>{(overallSlice?.record_count ?? slice.record_count).toLocaleString()}</b> total rows</span>
            <span>Profiled {model.profiled_at ? new Date(model.profiled_at).toLocaleString() : "—"}</span>
            <span>{model.tests.length} dbt tests</span>
            {temporalDimension && latestDimensionSlice && <span className="latest-partition"><span>Latest partition</span><strong>{latestDimensionSlice.dimension_value}</strong><small>{latestDimensionSlice.record_count.toLocaleString()} rows</small></span>}
          </div>
        </header>

        <div className="profile-by">
          <span>Profile by</span>
          <div className="segments">
            <button className={activeDimension === null ? "active" : ""} onClick={() => selectDimension(null)}>Overall</button>
            {dimensionNames.map((dimension) => <button className={activeDimension === dimension ? "active" : ""} key={dimension} onClick={() => selectDimension(dimension)}>{dimension}</button>)}
          </div>
          {activeDimension !== null && <span className="dimension-meta">{dimensionSlices.length.toLocaleString()} values</span>}
        </div>

        <div className="columns-heading">
          <div><h2>Columns</h2><span>{visibleColumns.length} of {summarySlice!.columns.length}</span></div>
          <div className="type-tabs" aria-label="Filter columns by type">
            {typeFilters.map((filter) => <button className={typeFilter === filter.value ? "active" : ""} key={filter.value} onClick={() => setTypeFilter(filter.value)}>{filter.label}</button>)}
          </div>
        </div>
        {activeDimension === null && <div className="table-wrap">
          <table><thead>{renderHeaders()}</thead><tbody>{visibleColumns.map((column) => <tr key={column.name}>{renderCells(column)}</tr>)}</tbody></table>
          {visibleColumns.length === 0 && <div className="empty-state">No columns match this type.</div>}
        </div>}
        {activeDimension !== null && <DimensionTable profiles={dimensionSlices} filter={typeFilter} dimensionName={activeDimension} includeEmpty={includeEmpty} temporal={temporalDimension} />}
      </>}
      {model && <LineagePanel model={model} models={models} onSelect={selectModel} />}
    </section>
  </main>;
}

export default App;
