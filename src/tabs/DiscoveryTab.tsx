import { useEffect, useMemo, useState } from "react";
import {
  getParams,
  startDiscovery,
  startHypothesisDiscovery,
  type HypothesisFamily,
  type ParamDef,
} from "../api/discovery";
import {
  getCurrentImport,
  listMarketDatasets,
  type CurrentImport,
  type MarketDataset,
} from "../api/data";
import { useJobs } from "../state/jobs";
import { useParamDefaults } from "../state/paramDefaults";
import JobProgress from "../components/JobProgress";
import { openResultWindow } from "../lib/windows";

type DiscoveryEngine = "hypothesis" | "legacy";
type ExecutionTimeframe = "m1" | "m5" | "m10" | "m15";

const FOLDER_KEYS = new Set(["DATA_FOLDER", "OUTPUT_FOLDER"]);
const HIDDEN_FROM_TAB = new Set([
  "TF1_FILE", "TF2_FILE", "TF3_FILE", "TF4_FILE", "TF5_FILE",
  "MULTI_SEED_BASE",
]);

const HYPOTHESIS_FAMILIES: Array<{ id: HypothesisFamily; label: string }> = [
  { id: "time_series_breakout", label: "Channel breaks" },
  { id: "session_range_breakout", label: "Range breaks" },
  { id: "trend_pullback", label: "Trend pullbacks" },
  { id: "volatility_expansion", label: "Volatility expansion" },
  { id: "regime_mean_reversion", label: "Mean reversion" },
  { id: "liquidity_sweep_reclaim", label: "Sweep reclaim" },
  { id: "failed_breakout_reversal", label: "Failed breakouts" },
  { id: "prior_day_level_continuation", label: "Prior-day levels" },
  { id: "volatility_spike_reversal", label: "Spike reversal" },
  { id: "opening_range_continuation_reversal", label: "Opening range" },
  { id: "trend_day_pullback", label: "Trend day pullbacks" },
  { id: "day_time_regime_filter", label: "Day/time regimes" },
  { id: "inside_bar_expansion", label: "Inside-bar expansion" },
];

function ParamTooltip({ description }: { description: string }) {
  if (!description) return null;
  return (
    <span className="param-tooltip-wrap">
      <span className="param-tooltip-icon" tabIndex={0} role="button" aria-label="Parameter description">?</span>
      <span className="param-tooltip-popup">{description}</span>
    </span>
  );
}

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

