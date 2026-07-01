import { useEffect, useMemo, useRef, useState } from "react";
import {
  startHypothesisDiscovery,
  type HypothesisFamily,
} from "../api/discovery";
import {
  listMarketDatasets,
  type MarketDataset,
} from "../api/data";
import { useJobs } from "../state/jobs";
import JobProgress from "../components/JobProgress";
import { openResultWindow } from "../lib/windows";

type HypothesisMode = "market_mind" | "manual";
type TargetRegime = "auto" | "trend" | "range_reversal" | "volatility_expansion" | "compression" | "session_liquidity";
type ExecutionTimeframe = "m1" | "m5" | "m10" | "m15";
type QueueMode = "sequential" | "parallel";
type GrammarBlockGroup = "liquidity" | "structure" | "imbalance" | "orderflow" | "sessions" | "volatility" | "smt";
type QueueStatus = "queued" | "starting" | "running" | "done" | "failed" | "cancelled";
type DiscoveryQueueItem = {
  id: string;
  label: string;
  timeframe: ExecutionTimeframe;
  grammarTimeframes: ExecutionTimeframe[];
  families: HypothesisFamily[];
  mode: HypothesisMode;
  targetRegime?: TargetRegime;
  status: QueueStatus;
  jobId?: string;
  error?: string;
};

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

const GRAMMAR_BLOCK_GROUPS: Array<{ id: GrammarBlockGroup; label: string; hint: string }> = [
  { id: "liquidity", label: "Liquidity", hint: "Sweeps, prior highs/lows, equal levels" },
  { id: "structure", label: "Structure", hint: "MSS, BOS, CHoCH, trend bias" },
  { id: "imbalance", label: "FVG / IFVG", hint: "Fair value gaps, inverse gaps, BPR" },
  { id: "orderflow", label: "Order Blocks", hint: "OB, breaker, mitigation, rejection" },
  { id: "sessions", label: "Sessions", hint: "Asian, London, NY, opening ranges" },
  { id: "volatility", label: "Volatility", hint: "Compression, expansion, spike reversal" },
  { id: "smt", label: "SMT", hint: "Strict proxy divergence; only trades when proxy data exists" },
];

const GRAMMAR_SIGNAL_TIMEFRAMES: Array<{ id: ExecutionTimeframe; label: string }> = [
  { id: "m1", label: "M1" },
  { id: "m5", label: "M5" },
  { id: "m10", label: "M10" },
  { id: "m15", label: "M15" },
];

const DISCOVERY_QUEUE_STORAGE_KEY = "betterDiscovery.discoveryQueue.v1";

const HYPOTHESIS_FAMILY_GROUPS: Array<{
  id: string;
  label: string;
  hint: string;
  timeframe: ExecutionTimeframe;
  families: HypothesisFamily[];
}> = [
  {
    id: "autonomous_grammar",
    label: "Market Mind",
    hint: "Regime-biased ICT/SMT blocks, sweeps, FVG, OB, sessions",
    timeframe: "m5",
    families: [
      "strategy_grammar",
    ],
  },
  {
    id: "reversal_trap",
    label: "Reversal / Trap",
    hint: "Stop hunts, failed breaks, snapbacks",
    timeframe: "m5",
    families: [
      "liquidity_sweep_reclaim",
      "failed_breakout_reversal",
      "volatility_spike_reversal",
      "regime_mean_reversion",
    ],
  },
  {
    id: "breakout_continuation",
    label: "Breakout / Continuation",
    hint: "Level breaks that keep moving",
    timeframe: "m10",
    families: [
      "time_series_breakout",
      "session_range_breakout",
      "prior_day_level_continuation",
      "volatility_expansion",
      "inside_bar_expansion",
    ],
  },
  {
    id: "trend_pullback",
    label: "Trend Pullback",
    hint: "Bigger-direction pullback entries",
    timeframe: "m15",
    families: [
      "trend_pullback",
      "trend_day_pullback",
      "day_time_regime_filter",
    ],
  },
  {
    id: "opening_session",
    label: "Opening / Session",
    hint: "Session opens and range behavior",
    timeframe: "m5",
    families: [
      "opening_range_continuation_reversal",
      "session_range_breakout",
      "prior_day_level_continuation",
      "day_time_regime_filter",
    ],
  },
];

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

