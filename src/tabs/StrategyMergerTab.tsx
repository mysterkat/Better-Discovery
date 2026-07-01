import { useEffect, useMemo, useState } from "react";
import {
  listLibrary,
  mergeLibraryStrategies,
  type LibraryEntry,
  type MergeMode,
} from "../api/library";
import { openFolder } from "../api/system";
import SavedStrategyPicker from "../components/SavedStrategyPicker";

const MODES: Array<{ id: MergeMode; label: string; detail: string }> = [
  {
    id: "regime_switch",
    label: "Regime switch",
    detail: "One strategy is meant for trend, another for chop/reversal, with a router deciding later.",
  },
  {
    id: "priority",
    label: "Priority",
    detail: "Use the first strategy first, then fall back to the next one if no signal is active.",
  },
  {
    id: "vote",
    label: "Vote",
    detail: "Require agreement before the merged strategy is considered active.",
  },
  {
    id: "portfolio",
    label: "Portfolio",
    detail: "Keep components separate but track them as one deployable research package.",
  },
];

function defaultName(entries: LibraryEntry[], selected: string[]): string {
  if (selected.length === 0) return "XAUUSD merged strategy";
  return selected
    .map((id) => {
      const entry = entries.find((item) => item.pattern_id === id);
      const metadata = entry?.metadata as Record<string, unknown> | undefined;
      return String(metadata?.name ?? id).slice(0, 18);
    })
    .join(" + ");
}

export default function StrategyMergerTab() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState<MergeMode>("regime_switch");
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

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

  const mergeableEntries = useMemo(() => {
    return entries.filter((entry) => {
      const metadata = entry.metadata as Record<string, unknown>;
      return metadata.__kind !== "merged";
    });
  }, [entries]);

  useEffect(() => {
    setName((current) => current || defaultName(entries, selected));
  }, [entries, selected]);

  const toggle = (patternId: string) => {
    setSelected((prev) => {
      if (prev.includes(patternId)) return prev.filter((id) => id !== patternId);
      if (prev.length >= 3) return prev;
      return [...prev, patternId];
    });
  };

  const createMerge = async () => {
    if (selected.length < 2) {
      setError("Pick 2 or 3 saved strategies first.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await mergeLibraryStrategies({
        name: name.trim() || defaultName(entries, selected),
        mode,
        notes,
        components: selected.map((pattern_id, index) => ({
          pattern_id,
          weight: 1,
          role: index === 0 ? "primary" : "complement",
        })),
      });
      setNotice(`Saved merged strategy ${result.entry.pattern_id}.`);
      await openFolder(result.entry.lib_path).catch(() => undefined);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="strategy-merger-root">
      <div className="tab-header">
        <h2>Strategy Merger</h2>
        <p className="tab-subtitle">
          Combine 2-3 saved strategies into one tracked research package.
        </p>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {notice && <div className="alert alert-success" style={{ marginBottom: 12 }}>{notice}</div>}

      <div className="merger-layout">
        <section className="merger-panel">
          <div className="section-label">Saved Strategies</div>
          <div className="compare-toolbar">
            <button className="btn btn-secondary btn-mini" onClick={() => reload().catch(() => {})}>
              Refresh
            </button>
            <span className="field-hint">{selected.length}/3 selected</span>
          </div>
          {loading ? (
            <p className="results-loading">Loading...</p>
          ) : (
            <SavedStrategyPicker
              entries={mergeableEntries}
              selected={selected}
              onToggle={toggle}
              maxSelected={3}
            />
          )}
        </section>

        <section className="merger-panel">
          <div className="section-label">Merge Setup</div>
          <label className="field">
            <span>Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>

          <div className="merger-mode-grid">
            {MODES.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`merger-mode${mode === item.id ? " active" : ""}`}
                onClick={() => setMode(item.id)}
              >
                <strong>{item.label}</strong>
                <span>{item.detail}</span>
              </button>
            ))}
          </div>

          <label className="field">
            <span>Notes</span>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={5}
              placeholder="Example: primary for H1/H4 trend, second for range reversals."
            />
          </label>

          <div className="alert alert-warn">
            This saves the merged strategy record. Combined one-EA execution still needs a dedicated router/risk-manager export before live deployment.
          </div>

          <button className="btn btn-primary" disabled={busy || selected.length < 2} onClick={createMerge}>
            {busy ? "Saving..." : "Save Merged Strategy"}
          </button>
        </section>
      </div>
    </div>
  );
}
