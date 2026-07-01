import { useEffect, useMemo, useState } from "react";
import {
  deleteLibraryEntry,
  exportLibraryHypothesisEa,
  listLibrary,
  type LibraryEntry,
} from "../api/library";
import { openFolder } from "../api/system";
import { titleCase } from "../lib/format";

function kindOf(entry: LibraryEntry): string {
  const metadata = entry.metadata as Record<string, unknown>;
  return String(metadata.__kind ?? (entry.set_path ? "set_pattern" : "strategy"));
}

function nameOf(entry: LibraryEntry): string {
  const metadata = entry.metadata as Record<string, unknown>;
  return String(metadata.name ?? entry.pattern_id);
}

function metricsOf(entry: LibraryEntry): Record<string, unknown> {
  const metadata = entry.metadata as Record<string, unknown>;
  return (metadata.metrics ?? metadata) as Record<string, unknown>;
}

function fmt(value: unknown, digits = 2): string {
  return typeof value === "number" && isFinite(value) ? value.toFixed(digits) : "-";
}

export default function StrategyLibraryTab() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      setEntries(await listLibrary());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reload().catch(() => {}); }, []);

  const kinds = useMemo(() => {
    return ["all", ...Array.from(new Set(entries.map(kindOf))).sort()];
  }, [entries]);

  const visible = useMemo(() => {
    return filter === "all" ? entries : entries.filter((entry) => kindOf(entry) === filter);
  }, [entries, filter]);

  const exportEntry = async (entry: LibraryEntry) => {
    setBusy(entry.pattern_id);
    setNotice(null);
    setError(null);
    try {
      const result = await exportLibraryHypothesisEa(entry.pattern_id);
      const path = result.mt5_experts_folder ?? result.preferred_mq5_path ?? result.mq5_path;
      setNotice(`Exported ${entry.pattern_id}: ${result.preferred_mq5_path ?? result.mq5_path}`);
      if (path) await openFolder(path).catch(() => undefined);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const removeEntry = async (entry: LibraryEntry) => {
    if (!confirm(`Delete ${entry.pattern_id} from the saved library?`)) return;
    setBusy(entry.pattern_id);
    setError(null);
    try {
      await deleteLibraryEntry(entry.pattern_id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="strategy-library-root">
      <div className="tab-header">
        <h2>Strategy Library</h2>
        <p className="tab-subtitle">
          Saved discovery candidates, hypothesis strategies, and merged strategy records.
        </p>
      </div>

      <div className="compare-toolbar">
        <button className="btn btn-secondary btn-mini" onClick={() => reload().catch(() => {})}>
          Refresh
        </button>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          {kinds.map((kind) => (
            <option key={kind} value={kind}>{kind === "all" ? "All types" : titleCase(kind)}</option>
          ))}
        </select>
        <span className="field-hint">{visible.length} shown · {entries.length} saved</span>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {notice && <div className="alert alert-success" style={{ marginBottom: 12 }}>{notice}</div>}

      {loading ? (
        <p className="results-loading">Loading saved strategies...</p>
      ) : visible.length === 0 ? (
        <div className="library-empty">
          Save candidates from Discovery Results, then review and export them here.
        </div>
      ) : (
        <div className="library-grid">
          {visible.map((entry) => {
            const metadata = entry.metadata as Record<string, unknown>;
            const metrics = metricsOf(entry);
            const kind = kindOf(entry);
            const components = Array.isArray(metadata.components) ? metadata.components : [];
            const canExportHypothesis = kind === "hypothesis";
            return (
              <article key={entry.pattern_id} className="library-card">
                <header className="library-card-header">
                  <div>
                    <div className="library-card-title" title={entry.pattern_id}>{nameOf(entry)}</div>
                    <div className="library-card-meta">
                      {titleCase(kind)} · saved {entry.saved_at ? new Date(entry.saved_at).toLocaleString() : "-"}
                    </div>
                  </div>
                  <span className="library-kind">{titleCase(kind)}</span>
                </header>

                <div className="library-metrics">
                  <div><span>PF</span><strong>{fmt(metrics.profit_factor ?? metrics.test_pf ?? metrics.ea_test_pf)}</strong></div>
                  <div><span>Pass</span><strong>{typeof metrics.challenge_active_pass_rate === "number" ? `${(metrics.challenge_active_pass_rate * 100).toFixed(1)}%` : "-"}</strong></div>
                  <div><span>Trades</span><strong>{fmt(metrics.trades ?? metrics.test_trades ?? metrics.ea_test_trades, 0)}</strong></div>
                  <div><span>DD</span><strong>{fmt(metrics.max_drawdown_pct)}</strong></div>
                </div>

                {typeof metadata.notes === "string" && metadata.notes && (
                  <p className="library-notes">{metadata.notes}</p>
                )}

                {components.length > 0 && (
                  <div className="library-components">
                    <span className="field-hint">Components</span>
                    {components.map((item, index) => {
                      const component = item as Record<string, unknown>;
                      return (
                        <span key={index} className="component-pill">
                          {String(component.name ?? component.pattern_id ?? index)}
                        </span>
                      );
                    })}
                  </div>
                )}

                <div className="library-actions">
                  <button className="btn-mini" onClick={() => openFolder(entry.lib_path).catch(() => undefined)}>
                    See in folder
                  </button>
                  {canExportHypothesis && (
                    <button
                      className="btn-mini"
                      disabled={busy === entry.pattern_id}
                      onClick={() => exportEntry(entry)}
                    >
                      {busy === entry.pattern_id ? "Exporting..." : "Export EA"}
                    </button>
                  )}
                  <button
                    className="btn-mini"
                    disabled={busy === entry.pattern_id}
                    onClick={() => removeEntry(entry)}
                  >
                    Delete
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
