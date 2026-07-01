import { useEffect, useMemo, useState } from "react";
import {
  evolveLibraryStrategy,
  listLibrary,
  type LibraryEntry,
} from "../api/library";
import SavedStrategyPicker from "../components/SavedStrategyPicker";

function isHypothesis(entry: LibraryEntry): boolean {
  const metadata = entry.metadata as Record<string, unknown>;
  return metadata.__kind === "hypothesis" && typeof metadata.hypothesis_strategy === "object";
}

export default function EvolutionLabTab() {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [childCount, setChildCount] = useState("50");
  const [seed, setSeed] = useState("910300");
  const [generation, setGeneration] = useState("1");
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

  useEffect(() => { reload().catch(() => undefined); }, []);

  const evolvable = useMemo(() => entries.filter(isHypothesis), [entries]);
  const selectedId = selected[0] ?? "";

  const run = async () => {
    if (!selectedId) {
      setError("Pick one saved hypothesis strategy first.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await evolveLibraryStrategy(selectedId, {
        child_count: Math.max(1, Math.min(250, Math.trunc(Number(childCount)) || 1)),
        seed: Math.max(0, Math.trunc(Number(seed)) || 0),
        generation: Math.max(1, Math.trunc(Number(generation)) || 1),
        notes,
      });
      setNotice(`Created ${result.created} evolved children in Strategy Library.`);
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
        <h2>Evolution Lab</h2>
        <p className="tab-subtitle">
          Mutate a saved strategy into nearby children, then test the children from Research Lab or Discovery.
        </p>
      </div>

      {error && <div className="alert alert-error" style={{ marginBottom: 12 }}>{error}</div>}
      {notice && <div className="alert alert-success" style={{ marginBottom: 12 }}>{notice}</div>}

      <div className="merger-layout">
        <section className="merger-panel">
          <div className="section-label">Parent Strategy</div>
          {loading ? (
            <p className="results-loading">Loading...</p>
          ) : (
            <SavedStrategyPicker
              entries={evolvable}
              selected={selected}
              onToggle={(id) => setSelected((current) => current[0] === id ? [] : [id])}
              maxSelected={1}
            />
          )}
        </section>

        <section className="merger-panel">
          <div className="section-label">Mutation Settings</div>
          <div className="form-grid-2">
            <label className="field">
              <span className="field-label">Children</span>
              <input className="field-input" value={childCount} onChange={(e) => setChildCount(e.target.value)} inputMode="numeric" />
            </label>
            <label className="field">
              <span className="field-label">Generation</span>
              <input className="field-input" value={generation} onChange={(e) => setGeneration(e.target.value)} inputMode="numeric" />
            </label>
            <label className="field">
              <span className="field-label">Seed</span>
              <div style={{ display: "flex", gap: 8 }}>
                <input className="field-input" value={seed} onChange={(e) => setSeed(e.target.value)} inputMode="numeric" />
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setSeed(String(Math.floor(Math.random() * 2_147_483_647)))}>
                  Random
                </button>
              </div>
            </label>
          </div>
          <label className="field">
            <span className="field-label">Notes</span>
            <textarea className="field-input" value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} />
          </label>
          <button className="btn btn-primary" onClick={run} disabled={busy || !selectedId}>
            {busy ? "Creating..." : "Create Children"}
          </button>
        </section>
      </div>
    </div>
  );
}
