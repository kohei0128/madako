import { useEffect, useMemo, useState } from "react";

import type { ColumnProfile, ModelProfile, ProfileSlice } from "./types";

type TypeFilter = "all" | "string" | "numeric" | "boolean" | "date";
type TrendRange = 30 | 90 | "all";
const typeFilters: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "All" }, { value: "string", label: "String" },
  { value: "numeric", label: "Numeric" }, { value: "boolean", label: "Boolean" },
  { value: "date", label: "Date" },
];

function matchesType(column: ColumnProfile, filter: TypeFilter): boolean {
  if (filter === "all") return true;
  if (filter === "string") return column.data_type === "STRING";
  if (filter === "numeric") return column.data_type === "INT64" || column.data_type === "FLOAT64";
  if (filter === "boolean") return column.data_type === "BOOL";
  return column.data_type === "DATE";
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
    return <div className="single-metric"><small>Distinct</small><strong>{column.distinct_count?.toLocaleString() ?? "—"}</strong></div>;
  }
  if (column.data_type === "BOOL") return <BooleanMetric column={column} slice={slice} />;
  return <PairMetric leftLabel="Min" leftValue={String(column.min_value ?? "—")} rightLabel="Max" rightValue={String(column.max_value ?? "—")} />;
}

function isTemporal(profiles: ProfileSlice[]): boolean {
  return profiles.length > 0 && profiles.every((profile) => /^\d{4}-\d{2}-\d{2}$/.test(profile.dimension_value ?? ""));
}

function heatIntensity(rate: number): number {
  if (rate <= 0) return 0;
  return Math.min(1, 0.18 + Math.sqrt(rate) * 1.4);
}

function HeatLegend({ label }: { label: string }) {
  return <div className="heat-heading"><span>{label}</span><span className="heat-legend"><small>Low</small><i /><i /><i /><i /><small>High</small></span></div>;
}

function TemporalTable({ profiles, filter, range, includeEmpty }: { profiles: ProfileSlice[]; filter: TypeFilter; range: TrendRange; includeEmpty: boolean }) {
  const ordered = [...profiles].sort((a, b) => (a.dimension_value ?? "").localeCompare(b.dimension_value ?? ""));
  const visibleProfiles = range === "all" ? ordered : ordered.slice(-range);
  const latest = visibleProfiles.at(-1);
  const previous = visibleProfiles.at(-2);
  if (!latest) return null;
  const columns = latest.columns.filter((column) => matchesType(column, filter));

  return <div className="table-wrap trend-table"><table>
    <thead><tr><th>Column</th><th>Type</th><th><HeatLegend label={`${includeEmpty ? "MISSING" : "NULL"} rate by date`} /></th><th>Latest</th><th>Latest metrics</th></tr></thead>
    <tbody>{columns.map((column) => {
      const previousColumn = previous?.columns.find((item) => item.name === column.name);
      return <tr key={column.name}>
        <td className="column-name"><strong>{column.name}</strong><small>{column.description}</small></td>
        <td><code>{column.data_type}</code></td>
        <td><div
          className="heatmap"
          aria-label={`NULL rate trend for ${column.name}`}
          style={{ "--point-count": visibleProfiles.length } as React.CSSProperties}
        >
          {visibleProfiles.map((profile) => {
            const point = profile.columns.find((item) => item.name === column.name);
            const metric = point ? missingMetric(point, includeEmpty) : { rate: 0, count: 0 };
            const rate = metric.rate;
            return <span key={profile.dimension_value} className={point ? `heat-cell ${rate === 0 ? "zero" : ""}` : "heat-cell missing"}
              style={{ "--heat": String(heatIntensity(rate)) } as React.CSSProperties}
              title={`${profile.dimension_value}: ${point ? `${(rate * 100).toFixed(1)}% ${includeEmpty ? "MISSING" : "NULL"} · ${missingDetail(point, includeEmpty)}` : "No data"}`} />;
          })}
        </div><small className="trend-dates"><span>{visibleProfiles[0]?.dimension_value}</span><span>{latest.dimension_value}</span></small></td>
        <td className="latest-value">{(missingMetric(column, includeEmpty).rate * 100).toFixed(1)}%<small title={missingDetail(column, includeEmpty)}>{missingMetric(column, includeEmpty).count.toLocaleString()} {includeEmpty ? "missing" : "nulls"}</small><small>{previousColumn ? `prev. ${(missingMetric(previousColumn, includeEmpty).rate * 100).toFixed(1)}%` : ""}</small></td>
        <td><CompactMetrics column={column} slice={latest} /></td>
      </tr>;
    })}</tbody>
  </table></div>;
}

