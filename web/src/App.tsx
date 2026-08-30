import { useEffect, useMemo, useState } from "react";

import type { ColumnProfile, ModelProfile, ProfileSlice } from "./types";

type TypeFilter = "all" | "string" | "numeric" | "boolean" | "date";
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

function NullMetric({ column }: { column: ColumnProfile }) {
  return <div className="null-metric">
    <div className="bar" style={{ "--rate": `${column.null_rate * 100}%` } as React.CSSProperties}>
      <span>{(column.null_rate * 100).toFixed(1)}%</span>
    </div>
    <small>{column.null_count.toLocaleString()}</small>
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

function App() {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [sliceIndex, setSliceIndex] = useState(0);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/models").then((response) => {
      if (!response.ok) throw new Error("Could not load profiles");
      return response.json() as Promise<ModelProfile[]>;
    }).then((data) => { setModels(data); setSelectedModel(data[0]?.name ?? ""); })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const model = models.find((item) => item.name === selectedModel);
  const slice = model?.profiles[sliceIndex];
  const dimensionNames = useMemo(() => Array.from(new Set(
    model?.profiles.flatMap((profile) => profile.dimension_name ? [profile.dimension_name] : []) ?? [],
  )), [model]);
  const activeDimension = slice?.dimension_name ?? null;
  const dimensionSlices = model?.profiles.filter((profile) => profile.dimension_name === activeDimension) ?? [];
  const visibleColumns = slice?.columns.filter((column) => matchesType(column, typeFilter)) ?? [];
  const filteredModels = useMemo(
    () => models.filter((item) => item.name.toLowerCase().includes(query.toLowerCase())), [models, query],
  );

  function selectDimension(dimensionName: string | null) {
    const index = model?.profiles.findIndex((profile) => profile.dimension_name === dimensionName) ?? -1;
    if (index >= 0) setSliceIndex(index);
  }

  function renderHeaders() {
    if (typeFilter === "string") return <tr><th>Column</th><th>NULL</th><th>Distinct</th></tr>;
    if (typeFilter === "numeric") return <tr><th>Column</th><th>Type</th><th>NULL</th><th>Min</th><th>Max</th></tr>;
    if (typeFilter === "boolean") return <tr><th>Column</th><th>NULL</th><th>TRUE</th></tr>;
    if (typeFilter === "date") return <tr><th>Column</th><th>NULL</th><th>Min date</th><th>Max date</th></tr>;
    return <tr><th>Column</th><th>Type</th><th>NULL</th><th>Metrics</th></tr>;
  }

  function renderCells(column: ColumnProfile) {
    const columnCell = <td className="column-name"><strong>{column.name}</strong><small>{column.description}</small></td>;
    if (typeFilter === "string") return <>{columnCell}<td><NullMetric column={column} /></td><td className="value-cell">{column.distinct_count?.toLocaleString() ?? "—"}</td></>;
    if (typeFilter === "numeric") return <>{columnCell}<td><code>{column.data_type}</code></td><td><NullMetric column={column} /></td><td className="value-cell">{String(column.min_value ?? "—")}</td><td className="value-cell">{String(column.max_value ?? "—")}</td></>;
    if (typeFilter === "boolean") return <>{columnCell}<td><NullMetric column={column} /></td><td><BooleanMetric column={column} slice={slice!} /></td></>;
    if (typeFilter === "date") return <>{columnCell}<td><NullMetric column={column} /></td><td className="value-cell">{String(column.min_value ?? "—")}</td><td className="value-cell">{String(column.max_value ?? "—")}</td></>;
    return <>{columnCell}<td><code>{column.data_type}</code></td><td><NullMetric column={column} /></td><td><CompactMetrics column={column} slice={slice!} /></td></>;
  }

  return <main className="shell">
    <aside className="explorer">
      <div className="brand">data profile <span>alpha</span></div>
      <label className="search"><span>Search models</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Model name" /></label>
      <div className="tree-label">Explorer</div>
      {filteredModels.map((item) => <button className={`model-item ${item.name === selectedModel ? "selected" : ""}`} key={item.name} onClick={() => { setSelectedModel(item.name); setSliceIndex(0); setTypeFilter("all"); }}>
        <span className="table-icon">▦</span><span><small>{item.database} / {item.schema}</small>{item.name}</span>
      </button>)}
    </aside>

    <section className="detail">
      {error && <div className="notice error">{error}. Is the API running?</div>}
      {!model && !error && <div className="notice">Loading profile…</div>}
      {model && slice && <>
        <header>
          <div className="eyebrow">{model.database} / {model.schema}</div>
          <div className="title-row"><h1>{model.name}</h1><span className="pill">{model.materialization}</span></div>
          <p>{model.description}</p>
          <div className="metadata"><span><b>{slice.record_count.toLocaleString()}</b> rows</span><span>Profiled {new Date(model.profiled_at).toLocaleString()}</span><span>{model.tests.length} dbt tests</span></div>
        </header>

        <div className="profile-by">
          <span>Profile by</span>
          <div className="segments">
            <button className={activeDimension === null ? "active" : ""} onClick={() => selectDimension(null)}>Overall</button>
            {dimensionNames.map((dimension) => <button className={activeDimension === dimension ? "active" : ""} key={dimension} onClick={() => selectDimension(dimension)}>{dimension}</button>)}
          </div>
          {activeDimension && dimensionSlices.length > 1 && <label className="dimension-value">Value
            <select value={sliceIndex} onChange={(event) => setSliceIndex(Number(event.target.value))}>
              {model.profiles.map((profile, index) => profile.dimension_name === activeDimension && <option value={index} key={profile.dimension_value}>{profile.dimension_value}</option>)}
            </select>
          </label>}
        </div>

        <div className="columns-heading">
          <div><h2>Columns</h2><span>{visibleColumns.length} of {slice.columns.length}</span></div>
          <div className="type-tabs" aria-label="Filter columns by type">
            {typeFilters.map((filter) => <button className={typeFilter === filter.value ? "active" : ""} key={filter.value} onClick={() => setTypeFilter(filter.value)}>{filter.label}</button>)}
          </div>
        </div>
        <div className="table-wrap">
          <table><thead>{renderHeaders()}</thead><tbody>{visibleColumns.map((column) => <tr key={column.name}>{renderCells(column)}</tr>)}</tbody></table>
          {visibleColumns.length === 0 && <div className="empty-state">No columns match this type.</div>}
        </div>
      </>}
    </section>
  </main>;
}

export default App;
