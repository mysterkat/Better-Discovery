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
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [overrideOnce, setOverrideOnce] = useState(false);
  const [folderOverrides, setFolderOverrides] = useState<Record<string, string>>({});
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["Data & Files", "General"]));
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const isRunning = !!jobId && job?.status === "running";
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed");

  useEffect(() => {
    getParams().then((loaded) => {
      setParams(loaded);
      // Pre-fill overrides from the persistent defaults store. We read via
      // getState() (not via a ref) so we pick up whatever has loaded by the
      // time /discovery/params resolves, even if the store finished loading
      // after this component mounted.
      const snap = useParamDefaults.getState().defaults;
      const initial: Record<string, string> = {};
      for (const p of loaded) {
        if (FOLDER_KEYS.has(p.key)) continue;
        if (p.key in snap && snap[p.key] != null) {
          initial[p.key] = String(snap[p.key]);
        }
      }
      setOverrides(initial);
    }).catch(() => {});
  }, []);

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

  const setValue = (key: string, val: string) =>
    setOverrides((prev) => ({ ...prev, [key]: val }));

  const setFolderVal = (key: string, val: string) =>
    setFolderOverrides((prev) => ({ ...prev, [key]: val }));

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const parsed: Record<string, unknown> = {};
      for (const p of params) {
        const raw = overrides[p.key]?.trim();
        if (!raw) continue;
        if (p.type === "bool") {
          parsed[p.key] = raw === "true" || raw === "1";
        } else if (p.type === "int") {
          const n = parseInt(raw, 10);
          if (!isNaN(n)) parsed[p.key] = n;
        } else if (p.type === "float") {
          const n = parseFloat(raw);
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

    if (p.type === "bool") {
      const val = overrides[p.key];
      const checked = val !== undefined ? val === "true" : Boolean(p.value);
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
          </label>
        </div>
      );
    }

    if (p.type === "str" && p.options && p.options.length > 0) {
      return (
        <div key={p.key} className="field">
          <label className="field-label">
            {p.label}
            <span className="field-default"> (default: {String(p.value)})</span>
          </label>
          <select
            className="field-input"
            value={overrides[p.key] ?? String(p.value ?? "")}
            onChange={(e) => setValue(p.key, e.target.value)}
            disabled={isRunning}
          >
            {p.options.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          {p.description && <span className="field-hint">{p.description}</span>}
        </div>
      );
    }

    const defVal = p.value != null ? String(p.value) : "";
    const hint = [
      p.min != null ? `min ${p.min}` : "",
      p.max != null ? `max ${p.max}` : "",
      p.step != null ? `step ${p.step}` : "",
    ].filter(Boolean).join(", ");

    return (
      <div key={p.key} className="field">
        <label className="field-label">
          {p.label}
          <span className="field-default"> (default: {defVal})</span>
        </label>
        <input
          className="field-input"
          type={p.type === "int" ? "number" : "text"}
          step={p.step ?? (p.type === "float" ? 0.01 : 1)}
          min={p.min}
          max={p.max}
          value={overrides[p.key] ?? ""}
          placeholder={defVal}
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