function formatQueueEta(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${hours}h ${rest}m`;
}

export default function DiscoveryTab() {
  const [hypothesisMode, setHypothesisMode] = useState<HypothesisMode>("market_mind");
  const [datasets, setDatasets] = useState<MarketDataset[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [timeframe, setTimeframe] = useState<ExecutionTimeframe>("m5");
  const [dateFrom, setDateFrom] = useState(dateInput(2000));
  const [dateTo, setDateTo] = useState(dateInput(0));
  const [families, setFamilies] = useState<HypothesisFamily[]>(
    ["liquidity_sweep_reclaim", "failed_breakout_reversal", "volatility_spike_reversal"],
  );
  const [grammarBlockGroups, setGrammarBlockGroups] = useState<GrammarBlockGroup[]>([
    "liquidity", "structure", "imbalance", "orderflow", "sessions", "volatility", "smt",
  ]);
  const [grammarTimeframes, setGrammarTimeframes] = useState<ExecutionTimeframe[]>(["m1", "m5", "m10", "m15"]);
  const [grammarComplexity, setGrammarComplexity] = useState<"simple" | "medium" | "complex">("medium");
  const [grammarRandomness, setGrammarRandomness] = useState<"low" | "balanced" | "high">("balanced");
  const [searchMode, setSearchMode] = useState<"market_mind" | "manual" | "broad" | "guided">("market_mind");
  const [targetRegime, setTargetRegime] = useState<TargetRegime>("auto");
  const [marketMindBiasPct, setMarketMindBiasPct] = useState("0.70");
  const [randomSeed, setRandomSeed] = useState("310200");
  const [guidedInitialFraction, setGuidedInitialFraction] = useState("0.35");
  const [guidedGenerations, setGuidedGenerations] = useState("3");
  const [guidedParentsKept, setGuidedParentsKept] = useState("30");
  const [guidedChildrenPerParent, setGuidedChildrenPerParent] = useState("30");
  const [guidedExplorationPct, setGuidedExplorationPct] = useState("0.25");
  const [parentMinProfitFactor, setParentMinProfitFactor] = useState("1.20");
  const [finalMinProfitFactor, setFinalMinProfitFactor] = useState("1.30");
  const [finalMinActivePassRate, setFinalMinActivePassRate] = useState("0.05");
  const [maxCandidateDrawdownPct, setMaxCandidateDrawdownPct] = useState("15");
  const [maxVariants, setMaxVariants] = useState("8000");
  const [minTradesPerWeek, setMinTradesPerWeek] = useState("5");
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

  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [queueItems, setQueueItems] = useState<DiscoveryQueueItem[]>(() => {
    try {
      const raw = localStorage.getItem(DISCOVERY_QUEUE_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw) as { items?: DiscoveryQueueItem[] };
      return Array.isArray(parsed.items) ? parsed.items : [];
    } catch {
      return [];
    }
  });
  const [queueRunning, setQueueRunning] = useState(() => {
    try {
      const raw = localStorage.getItem(DISCOVERY_QUEUE_STORAGE_KEY);
      if (!raw) return false;
      const parsed = JSON.parse(raw) as { running?: boolean };
      return !!parsed.running;
    } catch {
      return false;
    }
  });
  const [queueMode, setQueueMode] = useState<QueueMode>(() => {
    try {
      const raw = localStorage.getItem(DISCOVERY_QUEUE_STORAGE_KEY);
      if (!raw) return "sequential";
      const parsed = JSON.parse(raw) as { mode?: QueueMode };
      return parsed.mode === "parallel" ? "parallel" : "sequential";
    } catch {
      return "sequential";
    }
  });
  const [queueParallelLimit, setQueueParallelLimit] = useState(() => {
    try {
      const raw = localStorage.getItem(DISCOVERY_QUEUE_STORAGE_KEY);
      if (!raw) return "2";
      const parsed = JSON.parse(raw) as { parallelLimit?: string };
      return parsed.parallelLimit || "2";
    } catch {
      return "2";
    }
  });
  const queueStartLock = useRef(false);

  const job = useJobs((s) => (jobId ? s.jobs[jobId] : undefined));
  const jobs = useJobs((s) => s.jobs);
  const setActiveJob = useJobs((s) => s.setActive);
  const subscribeJob = useJobs((s) => s.subscribe);
  const isRunning = !!jobId && (job?.status === "running" || job?.status === "pending");
  const isDone = !!jobId && (job?.status === "done" || job?.status === "failed" || job?.status === "cancelled");

  useEffect(() => {
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
  const effectiveGrammarTimeframes = useMemo(
    () => Array.from(new Set<ExecutionTimeframe>([timeframe, ...grammarTimeframes])),
    [grammarTimeframes, timeframe],
  );
  const usesGrammar = hypothesisMode === "market_mind" || families.includes("strategy_grammar");
  const requiredTimeframes = useMemo(
    () => usesGrammar ? [...effectiveGrammarTimeframes, "h1", "h4"] : [timeframe, "h1", "h4"],
    [effectiveGrammarTimeframes, usesGrammar, timeframe],
  );
  const missingRequiredTimeframes = selectedDataset
    ? requiredTimeframes.filter((value) => !selectedDataset.timeframes.includes(value))
    : requiredTimeframes;
  const datasetReady = !!selectedDataset && missingRequiredTimeframes.length === 0;

  const queueActiveCount = queueItems.filter((item) => item.status === "starting" || item.status === "running").length;
  const queuePendingCount = queueItems.filter((item) => item.status === "queued").length;
  const queueTerminalCount = queueItems.filter((item) => ["done", "failed", "cancelled"].includes(item.status)).length;
  const queueHasActive = queueActiveCount > 0;

  useEffect(() => {
    try {
      localStorage.setItem(
        DISCOVERY_QUEUE_STORAGE_KEY,
        JSON.stringify({
          items: queueItems,
          running: queueRunning,
          mode: queueMode,
          parallelLimit: queueParallelLimit,
        }),
      );
    } catch {
      /* ignore storage failures */
    }
  }, [queueItems, queueRunning, queueMode, queueParallelLimit]);

  const toggleFamily = (id: HypothesisFamily) => {
    setFamilies((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  const toggleGrammarBlockGroup = (id: GrammarBlockGroup) => {
    setGrammarBlockGroups((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  const toggleGrammarTimeframe = (id: ExecutionTimeframe) => {
    setGrammarTimeframes((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  const applyFamilyGroup = (group: typeof HYPOTHESIS_FAMILY_GROUPS[number]) => {
    setFamilies(group.families);
    setTimeframe(group.timeframe);
  };

  const validateHypothesisForm = () => {
    if (!selectedDataset) return "Select a completed XAUUSD dataset first.";
    const required = usesGrammar ? [...effectiveGrammarTimeframes, "h1", "h4"] : [timeframe, "h1", "h4"];
    const missing = required.filter((value) => !selectedDataset.timeframes.includes(value));
    if (missing.length) return `Selected dataset is missing ${missing.map((value) => value.toUpperCase()).join(", ")}.`;
    const risk = parseNumberList(riskFractions);
    const stops = parseNumberList(dailyStops);
    const trades = parseIntList(maxTradesPerDay);
    if (!risk.length || !stops.length || !trades.length) {
      return "Risk, daily-stop, and max-trades grids must each have at least one value.";
    }
    if (hypothesisMode === "manual" && !families.length) return "Select at least one hypothesis family.";
    if (usesGrammar && !grammarBlockGroups.length) return "Select at least one grammar block group.";
    if (usesGrammar && !effectiveGrammarTimeframes.length) return "Select at least one grammar signal timeframe.";
    const variants = Math.trunc(Number(maxVariants));
    const seed = Math.trunc(Number(randomSeed));
    const minTradesPerFiveDays = Number(minTradesPerWeek);
    const workers = Math.trunc(Number(parallelWorkers));
    const attemptDays = Math.trunc(Number(maxAttemptDays));
    const guidedNums = [
      Number(guidedInitialFraction),
      Math.trunc(Number(guidedGenerations)),
      Math.trunc(Number(guidedParentsKept)),
      Math.trunc(Number(guidedChildrenPerParent)),
      Number(guidedExplorationPct),
      Number(parentMinProfitFactor),
      Number(finalMinProfitFactor),
      Number(finalMinActivePassRate),
      Number(maxCandidateDrawdownPct),
    ];
    if (!Number.isFinite(variants) || variants <= 0 || !Number.isFinite(seed) || seed < 0 || !Number.isFinite(minTradesPerFiveDays) || minTradesPerFiveDays <= 0 || !Number.isFinite(workers) || workers <= 0 || !Number.isFinite(attemptDays) || attemptDays <= 0) {
      return "Max variants, random seed, minimum trades/week, parallel workers, and max attempt days must be valid positive numbers.";
    }
    const bias = Number(marketMindBiasPct);
    if (hypothesisMode === "market_mind" && (!Number.isFinite(bias) || bias < 0 || bias > 1)) {
      return "Market Mind bias must be between 0 and 1.";
    }
    if (hypothesisMode === "manual" && families.includes("strategy_grammar") && guidedNums.some((value) => !Number.isFinite(value) || value < 0)) {
      return "Guided grammar settings must be valid positive numbers.";
    }
    return null;
  };

  const selectedDatasetHasTimeframe = (value: ExecutionTimeframe, grammarTfs: ExecutionTimeframe[] = []) =>
    !!selectedDataset &&
    Array.from(new Set([value, ...grammarTfs, "h1", "h4"])).every((tf) => selectedDataset.timeframes.includes(tf));

  const startHypothesisRun = async (
    runTimeframe: ExecutionTimeframe,
    runFamilies: HypothesisFamily[],
    runGrammarTimeframes: ExecutionTimeframe[] = grammarTimeframes,
    runMode: HypothesisMode = hypothesisMode,
    runTargetRegime: TargetRegime = targetRegime,
  ) => {
    if (!selectedDataset) throw new Error("Select a completed XAUUSD dataset first.");
    const grammarRun = runFamilies.length === 1 && runFamilies[0] === "strategy_grammar";
    const effectiveRunGrammarTimeframes = grammarRun ? Array.from(new Set<ExecutionTimeframe>([runTimeframe, ...runGrammarTimeframes])) : [];
    if (!selectedDatasetHasTimeframe(runTimeframe, effectiveRunGrammarTimeframes)) {
      const required = Array.from(new Set([runTimeframe, ...effectiveRunGrammarTimeframes, "h1", "h4"]));
      throw new Error(`Selected dataset must include ${required.map((tf) => tf.toUpperCase()).join(", ")}.`);
    }
    const risk = parseNumberList(riskFractions);
    const stops = parseNumberList(dailyStops);
    const trades = parseIntList(maxTradesPerDay);
    const variants = Math.trunc(Number(maxVariants));
    const minTradesPerFiveDays = Number(minTradesPerWeek);
    const workers = Math.trunc(Number(parallelWorkers));
    const attemptDays = Math.trunc(Number(maxAttemptDays));
    return startHypothesisDiscovery({
      dataset_id: selectedDataset.dataset_id,
      symbol: "XAUUSD",
      timeframe: runTimeframe,
      date_from: `${dateFrom}T00:00:00Z`,
      date_to: `${dateTo}T23:59:59Z`,
      families: runFamilies,
      grammar_timeframes: grammarRun ? effectiveRunGrammarTimeframes : undefined,
      grammar_block_groups: grammarRun ? grammarBlockGroups : undefined,
      grammar_complexity: grammarRun ? grammarComplexity : undefined,
      grammar_randomness: grammarRun ? grammarRandomness : undefined,
      search_mode: runMode === "market_mind" ? "market_mind" : grammarRun ? "guided" : "manual",
      target_regime: runMode === "market_mind" ? runTargetRegime : "auto",
      market_mind_bias_pct: runMode === "market_mind" ? Number(marketMindBiasPct) : undefined,
      random_seed: Math.trunc(Number(randomSeed)),
      guided_initial_fraction: Number(guidedInitialFraction),
      guided_generations: Math.trunc(Number(guidedGenerations)),
      guided_parents_kept: Math.trunc(Number(guidedParentsKept)),
      guided_children_per_parent: Math.trunc(Number(guidedChildrenPerParent)),
      guided_exploration_pct: Number(guidedExplorationPct),
      parent_min_profit_factor: Number(parentMinProfitFactor),
      final_min_profit_factor: Number(finalMinProfitFactor),
      final_min_active_pass_rate: Number(finalMinActivePassRate),
      max_candidate_drawdown_pct: Number(maxCandidateDrawdownPct),
      max_variants: variants,
      min_closed_trades: 1,
      min_trades_per_week: minTradesPerFiveDays,
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
  };

  const queueSelectedRun = () => {
    const message = validateHypothesisForm();
    if (message) {
      setError(message);
      return;
    }
    const group = HYPOTHESIS_FAMILY_GROUPS.find((item) =>
      item.families.length === families.length && item.families.every((id) => families.includes(id))
    );
    const runFamilies: HypothesisFamily[] = hypothesisMode === "market_mind" ? ["strategy_grammar"] : [...families];
    setQueueItems((current) => [
      ...current,
      {
        id: `queue_${Date.now()}_${current.length}`,
        label: hypothesisMode === "market_mind" ? `Market Mind ${targetRegime} (${grammarComplexity}, ${effectiveGrammarTimeframes.map((tf) => tf.toUpperCase()).join("+")})` : group?.label ?? `${families.length} custom families`,
        timeframe,
        grammarTimeframes: usesGrammar ? effectiveGrammarTimeframes : [],
        families: runFamilies,
        mode: hypothesisMode,
        targetRegime,
        status: "queued",
      },
    ]);
    setError(null);
  };

  const queueRecommendedRuns = () => {
    if (!selectedDataset) {
      setError("Select a completed XAUUSD dataset first.");
      return;
    }
    const runnable = HYPOTHESIS_FAMILY_GROUPS.filter((group) => selectedDatasetHasTimeframe(group.timeframe));
    if (!runnable.length) {
      setError("Selected dataset does not contain the preset execution timeframes plus H1 and H4.");
      return;
    }
    setQueueItems((current) => [
      ...current,
      ...runnable.map((group, index) => ({
        id: `queue_${Date.now()}_${current.length + index}`,
        label: group.label,
        timeframe: group.timeframe,
        grammarTimeframes: group.id === "autonomous_grammar" ? [group.timeframe] : [],
        families: [...group.families],
        mode: (group.id === "autonomous_grammar" ? "market_mind" : "manual") as HypothesisMode,
        targetRegime: group.id === "autonomous_grammar" ? targetRegime : "auto",
        status: "queued" as QueueStatus,
      })),
    ]);
    setError(null);
  };

  const removeQueueItem = (id: string) => {
    setQueueItems((current) => current.filter((item) => item.id !== id));
  };

  useEffect(() => {
    const jobIds = queueItems.map((item) => item.jobId).filter(Boolean) as string[];
    const unsubscribers = jobIds.map((id) => subscribeJob(id));
    return () => {
      unsubscribers.forEach((unsubscribe) => unsubscribe());
    };
  }, [queueItems.map((item) => item.jobId).filter(Boolean).join("|"), subscribeJob]);

  useEffect(() => {
    setQueueItems((current) => current.map((item) => {
      if (!item.jobId) return item;
      const queuedJob = jobs[item.jobId];
      if (!queuedJob) return item;
      if (queuedJob.status === "done") return { ...item, status: "done" };
      if (queuedJob.status === "failed") return { ...item, status: "failed", error: queuedJob.error ?? "Failed" };
      if (queuedJob.status === "cancelled") return { ...item, status: "cancelled", error: queuedJob.error ?? "Cancelled" };
      if (queuedJob.status === "running" || queuedJob.status === "pending") return { ...item, status: "running" };
      return item;
    }));
  }, [jobs]);

  useEffect(() => {
    if (!queueRunning || queueStartLock.current) return;
    const active = queueItems.filter((item) => item.status === "starting" || item.status === "running").length;
    const pending = queueItems.filter((item) => item.status === "queued");
    if (!pending.length) {
      if (active === 0) setQueueRunning(false);
      return;
    }
    const parallelLimit = queueMode === "parallel"
      ? Math.max(1, Math.min(2, Math.trunc(Number(queueParallelLimit)) || 1))
      : 1;
    const slots = Math.max(0, parallelLimit - active);
    if (slots <= 0) return;
    queueStartLock.current = true;
    const toStart = pending.slice(0, slots);
    (async () => {
      for (const item of toStart) {
        setQueueItems((current) => current.map((candidate) =>
          candidate.id === item.id ? { ...candidate, status: "starting" } : candidate
        ));
        try {
          const ref = await startHypothesisRun(item.timeframe, item.families, item.grammarTimeframes, item.mode ?? "manual", item.targetRegime ?? "auto");
          setQueueItems((current) => current.map((candidate) =>
            candidate.id === item.id ? { ...candidate, status: "running", jobId: ref.job_id } : candidate
          ));
          setJobId(ref.job_id);
          setActiveJob("discovery", ref.job_id);
        } catch (reason) {
          setQueueItems((current) => current.map((candidate) =>
            candidate.id === item.id
              ? { ...candidate, status: "failed", error: reason instanceof Error ? reason.message : String(reason) }
              : candidate
          ));
        }
      }
    })().finally(() => {
      queueStartLock.current = false;
    });
  }, [queueItems, queueRunning, queueMode, queueParallelLimit, selectedDatasetId, dateFrom, dateTo, maxVariants, minTradesPerWeek, parallelWorkers, searchMode, targetRegime, marketMindBiasPct, randomSeed, guidedInitialFraction, guidedGenerations, guidedParentsKept, guidedChildrenPerParent, guidedExplorationPct, parentMinProfitFactor, finalMinProfitFactor, finalMinActivePassRate, maxCandidateDrawdownPct, targetProfitPct, dailyLossPct, maxLossPct, maxAttemptDays, startFrequency, riskFractions, dailyStops, maxTradesPerDay, slippagePriceUnits]);

  const useDatasetRange = () => {
    if (!selectedDataset) return;
    const from = dateOnly(selectedDataset.requested_from);
    const to = dateOnly(selectedDataset.requested_to);
    if (from) setDateFrom(from);
    if (to) setDateTo(to);
  };

  const handleStartHypothesis = async () => {
    const message = validateHypothesisForm();
    if (message) {
      setError(message);
      return;
    }
    setStarting(true);
    setError(null);
    setJobId(null);
    try {
      const ref = await startHypothesisRun(timeframe, hypothesisMode === "market_mind" ? ["strategy_grammar"] : families, grammarTimeframes, hypothesisMode);
      setJobId(ref.job_id);
      setActiveJob("discovery", ref.job_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  };

  const handleJobDone = async () => {
    if (!jobId) return;
    await openResultWindow(
      `discovery-results-${jobId}`,
      "Strategy Discovery Results",
      { window: "discovery-results", jobId },
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
        <div className="section-label">Research Mode</div>
        <div className="segmented-control" role="tablist" aria-label="Hypothesis research mode">
          <button type="button" className={hypothesisMode === "market_mind" ? "active" : ""} onClick={() => { setHypothesisMode("market_mind"); setSearchMode("market_mind"); }} disabled={isRunning}>
            Market Mind
          </button>
          <button type="button" className={hypothesisMode === "manual" ? "active" : ""} onClick={() => { setHypothesisMode("manual"); setSearchMode("manual"); }} disabled={isRunning}>
            Manual
          </button>
        </div>

        {hypothesisMode === "market_mind" ? (
          <>
            <div className="form-grid-2 hypothesis-grid" style={{ marginTop: 14 }}>
              <div className="field">
                <label className="field-label">Target regime</label>
                <select className="field-input" value={targetRegime} onChange={(event) => setTargetRegime(event.target.value as TargetRegime)} disabled={isRunning}>
                  <option value="auto">Auto detect</option>
                  <option value="trend">Trend</option>
                  <option value="range_reversal">Range / reversal</option>
                  <option value="volatility_expansion">Volatility expansion</option>
                  <option value="compression">Compression</option>
                  <option value="session_liquidity">Session liquidity</option>
                </select>
                <span className="field-hint">Search one market behavior instead of one do-it-all strategy.</span>
              </div>
              <div className="field">
                <label className="field-label">Market bias %</label>
                <input className="field-input" value={marketMindBiasPct} onChange={(event) => setMarketMindBiasPct(event.target.value)} disabled={isRunning} inputMode="decimal" />
                <span className="field-hint">0.70 means 70% regime-biased generation and 30% random exploration.</span>
              </div>
              <div className="field">
                <label className="field-label">Strategy complexity</label>
                <select className="field-input" value={grammarComplexity} onChange={(event) => setGrammarComplexity(event.target.value as typeof grammarComplexity)} disabled={isRunning}>
                  <option value="simple">Simple</option>
                  <option value="medium">Medium</option>
                  <option value="complex">Complex</option>
                </select>
                <span className="field-hint">Controls how many blocks are combined before a signal can fire.</span>
              </div>
              <div className="field">
                <label className="field-label">Randomness</label>
                <select className="field-input" value={grammarRandomness} onChange={(event) => setGrammarRandomness(event.target.value as typeof grammarRandomness)} disabled={isRunning}>
                  <option value="low">Low</option>
                  <option value="balanced">Balanced</option>
                  <option value="high">High</option>
                </select>
                <span className="field-hint">Controls how broadly the generator samples valid strategy recipes.</span>
              </div>
            </div>
            <div className="section-label" style={{ marginTop: 14 }}>Signal Timeframes</div>
            <div className="timeframe-grid hypothesis-family-grid" style={{ marginTop: 8 }}>
              {GRAMMAR_SIGNAL_TIMEFRAMES.map((tf) => (
                <label className="check-option" key={tf.id}>
                  <input type="checkbox" checked={effectiveGrammarTimeframes.includes(tf.id)} onChange={() => toggleGrammarTimeframe(tf.id)} disabled={isRunning || tf.id === timeframe} />
                  <span>{tf.label}</span>
                </label>
              ))}
            </div>
            <span className="field-hint">The test timeframe is always included for execution; selected signal timeframes can be assigned per grammar block.</span>
            <div className="timeframe-grid hypothesis-family-grid" style={{ marginTop: 12 }}>
              {GRAMMAR_BLOCK_GROUPS.map((group) => (
                <label className="check-option" key={group.id} title={group.hint}>
                  <input type="checkbox" checked={grammarBlockGroups.includes(group.id)} onChange={() => toggleGrammarBlockGroup(group.id)} disabled={isRunning} />
                  <span>{group.label}</span>
                </label>
              ))}
            </div>
            <span className="field-hint">Grammar results export to MQL through the rule-tree translator. SMT remains strict and needs proxy data to produce signals.</span>
          </>
        ) : (
          <>
            <div className="form-grid-2 hypothesis-grid" style={{ marginTop: 14 }}>
              <div className="field">
                <label className="field-label">Manual grammar complexity</label>
                <select className="field-input" value={grammarComplexity} onChange={(event) => setGrammarComplexity(event.target.value as typeof grammarComplexity)} disabled={isRunning}>
                  <option value="simple">Simple</option>
                  <option value="medium">Medium</option>
                  <option value="complex">Complex</option>
                </select>
              </div>
              <div className="field">
                <label className="field-label">Manual randomness</label>
                <select className="field-input" value={grammarRandomness} onChange={(event) => setGrammarRandomness(event.target.value as typeof grammarRandomness)} disabled={isRunning}>
                  <option value="low">Low</option>
                  <option value="balanced">Balanced</option>
                  <option value="high">High</option>
                </select>
              </div>
            </div>
            <div className="hypothesis-family-presets" style={{ marginTop: 12 }}>
              {HYPOTHESIS_FAMILY_GROUPS.filter((group) => group.id !== "autonomous_grammar").map((group) => {
                const active = group.families.length === families.length && group.families.every((id) => families.includes(id));
                return (
                  <button
                    type="button"
                    key={group.id}
                    className={`hypothesis-family-preset${active ? " active" : ""}`}
                    onClick={() => applyFamilyGroup(group)}
                    disabled={isRunning}
                    title={`${group.hint}. Suggested timeframe: ${group.timeframe.toUpperCase()}`}
                  >
                    <span>{group.label}</span>
                    <small>{group.timeframe.toUpperCase()}</small>
                  </button>
                );
              })}
              <button
                type="button"
                className="hypothesis-family-preset"
                onClick={() => setFamilies(HYPOTHESIS_FAMILIES.map((item) => item.id))}
                disabled={isRunning}
              >
                <span>All Families</span>
                <small>mixed</small>
              </button>
              <button
                type="button"
                className="hypothesis-family-preset"
                onClick={() => setFamilies([])}
                disabled={isRunning}
              >
                <span>Clear</span>
                <small>manual</small>
              </button>
            </div>
            <div className="timeframe-grid hypothesis-family-grid">
              {HYPOTHESIS_FAMILIES.map((family) => (
                <label className="check-option" key={family.id}>
                  <input type="checkbox" checked={families.includes(family.id)} onChange={() => toggleFamily(family.id)} disabled={isRunning} />
                  <span>{family.label}</span>
                </label>
              ))}
            </div>
            {families.includes("strategy_grammar") && (
              <>
                <div className="section-label" style={{ marginTop: 14 }}>Manual Grammar Blocks</div>
                <div className="timeframe-grid hypothesis-family-grid" style={{ marginTop: 8 }}>
                  {GRAMMAR_SIGNAL_TIMEFRAMES.map((tf) => (
                    <label className="check-option" key={tf.id}>
                      <input type="checkbox" checked={effectiveGrammarTimeframes.includes(tf.id)} onChange={() => toggleGrammarTimeframe(tf.id)} disabled={isRunning || tf.id === timeframe} />
                      <span>{tf.label}</span>
                    </label>
                  ))}
                </div>
                <div className="timeframe-grid hypothesis-family-grid" style={{ marginTop: 12 }}>
                  {GRAMMAR_BLOCK_GROUPS.map((group) => (
                    <label className="check-option" key={group.id} title={group.hint}>
                      <input type="checkbox" checked={grammarBlockGroups.includes(group.id)} onChange={() => toggleGrammarBlockGroup(group.id)} disabled={isRunning} />
                      <span>{group.label}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>

      <div className="form-section">
        <div className="section-label">Search Quality</div>
        <span className="field-hint">
          Market Mind scans regime-biased grammar directly. Manual grammar can still use guided mutation; saved winners can be evolved later in Evolution Lab.
        </span>
        <div className="form-grid-2 hypothesis-grid" style={{ marginTop: 14 }}>
          {hypothesisMode === "manual" && families.includes("strategy_grammar") && (
            <div className="field">
              <label className="field-label">Parent PF gate</label>
              <input className="field-input" value={parentMinProfitFactor} onChange={(event) => setParentMinProfitFactor(event.target.value)} disabled={isRunning} inputMode="decimal" />
              <span className="field-hint">Only profitable manual-grammar candidates above this PF are mutated.</span>
            </div>
          )}
          <div className="field">
            <label className="field-label">Final PF gate</label>
            <input className="field-input" value={finalMinProfitFactor} onChange={(event) => setFinalMinProfitFactor(event.target.value)} disabled={isRunning} inputMode="decimal" />
            <span className="field-hint">Only candidates above this PF appear as finalists.</span>
          </div>
          <div className="field">
            <label className="field-label">Final active pass rate</label>
            <input className="field-input" value={finalMinActivePassRate} onChange={(event) => setFinalMinActivePassRate(event.target.value)} disabled={isRunning} inputMode="decimal" />
          </div>
          <div className="field">
            <label className="field-label">Max candidate DD %</label>
            <input className="field-input" value={maxCandidateDrawdownPct} onChange={(event) => setMaxCandidateDrawdownPct(event.target.value)} disabled={isRunning} inputMode="decimal" />
          </div>
        </div>
        {hypothesisMode === "manual" && families.includes("strategy_grammar") ? (
          <div className="form-grid-2 hypothesis-grid" style={{ marginTop: 10 }}>
            <div className="field">
              <label className="field-label">Initial scan fraction</label>
              <input className="field-input" value={guidedInitialFraction} onChange={(event) => setGuidedInitialFraction(event.target.value)} disabled={isRunning} inputMode="decimal" />
            </div>
            <div className="field">
              <label className="field-label">Generations</label>
              <input className="field-input" value={guidedGenerations} onChange={(event) => setGuidedGenerations(event.target.value)} disabled={isRunning} inputMode="numeric" />
            </div>
            <div className="field">
              <label className="field-label">Parents kept</label>
              <input className="field-input" value={guidedParentsKept} onChange={(event) => setGuidedParentsKept(event.target.value)} disabled={isRunning} inputMode="numeric" />
            </div>
            <div className="field">
              <label className="field-label">Children / parent</label>
              <input className="field-input" value={guidedChildrenPerParent} onChange={(event) => setGuidedChildrenPerParent(event.target.value)} disabled={isRunning} inputMode="numeric" />
            </div>
            <div className="field">
              <label className="field-label">Fresh exploration %</label>
              <input className="field-input" value={guidedExplorationPct} onChange={(event) => setGuidedExplorationPct(event.target.value)} disabled={isRunning} inputMode="decimal" />
            </div>
          </div>
        ) : null}
      </div>

      <div className="form-section">
        <div className="section-label">Search Size</div>
        <div className="form-grid-2">
          <div className="field">
            <label className="field-label">Max variants</label>
            <input className="field-input" value={maxVariants} onChange={(event) => setMaxVariants(event.target.value)} disabled={isRunning} inputMode="numeric" />
          </div>
          <div className="field">
            <label className="field-label">Min trades / 5 trading days</label>
            <input className="field-input" value={minTradesPerWeek} onChange={(event) => setMinTradesPerWeek(event.target.value)} disabled={isRunning} inputMode="decimal" />
            <span className="field-hint">Scales automatically with the selected date range.</span>
          </div>
          <div className="field">
            <label className="field-label">Parallel workers</label>
            <input className="field-input" value={parallelWorkers} onChange={(event) => setParallelWorkers(event.target.value)} disabled={isRunning} inputMode="numeric" />
            <span className="field-hint">Use 1 for lowest memory use; raise for chunked research runs.</span>
          </div>
          <div className="field">
            <label className="field-label">Random seed</label>
            <div style={{ display: "flex", gap: 8 }}>
              <input className="field-input" value={randomSeed} onChange={(event) => setRandomSeed(event.target.value)} disabled={isRunning} inputMode="numeric" />
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setRandomSeed(String(Math.floor(Math.random() * 2_147_483_647)))}
                disabled={isRunning}
              >
                Random
              </button>
            </div>
            <span className="field-hint">Same seed repeats the same candidate generation; Random gives a fresh run.</span>
          </div>
        </div>
      </div>

      <div className="form-section">
        <div className="section-label">Discovery Queue</div>
        <div className="queue-toolbar">
          <button type="button" className="btn btn-secondary btn-sm" onClick={queueSelectedRun} disabled={isRunning || queueRunning}>
            Queue Current Setup
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={queueRecommendedRuns} disabled={isRunning || queueRunning || !selectedDataset}>
            Queue Preset Set
          </button>
          <select className="field-input queue-mode-select" value={queueMode} onChange={(event) => setQueueMode(event.target.value as QueueMode)} disabled={queueRunning || queueHasActive}>
            <option value="sequential">Sequential</option>
            <option value="parallel">Parallel safe</option>
          </select>
          {queueMode === "parallel" && (
            <input
              className="field-input queue-limit-input"
              value={queueParallelLimit}
              onChange={(event) => setQueueParallelLimit(event.target.value)}
              disabled={queueRunning || queueHasActive}
              inputMode="numeric"
              title="Parallel queue is capped at 2 concurrent discovery jobs."
            />
          )}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setQueueRunning(true)}
            disabled={queueRunning || queuePendingCount === 0}
          >
            Run Queue
          </button>
          {queueRunning && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setQueueRunning(false)}>
              Pause Starting New
            </button>
          )}
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setQueueItems((current) => current.filter((item) => item.status === "running" || item.status === "starting"))}
            disabled={queueRunning || queueItems.length === 0}
          >
            Clear Waiting/Done
          </button>
        </div>
        <span className="field-hint">Parallel safe starts at most 2 discovery jobs. Queue mode is locked while jobs are active; pause only stops new jobs from starting.</span>
        {queueItems.length > 0 && (
          <div className="discovery-queue-list">
            <div className="discovery-queue-summary">
              {queuePendingCount} queued, {queueActiveCount} running, {queueTerminalCount} finished
            </div>
            {queueItems.map((item, index) => {
              const queuedJob = item.jobId ? jobs[item.jobId] : undefined;
              const resultJobId = item.jobId;
              const hypothesisProgress = queuedJob?.meta?.hypothesis_progress as {
                completed_variants?: number;
                total_variants?: number;
                accepted_variants?: number;
                variants_per_hour?: number;
                eta_seconds?: number | null;
                stage?: string;
                generation_index?: number | null;
                generation_total?: number | null;
                generation_phase?: string | null;
              } | undefined;
              const completedVariants = hypothesisProgress?.completed_variants ?? queuedJob?.stage_index;
              const totalVariants = hypothesisProgress?.total_variants ?? queuedJob?.stage_total;
              const stage = completedVariants != null && totalVariants != null
                ? `${completedVariants}/${totalVariants}`
                : "";
              const generationDetail = hypothesisProgress?.generation_index != null
                ? `gen ${hypothesisProgress.generation_index}${hypothesisProgress.generation_total != null ? `/${hypothesisProgress.generation_total}` : ""}${hypothesisProgress.generation_phase ? ` ${hypothesisProgress.generation_phase}` : ""}`
                : "";
              const eta = formatQueueEta(hypothesisProgress?.eta_seconds ?? queuedJob?.eta_seconds);
              const rate = hypothesisProgress?.variants_per_hour != null
                ? `${Math.round(hypothesisProgress.variants_per_hour)}/h`
                : "";
              return (
                <div className={`discovery-queue-item queue-status-${item.status}`} key={item.id}>
                  <div className="discovery-queue-main">
                    <strong>{index + 1}. {item.label}</strong>
                    <span>
                      {item.timeframe.toUpperCase()} - {item.families.includes("strategy_grammar") ? `grammar ${item.grammarTimeframes.map((tf) => tf.toUpperCase()).join("+")}` : `${item.families.length} families`} {stage ? `- ${stage}` : ""}
                    </span>
                    {(queuedJob?.stage_name || generationDetail || rate || eta || hypothesisProgress?.accepted_variants != null) && (
                      <span>
                        {[queuedJob?.stage_name, generationDetail, hypothesisProgress?.accepted_variants != null ? `${hypothesisProgress.accepted_variants} accepted` : "", rate, eta ? `ETA ${eta}` : ""].filter(Boolean).join(" - ")}
                      </span>
                    )}
                    {item.error && <span className="queue-error">{item.error}</span>}
                  </div>
                  <div className="discovery-queue-actions">
                    <span className="status-badge">{item.status}</span>
                    {resultJobId && queuedJob?.status === "done" && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => openResultWindow(`discovery-results-${resultJobId}`, "FTMO Hypothesis Results", { window: "discovery-results", jobId: resultJobId })}
                      >
                        Results
                      </button>
                    )}
                    {(item.status === "queued" || item.status === "done" || item.status === "failed" || item.status === "cancelled") && !queueRunning && (
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => removeQueueItem(item.id)}>
                        Remove
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
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

  return (
    <div className="tab-content discovery-tab">
      <div className="tab-header">
        <h2>Strategy Discovery</h2>
        <p className="tab-subtitle">
          Market Mind generates saved-strategy candidates from coded XAUUSD grammar and prop-firm rules.
        </p>
      </div>

      {renderHypothesisMode()}

      <div className="action-row" style={{ marginTop: 20 }}>
        <button
          className="btn btn-primary"
          onClick={handleStartHypothesis}
          disabled={starting || isRunning}
        >
          {starting ? "Starting..." : "Run Discovery"}
        </button>
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