function DimensionMetricSummary({ profiles, columnName }: { profiles: ProfileSlice[]; columnName: string }) {
  const points = profiles.flatMap((profile) => {
    const column = profile.columns.find((item) => item.name === columnName);
    return column ? [{ column, profile }] : [];
  });
  const first = points[0]?.column;
  if (!first) return <>—</>;
  if (first.data_type === "STRING") {
    const values = points.flatMap(({ column }) => column.distinct_count === null ? [] : [column.distinct_count]);
    return <PairMetric leftLabel="Min distinct" leftValue={Math.min(...values).toLocaleString()} rightLabel="Max distinct" rightValue={Math.max(...values).toLocaleString()} />;
  }
  if (first.data_type === "BOOL") {
    const rates = points.map(({ column, profile }) => profile.record_count ? ((column.true_count ?? 0) / profile.record_count) * 100 : 0);
    return <PairMetric leftLabel="Min true" leftValue={`${Math.min(...rates).toFixed(1)}%`} rightLabel="Max true" rightValue={`${Math.max(...rates).toFixed(1)}%`} />;
  }
  const minimums = points.flatMap(({ column }) => column.min_value === null ? [] : [column.min_value]);
  const maximums = points.flatMap(({ column }) => column.max_value === null ? [] : [column.max_value]);
  const min = first.data_type === "DATE" ? minimums.map(String).sort()[0] : Math.min(...minimums.map(Number));
  const max = first.data_type === "DATE" ? maximums.map(String).sort().at(-1) : Math.max(...maximums.map(Number));
  return <PairMetric leftLabel="Min" leftValue={String(min ?? "—")} rightLabel="Max" rightValue={String(max ?? "—")} />;
}

function CategoricalTable({ profiles, filter, dimensionName, includeEmpty }: { profiles: ProfileSlice[]; filter: TypeFilter; dimensionName: string; includeEmpty: boolean }) {
  const columns = profiles[0]?.columns.filter((column) => matchesType(column, filter)) ?? [];
  return <div className="table-wrap dimension-table"><table>
    <thead><tr><th>Column</th><th>Type</th><th><HeatLegend label={`${includeEmpty ? "MISSING" : "NULL"} rate by ${dimensionName}`} /></th><th>Metrics across values</th></tr></thead>
    <tbody>{columns.map((baseColumn) => <tr key={baseColumn.name}>
      <td className="column-name"><strong>{baseColumn.name}</strong><small>{baseColumn.description}</small></td>
      <td><code>{baseColumn.data_type}</code></td>
      <td><div
        className="heatmap categorical-heatmap"
        style={{ "--point-count": profiles.length } as React.CSSProperties}
      >{profiles.map((profile) => {
        const column = profile.columns.find((item) => item.name === baseColumn.name);
        const rate = column ? missingMetric(column, includeEmpty).rate : 0;
        return <span key={profile.dimension_value} className={column ? `heat-cell ${rate === 0 ? "zero" : ""}` : "heat-cell missing"}
          style={{ "--heat": String(heatIntensity(rate)) } as React.CSSProperties}
          title={`${dimensionName} = ${profile.dimension_value}: ${column ? `${(rate * 100).toFixed(1)}% ${includeEmpty ? "MISSING" : "NULL"} · ${missingDetail(column, includeEmpty)}` : "No data"}`} />;
      })}</div><small className="dimension-count">{profiles.length} values · hover to inspect</small></td>
      <td><DimensionMetricSummary profiles={profiles} columnName={baseColumn.name} /></td>
    </tr>)}</tbody>
  </table></div>;
}

