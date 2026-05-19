import { useState, useEffect, useCallback } from "react";
import {
  checkMt5,
  fetchMt5Data,
  getCurrentImport,
  getDefaultFolder,
  calcCandles,
  installMt5Helper,
  applyMt5Setup,
  type TfSpec,
  type Mt5CheckResult,
  type CurrentImport,
  type Mt5InstallResult,
  type Mt5ApplySetupResult,
} from "../api/data";
import { useJobs } from "../state/jobs";
import JobProgress from "../components/JobProgress";

const PREFIX_OPTIONS = [
  { value: "m", label: "m — Minute" },
  { value: "h", label: "h — Hour" },
  { value: "d", label: "d — Daily" },
  { value: "W", label: "W — Weekly" },
  { value: "M", label: "M — Monthly" },
];

const TF_TIME_OPTS: Record<string, number[]> = {
  m: [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30],
  h: [1, 2, 3, 4, 6, 8, 12],
  d: [1],
  W: [1],
  M: [1],
};

interface TfRow {
  prefix: "m" | "h" | "d" | "W" | "M";
  time_value: number;
  trading_days: string;
  candles: number | null;
}

function defaultTfRow(prefix: "m" | "h" | "d" | "W" | "M" = "m"): TfRow {
  return { prefix, time_value: 5, trading_days: "250", candles: null };
}

