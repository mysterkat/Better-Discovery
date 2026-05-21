import { useState, useEffect, useMemo } from "react";
import { getParams, startDiscovery, type ParamDef } from "../api/discovery";
import { getCurrentImport, type CurrentImport } from "../api/data";
import { useJobs } from "../state/jobs";
import { useParamDefaults } from "../state/paramDefaults";
import JobProgress from "../components/JobProgress";
import { openResultWindow } from "../lib/windows";

// Folder-type keys whose override is controlled by the one-shot toggle.
const FOLDER_KEYS = new Set(["DATA_FOLDER", "OUTPUT_FOLDER"]);

// fix 2b: (?) hover tooltip popup for param descriptions
function ParamTooltip({ description }: { description: string }) {
  if (!description) return null;
  return (
    <span className="param-tooltip-wrap">
      <span className="param-tooltip-icon" tabIndex={0} role="button" aria-label="Parameter description">?</span>
      <span className="param-tooltip-popup">{description}</span>
    </span>
  );
}

// TF filename keys are hidden from the per-run tab — the banner shows what's
// actually loaded (auto-detected from the latest MT5 import). Power users can
// still override these in Settings → Edit Default Values, where the override
// flows back into the tab automatically.
const HIDDEN_FROM_TAB = new Set([
  // TF filenames are auto-detected from the imported MT5 history.
  "TF1_FILE", "TF2_FILE", "TF3_FILE", "TF4_FILE", "TF5_FILE",
  // MULTI_SEED_BASE is locked to RANDOM_SEED by the bridge — exposing it
  // in the UI just creates two parallel knobs that disagree. The bridge
  // injects MULTI_SEED_BASE = RANDOM_SEED on every run.
  "MULTI_SEED_BASE",
]);

function formatAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const diffSec = Math.max(0, (Date.now() - then) / 1000);
  if (diffSec < 60) return "just now";
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

