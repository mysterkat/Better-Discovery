import { useState, useEffect, useMemo, useRef } from "react";
import { getMcParams, runAllPhases, type McRunAllRequest } from "../api/mc";
import { useJobs } from "../state/jobs";
import { useParamDefaults } from "../state/paramDefaults";
import { useMcRuns } from "../state/mcRuns";
import JobProgress from "../components/JobProgress";
import FileDropZone from "../components/FileDropZone";
import RunHistory from "../components/RunHistory";
import { openResultWindow } from "../lib/windows";
import { PROP_FIRM_PRESETS, findPreset } from "../data/propFirmPresets";
import type { ParamDef } from "../api/discovery";

// Pseudo-keys (not in MC_PARAM_META) that ride along on global_params so the
// dashboard verdict block can compute fee-aware ROI.
const CHALLENGE_FEE_KEY = "CHALLENGE_FEE";
const FEE_REFUND_KEY = "FEE_REFUNDED_ON_FIRST_PAYOUT";

type DataSource = "tradingview" | "mt5_html" | "local_ledger";

export default function MonteCarloTab() {
  const [params, setParams] = useState<ParamDef[]>([]);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [dataSource, setDataSource] = useState<DataSource>("mt5_html");
  const [csvPath, setCsvPath] = useState("");
  const [htmlPath, setHtmlPath] = useState("");
  const [ledgerPath, setLedgerPath] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<string>>(
    new Set(["Simulation", "Phase 1"]),
  );
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [presetId, setPresetId] = useState<string>("custom");
  const [savePromptOpen, setSavePromptOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saved, setSaved] = useState(false);

  const saveRun = useMcRuns((s) => s.save);

  // Subscribe to persistent defaults — these drive placeholders + labels
  // and act as the fallback at submit time when the user leaves a field empty.
  const persistentDefaults = useParamDefaults((s) => s.defaults);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const setActiveJob = useJobs((s) => s.setActive);
  const isRunning = !!jobId && (job?.status === "running" || job?.status === "pending");
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed" || job?.status === "cancelled");

  // Track which jobIds we've already auto-opened a dashboard window for so a
  // re-render doesn't pop a duplicate window.
  const openedFor = useRef<Set<string>>(new Set());

  useEffect(() => {
    getMcParams().then(setParams).catch(() => {});
    // Recover any in-flight MC job from a previous mount of this tab.
    const stored = useJobs.getState().activeByKind["mc_all"];
    if (stored) {
      const existing = useJobs.getState().jobs[stored];
      if (!existing || (existing.status !== "done" && existing.status !== "failed" && existing.status !== "cancelled")) {
        setJobId(stored);
      }
    }
  }, []);

  const trueDefault = (p: ParamDef): string => {
    const pd = persistentDefaults[p.key];
    if (pd != null) return String(pd);
    return p.value != null ? String(p.value) : "";
  };

  const isEdited = (key: string): boolean => {
    const v = overrides[key];
    return v != null && v.trim() !== "";
  };

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
    setPresetId("custom");
  };

  const applyPreset = (id: string) => {
    setPresetId(id);
    if (id === "custom") return;
    const preset = findPreset(id);
    if (!preset) return;
    setOverrides((prev) => {
      const next = { ...prev };
      for (const [k, v] of Object.entries(preset.overrides)) {
        next[k] = String(v);
      }
      // Pseudo-fields surfaced to the backend via global_params at submit time.
      next[CHALLENGE_FEE_KEY] = String(preset.challengeFee);
      next[FEE_REFUND_KEY] = preset.feeRefundedOnFirstPayout ? "true" : "false";
      return next;
    });
  };

  const coerce = (p: ParamDef, raw: string): unknown => {
    if (raw === "") return undefined;
    if (p.type === "bool") return raw === "true" || raw === "1";
    // Accept European decimal comma for numeric inputs.
    const normalized = raw.replace(",", ".");
    if (p.type === "int") { const n = parseInt(normalized, 10); return isNaN(n) ? undefined : n; }
    if (p.type === "float") { const n = parseFloat(normalized); return isNaN(n) ? undefined : n; }
    return raw;
  };

  const buildGroupParams = (groupName: string): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const p of (groups.get(groupName) ?? [])) {
      // Resolution order: user-typed (overrides) > persistent default.
      // If neither is set, omit — backend uses the code-level default.
      const userVal = overrides[p.key]?.trim() ?? "";
      let raw = userVal;
      if (!raw) {
        const pd = persistentDefaults[p.key];
        if (pd != null) raw = String(pd);
      }
      const val = coerce(p, raw);
      if (val !== undefined) out[p.key.toLowerCase()] = val;
    }
    return out;
  };

  const filePath = dataSource === "tradingview" ? csvPath : dataSource === "mt5_html" ? htmlPath : ledgerPath;
  const canRun = filePath.trim().length > 0;

  // When the job transitions to "done", auto-open the dashboard window once.
  useEffect(() => {
    if (!jobId || job?.status !== "done") return;
    if (openedFor.current.has(jobId)) return;
    openedFor.current.add(jobId);
    openResultWindow(`mc-dashboard-${jobId.slice(0, 8)}`,
      "Monte Carlo Dashboard",
      { window: "mc-dashboard", jobId });
  }, [jobId, job?.status]);

  const handleRun = async () => {
    if (!canRun) {
      setError(`Please select the ${dataSource === "mt5_html" ? "MT5 HTML report" : dataSource === "local_ledger" ? "local replay ledger" : "TradingView CSV"}.`);
      return;
    }
    setStarting(true);
    setError(null);
    setJobId(null);
    setSaved(false);
    setSavePromptOpen(false);
    try {
      const globalParams = buildGroupParams("Simulation");
      // Surface preset metadata (fee + refund policy + preset_id) to the
      // backend so the dashboard's verdict block can render fee-aware ROI.
      const fee = overrides[CHALLENGE_FEE_KEY];
      const refund = overrides[FEE_REFUND_KEY];
      if (fee != null && fee !== "") {
        const n = parseFloat(fee.replace(",", "."));
        if (!isNaN(n)) globalParams["challenge_fee"] = n;
      }
      if (refund != null && refund !== "") {
        globalParams["fee_refunded_on_first_payout"] = refund === "true";
      }
      if (presetId !== "custom") {
        globalParams["preset_id"] = presetId;
      }
      const req: McRunAllRequest = {
        data_source: dataSource,
        ...(dataSource === "mt5_html"
          ? { file_path_html: htmlPath.trim() }
          : dataSource === "local_ledger"
            ? { local_ledger_path: ledgerPath.trim() }
            : { pnl_csv_path: csvPath.trim() }),
        global_params: globalParams,
        phase1_params: buildGroupParams("Phase 1"),
        phase2_params: buildGroupParams("Phase 2"),
        funded_params: buildGroupParams("Funded"),
        longterm_params: buildGroupParams("Long-term"),
      };
      const ref = await runAllPhases(req);
      setJobId(ref.job_id);
      setActiveJob("mc_all", ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const reopenDashboard = () => {
    if (!jobId) return;
    openResultWindow(`mc-dashboard-${jobId.slice(0, 8)}`,
      "Monte Carlo Dashboard",
      { window: "mc-dashboard", jobId });
  };

  const openSavePrompt = () => {
    const preset = presetId !== "custom" ? findPreset(presetId) : null;
    const stamp = new Date().toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    setSaveName(preset ? `${preset.name} — ${stamp}` : `MC Run — ${stamp}`);
    setSavePromptOpen(true);
  };

  const confirmSaveRun = async () => {
    if (!jobId) return;
    const name = saveName.trim() || "Untitled run";
    await saveRun(jobId, name);
    setSaved(true);
    setSavePromptOpen(false);
  };

  const renderField = (p: ParamDef) => {
    const def = trueDefault(p);
    const edited = isEdited(p.key);
    const resetBtn = edited ? (
      <button
        type="button"
        className="pd-reset-btn"
        onClick={() => resetField(p.key)}
        title="Reset to default"
        style={{ marginLeft: 8 }}
      >↺</button>
    ) : null;

    if (p.type === "bool") {
      const userVal = overrides[p.key];
      const checked = userVal !== undefined
        ? userVal === "true"
        : Boolean(persistentDefaults[p.key] ?? p.value);
      return (
        <div key={p.key} className="field field-inline">
          <label className="toggle-label">
            <span className="toggle-wrap">
              <input type="checkbox" className="toggle-input" checked={checked}
                onChange={(e) => setValue(p.key, e.target.checked ? "true" : "false")}
                disabled={isRunning} />
              <span className="toggle-track" />
            </span>
            <span className="field-label" style={{ display: "inline" }}>{p.label}</span>
            {p.description && <span className="field-hint"> — {p.description}</span>}
            {resetBtn}
          </label>
        </div>
      );
    }
    if (p.type === "str" && p.options?.length) {
      return (
        <div key={p.key} className="field">
          <label className="field-label">{p.label}<span className="field-default"> (default: {def})</span>{resetBtn}</label>
          <select className="field-input"
            value={overrides[p.key] ?? def}
            onChange={(e) => setValue(p.key, e.target.value)} disabled={isRunning}>
            {p.options.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </div>
      );
    }
    return (
      <div key={p.key} className="field">
        <label className="field-label">{p.label}<span className="field-default"> (default: {def})</span>{resetBtn}</label>
        <input className="field-input" type="text"
          inputMode={p.type === "int" ? "numeric" : (p.type === "float" ? "decimal" : "text")}
          value={overrides[p.key] ?? ""} placeholder={def}
          onChange={(e) => setValue(p.key, e.target.value)} disabled={isRunning} />
        {p.description && <span className="field-hint">{p.description}{p.min != null ? ` (${p.min}–${p.max ?? "∞"})` : ""}</span>}
      </div>
    );
  };

  return (
    <div className="tab-content" style={{ maxWidth: 860 }}>
      <div className="tab-header">
        <h2>Monte Carlo</h2>
        <p className="tab-subtitle">
          Configure all four simulation phases and run them together in one job.
          Results open in a separate dashboard window with full charts.
        </p>
      </div>

      {/* Data source */}
      <div className="form-section">
        <div className="section-label">Data Source</div>
        <div className="mc-source-row">
          <button
            className={`mc-source-btn${dataSource === "tradingview" ? " active" : ""}`}
            onClick={() => setDataSource("tradingview")}
            disabled={isRunning}>
            📊 TradingView CSV
          </button>
          <button
            className={`mc-source-btn${dataSource === "mt5_html" ? " active" : ""}`}
            onClick={() => setDataSource("mt5_html")}
            disabled={isRunning}>
            🖥 MT5 HTML Report
          </button>
          <button
            className={`mc-source-btn${dataSource === "local_ledger" ? " active" : ""}`}
            onClick={() => setDataSource("local_ledger")}
            disabled={isRunning}>
            Local Replay Ledger
          </button>
        </div>

        {dataSource === "tradingview" ? (
          <FileDropZone
            label="TradingView CSV"
            value={csvPath}
            onChange={setCsvPath}
            accept=".csv"
            disabled={isRunning}
            hint="Export from TradingView Strategy Tester → List of Trades → Download CSV. Must include a Net P&L column."
          />
        ) : dataSource === "mt5_html" ? (
          <FileDropZone
            label="MT5 Strategy Tester HTML Report"
            value={htmlPath}
            onChange={setHtmlPath}
            accept=".html"
            disabled={isRunning}
            hint="In MT5 Strategy Tester: right-click results → Save as Report (.html). UTF-16 encoded — must contain a Deals table."
          />
        ) : (
          <FileDropZone
            label="Local Replay Trade Ledger"
            value={ledgerPath}
            onChange={setLedgerPath}
            accept=".csv,.parquet"
            disabled={isRunning}
            hint="Use closed_trades.csv or closed_trades.parquet exported by Research Lab."
          />
        )}
      </div>

      {/* Settings accordion */}
      {params.length > 0 && (
        <div className="form-section">
          <div className="section-label">Simulation Settings</div>

          {/* Prop firm preset selector */}
          <div className="mc-preset-row">
            <label className="mc-preset-label" htmlFor="mc-preset-select">
              Prop Firm:
            </label>
            <select
              id="mc-preset-select"
              className="field-input mc-preset-select"
              value={presetId}
              onChange={(e) => applyPreset(e.target.value)}
              disabled={isRunning}
              title={
                presetId !== "custom"
                  ? findPreset(presetId)?.description ?? ""
                  : "Use your own values for every field."
              }
            >
              <option value="custom">Custom (no preset)</option>
              {PROP_FIRM_PRESETS.map((p) => (
                <option key={p.id} value={p.id} title={p.description}>
                  {p.name}
                </option>
              ))}
            </select>
            {presetId !== "custom" && (
              <span className="mc-preset-hint">
                {findPreset(presetId)?.description}
              </span>
            )}
          </div>

          {[...groups.entries()].map(([group, gParams]) => (
            <div key={group} className="param-group">
              <button className="param-group-header" onClick={() => toggleGroup(group)}>
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
        </div>
      )}

      {params.length === 0 && (
        <p className="tab-loading" style={{ marginBottom: 16 }}>Loading simulation settings…</p>
      )}

      <div className="action-row">
        <button className="btn btn-primary" onClick={handleRun}
          disabled={starting || isRunning || !canRun}>
          {starting ? "Starting…" : "▶ Run All Phases"}
        </button>
        <button
          className="btn btn-secondary"
          onClick={handleResetToDefaults}
          disabled={isRunning || params.length === 0}
          title="Reset all fields to your saved defaults"
        >
          ↺ Reset to defaults
        </button>
        {isDone && job?.status === "done" && (
          <>
            <button className="btn btn-secondary" onClick={reopenDashboard}>
              Reopen Dashboard
            </button>
            <button className="btn btn-secondary" onClick={() => { setJobId(null); setError(null); }}>
              New Run
            </button>
          </>
        )}
        {isDone && job?.status !== "done" && (
          <button className="btn btn-secondary" onClick={() => { setJobId(null); setError(null); }}>
            New Run
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onError={(msg) => setError(msg)} />

      {job?.status === "done" && (
        <div className="mc-done-banner">
          ✔ Run complete — dashboard opened in a new window.
          {" "}<button className="link-btn" onClick={reopenDashboard}>Reopen</button>
        </div>
      )}
    </div>
  );
}
