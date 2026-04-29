import { useState, useEffect, useMemo } from "react";
import { getParams, startDiscovery, type ParamDef } from "../api/discovery";
import { useJobs } from "../state/jobs";
import { useParamDefaults } from "../state/paramDefaults";
import JobProgress from "../components/JobProgress";
import { openResultWindow } from "../lib/windows";

// Folder-type keys whose override is controlled by the one-shot toggle.
const FOLDER_KEYS = new Set(["DATA_FOLDER", "OUTPUT_FOLDER"]);

export default function DiscoveryTab() {
  const [params, setParams] = useState<ParamDef[]>([]);
  // `overrides` only contains user-typed values for THIS session.
  // A missing/empty entry means "use the true default" (persistent default
  // from the settings modal, falling back to the code-level default).
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [overrideOnce, setOverrideOnce] = useState(false);
  const [folderOverrides, setFolderOverrides] = useState<Record<string, string>>({});
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["Data & Files", "General"]));
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // Subscribe reactively to persistent defaults so changes made in the
  // settings modal propagate into placeholders + labels live.
  const persistentDefaults = useParamDefaults((s) => s.defaults);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const isRunning = !!jobId && job?.status === "running";
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed");

  useEffect(() => {
    getParams().then(setParams).catch(() => {});
  }, []);

  // The "true default" for a param: user's persistent default if set,
  // else the code-level default from pattern_discovery_v6.py.
  const trueDefault = (p: ParamDef): string => {
    const pd = persistentDefaults[p.key];
    if (pd != null) return String(pd);
    return p.value != null ? String(p.value) : "";
  };

  // Group params by their 'group' field.
  const groups = useMemo(() => {
    const map = new Map<string, ParamDef[]>();
    for (const p of params) {
      if (!map.has(p.group)) map.set(p.group, []);
      map.get(p.group)!.push(p);
    }
    return map;
  }, [params]);

  const toggleGroup = (g: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g); else next.add(g);
      return next;
    });

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
        <input
          className="field-input"
          type="text"
          inputMode={p.type === "int" ? "numeric" : (p.type === "float" ? "decimal" : "text")}
          value={overrides[p.key] ?? ""}
          placeholder={def}
          onChange={(e) => setValue(p.key, e.target.value)}
          disabled={isRunning}
        />
        {(p.description || hint) && (
          <span className="field-hint">
            {p.description}
            {hint ? ` (${hint})` : ""}
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
          {[...groups.entries()].map(([group, gParams]) => (
            <div key={group} className="param-group">
              <button
                className="param-group-header"
                onClick={() => toggleGroup(group)}
              >
                <span className="param-group-arrow">{openGroups.has(group) ? "▾" : "▸"}</span>
                {group}
                <span className="param-group-count">{gParams.length} settings</span>
              </button>
              {openGroups.has(group) && (
                <div className="param-group-body">
                  <div className="override-grid">
                    {gParams.map((p) => renderField(p))}
                  </div>
                </div>
              )}
            </div>
          ))}
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