function App() {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [sliceIndex, setSliceIndex] = useState(0);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [trendRange, setTrendRange] = useState<TrendRange>(30);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [expandedDatasets, setExpandedDatasets] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch("/api/models").then((response) => {
      if (!response.ok) throw new Error("Could not load profiles");
      return response.json() as Promise<ModelProfile[]>;
    }).then((data) => {
      setModels(data);
      setSelectedModel(data[0]?.name ?? "");
      if (data[0]) setExpandedDatasets(new Set([`${data[0].database}/${data[0].schema}`]));
    })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const model = models.find((item) => item.name === selectedModel);
  const includeEmpty = model?.profiling.treat_empty_string_as_null ?? false;
  const slice = model?.profiles[sliceIndex];
  const overallSlice = model?.profiles.find((profile) => profile.dimension_name === null);
  const dimensionNames = useMemo(() => Array.from(new Set(
    model?.profiles.flatMap((profile) => profile.dimension_name ? [profile.dimension_name] : []) ?? [],
  )), [model]);
  const activeDimension = slice?.dimension_name ?? null;
  const dimensionSlices = model?.profiles.filter((profile) => profile.dimension_name === activeDimension) ?? [];
  const temporalDimension = activeDimension !== null && isTemporal(dimensionSlices);
  const latestDimensionSlice = temporalDimension
    ? [...dimensionSlices].sort((a, b) => (a.dimension_value ?? "").localeCompare(b.dimension_value ?? "")).at(-1)
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

  function renderHeaders() {
    const missingLabel = includeEmpty ? "MISSING" : "NULL";
    if (typeFilter === "string") return <tr><th>Column</th><th>{missingLabel}</th><th>Distinct</th></tr>;
    if (typeFilter === "numeric") return <tr><th>Column</th><th>Type</th><th>{missingLabel}</th><th>Min</th><th>Max</th></tr>;
    if (typeFilter === "boolean") return <tr><th>Column</th><th>{missingLabel}</th><th>TRUE</th></tr>;
    if (typeFilter === "date") return <tr><th>Column</th><th>{missingLabel}</th><th>Min date</th><th>Max date</th></tr>;
    return <tr><th>Column</th><th>Type</th><th>{missingLabel}</th><th>Metrics</th></tr>;
  }

  function renderCells(column: ColumnProfile) {
    const columnCell = <td className="column-name"><strong>{column.name}</strong><small>{column.description}</small></td>;
    if (typeFilter === "string") return <>{columnCell}<td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td className="value-cell">{column.distinct_count?.toLocaleString() ?? "—"}</td></>;
    if (typeFilter === "numeric") return <>{columnCell}<td><code>{column.data_type}</code></td><td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td className="value-cell">{String(column.min_value ?? "—")}</td><td className="value-cell">{String(column.max_value ?? "—")}</td></>;
    if (typeFilter === "boolean") return <>{columnCell}<td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td><BooleanMetric column={column} slice={slice!} /></td></>;
    if (typeFilter === "date") return <>{columnCell}<td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td className="value-cell">{String(column.min_value ?? "—")}</td><td className="value-cell">{String(column.max_value ?? "—")}</td></>;
    return <>{columnCell}<td><code>{column.data_type}</code></td><td><NullMetric column={column} includeEmpty={includeEmpty} /></td><td><CompactMetrics column={column} slice={slice!} /></td></>;
  }

  return <main className="shell">
    <aside className="explorer">
      <div className="brand">data profile <span>alpha</span></div>
      <label className="search"><span>Search models</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Model name" /></label>
      <div className="tree-label">Explorer</div>
      {[...explorerGroups].sort(([left], [right]) => left.localeCompare(right)).map(([database, datasets]) => <div className="project-group" key={database}>
        <div className="project-name"><span>◆</span>{database}</div>
        {[...datasets].sort(([left], [right]) => left.localeCompare(right)).map(([schema, relations]) => {
          const datasetKey = `${database}/${schema}`;
          const isOpen = query.trim().length > 0 || expandedDatasets.has(datasetKey);
          return <div className="dataset-group" key={datasetKey}>
            <button className={`dataset-item ${isOpen ? "open" : ""}`} onClick={() => toggleDataset(datasetKey)} aria-expanded={isOpen}>
              <span className="chevron">›</span><span className="dataset-icon">▤</span><span>{schema}</span><small>{relations.length}</small>
            </button>
            {isOpen && <div className="dataset-relations">{[...relations].sort((left, right) => left.name.localeCompare(right.name)).map((item) => <button className={`model-item ${item.name === selectedModel ? "selected" : ""}`} key={item.unique_id || item.name} onClick={() => { setSelectedModel(item.name); setSliceIndex(0); setTypeFilter("all"); }}>
              <span className="table-icon">{item.resource_type === "source" ? "◇" : "▦"}</span><span><small>{item.resource_type}</small>{item.name}</span>
            </button>)}</div>}
          </div>;
        })}
      </div>)}
    </aside>

    <section className="detail">
      {error && <div className="notice error">{error}. Is the API running?</div>}
      {!model && !error && <div className="notice">Loading profile…</div>}
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
          <div className="metadata"><span><b>{(overallSlice?.record_count ?? slice.record_count).toLocaleString()}</b> total rows</span><span>Profiled {model.profiled_at ? new Date(model.profiled_at).toLocaleString() : "—"}</span><span>{model.tests.length} dbt tests</span></div>
          {temporalDimension && latestDimensionSlice && <div className="latest-partition"><span>Latest partition</span><strong>{latestDimensionSlice.dimension_value}</strong><small>{latestDimensionSlice.record_count.toLocaleString()} rows</small></div>}
        </header>

        <div className="profile-by">
          <span>Profile by</span>
          <div className="segments">
            <button className={activeDimension === null ? "active" : ""} onClick={() => selectDimension(null)}>Overall</button>
            {dimensionNames.map((dimension) => <button className={activeDimension === dimension ? "active" : ""} key={dimension} onClick={() => selectDimension(dimension)}>{dimension}</button>)}
          </div>
        </div>

        <div className="columns-heading">
          <div><h2>Columns</h2><span>{visibleColumns.length} of {summarySlice!.columns.length}</span></div>
          <div className="type-tabs" aria-label="Filter columns by type">
            {typeFilters.map((filter) => <button className={typeFilter === filter.value ? "active" : ""} key={filter.value} onClick={() => setTypeFilter(filter.value)}>{filter.label}</button>)}
          </div>
          {temporalDimension && <div className="range-tabs" aria-label="Trend range">
            {([30, 90, "all"] as TrendRange[]).map((range) => <button className={trendRange === range ? "active" : ""} key={range} onClick={() => setTrendRange(range)}>{range === "all" ? "All" : `Latest ${range}`}</button>)}
          </div>}
        </div>
        {activeDimension === null && <div className="table-wrap">
          <table><thead>{renderHeaders()}</thead><tbody>{visibleColumns.map((column) => <tr key={column.name}>{renderCells(column)}</tr>)}</tbody></table>
          {visibleColumns.length === 0 && <div className="empty-state">No columns match this type.</div>}
        </div>}
        {temporalDimension && <TemporalTable profiles={dimensionSlices} filter={typeFilter} range={trendRange} includeEmpty={includeEmpty} />}
        {activeDimension !== null && !temporalDimension && <CategoricalTable profiles={dimensionSlices} filter={typeFilter} dimensionName={activeDimension} includeEmpty={includeEmpty} />}
      </>}
    </section>
  </main>;
}

export default App;
