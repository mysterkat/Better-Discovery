import { useState, useEffect, useCallback } from "react";
import {
  checkMt5,
  fetchMt5Data,
  getDefaultFolder,
  calcCandles,
  type TfSpec,
  type Mt5CheckResult,
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

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const isRunning = !!jobId && job?.status === "running";

  useEffect(() => {
    getDefaultFolder().then(setDefaultFolder).catch(() => {});
  }, []);

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
    } catch (e) {
      setMt5Status({ ok: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setChecking(false);
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

  const handleRun = async () => {
    if (!mt5Status?.ok) {
      setError("Connect to MT5 first.");
      return;
    }
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
      const ref = await fetchMt5Data({ symbol: symbol.trim() || "XAUUSD", save_folder: folder, tf_specs });
      setJobId(ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleJobDone = (result: unknown) => {
    setJobResult(result);
    if (overrideOnce) {
      setOverrideOnce(false);
      setOverrideFolder("");
    }
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
    </div>
  );
}