function dateInput(daysAgo: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

function dateOnly(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  return isNaN(parsed.getTime()) ? "" : parsed.toISOString().slice(0, 10);
}

function parseNumberList(raw: string): number[] {
  return raw
    .split(/[,\s]+/)
    .map((item) => Number(item.trim().replace(",", ".")))
    .filter((value) => Number.isFinite(value));
}

function parseIntList(raw: string): number[] {
  return parseNumberList(raw).map((value) => Math.trunc(value)).filter((value) => value > 0);
}

export default function DiscoveryTab() {
  const [engine, setEngine] = useState<DiscoveryEngine>("hypothesis");
  const [params, setParams] = useState<ParamDef[]>([]);
  const [datasets, setDatasets] = useState<MarketDataset[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [timeframe, setTimeframe] = useState<ExecutionTimeframe>("m5");
  const [dateFrom, setDateFrom] = useState(dateInput(2000));
  const [dateTo, setDateTo] = useState(dateInput(0));
  const [families, setFamilies] = useState<HypothesisFamily[]>(
    HYPOTHESIS_FAMILIES.map((item) => item.id),
  );
  const [maxVariants, setMaxVariants] = useState("5000");
  const [minClosedTrades, setMinClosedTrades] = useState("180");
  const [parallelWorkers, setParallelWorkers] = useState("6");
  const [targetProfitPct, setTargetProfitPct] = useState("10");
  const [dailyLossPct, setDailyLossPct] = useState("5");
  const [maxLossPct, setMaxLossPct] = useState("10");
  const [maxAttemptDays, setMaxAttemptDays] = useState("10");
  const [startFrequency, setStartFrequency] = useState("1D");
  const [riskFractions, setRiskFractions] = useState("0.005, 0.0075, 0.01");
  const [dailyStops, setDailyStops] = useState("2, 3, 4");
  const [maxTradesPerDay, setMaxTradesPerDay] = useState("4, 8, 12");
  const [slippagePriceUnits, setSlippagePriceUnits] = useState("0.10");

  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [overrideOnce, setOverrideOnce] = useState(false);
  const [folderOverrides, setFolderOverrides] = useState<Record<string, string>>({});
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["Data & Files", "General"]));
  const [showAdvanced, setShowAdvanced] = useState<Set<string>>(new Set());
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [currentImport, setCurrentImport] = useState<CurrentImport | null>(null);

  const persistentDefaults = useParamDefaults((s) => s.defaults);
  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const setActiveJob = useJobs((s) => s.setActive);
  const isRunning = !!jobId && (job?.status === "running" || job?.status === "pending");
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed" || job?.status === "cancelled");

  useEffect(() => {
    getParams().then(setParams).catch(() => undefined);
    getCurrentImport().then(setCurrentImport).catch(() => undefined);
    listMarketDatasets().then(setDatasets).catch(() => undefined);
    const stored = useJobs.getState().activeByKind.discovery;
    if (stored) {
      const existing = useJobs.getState().jobs[stored];
      if (!existing || !["done", "failed", "cancelled"].includes(existing.status)) {
        setJobId(stored);
      }
    }
  }, []);

  const xauusdDatasets = useMemo(
    () => datasets.filter((dataset) =>
      dataset.state === "complete" && dataset.symbols.includes("XAUUSD"),
    ),
    [datasets],
  );

  useEffect(() => {
    if (!selectedDatasetId && xauusdDatasets.length) {
      const firstWithTf = xauusdDatasets.find((dataset) =>
        dataset.timeframes.includes(timeframe) &&
        dataset.timeframes.includes("h1") &&
        dataset.timeframes.includes("h4"),
      );
      const selected = firstWithTf ?? xauusdDatasets[0];
      setSelectedDatasetId(selected.dataset_id);
      const from = dateOnly(selected.requested_from);
      const to = dateOnly(selected.requested_to);
      if (from) setDateFrom(from);
      if (to) setDateTo(to);
    }
  }, [selectedDatasetId, timeframe, xauusdDatasets]);

  const selectedDataset = xauusdDatasets.find((dataset) => dataset.dataset_id === selectedDatasetId) ?? null;
  const requiredTimeframes = useMemo(() => [timeframe, "h1", "h4"], [timeframe]);
  const missingRequiredTimeframes = selectedDataset
    ? requiredTimeframes.filter((value) => !selectedDataset.timeframes.includes(value))
    : requiredTimeframes;
  const datasetReady = !!selectedDataset && missingRequiredTimeframes.length === 0;

  const tfFilesUserSet = ["TF1_FILE", "TF2_FILE", "TF3_FILE", "TF4_FILE", "TF5_FILE"].some(
    (k) => (overrides[k]?.trim() || persistentDefaults[k]) != null,
  );
  const autoDetectedTfs = currentImport?.exists
    ? currentImport.timeframes.slice(0, 5).map((tf) => tf.label).join(", ")
    : "";

  const trueDefault = (p: ParamDef): string => {
    const pd = persistentDefaults[p.key];
    if (pd != null) return String(pd);
    return p.value != null ? String(p.value) : "";
  };

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

  const currentValueOf = (key: string): string | null => {
    const ov = overrides[key]?.trim();
    if (ov) return ov;
    const pd = persistentDefaults[key];
    if (pd != null) return String(pd);
    const def = params.find((p) => p.key === key);
    return def?.value != null ? String(def.value) : null;
  };
  const groupGate = (gParams: ParamDef[]) =>
    gParams.find((p) => p.gated_by)?.gated_by ?? null;
  const isGroupActive = (gParams: ParamDef[]): boolean => {
    const g = groupGate(gParams);
    if (!g) return true;
    return currentValueOf(g.key) === g.value;
  };

  const isAdvanced = (p: ParamDef) => p.tier === "advanced";
  const partitionGroup = (gParams: ParamDef[]) => {
    const core: ParamDef[] = [];
    const advanced: ParamDef[] = [];
    for (const p of gParams) (isAdvanced(p) ? advanced : core).push(p);
    return { core, advanced };
  };
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

  const toggleFamily = (id: HypothesisFamily) => {
    setFamilies((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  const useDatasetRange = () => {
    if (!selectedDataset) return;
    const from = dateOnly(selectedDataset.requested_from);
    const to = dateOnly(selectedDataset.requested_to);
    if (from) setDateFrom(from);
    if (to) setDateTo(to);
  };

  const handleStartHypothesis = async () => {
    if (!selectedDataset) {
      setError("Select a completed XAUUSD dataset first.");
      return;
    }
    if (!datasetReady) {
      setError("Selected dataset must contain the test timeframe plus H1 and H4 context bars.");
      return;
    }
    const risk = parseNumberList(riskFractions);
    const stops = parseNumberList(dailyStops);
    const trades = parseIntList(maxTradesPerDay);
    if (!risk.length || !stops.length || !trades.length) {
      setError("Risk, daily-stop, and max-trades grids must each have at least one value.");
      return;
    }
    if (!families.length) {
      setError("Select at least one hypothesis family.");
      return;
    }
    const variants = Math.trunc(Number(maxVariants));
    const minTrades = Math.trunc(Number(minClosedTrades));
    const workers = Math.trunc(Number(parallelWorkers));
    const attemptDays = Math.trunc(Number(maxAttemptDays));
    if (!Number.isFinite(variants) || variants <= 0 || !Number.isFinite(minTrades) || minTrades <= 0 || !Number.isFinite(workers) || workers <= 0) {
      setError("Max variants, minimum trades, and parallel workers must be positive numbers.");
      return;
    }
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const ref = await startHypothesisDiscovery({
        dataset_id: selectedDataset.dataset_id,
        symbol: "XAUUSD",
        timeframe,
        date_from: `${dateFrom}T00:00:00Z`,
        date_to: `${dateTo}T23:59:59Z`,
        families,
        max_variants: variants,
        min_closed_trades: minTrades,
        parallel_workers: Math.min(workers, 32),
        slippage_price_units: Number(slippagePriceUnits),
        challenge: {
          target_profit_pct: Number(targetProfitPct),
          daily_loss_pct: Number(dailyLossPct),
          max_loss_pct: Number(maxLossPct),
          max_attempt_days: attemptDays,
          start_frequency: startFrequency.trim() || "1D",
          risk_fractions: risk,
          internal_daily_stop_pcts: stops,
          max_trades_per_day_options: trades,
        },
      });
      setJobId(ref.job_id);
      setActiveJob("discovery", ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleStartLegacy = async () => {
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const parsed: Record<string, unknown> = {};
      for (const p of params) {
        if (FOLDER_KEYS.has(p.key)) continue;
        const userVal = overrides[p.key]?.trim();
        let raw: string | undefined;
        if (userVal) raw = userVal;
        else {
          const pd = persistentDefaults[p.key];
          if (pd != null) raw = String(pd);
        }
        if (!raw) continue;
        const normalized = raw.replace(",", ".");
        if (p.type === "bool") parsed[p.key] = raw === "true" || raw === "1";
        else if (p.type === "int") {
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

  const handleStart = () => {
    if (engine === "hypothesis") return handleStartHypothesis();
    return handleStartLegacy();
  };

  const handleJobDone = async () => {
    if (overrideOnce) {
      setOverrideOnce(false);
      setFolderOverrides({});
    }
    if (!jobId) return;
    await openResultWindow(
      `discovery-results-${jobId}`,
      engine === "hypothesis" ? "FTMO Hypothesis Results" : "Pattern Discovery Results",
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
            <span className="field-hint-inline"> - {p.description}</span>
          </label>
          <div className="folder-display">{defaultVal || "-"}</div>
          {overrideOnce && (
            <input
              className="field-input"
              style={{ marginTop: 4 }}
              value={folderVal}
              onChange={(e) => setFolderVal(p.key, e.target.value)}
              placeholder="Override path for this run"
              disabled={isRunning}
            />
          )}
        </div>
      );
    }

    const def = trueDefault(p);
    const edited = isEdited(p.key);

    if (p.type === "bool") {
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
              {p.description && <span className="field-hint"> - {p.description}</span>}
            </span>
            {edited && (
              <button type="button" className="pd-reset-btn" onClick={() => resetField(p.key)} title="Reset to default" style={{ marginLeft: 8 }}>Reset</button>
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
              <button type="button" className="pd-reset-btn" onClick={() => resetField(p.key)} title="Reset to default" style={{ marginLeft: 8 }}>Reset</button>
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

    const isSeed = p.key === "RANDOM_SEED";
    const randomize = () => {
      const s = Math.floor(Math.random() * 2_147_483_646) + 1;
      setValue(p.key, String(s));
    };

    const num = (key: string) => parseFloat((currentValueOf(key) ?? "").replace(",", "."));
    let edgeFloorHint: string | null = null;
    if (p.key === "FILTER_EDGE_K") {
      const k = num("FILTER_EDGE_K");
      const twr = num("TARGET_WR_PCT");
      const tpf = num("TARGET_PF");
      if ([k, twr, tpf].every(Number.isFinite)) {
        const wrFloor = 50 + k * (twr - 50);
        const pfFloor = 1 + k * (tpf - 1);
        edgeFloorHint = `implied floors: WR ${wrFloor.toFixed(1)}%, PF ${pfFloor.toFixed(2)}`;
      }
    }

    return (
      <div key={p.key} className="field">
        <label className="field-label">
          {p.label}
          {p.description && <ParamTooltip description={p.description} />}
          <span className="field-default"> (default: {def})</span>
          {edited && (
            <button type="button" className="pd-reset-btn" onClick={() => resetField(p.key)} title="Reset to default" style={{ marginLeft: 8 }}>Reset</button>
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
            <button type="button" className="seed-random-btn" onClick={randomize} disabled={isRunning} title="Generate a random seed">
              Random
            </button>
          )}
        </div>
        {hint && <span className="field-hint">({hint})</span>}
        {edgeFloorHint && <span className="field-hint" style={{ fontWeight: 600 }}>{edgeFloorHint}</span>}
      </div>
    );
  };

  const renderHypothesisMode = () => (
    <>
      <div className="form-section">
        <div className="section-label">Dataset</div>
        <div className="form-grid-2">
          <div className="field">
            <label className="field-label">Market dataset</label>
            <select
              className="field-input"
              value={selectedDatasetId}
              onChange={(event) => setSelectedDatasetId(event.target.value)}
              disabled={isRunning}
            >
              <option value="">Select XAUUSD dataset</option>
              {xauusdDatasets.map((dataset) => (
                <option key={dataset.dataset_id} value={dataset.dataset_id}>
                  {dataset.dataset_id} - {dataset.requested_from.slice(0, 10)} to {dataset.requested_to.slice(0, 10)}
                </option>
              ))}
            </select>
            <span className="field-hint">Uses immutable MT5/imported bar datasets with H1 and H4 context.</span>
          </div>
          <div className="field">
            <label className="field-label">Test timeframe</label>
            <select className="field-input" value={timeframe} onChange={(event) => setTimeframe(event.target.value as ExecutionTimeframe)} disabled={isRunning}>
              <option value="m1">M1</option>
              <option value="m5">M5</option>
              <option value="m10">M10</option>
              <option value="m15">M15</option>
            </select>
            <span className="field-hint">Dataset must include this timeframe plus H1 and H4.</span>
          </div>
        </div>
        {selectedDataset && (
          <div className={`current-import-banner ${datasetReady ? "" : "banner-warn"}`}>
            <strong>{datasetReady ? "Ready:" : "Missing required bars:"}</strong>{" "}
            {datasetReady
              ? `${selectedDataset.symbols.join(", ")} - ${selectedDataset.timeframes.map((value) => value.toUpperCase()).join(", ")}`
              : `${missingRequiredTimeframes.map((value) => value.toUpperCase()).join(", ")} required; dataset has ${selectedDataset.timeframes.map((value) => value.toUpperCase()).join(", ") || "none"}`}
            <span className="field-hint" style={{ marginTop: 4 }}>
              {selectedDataset.requested_from.slice(0, 10)} to {selectedDataset.requested_to.slice(0, 10)}
            </span>
          </div>
        )}
        {!xauusdDatasets.length && (
          <div className="alert alert-warn">No completed XAUUSD market datasets found. Import XAUUSD bars first in Data Import.</div>
        )}
      </div>

      <div className="form-section">
        <div className="section-label">Walk Window</div>
        <div className="form-grid-2">
          <div className="field">
            <label className="field-label">From UTC</label>
            <input className="field-input" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} disabled={isRunning} />
          </div>
          <div className="field">
            <label className="field-label">To UTC</label>
            <input className="field-input" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} disabled={isRunning} />
          </div>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={useDatasetRange} disabled={!selectedDataset || isRunning}>
          Use Full Dataset Range
        </button>
      </div>

      <div className="form-section">
        <div className="section-label">Hypothesis Families</div>
        <div className="timeframe-grid hypothesis-family-grid">
          {HYPOTHESIS_FAMILIES.map((family) => (
            <label className="check-option" key={family.id}>
              <input type="checkbox" checked={families.includes(family.id)} onChange={() => toggleFamily(family.id)} disabled={isRunning} />
              <span>{family.label}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="form-section">
        <div className="section-label">Search Size</div>
        <div className="form-grid-2">
          <div className="field">
            <label className="field-label">Max variants</label>
            <input className="field-input" value={maxVariants} onChange={(event) => setMaxVariants(event.target.value)} disabled={isRunning} inputMode="numeric" />
          </div>
          <div className="field">
            <label className="field-label">Min closed trades</label>
            <input className="field-input" value={minClosedTrades} onChange={(event) => setMinClosedTrades(event.target.value)} disabled={isRunning} inputMode="numeric" />
          </div>
          <div className="field">
            <label className="field-label">Parallel workers</label>
            <input className="field-input" value={parallelWorkers} onChange={(event) => setParallelWorkers(event.target.value)} disabled={isRunning} inputMode="numeric" />
            <span className="field-hint">Use 1 for lowest memory use; raise for chunked research runs.</span>
          </div>
        </div>
      </div>

      <div className="form-section">
        <div className="section-label">FTMO Challenge Replay</div>
        <div className="form-grid-2 hypothesis-grid">
          <div className="field"><label className="field-label">Target profit %</label><input className="field-input" value={targetProfitPct} onChange={(event) => setTargetProfitPct(event.target.value)} disabled={isRunning} inputMode="decimal" /></div>
          <div className="field"><label className="field-label">Max attempt days</label><input className="field-input" value={maxAttemptDays} onChange={(event) => setMaxAttemptDays(event.target.value)} disabled={isRunning} inputMode="numeric" /></div>
          <div className="field"><label className="field-label">Daily loss %</label><input className="field-input" value={dailyLossPct} onChange={(event) => setDailyLossPct(event.target.value)} disabled={isRunning} inputMode="decimal" /></div>
          <div className="field"><label className="field-label">Max loss %</label><input className="field-input" value={maxLossPct} onChange={(event) => setMaxLossPct(event.target.value)} disabled={isRunning} inputMode="decimal" /></div>
          <div className="field"><label className="field-label">Risk fractions</label><input className="field-input" value={riskFractions} onChange={(event) => setRiskFractions(event.target.value)} disabled={isRunning} /></div>
          <div className="field"><label className="field-label">Internal daily stops %</label><input className="field-input" value={dailyStops} onChange={(event) => setDailyStops(event.target.value)} disabled={isRunning} /></div>
          <div className="field"><label className="field-label">Max trades/day grid</label><input className="field-input" value={maxTradesPerDay} onChange={(event) => setMaxTradesPerDay(event.target.value)} disabled={isRunning} /></div>
          <div className="field"><label className="field-label">Start frequency</label><input className="field-input" value={startFrequency} onChange={(event) => setStartFrequency(event.target.value)} disabled={isRunning} /></div>
          <div className="field"><label className="field-label">Slippage price units</label><input className="field-input" value={slippagePriceUnits} onChange={(event) => setSlippagePriceUnits(event.target.value)} disabled={isRunning} inputMode="decimal" /></div>
        </div>
      </div>
    </>
  );

  const renderLegacyMode = () => (
    <>
      {currentImport && (
        <div className="form-section">
          <div className="current-import-banner">
            {currentImport.exists ? (
              <>
                <strong>Data source:</strong>{" "}
                {currentImport.symbol ?? "-"}
                {" - "}
                {tfFilesUserSet
                  ? <span>using your TF overrides</span>
                  : <span>auto-detected: {autoDetectedTfs || "no timeframes found"}</span>}
                <span className="field-hint" style={{ marginLeft: 8 }}>
                  ({currentImport.timeframes.length} file{currentImport.timeframes.length === 1 ? "" : "s"} in hist_data
                  {currentImport.modified_at ? ` - imported ${formatAgo(currentImport.modified_at)}` : ""})
                </span>
              </>
            ) : (
              <span style={{ color: "var(--text2)" }}>
                <strong>No data imported.</strong>{" "}
                Use Data Import to fetch from MT5 first.
              </span>
            )}
          </div>
        </div>
      )}

      {params.length === 0 ? (
        <p className="tab-loading">Loading parameters...</p>
      ) : (
        <>
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
              Override input/output folders for this run only
            </label>
          </div>

          {[...groups.entries()].map(([group, gParams]) => {
            const { core, advanced } = partitionGroup(gParams);
            const advOpen = effectiveShowAdvanced(group, advanced);
            const gate = groupGate(gParams);
            const active = isGroupActive(gParams);
            return (
              <div key={group} className={`param-group${!active ? " param-group-inactive" : ""}`}>
                <div className="param-group-header-row">
                  <button className="param-group-header" onClick={() => toggleGroup(group)}>
                    <span className="param-group-arrow">{openGroups.has(group) ? "v" : ">"}</span>
                    {group}
                    <span className="param-group-count">{gParams.length} settings</span>
                    {gate && !active && (
                      <span className="param-group-gate-badge" title={`Only active when ${gate.key} = ${gate.value}`}>
                        inactive
                      </span>
                    )}
                  </button>
                  {groupHasEdits(group) && (
                    <button type="button" className="param-group-reset-btn" onClick={() => resetGroup(group)} title={`Reset ${group}`} disabled={isRunning}>Reset</button>
                  )}
                </div>
                {openGroups.has(group) && (
                  <div className="param-group-body">
                    {core.length > 0 && <div className="override-grid">{core.map((p) => renderField(p))}</div>}
                    {advanced.length > 0 && (
                      <>
                        <button type="button" className="param-advanced-toggle" onClick={() => toggleAdvanced(group)}>
                          <span className="param-group-arrow">{advOpen ? "v" : ">"}</span>
                          {advOpen ? "Hide advanced" : "Show advanced"}
                          <span className="param-group-count">{advanced.length}</span>
                        </button>
                        {advOpen && <div className="override-grid param-advanced-grid">{advanced.map((p) => renderField(p))}</div>}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
    </>
  );

  return (
    <div className="tab-content discovery-tab">
      <div className="tab-header">
        <h2>{engine === "hypothesis" ? "FTMO Hypothesis Discovery" : "Pattern Discovery"}</h2>
        <p className="tab-subtitle">
          {engine === "hypothesis"
            ? "Test coded XAUUSD ideas against target-first prop-firm rules."
            : "Run the original Pattern Discovery v6 random search."}
        </p>
      </div>

      <div className="form-section">
        <div className="segmented-control" role="tablist" aria-label="Discovery engine">
          <button type="button" className={engine === "hypothesis" ? "active" : ""} onClick={() => setEngine("hypothesis")} disabled={isRunning}>
            FTMO Hypothesis
          </button>
          <button type="button" className={engine === "legacy" ? "active" : ""} onClick={() => setEngine("legacy")} disabled={isRunning}>
            Legacy Random
          </button>
        </div>
      </div>

      {engine === "hypothesis" ? renderHypothesisMode() : renderLegacyMode()}

      <div className="action-row" style={{ marginTop: 20 }}>
        <button
          className="btn btn-primary"
          onClick={handleStart}
          disabled={starting || isRunning || (engine === "legacy" && params.length === 0)}
        >
          {starting ? "Starting..." : engine === "hypothesis" ? "Run FTMO Hypothesis" : "Run Legacy Discovery"}
        </button>
        {engine === "legacy" && (
          <button className="btn btn-secondary" onClick={handleResetToDefaults} disabled={isRunning || params.length === 0} title="Reset all fields to saved defaults">
            Reset to defaults
          </button>
        )}
        {isDone && (
          <button className="btn btn-secondary" onClick={() => { setJobId(null); setError(null); }}>
            New Run
          </button>
        )}
        {jobId && job?.status === "done" && (
          <button className="btn btn-accent" onClick={handleJobDone}>Open Results</button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onDone={handleJobDone} onError={(msg) => setError(msg)} />
    </div>
  );
}
