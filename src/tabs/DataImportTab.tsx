import { useEffect, useMemo, useState } from "react";
import {
  checkMt5,
  clearCurrentImport,
  deleteMarketDataset,
  fetchMt5Data,
  fetchMt5DataMany,
  fetchProviderData,
  getCurrentImport,
  getDefaultFolder,
  getMarketDataProviders,
  listMarketDatasets,
  type CurrentImport,
  type MarketDataProvider,
  type MarketDataset,
  type Mt5CheckResult,
  type TfSpec,
} from "../api/data";
import JobProgress from "../components/JobProgress";
import { useJobs } from "../state/jobs";

type DataSource = "mt5" | "dukascopy";

const TIMEFRAMES = ["m1", "m5", "m10", "m15", "m30", "h1", "h4", "d1"];
const MT5_TIMEFRAMES = ["m1", "m5", "m15", "m30", "h1", "h4", "d1"];

function dateInput(daysAgo: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

function timeframeSpec(timeframe: string, tradingDays: number): TfSpec {
  const match = timeframe.match(/^([a-zA-Z]+)(\d+)$/);
  if (!match) throw new Error(`Unsupported timeframe: ${timeframe}`);
  const rawPrefix = match[1];
  const value = Number(match[2]);
  if (!Number.isFinite(value) || value <= 0) throw new Error(`Unsupported timeframe: ${timeframe}`);
  const prefix = rawPrefix.toLowerCase() === "d" ? "d" : rawPrefix.toLowerCase();
  if (!["m", "h", "d"].includes(prefix)) throw new Error(`Unsupported timeframe: ${timeframe}`);
  return { prefix: prefix as TfSpec["prefix"], time_value: value, trading_days: tradingDays };
}

function datasetQualityLabel(dataset: MarketDataset): string {
  if (dataset.state === "failed") return dataset.error ? "Failed" : "Failed";
  const quality = dataset.quality ?? {};
  if (quality.passed === false) return "Issues";
  const symbols = quality.symbols;
  if (symbols && typeof symbols === "object") {
    let issueCount = 0;
    for (const symbolQuality of Object.values(symbols as Record<string, unknown>)) {
      if (!symbolQuality || typeof symbolQuality !== "object") continue;
      for (const timeframeQuality of Object.values(symbolQuality as Record<string, unknown>)) {
        if (!timeframeQuality || typeof timeframeQuality !== "object") continue;
        const audit = timeframeQuality as { passed?: boolean; issues?: unknown[] };
        if (audit.passed === false) issueCount += Array.isArray(audit.issues) ? audit.issues.length || 1 : 1;
      }
    }
    if (issueCount > 0) return `${issueCount} issue${issueCount === 1 ? "" : "s"}`;
  }
  return dataset.state === "complete" ? "Clean" : "-";
}

export default function DataImportTab() {
  const [providers, setProviders] = useState<MarketDataProvider[]>([]);
  const [datasets, setDatasets] = useState<MarketDataset[]>([]);
  const [source, setSource] = useState<DataSource>("mt5");
  const [provider, setProvider] = useState<"dukascopy">("dukascopy");
  const [symbols, setSymbols] = useState("XAUUSD");
  const [timeframes, setTimeframes] = useState<string[]>(["m1", "m5", "m10", "m15", "h1"]);
  const [dateFrom, setDateFrom] = useState(dateInput(30));
  const [dateTo, setDateTo] = useState(dateInput(0));
  const [includeTicks, setIncludeTicks] = useState(true);
  const [publishDiscovery, setPublishDiscovery] = useState(true);
  const [priceDigits, setPriceDigits] = useState("XAUUSD:3");
  const [mt5Symbols, setMt5Symbols] = useState("XAUUSD");
  const [mt5Timeframes, setMt5Timeframes] = useState<string[]>(["m5", "m15", "h1", "h4"]);
  const [mt5TradingDays, setMt5TradingDays] = useState("2000");
  const [mt5SaveFolder, setMt5SaveFolder] = useState("");
  const [mt5ClearExisting, setMt5ClearExisting] = useState(false);
  const [mt5Status, setMt5Status] = useState<Mt5CheckResult | null>(null);
  const [checkingMt5, setCheckingMt5] = useState(false);
  const [currentImport, setCurrentImport] = useState<CurrentImport | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [clearingCurrentImport, setClearingCurrentImport] = useState(false);
  const [deletingDatasetId, setDeletingDatasetId] = useState<string | null>(null);
  const setActiveJob = useJobs((state) => state.setActive);
  const job = useJobs((state) => jobId ? state.jobs[jobId] : undefined);
  const running = starting || job?.status === "pending" || job?.status === "running";

  const refresh = () => {
    listMarketDatasets().then(setDatasets).catch(() => undefined);
    getCurrentImport().then(setCurrentImport).catch(() => undefined);
  };
  useEffect(() => {
    getMarketDataProviders().then(setProviders).catch((reason: unknown) => setError(String(reason)));
    getDefaultFolder().then(setMt5SaveFolder).catch(() => undefined);
    getCurrentImport().then(setCurrentImport).catch(() => undefined);
    refresh();
    const active = useJobs.getState().activeByKind.mt5_fetch ?? useJobs.getState().activeByKind.market_data_fetch;
    if (active) setJobId(active);
  }, []);

  const parsedSymbols = useMemo(
    () => symbols.split(/[\s,]+/).map((value) => value.trim().toUpperCase()).filter(Boolean),
    [symbols],
  );
  const parsedMt5Symbols = useMemo(
    () => mt5Symbols.split(/[\s,]+/).map((value) => value.trim().toUpperCase()).filter(Boolean),
    [mt5Symbols],
  );

  const toggleTimeframe = (timeframe: string) => {
    setTimeframes((current) => current.includes(timeframe)
      ? current.filter((value) => value !== timeframe)
      : [...current, timeframe]);
  };

  const toggleMt5Timeframe = (timeframe: string) => {
    setMt5Timeframes((current) => current.includes(timeframe)
      ? current.filter((value) => value !== timeframe)
      : [...current, timeframe]);
  };

  const runMt5 = async () => {
    if (!parsedMt5Symbols.length || !mt5Timeframes.length) {
      setError("Select at least one MT5 symbol and timeframe.");
      return;
    }
    const tradingDays = Math.trunc(Number(mt5TradingDays));
    if (!Number.isFinite(tradingDays) || tradingDays <= 0) {
      setError("MT5 history days must be a positive number.");
      return;
    }
    if (mt5ClearExisting && currentImport?.exists) {
      const ok = window.confirm("Replace existing MT5 hist_data CSVs before importing?");
      if (!ok) return;
    }
    setStarting(true);
    setError(null);
    try {
      const tf_specs = mt5Timeframes.map((timeframe) => timeframeSpec(timeframe, tradingDays));
      const request = {
        save_folder: mt5SaveFolder,
        tf_specs,
        clear_existing: mt5ClearExisting,
      };
      const result = parsedMt5Symbols.length === 1
        ? await fetchMt5Data({ ...request, symbol: parsedMt5Symbols[0] })
        : await fetchMt5DataMany({ ...request, symbols: parsedMt5Symbols });
      setJobId(result.job_id);
      setActiveJob("mt5_fetch", result.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setStarting(false);
    }
  };

  const testMt5 = async () => {
    setCheckingMt5(true);
    setError(null);
    try {
      const result = await checkMt5();
      setMt5Status(result);
      if (!result.ok) setError(result.error ?? "MT5 connection failed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCheckingMt5(false);
    }
  };

  const clearMt5Import = async () => {
    if (!currentImport?.exists) return;
    const filenames = currentImport.timeframes.map((timeframe) => timeframe.filename).join(", ");
    const ok = window.confirm(`Delete current MT5 hist_data CSVs?\n\n${filenames}`);
    if (!ok) return;
    setClearingCurrentImport(true);
    setError(null);
    try {
      await clearCurrentImport();
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setClearingCurrentImport(false);
    }
  };

  const runDukascopy = async () => {
    if (!parsedSymbols.length || !timeframes.length) {
      setError("Select at least one symbol and timeframe.");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const result = await fetchProviderData({
        provider,
        symbols: parsedSymbols,
        timeframes,
        date_from: `${dateFrom}T00:00:00Z`,
        date_to: `${dateTo}T23:59:59Z`,
        include_ticks: includeTicks,
        write_discovery_csv: publishDiscovery,
        price_digits: Object.fromEntries(priceDigits.split(/[\s,]+/).map((part) => part.split(":"))
          .filter((pair) => pair.length === 2 && Number.isFinite(Number(pair[1])))
          .map(([name, digits]) => [name.toUpperCase(), Number(digits)])),
      });
      setJobId(result.job_id);
      setActiveJob("market_data_fetch", result.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setStarting(false);
    }
  };

  const resume = async (dataset: MarketDataset) => {
    setStarting(true);
    setError(null);
    try {
      const result = await fetchProviderData({
        provider: "dukascopy",
        symbols: dataset.symbols,
        timeframes: dataset.timeframes,
        date_from: dataset.requested_from,
        date_to: dataset.requested_to,
        include_ticks: dataset.import_options?.include_ticks ?? true,
        write_discovery_csv: dataset.import_options?.write_discovery_csv ?? true,
        price_digits: dataset.import_options?.price_digits ?? {},
        resume_dataset_id: dataset.dataset_id,
      });
      setJobId(result.job_id);
      setActiveJob("market_data_fetch", result.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setStarting(false);
    }
  };

  const removeDataset = async (dataset: MarketDataset) => {
    const detail = dataset.provider === "mt5"
      ? "This removes the dataset catalog copy. If these files are currently published to hist_data, use Clear MT5 files separately."
      : "This removes the dataset catalog copy and any retained tick/bar files for this import.";
    const ok = window.confirm(`Remove dataset history?\n\n${dataset.dataset_id}\n\n${detail}`);
    if (!ok) return;
    setDeletingDatasetId(dataset.dataset_id);
    setError(null);
    try {
      await deleteMarketDataset(dataset.dataset_id);
      refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeletingDatasetId(null);
    }
  };

  const run = source === "mt5" ? runMt5 : runDukascopy;

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Data Import</h2>
        <p className="tab-subtitle">Import MT5 broker bars for research or Dukascopy ticks for replay confirmation.</p>
      </div>

      <div className="form-section">
        <div className="section-label">Source</div>
        <div className="segmented-control" role="tablist" aria-label="Data source">
          <button type="button" className={source === "mt5" ? "active" : ""} onClick={() => setSource("mt5")} disabled={running}>
            MT5 Broker
          </button>
          <button type="button" className={source === "dukascopy" ? "active" : ""} onClick={() => setSource("dukascopy")} disabled={running}>
            Dukascopy Ticks
          </button>
        </div>
      </div>

      {source === "mt5" && (
        <>
          <div className="form-section">
            <div className="section-label">MT5 Broker History</div>
            <div className="form-grid-2">
              <div className="field">
                <label className="field-label">Symbols</label>
                <input className="field-input" value={mt5Symbols} onChange={(event) => setMt5Symbols(event.target.value)} disabled={running} />
                <span className="field-hint">MT5 broker symbol names, comma separated.</span>
              </div>
              <div className="field">
                <label className="field-label">History days</label>
                <input className="field-input" value={mt5TradingDays} onChange={(event) => setMt5TradingDays(event.target.value)} disabled={running} inputMode="numeric" />
                <span className="field-hint">Calendar days requested from the broker for every selected timeframe.</span>
              </div>
              <div className="field">
                <label className="field-label">Save folder</label>
                <input className="field-input" value={mt5SaveFolder} onChange={(event) => setMt5SaveFolder(event.target.value)} disabled={running} />
                <span className="field-hint">Leave as default to publish into Better Discovery hist_data.</span>
              </div>
              <div className="field">
                <label className="field-label">Connection</label>
                <button type="button" className="btn btn-secondary btn-sm" onClick={testMt5} disabled={running || checkingMt5}>
                  {checkingMt5 ? "Checking..." : "Check MT5"}
                </button>
                {mt5Status && (
                  <span className="field-hint">
                    {mt5Status.ok ? `${mt5Status.terminal ?? "connected"} ${mt5Status.account ?? ""}` : mt5Status.error}
                  </span>
                )}
              </div>
            </div>
            {currentImport?.exists && (
              <div className="current-import-banner" style={{ marginTop: 12 }}>
                <div>
                  <strong>Current MT5 import:</strong>{" "}
                  {currentImport.symbol ?? "mixed symbols"} - {currentImport.timeframes.map((value) => value.label).join(", ")}
                </div>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={clearMt5Import}
                  disabled={running || clearingCurrentImport}
                  style={{ marginTop: 8 }}
                >
                  {clearingCurrentImport ? "Clearing..." : "Clear MT5 files"}
                </button>
              </div>
            )}
          </div>

          <div className="form-section">
            <div className="section-label">MT5 Bar Timeframes</div>
            <div className="timeframe-grid">
              {MT5_TIMEFRAMES.map((timeframe) => (
                <label className="check-option" key={timeframe}>
                  <input type="checkbox" checked={mt5Timeframes.includes(timeframe)} onChange={() => toggleMt5Timeframe(timeframe)} disabled={running} />
                  <span>{timeframe.toUpperCase()}</span>
                </label>
              ))}
            </div>
            <div className="toggle-stack">
              <label className="toggle-label">
                <input type="checkbox" checked={mt5ClearExisting} onChange={(event) => setMt5ClearExisting(event.target.checked)} disabled={running} />
                Replace existing MT5 hist_data CSVs first
              </label>
            </div>
          </div>
        </>
      )}

      {source === "dukascopy" && (
        <>
          <div className="form-section">
            <div className="section-label">Dukascopy Dataset</div>
        <div className="form-grid-2">
          <div className="field">
            <label className="field-label">Provider</label>
            <select className="field-input" value={provider} onChange={(event) => setProvider(event.target.value as "dukascopy")} disabled={running}>
              {providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <span className="field-hint">{providers.find((item) => item.id === provider)?.venue}</span>
          </div>
          <div className="field">
            <label className="field-label">Symbols</label>
            <input className="field-input" value={symbols} onChange={(event) => setSymbols(event.target.value)} disabled={running} />
            <span className="field-hint">Dukascopy instrument names, comma separated.</span>
          </div>
          <div className="field">
            <label className="field-label">Price digits override</label>
            <input className="field-input" value={priceDigits} onChange={(event) => setPriceDigits(event.target.value)} disabled={running} />
            <span className="field-hint">Only needed for non-FX symbols. Format: XAUUSD:3, BTCUSD:1</span>
          </div>
        </div>
      </div>

      <div className="form-section">
        <div className="section-label">History Window</div>
        <div className="form-grid-2">
          <div className="field"><label className="field-label">From (UTC)</label><input className="field-input" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} disabled={running} /></div>
          <div className="field"><label className="field-label">To (UTC)</label><input className="field-input" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} disabled={running} /></div>
        </div>
      </div>

      <div className="form-section">
        <div className="section-label">Bar Timeframes</div>
        <div className="timeframe-grid">
          {TIMEFRAMES.map((timeframe) => (
            <label className="check-option" key={timeframe}>
              <input type="checkbox" checked={timeframes.includes(timeframe)} onChange={() => toggleTimeframe(timeframe)} disabled={running} />
              <span>{timeframe.toUpperCase()}</span>
            </label>
          ))}
        </div>
        <div className="toggle-stack">
          <label className="toggle-label"><input type="checkbox" checked={includeTicks} onChange={(event) => setIncludeTicks(event.target.checked)} disabled={running} /> Retain bid/ask ticks for local replay</label>
          <label className="toggle-label"><input type="checkbox" checked={publishDiscovery} onChange={(event) => setPublishDiscovery(event.target.checked)} disabled={running} /> Publish bars to the default Pattern Discovery folder</label>
        </div>
      </div>
        </>
      )}

      <div className="action-row">
        <button className="btn btn-primary" onClick={run} disabled={running}>
          {running ? "Importing..." : source === "mt5" ? "Import from MT5" : "Import Dukascopy Data"}
        </button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onDone={refresh} onError={setError} />

      <div className="form-section">
        <div className="section-label">Dataset Catalog</div>
        <div className="dataset-table-wrap">
          <table className="data-table">
            <thead><tr><th>Dataset</th><th>Provider</th><th>Symbols</th><th>Timeframes</th><th>State</th><th>Quality</th><th>Files</th><th>Action</th></tr></thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr key={dataset.dataset_id}>
                  <td title={dataset.dataset_id}>{dataset.dataset_id}</td><td>{dataset.provider}</td>
                  <td>{dataset.symbols.join(", ")}</td><td>{dataset.timeframes.map((value) => value.toUpperCase()).join(", ")}</td>
                  <td><span className={`status-badge ${dataset.state === "complete" ? "status-badge--ok" : dataset.state === "failed" ? "status-badge--err" : ""}`}>{dataset.state}</span></td>
                  <td title={dataset.error ?? ""}><span className={`status-badge ${datasetQualityLabel(dataset) === "Clean" ? "status-badge--ok" : datasetQualityLabel(dataset) === "-" ? "" : "status-badge--err"}`}>{datasetQualityLabel(dataset)}</span></td>
                  <td>{dataset.files.length}</td>
                  <td>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {dataset.state !== "complete" && dataset.import_options?.storage_layout === "daily_ticks_monthly_bars" && (
                        <button className="btn btn-secondary btn-sm" onClick={() => resume(dataset)} disabled={running || deletingDatasetId === dataset.dataset_id}>Resume</button>
                      )}
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => removeDataset(dataset)}
                        disabled={running || deletingDatasetId === dataset.dataset_id}
                      >
                        {deletingDatasetId === dataset.dataset_id ? "Deleting..." : "Delete"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!datasets.length && <tr><td colSpan={8}>No canonical datasets imported.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