export default function DiscoveryTab() {
  const [params, setParams] = useState<ParamDef[]>([]);
  // `overrides` only contains user-typed values for THIS session.
  // A missing/empty entry means "use the true default" (persistent default
  // from the settings modal, falling back to the code-level default).
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [overrideOnce, setOverrideOnce] = useState(false);
  const [folderOverrides, setFolderOverrides] = useState<Record<string, string>>({});
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["Data & Files", "General"]));
  // Per-group toggle for the "Show advanced (N)" collapse. A group's advanced
  // section is implicitly open when any of its advanced fields has an edit or
  // a persistent default override — see effectiveShowAdvanced() below.
  const [showAdvanced, setShowAdvanced] = useState<Set<string>>(new Set());
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [currentImport, setCurrentImport] = useState<CurrentImport | null>(null);

  // Subscribe reactively to persistent defaults so changes made in the
  // settings modal propagate into placeholders + labels live.
  const persistentDefaults = useParamDefaults((s) => s.defaults);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const setActiveJob = useJobs((s) => s.setActive);
  const isRunning = !!jobId && (job?.status === "running" || job?.status === "pending");
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed" || job?.status === "cancelled");

  useEffect(() => {
    getParams().then(setParams).catch(() => {});
    getCurrentImport().then(setCurrentImport).catch(() => {});
    // Recover an in-flight discovery job from a previous mount of this tab.
    const stored = useJobs.getState().activeByKind["discovery"];
    if (stored) {
      const existing = useJobs.getState().jobs[stored];
      if (!existing || (existing.status !== "done" && existing.status !== "failed" && existing.status !== "cancelled")) {
        setJobId(stored);
      }
    }
  }, []);

  // Discovery's auto-detect picks the smallest 3 timeframes if the user
  // hasn't customized any TFn_FILE override. We surface this in a banner so
  // it's obvious which files the run will actually use.
  const tfFilesUserSet = ["TF1_FILE", "TF2_FILE", "TF3_FILE", "TF4_FILE", "TF5_FILE"].some(
    (k) => (overrides[k]?.trim() || persistentDefaults[k]) != null,
  );
  const autoDetectedTfs = currentImport?.exists
    ? currentImport.timeframes.slice(0, 5).map((tf) => tf.label).join(", ")
    : "";

  // The "true default" for a param: user's persistent default if set,
  // else the code-level default from pattern_discovery_v6.py.
  const trueDefault = (p: ParamDef): string => {
    const pd = persistentDefaults[p.key];
    if (pd != null) return String(pd);
    return p.value != null ? String(p.value) : "";
  };

  // Group params by their 'group' field, skipping anything we hide from
  // the per-run tab (e.g. TFn_FILE — covered by the auto-detect banner).
  // Empty groups are dropped so the accordion doesn't show stub headers.
  const groups = useMemo(() => {
    const map = new Map<string, ParamDef[]>();
    for (const p of params) {
      if (HIDDEN_FROM_TAB.has(p.key)) continue;
      if (!map.has(p.group)) map.set(p.group, []);
      map.get(p.group)!.push(p);
    }
    for (const [k, v] of [...map.entries()]) {
      if (v.length === 0) map.delete(k);
    }
    return map;
  }, [params]);

  const toggleGroup = (g: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g); else next.add(g);
      return next;
    });

  const toggleAdvanced = (g: string) =>
    setShowAdvanced((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g); else next.add(g);
      return next;
    });

  // A group's advanced fields are tier=="advanced". Treat missing tier as core
  // so older backend builds keep rendering everything visibly.
  const isAdvanced = (p: ParamDef) => p.tier === "advanced";
  const partitionGroup = (gParams: ParamDef[]) => {
    const core: ParamDef[] = [];
    const advanced: ParamDef[] = [];
    for (const p of gParams) (isAdvanced(p) ? advanced : core).push(p);
    return { core, advanced };
  };
  // Auto-reveal the advanced section if the user has touched anything in it
  // (either this session's overrides or a saved persistent default), so an
  // edited field is never invisible.
  const effectiveShowAdvanced = (group: string, advanced: ParamDef[]) => {
    if (showAdvanced.has(group)) return true;
    return advanced.some((p) => isEdited(p.key) || persistentDefaults[p.key] != null);
  };

  const setValue = (key: string, val: string) => {
    setOverrides((prev) => {
      const next = { ...prev };
      if (val === "") delete next[key];
      else next[key] = val;
      return next;
    });
  };

  const resetField = (key: string) => {
    setOverrides((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const handleResetToDefaults = () => {
    setOverrides({});
  };

  const isEdited = (key: string): boolean => {
    const v = overrides[key];
    return v != null && v.trim() !== "";
  };

  const resetGroup = (groupName: string) => {
    const groupKeys = new Set((groups.get(groupName) ?? []).map((p) => p.key));
    setOverrides((prev) => {
      const next: Record<string, string> = {};
      for (const [k, v] of Object.entries(prev)) {
        if (!groupKeys.has(k)) next[k] = v;
      }
      return next;
    });
  };

  const groupHasEdits = (groupName: string): boolean =>
    (groups.get(groupName) ?? []).some((p) => isEdited(p.key));

  const setFolderVal = (key: string, val: string) =>
    setFolderOverrides((prev) => ({ ...prev, [key]: val }));

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const parsed: Record<string, unknown> = {};
      for (const p of params) {
        if (FOLDER_KEYS.has(p.key)) continue;
        // Resolution order: user-typed (overrides) > persistent default.
        // If neither is set, omit — backend uses the code-level default.
        const userVal = overrides[p.key]?.trim();
        let raw: string | undefined;
        if (userVal) {
          raw = userVal;
        } else {
          const pd = persistentDefaults[p.key];
          if (pd != null) raw = String(pd);
        }
        if (!raw) continue;
        // Accept European decimal comma for numeric inputs.
        const normalized = raw.replace(",", ".");
        if (p.type === "bool") {
          parsed[p.key] = raw === "true" || raw === "1";
        } else if (p.type === "int") {
          const n = parseInt(normalized, 10);
          if (!isNaN(n)) parsed[p.key] = n;
        } else if (p.type === "float") {
          const n = parseFloat(normalized);
          if (!isNaN(n)) parsed[p.key] = n;
        } else {
          parsed[p.key] = raw;
        }
      }
      if (overrideOnce) {
        for (const [k, v] of Object.entries(folderOverrides)) {
          if (v.trim()) parsed[k] = v.trim();
        }
      }
      const ref = await startDiscovery(parsed);
      setJobId(ref.job_id);
      setActiveJob("discovery", ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleJobDone = async () => {
    if (overrideOnce) {
      setOverrideOnce(false);
      setFolderOverrides({});
    }
    if (!jobId) return;
    await openResultWindow(
      `discovery-results-${jobId}`,
      "Pattern Discovery Results",
      { window: "discovery-results", jobId },
    );
  };

  const renderField = (p: ParamDef) => {
    const isFolder = p.type === "folder";
    if (isFolder) {
      const folderVal = folderOverrides[p.key] ?? "";
      const defaultVal = String(p.value ?? "");
      return (
        <div key={p.key} className="field">
          <label className="field-label">
            {p.label}
            <span className="field-hint-inline"> — {p.description}</span>
          </label>
          <div className="folder-display">{defaultVal || "—"}</div>
          {overrideOnce && (
            <input
              className="field-input"
              style={{ marginTop: 4 }}
              value={folderVal}
              onChange={(e) => setFolderVal(p.key, e.target.value)}
              placeholder="Override path (this run only)"
              disabled={isRunning}
            />
          )}
        </div>
      );
    }

    const def = trueDefault(p);
    const edited = isEdited(p.key);

    if (p.type === "bool") {
      // For booleans, the true default determines the unchecked-but-no-override state.
      const userVal = overrides[p.key];
      const checked = userVal !== undefined
        ? userVal === "true"
        : (def === "true" || def === "True" || Boolean(persistentDefaults[p.key] ?? p.value));
      return (
        <div key={p.key} className="field field-inline">
          <label className="toggle-label">
            <span className="toggle-wrap">
              <input
                type="checkbox"
                className="toggle-input"
                checked={checked}
                onChange={(e) => setValue(p.key, e.target.checked ? "true" : "false")}
                disabled={isRunning}
              />
              <span className="toggle-track" />
            </span>
            <span>
              <span className="field-label" style={{ display: "inline" }}>{p.label}</span>
              {p.description && <span className="field-hint"> — {p.description}</span>}
            </span>
            {edited && (
              <button
                type="button"
                className="pd-reset-btn"
                onClick={() => resetField(p.key)}
                title="Reset to default"
                style={{ marginLeft: 8 }}
              >↺</button>
            )}
          </label>
        </div>
      );
    }

    if (p.type === "str" && p.options && p.options.length > 0) {
      return (
        <div key={p.key} className="field">
          <label className="field-label">
            {p.label}
            <span className="field-default"> (default: {def})</span>
            {edited && (
              <button
                type="button"
                className="pd-reset-btn"
                onClick={() => resetField(p.key)}
                title="Reset to default"
                style={{ marginLeft: 8 }}
              >↺</button>
            )}
          </label>
          <select
            className="field-input"
            value={overrides[p.key] ?? def}
            onChange={(e) => setValue(p.key, e.target.value)}
            disabled={isRunning}
          >
            {p.options.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          {p.description && <span className="field-hint">{p.description}</span>}
        </div>
      );
    }

    const hint = [
      p.min != null ? `min ${p.min}` : "",
      p.max != null ? `max ${p.max}` : "",
      p.step != null ? `step ${p.step}` : "",
    ].filter(Boolean).join(", ");

    // Special-case the RANDOM_SEED field: pair the input with a 🎲 button
    // that fills in a fresh random seed in [1, 2^31-1]. Nothing else needs
    // a custom layout, so this is a narrow tweak to the standard renderer.
    const isSeed = p.key === "RANDOM_SEED";
    const randomize = () => {
      // 1..2^31-1 — wide enough to feel random, fits in a Python int signed range.
      const s = Math.floor(Math.random() * 2_147_483_646) + 1;
      setValue(p.key, String(s));
    };

    return (
      <div key={p.key} className="field">
        <label className="field-label">
          {p.label}
          {p.description && <ParamTooltip description={p.description} />}
          <span className="field-default"> (default: {def})</span>
          {edited && (
            <button
              type="button"
              className="pd-reset-btn"
              onClick={() => resetField(p.key)}
              title="Reset to default"
              style={{ marginLeft: 8 }}
            >↺</button>
          )}
        </label>
        <div className={isSeed ? "field-input-row" : undefined}>
          <input
            className="field-input"
            type="text"
            inputMode={p.type === "int" ? "numeric" : (p.type === "float" ? "decimal" : "text")}
            value={overrides[p.key] ?? ""}
            placeholder={def}
            onChange={(e) => setValue(p.key, e.target.value)}
            disabled={isRunning}
          />
          {isSeed && (
            <button
              type="button"
              className="seed-random-btn"
              onClick={randomize}
              disabled={isRunning}
              title="Generate a random seed"
            >
              🎲 Random
            </button>
          )}
        </div>
        {hint && (
          <span className="field-hint">
            {hint ? `(${hint})` : ""}
          </span>
        )}
      </div>
    );
  };

  return (
    <div className="tab-content" style={{ maxWidth: 860 }}>
      <div className="tab-header">
        <h2>Pattern Discovery</h2>
        <p className="tab-subtitle">
          Configure and run Pattern Discovery v6. All settings default to the values in
          <code> pattern_discovery_v6.py</code>; override only what you need.
        </p>
      </div>

      {currentImport && (
        <div className="form-section">
          <div className="current-import-banner">
            {currentImport.exists ? (
              <>
                <strong>Data source:</strong>{" "}
                {currentImport.symbol ?? "—"}
                {" · "}
                {tfFilesUserSet
                  ? <span>using your TF1/TF2/TF3 overrides</span>
                  : <span>auto-detected — {autoDetectedTfs || "no timeframes found"}</span>}
                <span className="field-hint" style={{ marginLeft: 8 }}>
                  ({currentImport.timeframes.length} file{currentImport.timeframes.length === 1 ? "" : "s"} in hist_data
                  {currentImport.modified_at ? ` · imported ${formatAgo(currentImport.modified_at)}` : ""})
                </span>
              </>
            ) : (
              <span style={{ color: "var(--text2)" }}>
                <strong>No data imported.</strong>{" "}
                Use the Data Import tab to fetch from MT5 first — discovery will fail without input data.
              </span>
            )}
          </div>
        </div>
      )}

      {params.length === 0 ? (
        <p className="tab-loading">Loading parameters…</p>
      ) : (
        <>
          {/* One-shot folder override toggle */}
          <div className="form-section">
            <label className="toggle-label">
              <span className="toggle-wrap">
                <input
                  type="checkbox"
                  className="toggle-input"
                  checked={overrideOnce}
                  onChange={(e) => {
                    setOverrideOnce(e.target.checked);
                    if (!e.target.checked) setFolderOverrides({});
                  }}
                  disabled={isRunning}
                />
                <span className="toggle-track" />
              </span>
              Override input/output folders for this run only (auto-resets after run)
            </label>
          </div>

          {/* Parameter groups */}
          {[...groups.entries()].map(([group, gParams]) => {
            const { core, advanced } = partitionGroup(gParams);
            const advOpen = effectiveShowAdvanced(group, advanced);
            return (
              <div key={group} className="param-group">
                <div className="param-group-header-row">
                  <button
                    className="param-group-header"
                    onClick={() => toggleGroup(group)}
                  >
                    <span className="param-group-arrow">{openGroups.has(group) ? "▾" : "▸"}</span>
                    {group}
                    <span className="param-group-count">{gParams.length} settings</span>
                  </button>
                  {groupHasEdits(group) && (
                    <button
                      type="button"
                      className="param-group-reset-btn"
                      onClick={() => resetGroup(group)}
                      title={`Reset all fields in "${group}" to their defaults`}
                      disabled={isRunning}
                    >↺</button>
                  )}
                </div>
                {openGroups.has(group) && (
                  <div className="param-group-body">
                    {core.length > 0 && (
                      <div className="override-grid">
                        {core.map((p) => renderField(p))}
                      </div>
                    )}
                    {advanced.length > 0 && (
                      <>
                        <button
                          type="button"
                          className="param-advanced-toggle"
                          onClick={() => toggleAdvanced(group)}
                          title={advOpen
                            ? "Hide advanced settings"
                            : "Show advanced settings for power users"}
                        >
                          <span className="param-group-arrow">{advOpen ? "▾" : "▸"}</span>
                          {advOpen ? "Hide advanced" : "Show advanced"}
                          <span className="param-group-count">{advanced.length}</span>
                        </button>
                        {advOpen && (
                          <div className="override-grid param-advanced-grid">
                            {advanced.map((p) => renderField(p))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}

      <div className="action-row" style={{ marginTop: 20 }}>
        <button
          className="btn btn-primary"
          onClick={handleStart}
          disabled={starting || isRunning || params.length === 0}
        >
          {starting ? "Starting…" : "▶ Run Discovery"}
        </button>
        <button
          className="btn btn-secondary"
          onClick={handleResetToDefaults}
          disabled={isRunning || params.length === 0}
          title="Reset all fields to your saved defaults"
        >
          ↺ Reset to defaults
        </button>
        {isDone && (
          <button className="btn btn-secondary" onClick={() => { setJobId(null); setError(null); }}>
            New Run
          </button>
        )}
        {jobId && job?.status === "done" && (
          <button className="btn btn-accent" onClick={handleJobDone}>↗ Open Results</button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <JobProgress jobId={jobId} onDone={handleJobDone} onError={(msg) => setError(msg)} />
    </div>
  );
}