export default function DataImportTab() {
  const [mt5Status, setMt5Status] = useState<Mt5CheckResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [symbol, setSymbol] = useState("XAUUSD");
  const [nTf, setNTf] = useState(3);
  const [rows, setRows] = useState<TfRow[]>([
    defaultTfRow("m"),
    defaultTfRow("m"),
    defaultTfRow("h"),
  ]);
  const [defaultFolder, setDefaultFolder] = useState("");
  const [overrideOnce, setOverrideOnce] = useState(false);
  const [overrideFolder, setOverrideFolder] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobResult, setJobResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [currentImport, setCurrentImport] = useState<CurrentImport | null>(null);
  const [confirmReplace, setConfirmReplace] = useState(false);
  // ── v0.7.0: BD indicator stack install + chart auto-setup ─────────────────
  const [installState, setInstallState] = useState<Mt5InstallResult | null>(null);
  const [installing, setInstalling] = useState(false);
  const [setupState, setSetupState] = useState<Mt5ApplySetupResult | null>(null);
  const [applying, setApplying] = useState(false);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const setActiveJob = useJobs((s) => s.setActive);
  const isRunning = !!jobId && (job?.status === "running" || job?.status === "pending");

  const refreshCurrentImport = useCallback(() => {
    getCurrentImport().then(setCurrentImport).catch(() => {});
  }, []);

  useEffect(() => {
    getDefaultFolder().then(setDefaultFolder).catch(() => {});
    refreshCurrentImport();
    // Recover any in-flight MT5 fetch from a previous mount of this tab.
    const stored = useJobs.getState().activeByKind["mt5_fetch"];
    if (stored) {
      const existing = useJobs.getState().jobs[stored];
      if (!existing || (existing.status !== "done" && existing.status !== "failed" && existing.status !== "cancelled")) {
        setJobId(stored);
      }
    }
  }, [refreshCurrentImport]);

  // Sync row array length when nTf changes.
  useEffect(() => {
    setRows((prev) => {
      if (nTf > prev.length) {
        return [...prev, ...Array.from({ length: nTf - prev.length }, () => defaultTfRow())];
      }
      return prev.slice(0, nTf);
    });
  }, [nTf]);

  const handleCheck = async () => {
    setChecking(true);
    try {
      const r = await checkMt5();
      setMt5Status(r);
      // v0.7.0: when MT5 connection succeeds for the first time in this
      // session, kick off the BD indicator install in the background. The
      // copy itself is idempotent (mtime-based) so re-runs are cheap.
      if (r.ok && installState === null) {
        void handleInstall();
      }
    } catch (e) {
      setMt5Status({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setChecking(false);
    }
  };

  const handleInstall = async () => {
    setInstalling(true);
    try {
      const r = await installMt5Helper();
      setInstallState(r);
    } catch (e) {
      setInstallState({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setInstalling(false);
    }
  };

  const handleApplySetup = async () => {
    setApplying(true);
    try {
      const tfs = rows.map((r) => `${r.prefix.toUpperCase()}${r.time_value}`);
      const r = await applyMt5Setup({
        symbol,
        timeframes: tfs,
        wait_for_ack_s: 10.0,
      });
      setSetupState(r);
    } catch (e) {
      setSetupState({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setApplying(false);
    }
  };

  const updateRow = useCallback(
    async (idx: number, patch: Partial<TfRow>) => {
      setRows((prev) => {
        const next = [...prev];
        next[idx] = { ...next[idx], ...patch };
        return next;
      });
    },
    [],
  );

  const recalcCandles = useCallback(
    async (idx: number, row: TfRow) => {
      const days = parseInt(row.trading_days, 10);
      if (!isNaN(days) && days > 0) {
        try {
          const n = await calcCandles(row.prefix, row.time_value, days);
          setRows((prev) => {
            const next = [...prev];
            if (next[idx]) next[idx] = { ...next[idx], candles: n };
            return next;
          });
        } catch {
          // ignore — backend not ready
        }
      }
    },
    [],
  );

  const handlePrefixChange = (idx: number, prefix: TfRow["prefix"]) => {
    const opts = TF_TIME_OPTS[prefix];
    const time_value = opts[0];
    const row = { ...rows[idx], prefix, time_value };
    updateRow(idx, { prefix, time_value }).then(() => recalcCandles(idx, row));
  };

  const handleTimeChange = (idx: number, time_value: number) => {
    const row = { ...rows[idx], time_value };
    updateRow(idx, { time_value }).then(() => recalcCandles(idx, row));
  };

  const handleDaysChange = (idx: number, trading_days: string) => {
    updateRow(idx, { trading_days, candles: null });
  };

  const handleDaysBlur = (idx: number) => {
    recalcCandles(idx, rows[idx]);
  };

  const performFetch = async (clearExisting: boolean) => {
    const folder = overrideOnce && overrideFolder.trim()
      ? overrideFolder.trim()
      : defaultFolder;
    const tf_specs: TfSpec[] = rows.map((r) => ({
      prefix: r.prefix,
      time_value: r.time_value,
      trading_days: Math.max(1, parseInt(r.trading_days, 10) || 1),
    }));
    setStarting(true);
    setError(null);
    setJobId(null);
    setJobResult(null);
    try {
      const ref = await fetchMt5Data({
        symbol: symbol.trim() || "XAUUSD",
        save_folder: folder,
        tf_specs,
        clear_existing: clearExisting,
      });
      setJobId(ref.job_id);
      setActiveJob("mt5_fetch", ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleRun = async () => {
    if (!mt5Status?.ok) {
      setError("Connect to MT5 first.");
      return;
    }
    // Only the canonical hist_data folder is auto-cleared. If the user is
    // pointing at a custom folder via "override once", skip the confirmation
    // and don't touch their custom folder.
    const usingDefaultFolder = !(overrideOnce && overrideFolder.trim());
    if (usingDefaultFolder && currentImport?.exists) {
      setConfirmReplace(true);
      return;
    }
    void performFetch(false);
  };

  const handleConfirmReplace = () => {
    setConfirmReplace(false);
    void performFetch(true);
  };

  const handleCancelReplace = () => {
    setConfirmReplace(false);
  };

  const handleJobDone = (result: unknown) => {
    setJobResult(result);
    if (overrideOnce) {
      setOverrideOnce(false);
      setOverrideFolder("");
    }
    // Refresh the "current import" snapshot so subsequent fetches see the
    // new files for the confirmation logic and the discovery auto-detect.
    refreshCurrentImport();
  };

  const outputFolder = overrideOnce && overrideFolder.trim() ? overrideFolder.trim() : defaultFolder;
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed");

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Data Import</h2>
        <p className="tab-subtitle">
          Connect to MetaTrader 5 and pull historical candle data. Output CSVs land in the
          hist_data folder and are immediately available to Pattern Discovery.
        </p>
      </div>

      {/* MT5 Connection */}
      <div className="form-section">
        <div className="section-label">MetaTrader 5 Connection</div>
        <div className="action-row" style={{ marginTop: 6 }}>
          <button className="btn btn-secondary" onClick={handleCheck} disabled={checking}>
            {checking ? "Checking…" : "⟳ Test Connection"}
          </button>
          {mt5Status && (
            <span className={`status-badge ${mt5Status.ok ? "status-badge--ok" : "status-badge--err"}`}>
              {mt5Status.ok
                ? `● Connected — ${mt5Status.terminal}${mt5Status.account ? ` (${mt5Status.account})` : ""}`
                : `✗ ${mt5Status.error}`}
            </span>
          )}
        </div>
      </div>

      {/* v0.7.0: BD indicator stack + chart auto-setup */}
      {mt5Status?.ok && (
        <div className="form-section">
          <div className="section-label">BD Indicator Stack</div>
          <div className="action-row" style={{ marginTop: 6, flexWrap: "wrap", gap: 8 }}>
            <button
              className="btn btn-secondary"
              onClick={handleInstall}
              disabled={installing}
              title="Copy the 12 BD_*.mq5 indicators + BD_AutoSetup helper EA into the live MT5 install. Idempotent."
            >
              {installing ? "Installing…" : "⟳ Install / Update Indicators"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleApplySetup}
              disabled={
                applying ||
                !installState?.ok ||
                installState?.metaeditor !== "found" ||
                !symbol.trim()
              }
              title="Tell BD_AutoSetup (must be attached to a chart in MT5) to open charts for the symbol+timeframes above with the BD indicator stack."
            >
              {applying ? "Applying…" : "↻ Open Charts in MT5"}
            </button>
          </div>

          {installState && (
            <div style={{ marginTop: 8 }}>
              {!installState.ok && (
                <span className="status-badge status-badge--err">
                  ✗ Install failed — {installState.error}
                </span>
              )}
              {installState.ok && (
                <>
                  <span className="status-badge status-badge--ok">
                    ● Indicators installed
                    {installState.indicators &&
                      ` (${installState.indicators.copied.length} copied, ${installState.indicators.skipped.length} unchanged)`}
                  </span>
                  {installState.metaeditor === "missing" && (
                    <div className="field-hint" style={{ marginTop: 6, color: "var(--warn-text, #c08020)" }}>
                      ⚠️ MetaEditor not found next to terminal64.exe. Open MT5
                      → press F4 once so MetaEditor compiles the freshly
                      installed BD_*.mq5 sources, then return here.
                    </div>
                  )}
                  {installState.next_steps?.map((s, i) => (
                    <div key={i} className="field-hint" style={{ marginTop: 6 }}>
                      → {s}
                    </div>
                  ))}
                  {installState.compiled?.some((c) => !c.ok) && (
                    <div className="field-hint" style={{ marginTop: 6, color: "var(--err-text, #c04040)" }}>
                      ⚠️ {installState.compiled.filter((c) => !c.ok).map((c) => c.name).join(", ")} failed
                      to compile. Open MetaEditor and check the log.
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {setupState && (
            <div style={{ marginTop: 8 }}>
              {setupState.ok && setupState.acked ? (
                <span className="status-badge status-badge--ok">
                  ● {setupState.ack?.opened.length ?? 0} chart(s) opened in MT5
                </span>
              ) : (
                <span className="status-badge status-badge--err">
                  ✗ {setupState.error}
                </span>
              )}
              {setupState.ack?.errors && setupState.ack.errors.length > 0 && (
                <div className="field-hint" style={{ marginTop: 6, color: "var(--warn-text, #c08020)" }}>
                  ⚠️ Helper EA reported {setupState.ack.errors.length} issue(s):
                  <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                    {setupState.ack.errors.map((e, i) => (
                      <li key={i} style={{ listStyle: "disc" }}>{e}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Symbol */}
      <div className="form-section">
        <div className="field">
          <label className="field-label">Symbol</label>
          <input
            className="field-input field-sm"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="XAUUSD"
            disabled={isRunning}
          />
          <span className="field-hint">MT5 symbol name — must match exactly as it appears in Market Watch.</span>
        </div>
      </div>

      {/* Number of timeframes */}
      <div className="form-section">
        <div className="field">
          <label className="field-label">Number of Timeframes</label>
          <input
            className="field-input field-sm"
            type="number"
            min={1}
            max={10}
            value={nTf}
            onChange={(e) => setNTf(Math.max(1, Math.min(10, parseInt(e.target.value, 10) || 1)))}
            disabled={isRunning}
          />
        </div>

        {/* TF rows */}
        <div className="tf-rows">
          {rows.map((row, idx) => (
            <div key={idx} className="tf-row">
              <span className="tf-row-label">TF {idx + 1}</span>

              {/* Prefix */}
              <select
                className="field-input tf-select"
                value={row.prefix}
                onChange={(e) => handlePrefixChange(idx, e.target.value as TfRow["prefix"])}
                disabled={isRunning}
              >
                {PREFIX_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>

              {/* Time value */}
              <select
                className="field-input tf-select"
                value={row.time_value}
                onChange={(e) => handleTimeChange(idx, parseInt(e.target.value, 10))}
                disabled={isRunning || row.prefix === "d" || row.prefix === "W" || row.prefix === "M"}
              >
                {(TF_TIME_OPTS[row.prefix] || [1]).map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>

              {/* Trading days */}
              <div className="tf-days-wrap">
                <input
                  className="field-input tf-days-input"
                  type="number"
                  min={1}
                  value={row.trading_days}
                  onChange={(e) => handleDaysChange(idx, e.target.value)}
                  onBlur={() => handleDaysBlur(idx)}
                  placeholder="days"
                  disabled={isRunning}
                />
                <span className="tf-days-label">trading days</span>
              </div>

              {/* Candle estimate */}
              <span className="tf-candles">
                {row.candles != null ? `≈ ${row.candles.toLocaleString()} candles` : "—"}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Output folder */}
      <div className="form-section">
        <div className="section-label">Output Folder</div>
        <div className="folder-display">{outputFolder || "Loading…"}</div>
        <div className="action-row" style={{ marginTop: 8 }}>
          <label className="toggle-label">
            <span className="toggle-wrap">
              <input
                type="checkbox"
                className="toggle-input"
                checked={overrideOnce}
                onChange={(e) => setOverrideOnce(e.target.checked)}
                disabled={isRunning}
              />
              <span className="toggle-track" />
            </span>
            Override once (resets after run)
          </label>
        </div>
        {overrideOnce && (
          <div className="field" style={{ marginTop: 8 }}>
            <input
              className="field-input"
              value={overrideFolder}
              onChange={(e) => setOverrideFolder(e.target.value)}
              placeholder="C:\path\to\folder"
              disabled={isRunning}
            />
          </div>
        )}
      </div>

      {/* Current import status */}
      {currentImport && currentImport.exists && (
        <div className="form-section">
          <div className="section-label">Currently Imported</div>
          <div className="current-import-banner">
            <strong>{currentImport.symbol ?? "—"}</strong>
            {" · "}
            {currentImport.timeframes.map((tf) => tf.label).join(", ")}
            <span className="field-hint" style={{ marginLeft: 8 }}>
              ({currentImport.timeframes.length} file{currentImport.timeframes.length === 1 ? "" : "s"})
            </span>
          </div>
        </div>
      )}

      {/* Run */}
      <div className="action-row">
        <button
          className="btn btn-primary"
          onClick={handleRun}
          disabled={starting || isRunning || !mt5Status?.ok}
        >
          {starting ? "Starting…" : "↓ Fetch Data from MT5"}
        </button>
        {isDone && (
          <button className="btn btn-secondary" onClick={() => { setJobId(null); setJobResult(null); setError(null); }}>
            New Run
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <JobProgress
        jobId={jobId}
        onDone={handleJobDone}
        onError={(msg) => setError(msg)}
      />

      {/* Results */}
      {jobResult != null && (() => {
        const r = jobResult as {
          ok: boolean; terminal?: string; save_folder?: string;
          files?: { label: string; ok: boolean; candles: number; path: string; error: string | null }[];
        };
        return (
          <div className="result-section" style={{ marginTop: 16 }}>
            <div className="section-label">Download Results</div>
            <div className="stat-row">
              <div className="stat-card">
                <span className="stat-label">Terminal</span>
                <span className="stat-value" style={{ fontSize: 12 }}>{r.terminal ?? "—"}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Saved to</span>
                <span className="stat-value" style={{ fontSize: 12 }}>{r.save_folder ?? "—"}</span>
              </div>
            </div>
            <div className="tf-result-list">
              {(r.files ?? []).map((f, i) => (
                <div key={i} className={`tf-result-row ${f.ok ? "tf-result-ok" : "tf-result-err"}`}>
                  <span className="tf-result-status">{f.ok ? "✓" : "✗"}</span>
                  <span className="tf-result-label">{f.label.toUpperCase()}</span>
                  {f.ok ? (
                    <>
                      <span className="tf-result-candles">{f.candles.toLocaleString()} candles</span>
                      <span className="tf-result-path">{f.path}</span>
                    </>
                  ) : (
                    <span className="tf-result-error">{f.error}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Replace-existing confirmation */}
      {confirmReplace && currentImport && (
        <div className="confirm-modal-backdrop" onClick={handleCancelReplace}>
          <div className="confirm-modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="confirm-modal-title">Replace existing import?</h3>
            <p className="confirm-modal-body">
              The hist_data folder currently contains an import of{" "}
              <strong>{currentImport.symbol ?? "—"}</strong>
              {" "}({currentImport.timeframes.length} file
              {currentImport.timeframes.length === 1 ? "" : "s"}).
              Continuing will <strong>delete it</strong> before fetching{" "}
              <strong>{symbol.trim() || "XAUUSD"}</strong> ({rows.length} timeframe
              {rows.length === 1 ? "" : "s"}).
            </p>
            <ul className="confirm-modal-list">
              {currentImport.timeframes.map((tf) => (
                <li key={tf.filename}>
                  <code>{tf.filename}</code>
                </li>
              ))}
            </ul>
            <div className="confirm-modal-actions">
              <button className="btn btn-secondary" onClick={handleCancelReplace}>
                Cancel
              </button>
              <button className="btn btn-danger" onClick={handleConfirmReplace}>
                Yes, delete and fetch
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
