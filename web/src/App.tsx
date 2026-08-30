import { useEffect, useMemo, useState } from "react";

import type { ColumnProfile, ModelProfile, ProfileSlice } from "./types";

function metric(column: ColumnProfile, slice: ProfileSlice): string {
  if (column.data_type === "STRING") return `${column.distinct_count?.toLocaleString()} distinct`;
  if (column.data_type === "BOOL") {
    const trueRate = slice.record_count ? ((column.true_count ?? 0) / slice.record_count) * 100 : 0;
    return `${(column.true_count ?? 0).toLocaleString()} true (${trueRate.toFixed(1)}%)`;
  }
  return `${column.min_value ?? "—"} → ${column.max_value ?? "—"}`;
}

function App() {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [sliceIndex, setSliceIndex] = useState(0);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/models")
      .then((response) => {
        if (!response.ok) throw new Error("Could not load profiles");
        return response.json() as Promise<ModelProfile[]>;
      })
      .then((data) => {
        setModels(data);
        setSelectedModel(data[0]?.name ?? "");
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const model = models.find((item) => item.name === selectedModel);
  const slice = model?.profiles[sliceIndex];
  const filteredModels = useMemo(
    () => models.filter((item) => item.name.toLowerCase().includes(query.toLowerCase())),
    [models, query],
  );

  return (
    <main className="shell">
      <aside className="explorer">
        <div className="brand">data profile <span>alpha</span></div>
        <label className="search">
          <span>Search models</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Model name" />
        </label>
        <div className="tree-label">Explorer</div>
        {filteredModels.map((item) => (
          <button
            className={`model-item ${item.name === selectedModel ? "selected" : ""}`}
            key={item.name}
            onClick={() => { setSelectedModel(item.name); setSliceIndex(0); }}
          >
            <span className="table-icon">▦</span>
            <span><small>{item.database} / {item.schema}</small>{item.name}</span>
          </button>
        ))}
      </aside>

      <section className="detail">
        {error && <div className="notice error">{error}. Is the API running?</div>}
        {!model && !error && <div className="notice">Loading profile…</div>}
        {model && slice && (
          <>
            <header>
              <div className="eyebrow">{model.database} / {model.schema}</div>
              <div className="title-row"><h1>{model.name}</h1><span className="pill">{model.materialization}</span></div>
              <p>{model.description}</p>
              <div className="metadata">
                <span><b>{slice.record_count.toLocaleString()}</b> rows</span>
                <span>Profiled {new Date(model.profiled_at).toLocaleString()}</span>
                <span>{model.tests.length} dbt tests</span>
              </div>
            </header>

            <div className="toolbar">
              <div><h2>Columns</h2><span>{slice.columns.length} columns</span></div>
              <label>Profile view
                <select value={sliceIndex} onChange={(event) => setSliceIndex(Number(event.target.value))}>
                  {model.profiles.map((profile, index) => (
                    <option value={index} key={`${profile.dimension_name}-${profile.dimension_value}`}>
                      {profile.dimension_name ? `${profile.dimension_name} = ${profile.dimension_value}` : "Overall"}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="table-wrap">
              <table>
                <thead><tr><th>Column</th><th>Type</th><th>Nulls</th><th>Type metric</th></tr></thead>
                <tbody>
                  {slice.columns.map((column) => (
                    <tr key={column.name}>
                      <td><strong>{column.name}</strong><small>{column.description}</small></td>
                      <td><code>{column.data_type}</code></td>
                      <td className="null-cell">
                        <div className="bar" style={{ "--rate": `${column.null_rate * 100}%` } as React.CSSProperties}>
                          <span>{(column.null_rate * 100).toFixed(1)}%</span>
                        </div>
                        <small>{column.null_count.toLocaleString()} rows</small>
                      </td>
                      <td>{metric(column, slice)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </main>
  );
}

export default App;

