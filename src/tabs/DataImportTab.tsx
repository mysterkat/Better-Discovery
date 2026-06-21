import { useEffect, useMemo, useState } from "react";
import {
  fetchProviderData,
  getMarketDataProviders,
  listMarketDatasets,
  type MarketDataProvider,
  type MarketDataset,
} from "../api/data";
import JobProgress from "../components/JobProgress";
import { useJobs } from "../state/jobs";

const TIMEFRAMES = ["m1", "m5", "m10", "m15", "m30", "h1", "h4", "d1"];

function dateInput(daysAgo: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

export default function DataImportTab() {
  const [providers, setProviders] = useState<MarketDataProvider[]>([]);
  const [datasets, setDatasets] = useState<MarketDataset[]>([]);
  const [provider, setProvider] = useState<"dukascopy">("dukascopy");
  const [symbols, setSymbols] = useState("XAUUSD");
  const [timeframes, setTimeframes] = useState<string[]>(["m1", "m5", "m10", "m15", "h1"]);
  const [dateFrom, setDateFrom] = useState(dateInput(30));
  const [dateTo, setDateTo] = useState(dateInput(0));
  const [includeTicks, setIncludeTicks] = useState(true);
  const [publishDiscovery, setPublishDiscovery] = useState(true);
  const [priceDigits, setPriceDigits] = useState("XAUUSD:3");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const setActiveJob = useJobs((state) => state.setActive);
  const job = useJobs((state) => jobId ? state.jobs[jobId] : undefined);
  const running = starting || job?.status === "pending" || job?.status === "running";

  const refresh = () => listMarketDatasets().then(setDatasets).catch(() => undefined);
  useEffect(() => {
    getMarketDataProviders().then(setProviders).catch((reason: unknown) => setError(String(reason)));
    refresh();
    const active = useJobs.getState().activeByKind.market_data_fetch;
    if (active) setJobId(active);
  }, []);

  const parsedSymbols = useMemo(
    () => symbols.split(/[\s,]+/).map((value) => value.trim().toUpperCase()).filter(Boolean),
    [symbols],
  );

  const toggleTimeframe = (timeframe: string) => {
    setTimeframes((current) => current.includes(timeframe)
      ? current.filter((value) => value !== timeframe)
      : [...current, timeframe]);
  };

  const run = async () => {
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

  return (
    <div className="tab-content">
      <div className="tab-header">
        <h2>Data Import</h2>
        <p className="tab-subtitle">Build an immutable tick dataset and publish compatible bars to Pattern Discovery.</p>
      </div>

      <div className="form-section">
        <div className="section-label">Source</div>
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

      <div className="action-row">
        <button className="btn btn-primary" onClick={run} disabled={running}>{running ? "Importing..." : "Import Market Data"}</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onDone={refresh} onError={setError} />

      <div className="form-section">
        <div className="section-label">Dataset Catalog</div>
        <div className="dataset-table-wrap">
          <table className="data-table">
            <thead><tr><th>Dataset</th><th>Provider</th><th>Symbols</th><th>Timeframes</th><th>State</th><th>Files</th><th>Action</th></tr></thead>
            <tbody>
              {datasets.map((dataset) => (
                <tr key={dataset.dataset_id}>
                  <td title={dataset.dataset_id}>{dataset.dataset_id}</td><td>{dataset.provider}</td>
                  <td>{dataset.symbols.join(", ")}</td><td>{dataset.timeframes.map((value) => value.toUpperCase()).join(", ")}</td>
                  <td><span className={`status-badge ${dataset.state === "complete" ? "status-badge--ok" : dataset.state === "failed" ? "status-badge--err" : ""}`}>{dataset.state}</span></td>
                  <td>{dataset.files.length}</td>
                  <td>{dataset.state !== "complete" && dataset.import_options?.storage_layout === "daily_ticks_monthly_bars"
                    ? <button className="btn btn-secondary btn-sm" onClick={() => resume(dataset)} disabled={running}>Resume</button>
                    : "-"}</td>
                </tr>
              ))}
              {!datasets.length && <tr><td colSpan={7}>No canonical datasets imported.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
