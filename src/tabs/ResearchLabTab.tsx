import { useEffect, useMemo, useState } from "react";
import Plot from "../components/charts/plotly";
import { listMarketDatasets, type MarketDataset } from "../api/data";
import { listReplayExperiments, runLocalReplay, runLocalRobustness, type ReplayExperiment, type ReplayResult, type RobustnessResult } from "../api/research";
import { compareMc, type McCompareResult } from "../api/mc";
import JobProgress from "../components/JobProgress";
import { useJobs } from "../state/jobs";

export default function ResearchLabTab() {
  const [datasets, setDatasets] = useState<MarketDataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [setPath, setSetPath] = useState("");
  const [symbol, setSymbol] = useState("XAUUSD");
  const [timeframe, setTimeframe] = useState("m10");
  const [role, setRole] = useState<"validation" | "walk_forward" | "lockbox">("validation");
  const [contractSize, setContractSize] = useState(100);
  const [commission, setCommission] = useState(7);
  const [slippage, setSlippage] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mt5Report, setMt5Report] = useState("");
  const [compareJobId, setCompareJobId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<McCompareResult | null>(null);
  const [robustnessJobId, setRobustnessJobId] = useState<string | null>(null);
  const [robustness, setRobustness] = useState<RobustnessResult | null>(null);
  const [recentReplays, setRecentReplays] = useState<ReplayExperiment[]>([]);
  const [recentReplayId, setRecentReplayId] = useState("");
  const setActiveJob = useJobs((state) => state.setActive);
  const job = useJobs((state) => jobId ? state.jobs[jobId] : undefined);
  const running = job?.status === "pending" || job?.status === "running";

  useEffect(() => {
    listMarketDatasets().then((items) => {
      const complete = items.filter((item) => item.state === "complete");
      setDatasets(complete);
      setDatasetId((current) => current || complete[0]?.dataset_id || "");
    }).catch((reason: unknown) => setError(String(reason)));
    const active = useJobs.getState().activeByKind.local_replay;
    if (active) setJobId(active);
    listReplayExperiments().then((items) => {
      setRecentReplays(items);
      if (items[0]?.result) {
        setRecentReplayId(items[0].id);
        setResult(items[0].result);
        setDatasetId(items[0].request.dataset_id);
        setSetPath(items[0].request.set_path);
        setSymbol(items[0].request.symbol);
        setTimeframe(items[0].request.timeframe);
        setRole(items[0].request.dataset_role);
        setContractSize(items[0].request.contract_size);
        setCommission(items[0].request.commission_per_lot_round_turn);
        setSlippage(items[0].request.slippage_points);
      }
    }).catch(() => undefined);
  }, []);

  const selected = datasets.find((item) => item.dataset_id === datasetId);
  useEffect(() => {
    if (!selected) return;
    if (!selected.symbols.includes(symbol)) setSymbol(selected.symbols[0] || "XAUUSD");
    if (!selected.timeframes.includes(timeframe)) setTimeframe(selected.timeframes[0] || "m1");
  }, [selected, symbol, timeframe]);

  const entries = useMemo(() => result?.chart.trades ?? [], [result]);
  const run = async () => {
    if (!datasetId || !setPath.trim()) {
      setError("Select a dataset and strategy .set file.");
      return;
    }
    setError(null);
    setResult(null);
    try {
      const reference = await runLocalReplay({
        dataset_id: datasetId, set_path: setPath.trim(), symbol, timeframe,
        dataset_role: role, initial_balance: 10_000, contract_size: contractSize,
        commission_per_lot_round_turn: commission, slippage_points: slippage,
        session_utc_offset: 0, chart_max_bars: 2500,
      });
      setJobId(reference.job_id);
      setActiveJob("local_replay", reference.job_id);
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

  const loadRecentReplay = (experimentId: string) => {
    setRecentReplayId(experimentId);
    const replay = recentReplays.find((item) => item.id === experimentId);
    if (!replay?.result) return;
    setResult(replay.result);
    setDatasetId(replay.request.dataset_id);
    setSetPath(replay.request.set_path);
    setSymbol(replay.request.symbol);
    setTimeframe(replay.request.timeframe);
    setRole(replay.request.dataset_role);
    setContractSize(replay.request.contract_size);
    setCommission(replay.request.commission_per_lot_round_turn);
    setSlippage(replay.request.slippage_points);
  };

  return (
    <div className="tab-content research-lab">
      <div className="tab-header"><h2>Research Lab</h2><p className="tab-subtitle">Bid/ask replay, trade inspection, and Monte Carlo ledger export.</p></div>
      {recentReplays.length > 0 && <div className="recent-replay-row"><label className="field-label">Recent replay</label><select className="field-input" value={recentReplayId} onChange={(event) => loadRecentReplay(event.target.value)}>{recentReplays.map((item) => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.request.symbol} {item.request.timeframe.toUpperCase()} · {item.result?.metrics.trades ?? 0} trades</option>)}</select></div>}
      <div className="research-controls">
        <div className="field research-dataset"><label className="field-label">Dataset</label><select className="field-input" value={datasetId} onChange={(event) => setDatasetId(event.target.value)} disabled={running}>{datasets.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.dataset_id}</option>)}</select></div>
        <div className="field research-set"><label className="field-label">Strategy .set</label><input className="field-input" value={setPath} onChange={(event) => setSetPath(event.target.value)} placeholder="C:\path\strategy.set" disabled={running} /></div>
        <div className="field"><label className="field-label">Symbol</label><select className="field-input" value={symbol} onChange={(event) => setSymbol(event.target.value)} disabled={running}>{selected?.symbols.map((value) => <option key={value}>{value}</option>)}</select></div>
        <div className="field"><label className="field-label">Timeframe</label><select className="field-input" value={timeframe} onChange={(event) => setTimeframe(event.target.value)} disabled={running}>{selected?.timeframes.map((value) => <option key={value} value={value}>{value.toUpperCase()}</option>)}</select></div>
        <div className="field"><label className="field-label">Role</label><select className="field-input" value={role} onChange={(event) => setRole(event.target.value as typeof role)} disabled={running}><option value="validation">Validation</option><option value="walk_forward">Walk forward</option><option value="lockbox">Lockbox</option></select></div>
      </div>
      <div className="research-costs">
        <label>Contract size <input className="field-input field-sm" type="number" value={contractSize} onChange={(event) => setContractSize(Number(event.target.value))} /></label>
        <label>Commission / lot <input className="field-input field-sm" type="number" value={commission} onChange={(event) => setCommission(Number(event.target.value))} /></label>
        <label>Slippage points <input className="field-input field-sm" type="number" value={slippage} onChange={(event) => setSlippage(Number(event.target.value))} /></label>
        <button className="btn btn-primary" onClick={run} disabled={running || !datasetId}>Run Replay</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <JobProgress jobId={jobId} onDone={(value) => setResult(value as ReplayResult)} onError={setError} />

      {result && <>
        <div className="replay-kpis">
          <div><span>Trades</span><strong>{result.metrics.trades}</strong></div>
          <div><span>Profit factor</span><strong>{result.metrics.profit_factor?.toFixed(2) ?? "-"}</strong></div>
          <div><span>Net profit</span><strong>{result.metrics.net_profit.toFixed(2)}</strong></div>
          <div><span>Win rate</span><strong>{result.metrics.win_rate_pct?.toFixed(1) ?? "-"}%</strong></div>
          <div><span>Max drawdown</span><strong>{result.metrics.max_drawdown_pct.toFixed(2)}%</strong></div>
        </div>
        <div className="replay-chart">
          <Plot
            data={[
              { type: "candlestick", x: result.chart.bars.map((bar) => bar.time), open: result.chart.bars.map((bar) => bar.open), high: result.chart.bars.map((bar) => bar.high), low: result.chart.bars.map((bar) => bar.low), close: result.chart.bars.map((bar) => bar.close), name: symbol },
              { type: "scatter", mode: "markers", x: entries.map((trade) => trade.entry_time), y: entries.map((trade) => trade.entry_price), marker: { size: 7, color: entries.map((trade) => trade.direction === "long" ? "#2da44e" : "#cf222e"), symbol: entries.map((trade) => trade.direction === "long" ? "triangle-up" : "triangle-down") }, name: "Entries" },
              { type: "scatter", mode: "markers", x: entries.map((trade) => trade.exit_time), y: entries.map((trade) => trade.exit_price), marker: { size: 6, color: entries.map((trade) => trade.net_pnl >= 0 ? "#58a6ff" : "#d29922"), symbol: "x" }, name: "Exits" },
            ]}
            layout={{ autosize: true, height: 520, paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: { color: "#8b949e" }, margin: { l: 52, r: 18, t: 20, b: 40 }, xaxis: { rangeslider: { visible: false }, gridcolor: "#30363d" }, yaxis: { gridcolor: "#30363d" }, showlegend: true }}
            config={{ responsive: true, displaylogo: false }}
            style={{ width: "100%" }}
          />
        </div>
        <div className="ledger-paths"><div><span>CSV ledger</span><code>{result.ledger_csv}</code></div><div><span>Parquet ledger</span><code>{result.ledger_parquet}</code></div></div>
        <div className="robustness-band"><button className="btn btn-secondary" onClick={runRobustness}>Run Permutation Gates</button><span>5,000 block permutations, 5 chronological folds, seed 42</span></div>
        <JobProgress jobId={robustnessJobId} onDone={(value) => setRobustness(value as RobustnessResult)} onError={setError} />
        {robustness && <div className="robustness-result">
          <div className={`comparison-verdict ${robustness.gate.decision === "pass" ? "pass" : "block"}`}><span>Local robustness gate</span><strong>{robustness.gate.decision.toUpperCase()}</strong></div>
          <div className="replay-kpis robustness-kpis"><div><span>Permutation p</span><strong>{robustness.overall.p_value.toFixed(4)}</strong></div><div><span>Z score</span><strong>{robustness.overall.z_score?.toFixed(2) ?? "-"}</strong></div><div><span>Positive folds</span><strong>{robustness.walk_forward.positive_folds}/5</strong></div><div><span>Significant folds</span><strong>{robustness.walk_forward.significant_folds}/5</strong></div></div>
          <table className="data-table"><thead><tr><th>Fold</th><th>Trades</th><th>Net</th><th>PF</th><th>Permutation p</th></tr></thead><tbody>{robustness.walk_forward.folds.map((fold) => <tr key={fold.fold}><td>{fold.fold}</td><td>{fold.trades}</td><td>{fold.net_profit.toFixed(2)}</td><td>{fold.profit_factor?.toFixed(2) ?? "-"}</td><td>{fold.permutation_p_value.toFixed(4)}</td></tr>)}</tbody></table>
        </div>}
        <div className="mc-compare-band">
          <div className="field"><label className="field-label">MT5 HTML report</label><input className="field-input" value={mt5Report} onChange={(event) => setMt5Report(event.target.value)} placeholder="C:\path\report.htm" /></div>
          <button className="btn btn-secondary" onClick={compare} disabled={!mt5Report.trim() || !!compareJobId && useJobs.getState().jobs[compareJobId]?.status === "running"}>Compare Monte Carlo</button>
        </div>
        <JobProgress jobId={compareJobId} onDone={(value) => setComparison(value as McCompareResult)} onError={setError} />
        {comparison && <div className="comparison-result">
          <div className={`comparison-verdict ${comparison.parity.decision}`}><span>Parity gate</span><strong>{comparison.parity.decision.toUpperCase()}</strong></div>
          <table className="data-table"><thead><tr><th>Measure</th><th>Local</th><th>MT5</th><th>Delta</th></tr></thead><tbody>
            <tr><td>Closed trades</td><td>{comparison.parity.local_trades}</td><td>{comparison.parity.mt5_trades}</td><td>{comparison.parity.trade_count_delta_pct.toFixed(2)}%</td></tr>
            <tr><td>Net profit</td><td>{comparison.parity.local_net_profit.toFixed(2)}</td><td>{comparison.parity.mt5_net_profit.toFixed(2)}</td><td>{comparison.parity.net_profit_delta_pct.toFixed(2)}%</td></tr>
            {Object.keys(comparison.headlines.local).map((key) => <tr key={key}><td>{key.replaceAll("_", " ")}</td><td>{comparison.headlines.local[key]?.toFixed(3) ?? "-"}</td><td>{comparison.headlines.mt5[key]?.toFixed(3) ?? "-"}</td><td>{comparison.headlines.delta[key]?.toFixed(3) ?? "-"}</td></tr>)}
          </tbody></table>
        </div>}
        {result.warnings.map((warning) => <div className="alert" key={warning}>{warning}</div>)}
      </>}
    </div>
  );
}
