import { useEffect, useMemo, useState } from "react";
import { listMarketDatasets, type MarketDataset } from "../api/data";
import { listLibrary, type LibraryEntry } from "../api/library";
import {
  listSavedReplayExperiments,
  runLocalRobustness,
  runSavedStrategyReplay,
  runStrategyValidation,
  type ReplayExperiment,
  type RobustnessResult,
  type SavedStrategyReplayResult,
  type StrategyValidationResult,
} from "../api/research";
import { compareMc, type McCompareResult } from "../api/mc";
import JobProgress from "../components/JobProgress";
import SavedStrategyPicker from "../components/SavedStrategyPicker";
import { useJobs } from "../state/jobs";

function metric(result: SavedStrategyReplayResult | null, key: string): number | null {
  const value = result?.metrics?.[key];
  return typeof value === "number" ? value : null;
}

function fmt(value: number | null, decimals = 2): string {
  return value == null || Number.isNaN(value) ? "-" : value.toFixed(decimals);
}

function defaultDate(daysAgo: number): string {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

export default function ResearchLabTab() {
  const [datasets, setDatasets] = useState<MarketDataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [library, setLibrary] = useState<LibraryEntry[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<string[]>([]);
  const [role, setRole] = useState<"validation" | "walk_forward" | "lockbox">("validation");
  const [dateFrom, setDateFrom] = useState(defaultDate(365));
  const [dateTo, setDateTo] = useState(defaultDate(0));
  const [initialBalance, setInitialBalance] = useState(10_000);
  const [lotSize, setLotSize] = useState(0.1);
  const [contractSize, setContractSize] = useState(100);
  const [commission, setCommission] = useState(7);
  const [slippage, setSlippage] = useState(0.1);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<SavedStrategyReplayResult | null>(null);
  const [recentReplays, setRecentReplays] = useState<ReplayExperiment[]>([]);
  const [recentReplayId, setRecentReplayId] = useState("");
  const [robustnessJobId, setRobustnessJobId] = useState<string | null>(null);
  const [robustness, setRobustness] = useState<RobustnessResult | null>(null);
  const [mt5Report, setMt5Report] = useState("");
  const [compareJobId, setCompareJobId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<McCompareResult | null>(null);
  const [validationJobId, setValidationJobId] = useState<string | null>(null);
  const [validation, setValidation] = useState<StrategyValidationResult | null>(null);
  const [oosFraction, setOosFraction] = useState(0.3);
  const [walkTrainMonths, setWalkTrainMonths] = useState(24);
  const [walkTestMonths, setWalkTestMonths] = useState(6);
  const [walkMutationSamples, setWalkMutationSamples] = useState(12);
  const [stabilitySamples, setStabilitySamples] = useState(24);
  const [stabilitySeed, setStabilitySeed] = useState(42);
  const [minProfitFactor, setMinProfitFactor] = useState(1.3);
  const [minSharpe, setMinSharpe] = useState(1.0);
  const [validationMaxDrawdown, setValidationMaxDrawdown] = useState(12);
  const [minWalkPassRate, setMinWalkPassRate] = useState(0.6);
  const [minStabilityPassRate, setMinStabilityPassRate] = useState(0.6);
  const [validationMinTrades, setValidationMinTrades] = useState(100);
  const [error, setError] = useState<string | null>(null);

  const setActiveJob = useJobs((state) => state.setActive);
  const job = useJobs((state) => (jobId ? state.jobs[jobId] : undefined));
  const validationJob = useJobs((state) => (validationJobId ? state.jobs[validationJobId] : undefined));
  const running = job?.status === "pending" || job?.status === "running";
  const validationRunning = validationJob?.status === "pending" || validationJob?.status === "running";
  const hypothesisEntries = useMemo(
    () => library.filter((entry) => {
      const metadata = entry.metadata as Record<string, unknown>;
      return metadata.__kind === "hypothesis" && typeof metadata.hypothesis_strategy === "object";
    }),
    [library],
  );

  const refreshRecent = () => {
    listSavedReplayExperiments()
      .then((items) => {
        setRecentReplays(items);
        if (!recentReplayId && items[0]?.result) {
          setRecentReplayId(items[0].id);
        }
      })
      .catch(() => undefined);
  };

  useEffect(() => {
    listMarketDatasets()
      .then((items) => {
        const complete = items.filter((item) => item.state === "complete");
        setDatasets(complete);
        setDatasetId((current) => current || complete[0]?.dataset_id || "");
      })
      .catch((reason: unknown) => setError(String(reason)));
    listLibrary()
      .then((entries) => setLibrary(entries))
      .catch((reason: unknown) => setError(String(reason)));
    const active = useJobs.getState().activeByKind.saved_strategy_replay;
    if (active) setJobId(active);
    const activeValidation = useJobs.getState().activeByKind.strategy_validation;
    if (activeValidation) setValidationJobId(activeValidation);
    refreshRecent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadRecentReplay = (experimentId: string) => {
    setRecentReplayId(experimentId);
    const replay = recentReplays.find((item) => item.id === experimentId);
    const replayResult = replay?.result as SavedStrategyReplayResult | null | undefined;
    if (!replayResult) return;
    setResult(replayResult);
    const request = (replay?.request ?? {}) as Record<string, unknown>;
    setDatasetId(String(request.dataset_id ?? replayResult.dataset_id));
    setRole(String(request.dataset_role ?? replayResult.dataset_role) as typeof role);
    setDateFrom(String(request.date_from ?? dateFrom).slice(0, 10));
    setDateTo(String(request.date_to ?? dateTo).slice(0, 10));
  };

  const run = async () => {
    const patternId = selectedStrategy[0];
    if (!datasetId || !patternId) {
      setError("Select a dataset and one saved hypothesis strategy.");
      return;
    }
    setError(null);
    setResult(null);
    setRobustness(null);
    setComparison(null);
    try {
      const reference = await runSavedStrategyReplay({
        dataset_id: datasetId,
        pattern_id: patternId,
        date_from: `${dateFrom}T00:00:00Z`,
        date_to: `${dateTo}T00:00:00Z`,
        dataset_role: role,
        initial_balance: initialBalance,
        lot_size: lotSize,
        contract_size: contractSize,
        commission_per_lot_round_turn: commission,
        slippage_price_units: slippage,
      });
      setJobId(reference.job_id);
      setActiveJob("saved_strategy_replay", reference.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const runRobustness = async () => {
    if (!result) return;
    setError(null);
    setRobustness(null);
    try {
      const reference = await runLocalRobustness(result.ledger_parquet);
      setRobustnessJobId(reference.job_id);
      setActiveJob("local_robustness", reference.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const compare = async () => {
    if (!result || !mt5Report.trim()) return;
    setError(null);
    setComparison(null);
    try {
      const reference = await compareMc({
        local_ledger_path: result.ledger_parquet,
        mt5_report_path: mt5Report.trim(),
        global_params: { n_sims: 10_000, seed: 42, bootstrap_block_size: 5 },
      });
      setCompareJobId(reference.job_id);
      setActiveJob("mc_compare", reference.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const runValidation = async () => {
    const patternId = selectedStrategy[0];
    if (!datasetId || !patternId) {
      setError("Select a dataset and one saved hypothesis strategy.");
      return;
    }
    setError(null);
    setValidation(null);
    try {
      const reference = await runStrategyValidation({
        dataset_id: datasetId,
        pattern_id: patternId,
        date_from: `${dateFrom}T00:00:00Z`,
        date_to: `${dateTo}T00:00:00Z`,
        initial_balance: initialBalance,
        lot_size: lotSize,
        contract_size: contractSize,
        commission_per_lot_round_turn: commission,
        slippage_price_units: slippage,
        oos_fraction: oosFraction,
        walk_train_months: walkTrainMonths,
        walk_test_months: walkTestMonths,
        walk_mutation_samples: walkMutationSamples,
        stability_samples: stabilitySamples,
        stability_seed: stabilitySeed,
        min_profit_factor: minProfitFactor,
        min_sharpe: minSharpe,
        max_drawdown_pct: validationMaxDrawdown,
        min_walk_forward_pass_rate: minWalkPassRate,
        min_stability_pass_rate: minStabilityPassRate,
        min_trades: validationMinTrades,
      });
      setValidationJobId(reference.job_id);
      setActiveJob("strategy_validation", reference.job_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const useValidationOosLedger = () => {
    const oos = validation?.is_oos.out_of_sample;
    if (!validation || !oos?.ledger_parquet) return;
    setResult({
      experiment_id: validation.experiment_id,
      pattern_id: validation.pattern_id,
      strategy_id: validation.strategy_id,
      library_name: `${validation.library_name} OOS`,
      strategy_fingerprint: "",
      dataset_id: validation.dataset_id,
      dataset_role: "validation",
      ledger_csv: oos.ledger_csv ?? "",
      ledger_parquet: oos.ledger_parquet,
      metrics: oos.metrics,
      gate: { decision: oos.gate.decision === "pass" ? "promote" : "reject", checks: oos.gate.checks },
    });
  };

  return (
    <div className="tab-content research-lab">
      <div className="tab-header">
        <h2>Research Lab</h2>
        <p className="tab-subtitle">Replay saved strategies on imported bar data, preserve ledgers, then send them into robustness or Monte Carlo checks.</p>
      </div>

      {recentReplays.length > 0 && (
        <div className="recent-replay-row">
          <label className="field-label">Recent saved replay</label>
          <select className="field-input" value={recentReplayId} onChange={(event) => loadRecentReplay(event.target.value)}>
            <option value="">Select replay</option>
            {recentReplays.map((item) => {
              const itemResult = item.result as SavedStrategyReplayResult | null;
              return (
                <option key={item.id} value={item.id}>
                  {new Date(item.created_at).toLocaleString()} · {itemResult?.library_name ?? itemResult?.strategy_id ?? item.id} · {itemResult?.metrics.trades ?? 0} trades
                </option>
              );
            })}
          </select>
        </div>
      )}

      <div className="merger-layout">
        <section className="merger-panel">
          <h3>Saved Strategy</h3>
          <SavedStrategyPicker
            entries={hypothesisEntries}
            selected={selectedStrategy}
            maxSelected={1}
            onToggle={(patternId) => setSelectedStrategy((current) => current.includes(patternId) ? [] : [patternId])}
          />
        </section>

        <section className="merger-panel">
          <h3>Replay Settings</h3>
          <div className="research-controls">
            <div className="field research-dataset">
              <label className="field-label">Dataset</label>
              <select className="field-input" value={datasetId} onChange={(event) => setDatasetId(event.target.value)} disabled={running}>
                {datasets.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.dataset_id}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="field-label">Role</label>
              <select className="field-input" value={role} onChange={(event) => setRole(event.target.value as typeof role)} disabled={running}>
                <option value="validation">Validation</option>
                <option value="walk_forward">Walk forward</option>
                <option value="lockbox">Lockbox</option>
              </select>
            </div>
            <div className="field">
              <label className="field-label">From</label>
              <input className="field-input" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} disabled={running} />
            </div>
            <div className="field">
              <label className="field-label">To</label>
              <input className="field-input" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} disabled={running} />
            </div>
          </div>
          <div className="research-costs">
            <label>Balance <input className="field-input field-sm" type="number" value={initialBalance} onChange={(event) => setInitialBalance(Number(event.target.value))} /></label>
            <label>Lot size <input className="field-input field-sm" type="number" step="0.01" value={lotSize} onChange={(event) => setLotSize(Number(event.target.value))} /></label>
            <label>Contract size <input className="field-input field-sm" type="number" value={contractSize} onChange={(event) => setContractSize(Number(event.target.value))} /></label>
            <label>Commission / lot <input className="field-input field-sm" type="number" value={commission} onChange={(event) => setCommission(Number(event.target.value))} /></label>
            <label>Slippage price <input className="field-input field-sm" type="number" step="0.01" value={slippage} onChange={(event) => setSlippage(Number(event.target.value))} /></label>
          </div>
          <button className="btn btn-primary" onClick={run} disabled={running || !datasetId || selectedStrategy.length !== 1}>
            {running ? "Running..." : "Run Saved Replay"}
          </button>
        </section>
      </div>

      <section className="merger-panel validation-panel">
        <h3>Validation Pipeline</h3>
        <p className="field-hint">Run this after discovery on saved strategies you actually like. Monte Carlo stays separate; use the OOS ledger handoff after this passes.</p>
        <div className="research-controls">
          <label>OOS fraction <input className="field-input field-sm" type="number" min="0.1" max="0.6" step="0.05" value={oosFraction} onChange={(event) => setOosFraction(Number(event.target.value))} /></label>
          <label>WF train months <input className="field-input field-sm" type="number" min="3" max="120" value={walkTrainMonths} onChange={(event) => setWalkTrainMonths(Number(event.target.value))} /></label>
          <label>WF test months <input className="field-input field-sm" type="number" min="1" max="24" value={walkTestMonths} onChange={(event) => setWalkTestMonths(Number(event.target.value))} /></label>
          <label>WF mutations <input className="field-input field-sm" type="number" min="0" max="100" value={walkMutationSamples} onChange={(event) => setWalkMutationSamples(Number(event.target.value))} /></label>
          <label>Stability samples <input className="field-input field-sm" type="number" min="0" max="200" value={stabilitySamples} onChange={(event) => setStabilitySamples(Number(event.target.value))} /></label>
          <label>Stability seed <input className="field-input field-sm" type="number" min="0" value={stabilitySeed} onChange={(event) => setStabilitySeed(Number(event.target.value))} /></label>
          <label>Min PF <input className="field-input field-sm" type="number" min="1" max="5" step="0.05" value={minProfitFactor} onChange={(event) => setMinProfitFactor(Number(event.target.value))} /></label>
          <label>Min Sharpe <input className="field-input field-sm" type="number" min="-5" max="10" step="0.1" value={minSharpe} onChange={(event) => setMinSharpe(Number(event.target.value))} /></label>
          <label>Max DD % <input className="field-input field-sm" type="number" min="1" max="100" step="0.5" value={validationMaxDrawdown} onChange={(event) => setValidationMaxDrawdown(Number(event.target.value))} /></label>
          <label>WF pass rate <input className="field-input field-sm" type="number" min="0" max="1" step="0.05" value={minWalkPassRate} onChange={(event) => setMinWalkPassRate(Number(event.target.value))} /></label>
          <label>Stability pass <input className="field-input field-sm" type="number" min="0" max="1" step="0.05" value={minStabilityPassRate} onChange={(event) => setMinStabilityPassRate(Number(event.target.value))} /></label>
          <label>Min trades <input className="field-input field-sm" type="number" min="1" value={validationMinTrades} onChange={(event) => setValidationMinTrades(Number(event.target.value))} /></label>
        </div>
        <button className="btn btn-primary" onClick={runValidation} disabled={validationRunning || !datasetId || selectedStrategy.length !== 1}>
          {validationRunning ? "Validating..." : "Run Validation Pipeline"}
        </button>
      </section>

      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onDone={(value) => { setResult(value as SavedStrategyReplayResult); refreshRecent(); }} onError={setError} />
      <JobProgress jobId={validationJobId} onDone={(value) => setValidation(value as StrategyValidationResult)} onError={setError} />

      {validation && (
        <div className="validation-result">
          <div className={`comparison-verdict ${validation.overall.decision === "pass" ? "pass" : "block"}`}>
            <span>Overall validation</span><strong>{validation.overall.decision.toUpperCase()}</strong>
          </div>
          <div className="replay-kpis">
            <div><span>OOS PF</span><strong>{fmt(validation.is_oos.out_of_sample.metrics.profit_factor)}</strong></div>
            <div><span>OOS Sharpe</span><strong>{fmt(validation.is_oos.out_of_sample.metrics.sharpe)}</strong></div>
            <div><span>OOS Net</span><strong>{fmt(validation.is_oos.out_of_sample.metrics.net_profit)}</strong></div>
            <div><span>OOS DD</span><strong>{fmt(validation.is_oos.out_of_sample.metrics.max_drawdown_pct)}%</strong></div>
            <div><span>WF pass</span><strong>{fmt(validation.walk_forward.pass_rate * 100, 0)}%</strong></div>
            <div><span>Stability</span><strong>{fmt(validation.parameter_stability.pass_rate * 100, 0)}%</strong></div>
          </div>
          <div className="ledger-paths">
            <div><span>Validation folder</span><code>{validation.artifact_folder}</code></div>
            {validation.is_oos.out_of_sample.ledger_parquet && <div><span>OOS ledger</span><code>{validation.is_oos.out_of_sample.ledger_parquet}</code></div>}
          </div>
          <button className="btn btn-secondary" onClick={useValidationOosLedger} disabled={!validation.is_oos.out_of_sample.ledger_parquet}>
            Use OOS Ledger For Robustness / MT5 Compare
          </button>

          <h3>Walk Forward</h3>
          <table className="data-table">
            <thead><tr><th>Fold</th><th>Test</th><th>PF</th><th>Sharpe</th><th>Net</th><th>DD %</th><th>Gate</th></tr></thead>
            <tbody>
              {validation.walk_forward.folds.map((fold) => (
                <tr key={fold.fold}>
                  <td>{fold.fold}</td>
                  <td>{fold.test_from.slice(0, 10)} to {fold.test_to.slice(0, 10)}</td>
                  <td>{fmt(fold.test_metrics.profit_factor)}</td>
                  <td>{fmt(fold.test_metrics.sharpe)}</td>
                  <td>{fmt(fold.test_metrics.net_profit)}</td>
                  <td>{fmt(fold.test_metrics.max_drawdown_pct)}</td>
                  <td>{fold.gate.decision}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Regime Breakdown</h3>
          <div className="merger-layout">
            {Object.entries(validation.regime_breakdown).map(([name, rows]) => (
              <section className="merger-panel" key={name}>
                <h3>{name.replace("_", " ")}</h3>
                <table className="data-table">
                  <thead><tr><th>Bucket</th><th>Trades</th><th>PF</th><th>Net</th></tr></thead>
                  <tbody>
                    {rows.slice(0, 8).map((row) => (
                      <tr key={row.bucket}>
                        <td>{row.bucket}</td>
                        <td>{fmt(row.metrics.trades, 0)}</td>
                        <td>{fmt(row.metrics.profit_factor)}</td>
                        <td>{fmt(row.metrics.net_profit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))}
          </div>
        </div>
      )}

      {result && (
        <>
          <div className="replay-kpis">
            <div><span>Trades</span><strong>{fmt(metric(result, "trades"), 0)}</strong></div>
            <div><span>Profit factor</span><strong>{fmt(metric(result, "profit_factor"))}</strong></div>
            <div><span>Net profit</span><strong>{fmt(metric(result, "net_profit"))}</strong></div>
            <div><span>Win rate</span><strong>{fmt(metric(result, "win_rate_pct"), 1)}%</strong></div>
            <div><span>Max drawdown</span><strong>{fmt(metric(result, "max_drawdown_pct"))}%</strong></div>
            <div><span>Gate</span><strong>{result.gate.decision.toUpperCase()}</strong></div>
          </div>

          <div className="ledger-paths">
            <div><span>CSV ledger</span><code>{result.ledger_csv}</code></div>
            <div><span>Parquet ledger</span><code>{result.ledger_parquet}</code></div>
          </div>

          <div className="robustness-band">
            <button className="btn btn-secondary" onClick={runRobustness}>Run Permutation Gates</button>
            <span>Uses the saved closed-trade ledger from this replay.</span>
          </div>
          <JobProgress jobId={robustnessJobId} onDone={(value) => setRobustness(value as RobustnessResult)} onError={setError} />
          {robustness && (
            <div className="robustness-result">
              <div className={`comparison-verdict ${robustness.gate.decision === "pass" ? "pass" : "block"}`}>
                <span>Local robustness gate</span><strong>{robustness.gate.decision.toUpperCase()}</strong>
              </div>
              <div className="replay-kpis robustness-kpis">
                <div><span>Permutation p</span><strong>{robustness.overall.p_value.toFixed(4)}</strong></div>
                <div><span>Z score</span><strong>{robustness.overall.z_score?.toFixed(2) ?? "-"}</strong></div>
                <div><span>Positive folds</span><strong>{robustness.walk_forward.positive_folds}/5</strong></div>
                <div><span>Significant folds</span><strong>{robustness.walk_forward.significant_folds}/5</strong></div>
              </div>
            </div>
          )}

          <div className="mc-compare-band">
            <div className="field">
              <label className="field-label">MT5 HTML report</label>
              <input className="field-input" value={mt5Report} onChange={(event) => setMt5Report(event.target.value)} placeholder="C:\path\report.htm" />
            </div>
            <button className="btn btn-secondary" onClick={compare} disabled={!mt5Report.trim()}>Compare MT5 vs Local</button>
          </div>
          <JobProgress jobId={compareJobId} onDone={(value) => setComparison(value as McCompareResult)} onError={setError} />
          {comparison && (
            <div className="comparison-result">
              <div className={`comparison-verdict ${comparison.parity.decision}`}>
                <span>Parity gate</span><strong>{comparison.parity.decision.toUpperCase()}</strong>
              </div>
              <table className="data-table">
                <thead><tr><th>Measure</th><th>Local</th><th>MT5</th><th>Delta</th></tr></thead>
                <tbody>
                  <tr><td>Closed trades</td><td>{comparison.parity.local_trades}</td><td>{comparison.parity.mt5_trades}</td><td>{comparison.parity.trade_count_delta_pct.toFixed(2)}%</td></tr>
                  <tr><td>Net profit</td><td>{comparison.parity.local_net_profit.toFixed(2)}</td><td>{comparison.parity.mt5_net_profit.toFixed(2)}</td><td>{comparison.parity.net_profit_delta_pct.toFixed(2)}%</td></tr>
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
