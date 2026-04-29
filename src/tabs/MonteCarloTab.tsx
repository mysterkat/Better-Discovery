import { useState, useEffect, useMemo } from "react";
import { getMcParams, runAllPhases, type McRunAllRequest } from "../api/mc";
import { useJobs } from "../state/jobs";
import { useParamDefaults } from "../state/paramDefaults";
import JobProgress from "../components/JobProgress";
import type { ParamDef } from "../api/discovery";

type DataSource = "tradingview" | "mt5_html";
type ResultPhase = "phase1" | "phase2" | "funded" | "longterm";

const PHASE_LABELS: Record<ResultPhase, string> = {
  phase1: "Phase 1 — Challenge",
  phase2: "Phase 2 — Verification",
  funded: "Funded Account",
  longterm: "Long-term",
};

export default function MonteCarloTab() {
  const [params, setParams] = useState<ParamDef[]>([]);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [dataSource, setDataSource] = useState<DataSource>("tradingview");
  const [csvPath, setCsvPath] = useState("");
  const [htmlPath, setHtmlPath] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<string>>(
    new Set(["Simulation", "Phase 1"]),
  );
  const [jobId, setJobId] = useState<string | null>(null);
  const [allResults, setAllResults] = useState<Record<string, unknown> | null>(null);
  const [activeTab, setActiveTab] = useState<ResultPhase>("phase1");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // Subscribe to persistent defaults — these drive placeholders + labels
  // and act as the fallback at submit time when the user leaves a field empty.
  const persistentDefaults = useParamDefaults((s) => s.defaults);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const isRunning = !!jobId && job?.status === "running";
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed");

  useEffect(() => {
    getMcParams().then(setParams).catch(() => {});
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

  const filePath = dataSource === "tradingview" ? csvPath : htmlPath;
  const canRun = filePath.trim().length > 0;

  const handleRun = async () => {
    if (!canRun) {
      setError(`Please enter the ${dataSource === "mt5_html" ? "MT5 HTML report" : "TradingView CSV"} file path.`);
      return;
    }
    setStarting(true);
    setError(null);
    setJobId(null);
    setAllResults(null);
    try {
      const req: McRunAllRequest = {
        data_source: dataSource,
        ...(dataSource === "mt5_html"
          ? { file_path_html: htmlPath.trim() }
          : { pnl_csv_path: csvPath.trim() }),
        global_params: buildGroupParams("Simulation"),
        phase1_params: buildGroupParams("Phase 1"),
        phase2_params: buildGroupParams("Phase 2"),
        funded_params: buildGroupParams("Funded"),
        longterm_params: buildGroupParams("Long-term"),
      };
      const ref = await runAllPhases(req);
      setJobId(ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleJobDone = (result: unknown) => {
    setAllResults(result as Record<string, unknown>);
    setActiveTab("phase1");
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
          Simulations share pre-drawn random paths — runs once, results in four tabs.
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
        </div>

        {dataSource === "tradingview" ? (
          <div className="field" style={{ marginTop: 10 }}>
            <label className="field-label">TradingView CSV Path</label>
            <input className="field-input" value={csvPath}
              onChange={(e) => setCsvPath(e.target.value)}
              placeholder="C:\…\strategy_results.csv"
              disabled={isRunning} />
            <span className="field-hint">
              Export from TradingView Strategy Tester → List of Trades → download CSV.
              The file must include a Net P&amp;L column.
            </span>
          </div>
        ) : (
          <div className="field" style={{ marginTop: 10 }}>
            <label className="field-label">MT5 Strategy Tester HTML Report Path</label>
            <input className="field-input" value={htmlPath}
              onChange={(e) => setHtmlPath(e.target.value)}
              placeholder="C:\…\Report.html"
              disabled={isRunning} />
            <span className="field-hint">
              In MetaTrader 5 Strategy Tester: right-click results → Save as Report (.html).
              UTF-16 encoded — open in notepad to verify it has a Deals table.
            </span>
          </div>
        )}
      </div>

      {/* Settings accordion */}
      {params.length > 0 && (
        <div className="form-section">
          <div className="section-label">Simulation Settings</div>
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
        {isDone && (
          <button className="btn btn-secondary" onClick={() => { setJobId(null); setAllResults(null); setError(null); }}>
            New Run
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onDone={handleJobDone} onError={(msg) => setError(msg)} />

      {/* Results tabs */}
      {allResults && (
        <div className="mc-results" style={{ marginTop: 24 }}>
          <div className="phase-tabs">
            {(["phase1", "phase2", "funded", "longterm"] as ResultPhase[]).map((p) => (
              <button key={p}
                className={`phase-tab${activeTab === p ? " active" : ""}`}
                onClick={() => setActiveTab(p)}>
                {PHASE_LABELS[p]}
              </button>
            ))}
          </div>
          <div className="phase-tab-panel">
            <PhaseResults data={allResults[activeTab] as Record<string, unknown>} phase={activeTab} />
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  const display = typeof value === "number"
    ? (value > 100 ? value.toLocaleString(undefined, { maximumFractionDigits: 0 }) : value.toFixed(2))
    : value;
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{display}</span>
    </div>
  );
}

function PhaseResults({ data, phase }: { data: Record<string, unknown> | null | undefined; phase: ResultPhase }) {
  if (!data) return <p className="tab-loading">No results yet.</p>;

  const pct = (v: unknown) => `${Number(v ?? 0).toFixed(1)}%`;
  const days = (v: unknown) => `${Number(v ?? 0).toFixed(1)} days`;
  const dollar = (v: unknown) => `$${Number(v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

  if (phase === "phase1" || phase === "phase2") {
    return (
      <div>
        <div className="stat-row">
          <StatCard label="Pass Rate" value={pct(data.pass_rate)} />
          <StatCard label="Passed" value={Number(data.n_passed ?? 0)} />
          <StatCard label="Failed" value={Number(data.n_failed ?? 0)} />
        </div>
        <div className="stat-row" style={{ marginTop: 8 }}>
          <StatCard label="Avg Days" value={days(data.avg_days)} />
          <StatCard label="P10 Days" value={days(data.days_p10)} />
          <StatCard label="P50 Days" value={days(data.days_p50)} />
          <StatCard label="P90 Days" value={days(data.days_p90)} />
        </div>
        <div className="stat-row" style={{ marginTop: 8 }}>
          <StatCard label="Daily DD Breach" value={pct((data.fail_pcts as Record<string, unknown>)?.daily_dd ?? data.daily_dd_breach_pct)} />
          <StatCard label="Total DD Breach" value={pct((data.fail_pcts as Record<string, unknown>)?.total_dd ?? data.total_dd_breach_pct)} />
        </div>
      </div>
    );
  }

  if (phase === "funded") {
    return (
      <div>
        <div className="stat-row">
          <StatCard label="Breach Rate" value={pct(data.breach_rate)} />
          <StatCard label="Payout Rate" value={pct(data.payout_rate)} />
          <StatCard label="Avg Earnings" value={dollar(data.avg_total_earnings)} />
        </div>
        <div className="stat-row" style={{ marginTop: 8 }}>
          <StatCard label="Avg Payouts" value={Number(data.avg_payout_count ?? 0).toFixed(1)} />
          <StatCard label="First Payout" value={days(data.avg_first_payout_day)} />
          <StatCard label="Avg Days Active" value={days(data.avg_days_active)} />
        </div>
        {!!(data.breach_pcts as Record<string, unknown>) && (
          <div className="stat-row" style={{ marginTop: 8 }}>
            <StatCard label="Daily DD Breach" value={pct((data.breach_pcts as Record<string, unknown>).daily_dd)} />
            <StatCard label="Total DD Breach" value={pct((data.breach_pcts as Record<string, unknown>).total_dd)} />
          </div>
        )}
      </div>
    );
  }

  // longterm
  const bm = data.benchmark as Record<string, unknown> | null | undefined;
  return (
    <div>
      <div className="stat-row">
        <StatCard label="Survival Rate" value={pct((Number(data.pass_rate ?? 0)) * 100)} />
        <StatCard label="Median Final Equity" value={dollar(data.median_equity)} />
        <StatCard label="P10 Equity" value={dollar(data.p10_equity)} />
        <StatCard label="P90 Equity" value={dollar(data.p90_equity)} />
      </div>
      <div className="stat-row" style={{ marginTop: 8 }}>
        <StatCard label="Median Max DD" value={pct((Number(data.median_max_dd ?? 0)) * 100)} />
        <StatCard label="Median Sharpe" value={Number(data.median_sharpe ?? 0).toFixed(2)} />
        <StatCard label="Ann. Return" value={pct((Number(data.annualized_return ?? 0)) * 100)} />
      </div>
      {bm && !bm.error && (
        <div style={{ marginTop: 16 }}>
          <div className="section-label">Benchmark: {String(bm.ticker ?? "")}</div>
          <div className="stat-row">
            <StatCard label="Benchmark Ann. Return" value={pct((Number(bm.annualized_return ?? 0)) * 100)} />
            <StatCard label="Benchmark Sharpe" value={Number(bm.sharpe ?? 0).toFixed(2)} />
            <StatCard label="Benchmark Final Equity" value={dollar(bm.final_equity)} />
          </div>
        </div>
      )}
      {!!bm?.error && <p className="field-hint" style={{ color: "var(--warn, #e69500)" }}>Benchmark error: {String(bm.error)}</p>}
    </div>
  );
}
