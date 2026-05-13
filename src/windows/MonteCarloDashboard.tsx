/**
 * Monte Carlo dashboard window — all four phases in one window with charts.
 *
 * Loaded when URL has ?window=mc-dashboard&jobId=<jobId>.
 *
 * Mirrors the legacy `build_dashboard()` from mc_funded_test.py:
 *   • Phase 1 (Challenge):    KPI table + equity fan + pass donut + fail bar + days hist
 *   • Phase 2 (Verification): same five plus an evaluation funnel
 *   • Funded:                 KPI + fan w/ floor + survival + breach donut + earnings hist
 *                              + earnings-vs-payouts scatter + payout count bar
 *                              + first payout hist + breach day hist + breach reasons bar
 *   • Long-term:              KPI + equity fan w/ benchmark + max DD histogram
 *
 * All charts pull palette from CSS variables so they re-theme with the app.
 */

import { useEffect, useMemo, useState } from "react";
import Plot from "../components/charts/plotly";
import ChartErrorBoundary from "../components/charts/ChartErrorBoundary";
import type { Data, Layout } from "plotly.js";

import { getMcResults } from "../api/mc";
import type {
  AllPhasesResult,
  EvalPhaseResult,
  FundedResult,
  JobRef,
  LongtermResult,
  RegimeData,
} from "../api/mc";
import {
  baseLayout,
  bandColors,
  PLOT_CONFIG,
  percentilesPerDay,
  readTheme,
  type ChartTheme,
} from "../components/charts/mcDashboard/theme";
import { useSettings } from "../state/settings";

type PhaseId = "phase1" | "phase2" | "funded" | "longterm";

const PHASE_LABELS: Record<PhaseId, string> = {
  phase1:   "Phase 1 — Challenge",
  phase2:   "Phase 2 — Verification",
  funded:   "Funded Account",
  longterm: "Long-term",
};

export default function MonteCarloDashboard() {
  const params = new URLSearchParams(window.location.search);
  const jobId  = params.get("jobId") ?? "";

  const loadSettings = useSettings((s) => s.load);
  useEffect(() => { loadSettings(); }, [loadSettings]);

  const [job,   setJob]   = useState<JobRef | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<PhaseId>("phase1");
  const [disclaimerDismissed, setDisclaimerDismissed] = useState<boolean>(() => {
    try { return localStorage.getItem("mc-disclaimer-dismissed") === "1"; }
    catch { return false; }
  });
  const dismissDisclaimer = () => {
    try { localStorage.setItem("mc-disclaimer-dismissed", "1"); } catch { /* ignore */ }
    setDisclaimerDismissed(true);
  };

  // Force chart re-render when CSS theme changes by tracking the data-theme attr.
  const [themeTick, setThemeTick] = useState(0);
  useEffect(() => {
    const obs = new MutationObserver(() => setThemeTick((n) => n + 1));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  useEffect(() => {
    if (!jobId) { setError("No jobId in URL."); return; }
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      try {
        const r = await getMcResults(jobId);
        if (cancelled) return;
        setJob(r);
        if (r.status === "done" || r.status === "failed" || r.status === "cancelled") {
          if (timer) clearInterval(timer);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        if (timer) clearInterval(timer);
      }
    };

    poll();
    timer = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [jobId]);

  const result = job?.result as AllPhasesResult | null | undefined;

  return (
    <div className="mc-dash">
      <div className="mc-dash-header">
        <div>
          <h1>Monte Carlo Dashboard</h1>
          <p className="mc-dash-sub">
            Bootstrap resampling — single job, four phases sharing the same random paths
          </p>
        </div>
        {jobId && <span className="job-id-badge">{jobId.slice(0, 8)}</span>}
      </div>

      {!disclaimerDismissed && (() => {
        // Hide automatically when the user has tightened the safety margin —
        // factor < 1.0 means they've already opted into intraday-aware DD.
        const factor = (result?.verdict?.global?.intraday_dd_factor
                     ?? (result as Record<string, any> | null | undefined)?.intraday_dd_factor
                     ?? 1.0) as number;
        if (factor < 1.0) return null;
        return (
          <div className="mc-disclaimer" role="note">
            <button
              type="button"
              className="mc-disclaimer-close"
              aria-label="Dismiss disclaimer"
              onClick={dismissDisclaimer}
            >
              ×
            </button>
            <span className="mc-disclaimer-prefix">⚠ Simulation Limitation:</span>{" "}
            Daily loss is checked at end-of-day only. Real prop firms check intraday
            floating drawdown on open positions. Results may underestimate breach risk
            by 20–40% vs live trading. Use the <strong>Intraday DD Factor</strong>{" "}
            parameter (currently 1.0 = off) to add a safety margin.
          </div>
        );
      })()}

      {error && <div className="alert alert-error">{error}</div>}
      {!job && !error && <p className="results-loading">Fetching results…</p>}
      {(job?.status === "pending" || job?.status === "running") && (
        <p className="results-loading">Simulation running — this window will update automatically.</p>
      )}
      {job?.status === "failed" && (
        <div className="alert alert-error">Run failed: {job.error}</div>
      )}

      {job?.status === "done" && result && (
        <>
          <WarningsPanel result={result as unknown as Record<string, any>} />

          <div className="mc-dash-tabs">
            {(["phase1", "phase2", "funded", "longterm"] as PhaseId[]).map((p) => (
              <button key={p}
                className={`mc-dash-tab${active === p ? " active" : ""}`}
                onClick={() => setActive(p)}>
                {PHASE_LABELS[p]}
              </button>
            ))}
          </div>

          <div className="mc-dash-panel" key={`${active}-${themeTick}`}>
            <ChartErrorBoundary label={PHASE_LABELS[active]}>
              {active === "phase1"   && <Phase1Panel data={result.phase1} regime={result.regime} extras={result as unknown as Record<string, any>} />}
              {active === "phase2"   && <Phase2Panel data={result.phase2} extras={result as unknown as Record<string, any>} />}
              {active === "funded"   && <FundedPanel data={result.funded} extras={result as unknown as Record<string, any>} />}
              {active === "longterm" && <LongtermPanel data={result.longterm} extras={result as unknown as Record<string, any>} />}
            </ChartErrorBoundary>
          </div>
        </>
      )}

      {job?.status === "done" && !result && (
        <div className="alert alert-warn">
          Run completed but returned no data. Check backend logs.
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  PANELS — one per phase tab
// ────────────────────────────────────────────────────────────────────────────

function Phase1Panel({ data, regime, extras }: { data: EvalPhaseResult; regime: RegimeData | null; extras: Record<string, any> }) {
  const t = readTheme();
  const verdict = (extras?.verdict ?? {}) as Record<string, any>;
  const phase1V = (verdict.phase1 ?? null) as Record<string, any> | null;
  const globalV = (verdict.global ?? null) as Record<string, any> | null;
  const lotSweep = (extras?.lot_sweep ?? null) as Array<Record<string, any>> | null;

  const ci = phase1V ? wilsonCi(phase1V.pass_rate, phase1V.pass_rate_ci_low, phase1V.pass_rate_ci_high) : null;

  const baseCells: KpiCell[] = [
    { label: "Account",        value: usd(data.balance) },
    { label: "Profit Target",  value: pctOf(data.profit_pct, data.balance) },
    { label: "Max Daily Loss", value: pctOf(data.daily_dd_pct, data.balance) + " — daily reset" },
    { label: "Max Loss",       value: pctOf(data.total_dd_pct, data.balance) + " — fixed floor" },
    { label: "Min Days",       value: String(data.min_days) },
    { label: "Pass Rate",      value: pct(data.pass_rate),
      tone: data.pass_rate >= 50 ? "good" : "bad" },
    { label: "Avg Days",       value: data.avg_days > 0 ? data.avg_days.toFixed(1) + "d" : "N/A" },
  ];
  if (globalV && globalV.kelly_fraction != null) {
    const kv = String(globalV.kelly_verdict ?? "").toLowerCase();
    const tone: KpiCell["tone"] =
      kv.includes("negative") ? "bad" :
      kv.includes("near optimal") || kv.includes("optimal") ? "good" :
      kv.includes("aggressive") ? "alt" : undefined;
    baseCells.push({
      label: "Kelly Fraction",
      value: Number(globalV.kelly_fraction).toFixed(3),
      tone,
    });
  }

  return (
    <>
      <VerdictBlock phase="phase1" v={phase1V} globalV={globalV} />
      <KpiStrip cells={baseCells} />

      <ChartCard span="full">
        <EquityFan
          curves={data.equity_curves}
          balance={data.balance}
          targetPct={data.profit_pct}
          totalDdPct={data.total_dd_pct}
          dailyDdPct={data.daily_dd_pct}
          breachMask={(data.results_df.records as Array<{ passed: boolean }>).map((r) => !r.passed)}
          theme={t}
          title="Phase 1 — Challenge Equity Fan"
        />
      </ChartCard>

      <div className="mc-dash-grid">
        <ChartCard>
          <PassDonut
            passRate={data.pass_rate}
            nPassed={data.n_passed}
            nFailed={data.n_failed}
            title="Challenge Pass Rate"
            theme={t}
            ci={ci}
          />
        </ChartCard>
        <ChartCard><FailReasonsBar pcts={data.fail_pcts} title="Challenge — Fail Reasons" theme={t} /></ChartCard>
      </div>

      <ChartCard span="full">
        <DaysHistogram
          records={data.results_df.records as Array<{ passed?: boolean; days?: number }>}
          avgDays={data.avg_days}
          minDays={data.min_days}
          title="Challenge — Days to Pass"
          theme={t}
        />
      </ChartCard>

      {lotSweep && lotSweep.length > 0 && (
        <ChartCard span="full">
          <LotSweepChart sweep={lotSweep} theme={t} />
        </ChartCard>
      )}

      {regime && (
        <ChartCard span="full">
          <RegimeHeatmap regime={regime} theme={t} />
        </ChartCard>
      )}
    </>
  );
}

function Phase2Panel({ data, extras }: { data: EvalPhaseResult; extras: Record<string, any> }) {
  const t = readTheme();
  const nP1 = data.n_p1_passed ?? data.n_passed + data.n_failed;
  const nTotal = data.combined_pass_rate
    ? Math.round(data.n_passed / (data.combined_pass_rate / 100))
    : nP1;

  const verdict = (extras?.verdict ?? {}) as Record<string, any>;
  const phase2V = (verdict.phase2 ?? null) as Record<string, any> | null;
  const globalV = (verdict.global ?? null) as Record<string, any> | null;
  const ci = phase2V ? wilsonCi(phase2V.pass_rate, phase2V.pass_rate_ci_low, phase2V.pass_rate_ci_high) : null;

  return (
    <>
      <VerdictBlock phase="phase2" v={phase2V} globalV={globalV} />
      <KpiStrip
        cells={[
          { label: "Account",        value: usd(data.balance) },
          { label: "Profit Target",  value: pctOf(data.profit_pct, data.balance) },
          { label: "Max Daily Loss", value: pctOf(data.daily_dd_pct, data.balance) },
          { label: "Max Loss",       value: pctOf(data.total_dd_pct, data.balance) },
          { label: "Min Days",       value: String(data.min_days) },
          { label: "P2 Pass Rate",   value: pct(data.pass_rate),
            tone: data.pass_rate >= 50 ? "good" : "bad" },
          { label: "Combined",       value: pct(data.combined_pass_rate ?? 0),
            tone: (data.combined_pass_rate ?? 0) >= 20 ? "good" : "alt" },
        ]}
      />

      <ChartCard span="full">
        <EquityFan
          curves={data.equity_curves}
          balance={data.balance}
          targetPct={data.profit_pct}
          totalDdPct={data.total_dd_pct}
          dailyDdPct={data.daily_dd_pct}
          breachMask={(data.results_df.records as Array<{ passed: boolean }>).map((r) => !r.passed)}
          theme={t}
          title="Phase 2 — Verification Equity Fan"
        />
      </ChartCard>

      <div className="mc-dash-grid">
        <ChartCard>
          <PassDonut
            passRate={data.pass_rate}
            nPassed={data.n_passed}
            nFailed={data.n_failed}
            title="Verification Pass Rate (of P1 passers)"
            theme={t}
            ci={ci}
          />
        </ChartCard>
        <ChartCard><FailReasonsBar pcts={data.fail_pcts} title="Verification — Fail Reasons" theme={t} /></ChartCard>
      </div>

      <ChartCard span="full">
        <DaysHistogram
          records={data.results_df.records as Array<{ passed?: boolean; days?: number }>}
          avgDays={data.avg_days}
          minDays={data.min_days}
          title="Verification — Days to Pass"
          theme={t}
        />
      </ChartCard>

      <ChartCard span="full">
        <Funnel nTotal={nTotal} nP1={nP1} nP2={data.n_passed} theme={t} />
      </ChartCard>
    </>
  );
}

function FundedPanel({ data, extras }: { data: FundedResult; extras: Record<string, any> }) {
  const t = readTheme();
  const records = data.results_df.records as Array<{
    payout_count: number;
    total_earnings: number;
    breach_day: number | null;
    first_payout_day: number | null;
    days_active: number;
  }>;

  const verdict = (extras?.verdict ?? {}) as Record<string, any>;
  const fundedV = (verdict.funded ?? null) as Record<string, any> | null;
  const globalV = (verdict.global ?? null) as Record<string, any> | null;
  const cadenceSweep = (extras?.payout_cadence_sweep ?? null) as Array<Record<string, any>> | null;

  const cells: KpiCell[] = [
    { label: "Account",         value: usd(data.balance) },
    { label: "Max Daily Loss",  value: pctOf(data.daily_dd_pct, data.balance) },
    { label: "Max Loss",        value: pctOf(data.total_dd_pct, data.balance) },
    { label: "≥1 Payout Rate",  value: pct(data.payout_rate),
      tone: data.payout_rate >= 50 ? "good" : "bad" },
    { label: "Breach Rate",     value: pct(data.breach_rate),
      tone: data.breach_rate > 50 ? "bad" : "alt" },
    { label: "Avg Earnings",    value: usd(data.avg_total_earnings),
      tone: data.avg_total_earnings > 0 ? "good" : "bad" },
    { label: "Avg Payouts",     value: data.avg_payout_count.toFixed(2) },
    { label: "First Payout",    value: data.avg_first_payout_day > 0
      ? data.avg_first_payout_day.toFixed(1) + "d" : "N/A" },
  ];

  if (fundedV?.expected_monthly_usd != null) {
    cells.push({
      label: "Expected $/month",
      value: usd(Number(fundedV.expected_monthly_usd)),
      tone: Number(fundedV.expected_monthly_usd) > 0 ? "good" : "bad",
    });
  }
  if (fundedV?.expected_lifetime_months != null) {
    cells.push({
      label: "Expected Lifetime",
      value: `${Number(fundedV.expected_lifetime_months).toFixed(1)} mo`,
    });
  }
  if (globalV?.roi_pass_rate != null) {
    const fee = Number(globalV.challenge_fee ?? 0);
    cells.push({
      label: "ROI vs Fee" + (fee > 0 ? ` (fee: $${fee.toFixed(0)})` : ""),
      value: pct(Number(globalV.roi_pass_rate)),
      tone: Number(globalV.roi_pass_rate) >= 100 ? "good" : "bad",
    });
  }
  // Avg $ per actual payout (skips sims that never paid out). Different from
  // avg_total_earnings — this is "average size of a single payout when one happens".
  const avgPerPayout = (data as Record<string, any>).avg_earnings_per_payout;
  if (avgPerPayout != null) {
    cells.push({
      label: "Avg $/Payout",
      value: usd(Number(avgPerPayout)),
      tone: Number(avgPerPayout) > 0 ? "good" : undefined,
    });
  }

  return (
    <>
      <VerdictBlock phase="funded" v={fundedV} globalV={globalV} />
      <KpiStrip cells={cells} />

      <ChartCard span="full">
        <EquityFan
          curves={data.equity_curves}
          balance={data.balance}
          totalDdPct={data.total_dd_pct}
          dailyDdPct={data.daily_dd_pct}
          theme={t}
          title="Funded — Equity Fan"
        />
      </ChartCard>

      <ChartCard span="full">
        <SurvivalCurve survival={data.survival ?? []} theme={t} />
      </ChartCard>

      <div className="mc-dash-grid">
        <ChartCard><BreachDonut breachRate={data.breach_rate} theme={t} /></ChartCard>
        <ChartCard><EarningsHistogram records={records} avg={data.avg_total_earnings} theme={t} /></ChartCard>
      </div>

      <ChartCard span="full">
        <EarningsVsPayouts records={records}
                           byCount={(data as Record<string, any>).earnings_by_payout_count}
                           theme={t} />
      </ChartCard>

      <div className="mc-dash-grid mc-dash-grid-3">
        <ChartCard><PayoutCountBar records={records} theme={t} /></ChartCard>
        <ChartCard><FirstPayoutHistogram records={records} theme={t} /></ChartCard>
        <ChartCard><BreachDayHistogram records={records} theme={t} /></ChartCard>
      </div>

      <div className="mc-dash-grid">
        <ChartCard><FailReasonsBar pcts={data.breach_pcts} title="Funded — Breach Reasons" theme={t} /></ChartCard>
      </div>

      {cadenceSweep && cadenceSweep.length > 0 && (
        <ChartCard span="full">
          <PayoutCadenceChart sweep={cadenceSweep} theme={t} />
        </ChartCard>
      )}
    </>
  );
}

function LongtermPanel({ data, extras }: { data: LongtermResult; extras: Record<string, any> }) {
  const t = readTheme();
  const ruinPct = (data.balance - data.ruin_floor) / data.balance;
  const survivalRate = data.pass_rate * 100;
  const bm = data.benchmark;

  const kpiCells: KpiCell[] = [
    { label: "Horizon",            value: `${data.n_days} days (~${(data.n_days / 252).toFixed(1)}y)` },
    { label: "Sims",               value: String(data.final_equity.length) },
    { label: "Survival",           value: pct(survivalRate),
      tone: survivalRate >= 90 ? "good" : "bad" },
    { label: "Median Final Equity", value: usd(data.median_equity),
      tone: data.median_equity >= data.balance ? "good" : "bad" },
    { label: "P10 / P90 Equity",   value: `${usd(data.p10_equity)} · ${usd(data.p90_equity)}` },
    { label: "Median Max DD",      value: pct(data.median_max_dd * 100) },
    { label: "Median Sharpe",      value: data.median_sharpe.toFixed(2),
      tone: data.median_sharpe > 1 ? "good" : data.median_sharpe > 0 ? "alt" : "bad" },
    { label: "Annualised Return",  value: pct(data.annualized_return * 100) },
  ];
  if (bm && !bm.error) {
    kpiCells.push({
      label: `B&H ${bm.ticker}`,
      value: `${pct((bm.annualized_return ?? 0) * 100)} ann.`,
    });
  }

  const verdict = (extras?.verdict ?? {}) as Record<string, any>;
  const longV = (verdict.longterm ?? null) as Record<string, any> | null;
  const globalV = (verdict.global ?? null) as Record<string, any> | null;
  const ruinHorizons = (extras?.ruin_horizons ?? null) as Array<Record<string, any>> | null;

  return (
    <>
      <VerdictBlock phase="longterm" v={longV} globalV={globalV} />
      <KpiStrip cells={kpiCells} />

      {bm?.error && (
        <div className="alert alert-warn" style={{ marginTop: 8 }}>
          Benchmark error: {bm.error}
        </div>
      )}

      <ChartCard span="full">
        <LongtermEquityFan
          paths={data.equity_paths}
          balance={data.balance}
          ruinFloor={data.ruin_floor}
          ruinPct={ruinPct}
          theme={t}
          benchmark={bm && !bm.error ? bm : null}
        />
      </ChartCard>

      <ChartCard span="full">
        <MaxDdHistogram maxDd={data.max_dd} theme={t} />
      </ChartCard>

      {ruinHorizons && ruinHorizons.length > 0 && (
        <ChartCard span="full">
          <RuinHorizonChart horizons={ruinHorizons} theme={t} />
        </ChartCard>
      )}
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  CHART COMPONENTS
// ────────────────────────────────────────────────────────────────────────────

interface KpiCell {
  label: string;
  value: string;
  tone?: "good" | "bad" | "alt";
}

function KpiStrip({ cells }: { cells: KpiCell[] }) {
  return (
    <div className="mc-kpi-strip">
      {cells.map((c, i) => (
        <div key={i} className="mc-kpi-cell">
          <span className="mc-kpi-label">{c.label}</span>
          <span className={`mc-kpi-value mc-kpi-tone-${c.tone ?? "neutral"}`}>{c.value}</span>
        </div>
      ))}
    </div>
  );
}

function ChartCard({ children, span }: { children: React.ReactNode; span?: "full" }) {
  return <div className={`mc-chart-card${span === "full" ? " mc-chart-card-full" : ""}`}>{children}</div>;
}

// ── Equity fan ─────────────────────────────────────────────────────────────

interface EquityFanProps {
  curves?: number[][];
  balance: number;
  targetPct?: number;
  totalDdPct?: number;
  dailyDdPct?: number;
  breachMask?: boolean[];
  theme: ChartTheme;
  title: string;
}

function EquityFan(props: EquityFanProps) {
  const { curves, balance, targetPct, totalDdPct, dailyDdPct, breachMask, theme: t, title } = props;
  const [showSims, setShowSims] = useState(false);
  const layout = useMemo(() => {
    const l = baseLayout(t, title);
    return {
      ...l,
      xaxis: { ...l.xaxis, title: { text: "Trading Day #", font: { color: t.text2 } } },
      yaxis: { ...l.yaxis, title: { text: "Account Equity ($)", font: { color: t.text2 } }, rangemode: "nonnegative" },
      hovermode: "x unified",
      height: 380,
    } as Partial<Layout>;
  }, [t, title]);

  const safeCurves = curves ?? [];
  const nDays = safeCurves[0]?.length ?? 0;
  const x     = useMemo(() => Array.from({ length: nDays }, (_, i) => i), [nDays]);
  const pcts  = useMemo(
    () => (safeCurves.length === 0 ? null : percentilesPerDay(safeCurves, [5, 25, 50, 75, 95])),
    [safeCurves],
  );

  if (!curves || curves.length === 0 || !pcts) {
    return <EmptyChart label="No equity-curve data returned" theme={t} />;
  }

  const cols  = bandColors(t);
  const traces: Data[] = [];

  // Sample sims — controlled by the Show/Hide Sims React button above the chart.
  // Cap at 60 paths for render performance; bands convey the distribution anyway.
  const SAMPLE = 60;
  const idxes  = curves.length <= SAMPLE
    ? curves.map((_, i) => i)
    : Array.from({ length: SAMPLE }, (_, i) => Math.floor((i / SAMPLE) * curves.length));
  for (let i = 0; i < idxes.length; i++) {
    const idx = idxes[i];
    const breached = breachMask?.[idx] ?? false;
    traces.push({
      x, y: curves[idx], type: "scatter", mode: "lines",
      line: { color: breached ? hexA(t.fail, 0.55) : cols.sample, width: 1 },
      hoverinfo: "skip",
      showlegend: i === 0 && showSims,
      name: "Simulations",
      legendgroup: "sims",
      visible: showSims,
    } as Data);
  }

  // Bands (5–95, 25–75)
  traces.push({
    x: [...x, ...x.slice().reverse()],
    y: [...pcts[95], ...pcts[5].slice().reverse()],
    fill: "toself", fillcolor: cols.fillOuter, line: { color: "rgba(0,0,0,0)" },
    name: "P5–P95", hoverinfo: "skip", type: "scatter",
  } as Data);
  traces.push({
    x: [...x, ...x.slice().reverse()],
    y: [...pcts[75], ...pcts[25].slice().reverse()],
    fill: "toself", fillcolor: cols.fillInner, line: { color: "rgba(0,0,0,0)" },
    name: "P25–P75", hoverinfo: "skip", type: "scatter",
  } as Data);

  // Percentile lines
  ([
    { p: 5,  c: cols.p5,  d: "dot"   },
    { p: 25, c: cols.p25, d: "dash"  },
    { p: 50, c: cols.p50, d: "solid" },
    { p: 75, c: cols.p75, d: "dash"  },
    { p: 95, c: cols.p95, d: "dot"   },
  ] as const).forEach(({ p, c, d }) => {
    traces.push({
      x, y: pcts[p], type: "scatter", mode: "lines",
      line: { color: c, dash: d, width: 1.5 }, name: `P${p}`,
    } as Data);
  });

  // Reference lines
  const shapes: NonNullable<Layout["shapes"]> = [];
  const annotations: NonNullable<Layout["annotations"]> = [];
  const refLine = (y: number, color: string, dash: "dash" | "dot", label: string) => {
    shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: y, y1: y,
                  line: { color, dash, width: 1.5 } });
    annotations.push({ xref: "paper", x: 1, y, xanchor: "right", yanchor: "bottom",
                       text: label, font: { color, size: 11 }, showarrow: false });
  };
  refLine(balance, hexA(t.text, 0.7), "dash", `Start: $${Math.round(balance).toLocaleString()}`);
  if (targetPct != null) {
    const tv = balance * (1 + targetPct);
    refLine(tv, t.pass, "dash", `Target +${Math.round(targetPct * 100)}%: $${Math.round(tv).toLocaleString()}`);
  }
  if (totalDdPct != null) {
    const fv = balance * (1 - totalDdPct);
    refLine(fv, t.fail, "dot", `Max Loss floor: $${Math.round(fv).toLocaleString()}`);
  }
  if (dailyDdPct != null) {
    const dv = balance - balance * dailyDdPct;
    refLine(dv, t.alt, "dot", `Daily floor (start): $${Math.round(dv).toLocaleString()}`);
  }

  return (
    <FanWrapper showSims={showSims} setShowSims={setShowSims}>
      <Plot data={traces}
        layout={{ ...layout, shapes, annotations } as Partial<Layout>}
        config={PLOT_CONFIG}
        style={{ width: "100%" }}
        useResizeHandler
      />
    </FanWrapper>
  );
}

// ── Show/Hide Sims toggle (shared by both equity fans) ────────────────────

function FanWrapper({
  children, showSims, setShowSims,
}: {
  children: React.ReactNode;
  showSims: boolean;
  setShowSims: (v: boolean) => void;
}) {
  return (
    <div className="mc-fan-wrap">
      <div className="mc-fan-toolbar">
        <button
          type="button"
          className={`mc-fan-toggle${showSims ? " active" : ""}`}
          onClick={() => setShowSims(!showSims)}
          title={showSims ? "Hide individual simulation paths" : "Show individual simulation paths"}
        >
          {showSims ? "◉ Hide Sims" : "○ Show Sims"}
        </button>
      </div>
      {children}
    </div>
  );
}

// ── Long-term equity fan (with optional benchmark line) ───────────────────

function LongtermEquityFan({ paths, balance, ruinFloor, ruinPct, theme: t, benchmark }: {
  paths: number[][]; balance: number; ruinFloor: number; ruinPct: number;
  theme: ChartTheme;
  benchmark: { ticker?: string; final_equity?: number; annualized_return?: number } | null;
}) {
  const [showSims, setShowSims] = useState(false);
  const layout = useMemo(() => {
    const l = baseLayout(t, `Long-term — ${paths[0]?.length ?? 0}-Day Equity Fan`);
    return {
      ...l,
      xaxis: { ...l.xaxis, title: { text: "Trading Day #", font: { color: t.text2 } } },
      yaxis: { ...l.yaxis, title: { text: "Account Equity ($)", font: { color: t.text2 } }, rangemode: "nonnegative" },
      hovermode: "x unified",
      height: 420,
    } as Partial<Layout>;
  }, [t, paths]);

  if (!paths || paths.length === 0) {
    return <EmptyChart label="No equity-path data returned" theme={t} />;
  }

  const nDays = paths[0].length;
  const x     = Array.from({ length: nDays }, (_, i) => i);
  const pcts  = useMemo(() => percentilesPerDay(paths, [5, 25, 50, 75, 95]), [paths]);
  const cols  = bandColors(t);

  const traces: Data[] = [];
  const SAMPLE = 60;
  const idxes  = paths.length <= SAMPLE
    ? paths.map((_, i) => i)
    : Array.from({ length: SAMPLE }, (_, i) => Math.floor((i / SAMPLE) * paths.length));
  idxes.forEach((idx, i) => {
    traces.push({
      x, y: paths[idx], type: "scatter", mode: "lines",
      line: { color: cols.sample, width: 0.8 },
      hoverinfo: "skip", showlegend: i === 0 && showSims,
      name: "Simulations", legendgroup: "sims",
      visible: showSims,
    } as Data);
  });
  traces.push({
    x: [...x, ...x.slice().reverse()],
    y: [...pcts[95], ...pcts[5].slice().reverse()],
    fill: "toself", fillcolor: cols.fillOuter, line: { color: "rgba(0,0,0,0)" },
    name: "P5–P95", hoverinfo: "skip", type: "scatter",
  } as Data);
  traces.push({
    x: [...x, ...x.slice().reverse()],
    y: [...pcts[75], ...pcts[25].slice().reverse()],
    fill: "toself", fillcolor: cols.fillInner, line: { color: "rgba(0,0,0,0)" },
    name: "P25–P75", hoverinfo: "skip", type: "scatter",
  } as Data);
  ([
    { p: 5,  c: cols.p5,  d: "dot"   },
    { p: 25, c: cols.p25, d: "dash"  },
    { p: 50, c: cols.p50, d: "solid" },
    { p: 75, c: cols.p75, d: "dash"  },
    { p: 95, c: cols.p95, d: "dot"   },
  ] as const).forEach(({ p, c, d }) => {
    traces.push({
      x, y: pcts[p], type: "scatter", mode: "lines",
      line: { color: c, dash: d, width: 1.5 }, name: `P${p}`,
    } as Data);
  });

  // Synthetic linear "Buy & Hold" line — endpoint matches benchmark final equity.
  if (benchmark?.final_equity != null) {
    const endVal = benchmark.final_equity;
    const slope  = (endVal - balance) / Math.max(nDays - 1, 1);
    const bmY    = x.map((d) => balance + slope * d);
    traces.push({
      x, y: bmY, type: "scatter", mode: "lines",
      line: { color: t.warn, width: 2.5 },
      name: `Buy & Hold ${benchmark.ticker ?? ""}`,
    } as Data);
  }

  const shapes: NonNullable<Layout["shapes"]> = [];
  const annotations: NonNullable<Layout["annotations"]> = [];
  shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: balance, y1: balance,
                line: { color: hexA(t.text, 0.7), dash: "dash", width: 1.0 } });
  annotations.push({ xref: "paper", x: 1, y: balance, xanchor: "right", yanchor: "bottom",
                     text: `Start: $${Math.round(balance).toLocaleString()}`,
                     font: { color: hexA(t.text, 0.7), size: 11 }, showarrow: false });
  shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: ruinFloor, y1: ruinFloor,
                line: { color: t.fail, dash: "dot", width: 1.5 } });
  annotations.push({ xref: "paper", x: 1, y: ruinFloor, xanchor: "right", yanchor: "bottom",
                     text: `Ruin floor (${(ruinPct * 100).toFixed(0)}% DD): $${Math.round(ruinFloor).toLocaleString()}`,
                     font: { color: t.fail, size: 11 }, showarrow: false });

  return (
    <FanWrapper showSims={showSims} setShowSims={setShowSims}>
      <Plot data={traces}
        layout={{ ...layout, shapes, annotations } as Partial<Layout>}
        config={PLOT_CONFIG}
        style={{ width: "100%" }}
        useResizeHandler
      />
    </FanWrapper>
  );
}

// ── Regime Markov heatmap + stationary distribution ──────────────────────

function RegimeHeatmap({ regime, theme: t }: { regime: RegimeData; theme: ChartTheme }) {
  // Top half: 5x5 transition probability heatmap.
  // Bottom half: stationary distribution as a horizontal bar — same x-axis labels.
  const z = regime.trans_matrix;
  const labels = regime.labels;
  const textGrid: string[][] = z.map((row) => row.map((v) => v.toFixed(2)));

  const heatTrace: Data = {
    type: "heatmap", z, x: labels, y: labels,
    text: textGrid as unknown as string[],
    texttemplate: "%{text}",
    colorscale: [
      [0,    "rgba(0,0,0,0)"],
      [0.25, hexA(t.accent2, 0.25)],
      [0.50, hexA(t.accent2, 0.55)],
      [1.00, t.accent2],
    ],
    showscale: true,
    zmin: 0, zmax: 1,
    hovertemplate: "From %{y} → %{x}: %{z:.2f}<extra></extra>",
    xaxis: "x", yaxis: "y",
    colorbar: { tickfont: { color: t.text2 }, outlinewidth: 0 },
  } as Data;

  const barTrace: Data = {
    type: "bar", x: labels, y: regime.stationary_dist,
    marker: { color: t.accent2, opacity: 0.85 },
    text: regime.stationary_dist.map((v) => v.toFixed(2)),
    textposition: "outside", textfont: { color: t.text2 },
    hovertemplate: "%{x}: %{y:.2f}<extra></extra>",
    xaxis: "x2", yaxis: "y2",
  } as Data;

  const layout: Partial<Layout> = {
    ...baseLayout(t, "Regime Transition Probabilities (Markov)"),
    height: 540,
    grid: { rows: 2, columns: 1, pattern: "independent", roworder: "top to bottom" },
    showlegend: false,
    xaxis:  { domain: [0, 1], anchor: "y",  title: { text: "To regime",   font: { color: t.text2 } },
              tickfont: { color: t.text2 } },
    yaxis:  { domain: [0.42, 1], anchor: "x", title: { text: "From regime", font: { color: t.text2 } },
              tickfont: { color: t.text2 }, autorange: "reversed" },
    xaxis2: { domain: [0, 1], anchor: "y2",
              tickfont: { color: t.text2 } },
    yaxis2: { domain: [0, 0.30], anchor: "x2",
              title: { text: "Stationary", font: { color: t.text2 } },
              tickfont: { color: t.text2 }, gridcolor: t.axisGrid },
    annotations: [
      { text: "Transition Probabilities", xref: "paper", yref: "paper", x: 0, y: 1.04,
        xanchor: "left", showarrow: false, font: { color: t.text, size: 12 } },
      { text: "Stationary Distribution", xref: "paper", yref: "paper", x: 0, y: 0.34,
        xanchor: "left", showarrow: false, font: { color: t.text, size: 12 } },
    ],
  };

  return (
    <Plot data={[heatTrace, barTrace]}
      layout={layout}
      config={PLOT_CONFIG}
      style={{ width: "100%" }}
      useResizeHandler
    />
  );
}

// ── Donuts ─────────────────────────────────────────────────────────────────

function PassDonut({ passRate, nPassed, nFailed, title, theme: t, ci }: {
  passRate: number; nPassed: number; nFailed: number; title: string; theme: ChartTheme;
  ci?: { low: number; high: number; halfWidth: number } | null;
}) {
  const data: Data[] = [{
    type: "pie", hole: 0.65,
    labels: ["Pass", "Fail"],
    values: [Math.max(passRate, 0.0001), Math.max(100 - passRate, 0.0001)],
    marker: { colors: [t.pass, t.fail] },
    textinfo: "label+percent",
    textfont: { color: t.text, size: 12 },
    hovertemplate: "%{label}: %{value:.2f}%<extra></extra>",
  } as Data];

  const centerText = ci
    ? `${passRate.toFixed(1)}% ±${ci.halfWidth.toFixed(1)}%`
    : `${passRate.toFixed(1)}%`;

  const layout: Partial<Layout> = {
    ...baseLayout(t, title),
    height: 320, showlegend: true,
    annotations: [
      { text: centerText, x: 0.5, y: 0.55, showarrow: false,
        font: { size: ci ? 22 : 26, color: passRate >= 50 ? t.pass : t.fail } },
      { text: "Pass Rate", x: 0.5, y: 0.42, showarrow: false,
        font: { size: 12, color: t.text2 } },
      { text: `Passed: ${nPassed.toLocaleString()}`, x: 0.05, y: 1.05, xref: "paper", yref: "paper",
        showarrow: false, font: { size: 11, color: t.pass } },
      { text: `Failed: ${nFailed.toLocaleString()}`, x: 0.95, y: 1.05, xref: "paper", yref: "paper",
        xanchor: "right", showarrow: false, font: { size: 11, color: t.fail } },
    ],
  };

  const lowPower = ci != null && (ci.high - ci.low) > 2;

  return (
    <>
      <Plot data={data} layout={layout} config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler />
      {lowPower && (
        <div className="mc-donut-warn" style={{
          fontSize: 11, color: t.alt, textAlign: "center", marginTop: 4,
        }}>
          ⚠ low confidence — increase Simulations
        </div>
      )}
    </>
  );
}

function BreachDonut({ breachRate, theme: t }: { breachRate: number; theme: ChartTheme }) {
  const data: Data[] = [{
    type: "pie", hole: 0.65,
    labels: ["No Breach", "Breached"],
    values: [Math.max(100 - breachRate, 0.0001), Math.max(breachRate, 0.0001)],
    marker: { colors: [t.pass, t.fail] },
    textinfo: "label+percent",
    textfont: { color: t.text, size: 12 },
    hovertemplate: "%{label}: %{value:.2f}%<extra></extra>",
  } as Data];
  const layout: Partial<Layout> = {
    ...baseLayout(t, "Funded — Breach Rate"),
    height: 320, showlegend: true,
    annotations: [
      { text: `${breachRate.toFixed(1)}%`, x: 0.5, y: 0.55, showarrow: false,
        font: { size: 26, color: breachRate > 50 ? t.fail : t.pass } },
      { text: "Breach Rate", x: 0.5, y: 0.42, showarrow: false,
        font: { size: 12, color: t.text2 } },
    ],
  };
  return <Plot data={data} layout={layout} config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler />;
}

// ── Bars / histograms ──────────────────────────────────────────────────────

function FailReasonsBar({ pcts, title, theme: t }: {
  pcts: { daily_dd?: number; total_dd?: number; profit_shortfall?: number };
  title: string; theme: ChartTheme;
}) {
  const labels = ["Daily Loss Exceeded", "Max Loss Exceeded", "Profit Shortfall"];
  const values = [pcts.daily_dd ?? 0, pcts.total_dd ?? 0, pcts.profit_shortfall ?? 0];
  const data: Data[] = [{
    type: "bar", x: labels, y: values,
    marker: { color: [t.fail, t.alt, t.text2], opacity: 0.85 },
    text: values.map((v) => v.toFixed(1) + "%"),
    textposition: "outside", textfont: { color: t.text },
    hoverinfo: "y",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, title),
                yaxis: { ...baseLayout(t).yaxis, title: { text: "% of Failed Runs", font: { color: t.text2 } } },
                bargap: 0.35, height: 320 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function DaysHistogram({ records, avgDays, minDays, title, theme: t }: {
  records: Array<{ passed?: boolean; days?: number }>;
  avgDays: number; minDays: number; title: string; theme: ChartTheme;
}) {
  const days = records
    .filter((r) => r.passed !== false && typeof r.days === "number")
    .map((r) => r.days as number);
  if (days.length === 0) return <EmptyChart label="No passing simulations" theme={t} />;

  const data: Data[] = [{
    type: "histogram", x: days, nbinsx: 30,
    marker: { color: t.pass, opacity: 0.8 },
    histnorm: "percent",
    hovertemplate: "Day %{x}: %{y:.1f}%<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, title),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Trading Days", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Probability (%)", font: { color: t.text2 } } },
                bargap: 0.06, height: 320,
                shapes: [
                  { type: "line", x0: avgDays, x1: avgDays, yref: "paper", y0: 0, y1: 1,
                    line: { color: t.accent2, dash: "dash", width: 1.5 } },
                  { type: "line", x0: minDays, x1: minDays, yref: "paper", y0: 0, y1: 1,
                    line: { color: t.alt, dash: "dot", width: 1.5 } },
                ],
                annotations: [
                  { x: avgDays, y: 1, xref: "x", yref: "paper",
                    text: `Avg: ${avgDays.toFixed(1)}d`,
                    font: { color: t.accent2, size: 11 }, showarrow: false, yshift: 12 },
                  { x: minDays, y: 1, xref: "x", yref: "paper",
                    text: `Min: ${minDays}d`,
                    font: { color: t.alt, size: 11 }, showarrow: false, yshift: -2 },
                ],
              } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function SurvivalCurve({ survival, theme: t }: { survival: number[]; theme: ChartTheme }) {
  if (!survival.length) return <EmptyChart label="No survival data" theme={t} />;
  const x = survival.map((_, i) => i);
  const y = survival.map((v) => v * 100);
  const data: Data[] = [{
    type: "scatter", mode: "lines", x, y,
    line: { color: t.pass, width: 2 },
    fill: "tozeroy", fillcolor: hexA(t.pass, 0.15),
    hovertemplate: "Day %{x}: %{y:.1f}% surviving<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, "Funded — Survival Curve"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Trading Day #", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "% of Accounts Still Active", font: { color: t.text2 } },
                         range: [0, 105] },
                shapes: [
                  { type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50,
                    line: { color: t.alt, dash: "dash", width: 1 } },
                ],
                annotations: [
                  { xref: "paper", x: 0.99, y: 50, xanchor: "right", yanchor: "bottom",
                    text: "50% survival", font: { color: t.alt, size: 11 }, showarrow: false },
                ],
                height: 340 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function EarningsHistogram({ records, avg, theme: t }: {
  records: Array<{ total_earnings: number }>; avg: number; theme: ChartTheme;
}) {
  if (!records.length) return <EmptyChart label="No earnings data" theme={t} />;
  const x = records.map((r) => r.total_earnings);
  const data: Data[] = [{
    type: "histogram", x, nbinsx: 50,
    marker: { color: t.pass, opacity: 0.75 },
    hovertemplate: "$%{x:.0f}: %{y} sims<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, "Funded — Total Earnings Distribution"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Total Earnings ($)", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Count", font: { color: t.text2 } } },
                shapes: [{ type: "line", x0: avg, x1: avg, yref: "paper", y0: 0, y1: 1,
                           line: { color: t.accent2, dash: "dash", width: 1.5 } }],
                annotations: [{ x: avg, yref: "paper", y: 1, xref: "x",
                                text: `Avg: $${Math.round(avg).toLocaleString()}`,
                                font: { color: t.accent2, size: 11 }, showarrow: false, yshift: 10 }],
                height: 320 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function EarningsVsPayouts({ records, byCount, theme: t }: {
  records: Array<{ payout_count: number; total_earnings: number }>;
  byCount?: Array<{ payout_count: number; mean_earnings: number; count: number }>;
  theme: ChartTheme;
}) {
  if (!records.length) return <EmptyChart label="No data" theme={t} />;
  const traces: Data[] = [
    {
      type: "scatter", mode: "markers",
      x: records.map((r) => r.payout_count),
      y: records.map((r) => r.total_earnings),
      marker: { color: t.accent2, size: 4, opacity: 0.35 },
      hovertemplate: "Payouts: %{x} — $%{y:.0f}<extra></extra>",
      name: "Per-sim",
    } as Data,
  ];
  // Overlay: mean earnings at each integer payout count (the "trend line").
  if (byCount && byCount.length) {
    traces.push({
      type: "scatter", mode: "lines+markers",
      x: byCount.map((b) => b.payout_count),
      y: byCount.map((b) => b.mean_earnings),
      line: { color: t.pass, width: 2 },
      marker: { color: t.pass, size: 9, symbol: "diamond",
                line: { color: t.text, width: 1 } },
      hovertemplate: "%{x} payouts: avg $%{y:.0f} (n=%{customdata})<extra></extra>",
      customdata: byCount.map((b) => b.count),
      name: "Mean per bucket",
    } as Data);
  }
  return (
    <Plot data={traces}
      layout={{ ...baseLayout(t, "Funded — Earnings vs Number of Payouts"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Number of Payouts", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Total Earnings ($)", font: { color: t.text2 } } },
                legend: { orientation: "h", y: -0.2, font: { color: t.text2 } },
                height: 360 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function PayoutCountBar({ records, theme: t }: {
  records: Array<{ payout_count: number }>; theme: ChartTheme;
}) {
  if (!records.length) return <EmptyChart label="No data" theme={t} />;
  const counts = new Map<number, number>();
  for (const r of records) counts.set(r.payout_count, (counts.get(r.payout_count) ?? 0) + 1);
  const xs = [...counts.keys()].sort((a, b) => a - b);
  const total = records.length;
  const ys = xs.map((k) => ((counts.get(k) ?? 0) / total) * 100);
  const data: Data[] = [{
    type: "bar", x: xs, y: ys,
    marker: { color: t.accent2, opacity: 0.8 },
    hovertemplate: "%{x} payouts: %{y:.1f}%<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, "Funded — Payout Count Distribution"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "# Payouts", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Probability (%)", font: { color: t.text2 } } },
                bargap: 0.2, height: 300 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function FirstPayoutHistogram({ records, theme: t }: {
  records: Array<{ first_payout_day: number | null }>; theme: ChartTheme;
}) {
  const days = records.map((r) => r.first_payout_day).filter((v): v is number => v != null);
  if (!days.length) return <EmptyChart label="No payouts" theme={t} />;
  const avg = days.reduce((s, v) => s + v, 0) / days.length;
  const data: Data[] = [{
    type: "histogram", x: days, nbinsx: 40,
    marker: { color: t.accent2, opacity: 0.75 },
    hovertemplate: "Day %{x}: %{y} sims<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, "Funded — Days to First Payout"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Trading Day", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Count", font: { color: t.text2 } } },
                shapes: [{ type: "line", x0: avg, x1: avg, yref: "paper", y0: 0, y1: 1,
                           line: { color: t.alt, dash: "dash", width: 1.5 } }],
                annotations: [{ x: avg, y: 1, xref: "x", yref: "paper",
                                text: `Avg: ${avg.toFixed(1)}d`,
                                font: { color: t.alt, size: 11 }, showarrow: false, yshift: 10 }],
                height: 300 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function BreachDayHistogram({ records, theme: t }: {
  records: Array<{ breach_day: number | null }>; theme: ChartTheme;
}) {
  const days = records.map((r) => r.breach_day).filter((v): v is number => v != null);
  if (!days.length) return <EmptyChart label="No breaches" theme={t} accent="good" />;
  const avg = days.reduce((s, v) => s + v, 0) / days.length;
  const data: Data[] = [{
    type: "histogram", x: days, nbinsx: 40,
    marker: { color: t.fail, opacity: 0.75 },
    hovertemplate: "Day %{x}: %{y} sims<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, "Funded — Breach Day Distribution"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Trading Day of Breach", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Count", font: { color: t.text2 } } },
                shapes: [{ type: "line", x0: avg, x1: avg, yref: "paper", y0: 0, y1: 1,
                           line: { color: t.alt, dash: "dash", width: 1.5 } }],
                annotations: [{ x: avg, y: 1, xref: "x", yref: "paper",
                                text: `Avg: ${avg.toFixed(1)}d`,
                                font: { color: t.alt, size: 11 }, showarrow: false, yshift: 10 }],
                height: 300 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function MaxDdHistogram({ maxDd, theme: t }: { maxDd: number[]; theme: ChartTheme }) {
  if (!maxDd.length) return <EmptyChart label="No drawdown data" theme={t} />;
  const pct100 = maxDd.map((v) => v * 100);
  const med = [...pct100].sort((a, b) => a - b)[Math.floor(pct100.length / 2)];
  const data: Data[] = [{
    type: "histogram", x: pct100, nbinsx: 50,
    marker: { color: t.fail, opacity: 0.75 },
    hovertemplate: "%{x:.1f}%: %{y} sims<extra></extra>",
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, "Long-term — Max Drawdown % Distribution"),
                xaxis: { ...baseLayout(t).xaxis, title: { text: "Max Drawdown (%)", font: { color: t.text2 } } },
                yaxis: { ...baseLayout(t).yaxis, title: { text: "Count", font: { color: t.text2 } } },
                shapes: [{ type: "line", x0: med, x1: med, yref: "paper", y0: 0, y1: 1,
                           line: { color: t.accent2, dash: "dash", width: 1.5 } }],
                annotations: [{ x: med, y: 1, xref: "x", yref: "paper",
                                text: `Median: ${med.toFixed(1)}%`,
                                font: { color: t.accent2, size: 11 }, showarrow: false, yshift: 10 }],
                height: 360 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function Funnel({ nTotal, nP1, nP2, theme: t }: {
  nTotal: number; nP1: number; nP2: number; theme: ChartTheme;
}) {
  const text = [
    `${nTotal.toLocaleString()}  (100%)`,
    `${nP1.toLocaleString()}  (${pct((nP1 / Math.max(nTotal, 1)) * 100)} of total)`,
    `${nP2.toLocaleString()}  (${pct((nP2 / Math.max(nTotal, 1)) * 100)} of total)`,
  ];
  const data: Data[] = [{
    type: "funnel" as unknown as "bar",
    y: ["Started Challenge", "Passed Challenge", "Passed Verification (Funded)"],
    x: [nTotal, nP1, nP2],
    text,
    textinfo: "text",
    textfont: { color: t.text, size: 13 },
    marker: { color: [t.accent2, t.alt, t.pass] },
  } as Data];
  return (
    <Plot data={data}
      layout={{ ...baseLayout(t, `Evaluation Funnel — ${nTotal.toLocaleString()} simulations`),
                margin: { l: 220, r: 24, t: 44, b: 24 },
                height: 280 } as Partial<Layout>}
      config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler
    />
  );
}

function EmptyChart({ label, theme: t, accent }: { label: string; theme: ChartTheme; accent?: "good" }) {
  return (
    <div className="mc-chart-empty"
      style={{ color: accent === "good" ? t.pass : t.text2 }}>
      {label}
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function pct(v: number): string { return `${v.toFixed(1)}%`; }
function pctOf(p: number, base: number): string {
  return `${(p * 100).toFixed(0)}% ($${Math.round(base * p).toLocaleString()})`;
}
function usd(v: number): string {
  return `$${Math.round(v).toLocaleString()}`;
}
function wilsonCi(passRate: number, ciLow: unknown, ciHigh: unknown):
  { low: number; high: number; halfWidth: number } | null {
  const low = Number(ciLow);
  const high = Number(ciHigh);
  if (!Number.isFinite(low) || !Number.isFinite(high) || high < low) return null;
  return { low, high, halfWidth: (high - low) / 2 };
}

// ────────────────────────────────────────────────────────────────────────────
//  Warnings Panel — auto-flagged issues at top of dashboard
// ────────────────────────────────────────────────────────────────────────────

interface WarningSpec {
  id: string;
  message: string;
}

function WarningsPanel({ result }: { result: Record<string, any> }) {
  const verdict = (result?.verdict ?? null) as Record<string, any> | null;
  const warnings: WarningSpec[] = useMemo(() => {
    const list: WarningSpec[] = [];
    if (!verdict) return list;
    const p1 = verdict.phase1 as Record<string, any> | undefined;
    const p2 = verdict.phase2 as Record<string, any> | undefined;
    const funded = verdict.funded as Record<string, any> | undefined;
    const longterm = verdict.longterm as Record<string, any> | undefined;
    const global = verdict.global as Record<string, any> | undefined;

    const ciWidth = (v?: Record<string, any>): number | null => {
      if (!v || v.pass_rate_ci_low == null || v.pass_rate_ci_high == null) return null;
      return Number(v.pass_rate_ci_high) - Number(v.pass_rate_ci_low);
    };
    const w1 = ciWidth(p1);
    const w2 = ciWidth(p2);
    if ((w1 != null && w1 > 5) || (w2 != null && w2 > 5)) {
      list.push({ id: "ci-width", message: "Pass-rate confidence interval > 5pp — low statistical power, increase sims." });
    }
    if (global?.kelly_fraction != null && Number(global.kelly_fraction) < 0) {
      list.push({ id: "kelly-neg", message: "Kelly fraction is negative — strategy has negative expected value." });
    }
    if (longterm?.p_ruin_1y != null && Number(longterm.p_ruin_1y) > 0.5) {
      list.push({ id: "ruin-1y", message: "P(ruin) at 1 year > 50% — strategy unlikely to survive a year." });
    }
    if (p1?.dominant_fail && String(p1.dominant_fail).toLowerCase().includes("daily") &&
        p1.dominant_fail_pct != null && Number(p1.dominant_fail_pct) > 50) {
      list.push({ id: "p1-daily", message: "Daily DD is the binding constraint on Phase 1 — reduce per-trade size." });
    }
    if (funded?.breach_rate != null && Number(funded.breach_rate) > 60) {
      list.push({ id: "funded-breach", message: "Funded breach rate > 60% — most accounts blow up." });
    }
    return list;
  }, [verdict]);

  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const visible = warnings.filter((w) => !dismissed.has(w.id));
  if (!visible.length) return null;

  return (
    <div className="mc-warnings-panel" style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
      {visible.map((w) => (
        <div key={w.id} className="alert alert-error mc-warning-banner"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <span>⚠ {w.message}</span>
          <button
            type="button"
            aria-label="Dismiss warning"
            onClick={() => setDismissed((prev) => new Set(prev).add(w.id))}
            style={{ background: "transparent", border: "none", color: "inherit",
                     cursor: "pointer", fontSize: 16, padding: "0 4px" }}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
//  Verdict Block — plain-English insight per phase
// ────────────────────────────────────────────────────────────────────────────

function VerdictBlock({ phase, v, globalV }: {
  phase: PhaseId;
  v: Record<string, any> | null;
  globalV: Record<string, any> | null;
}) {
  if (!v) {
    return (
      <div className="mc-verdict" style={verdictStyle()}>
        <div className="mc-verdict-headline">Verdict: data not available</div>
      </div>
    );
  }

  let headline = "";
  let secondary = "";
  let insight = "";

  if (phase === "phase1" || phase === "phase2") {
    const pr = Number(v.pass_rate ?? 0);
    const lo = v.pass_rate_ci_low != null ? Number(v.pass_rate_ci_low) : null;
    const hi = v.pass_rate_ci_high != null ? Number(v.pass_rate_ci_high) : null;
    const phaseLabel = phase === "phase1" ? "this phase" : "verification";
    const ciStr = (lo != null && hi != null)
      ? `  (95% CI: ${lo.toFixed(1)}% – ${hi.toFixed(1)}%)`
      : "";
    headline = `🎯  ${pr.toFixed(1)}% chance of clearing ${phaseLabel}${ciStr}`;
    const med = v.median_days != null ? `${Number(v.median_days).toFixed(0)} days` : "N/A";
    const dom = v.dominant_fail ? String(v.dominant_fail) : null;
    const domPct = v.dominant_fail_pct != null ? Number(v.dominant_fail_pct).toFixed(0) : null;
    secondary = `Median time-to-pass: ${med}` + (dom && domPct ? ` · Of failures, ${domPct}% blew the ${dom}` : "");
    if (dom && domPct && Number(domPct) > 50) {
      insight = `tight on ${dom} — consider reducing per-trade size by 25%.`;
    } else if (pr < 30) {
      insight = "low pass rate — consider lowering targets or improving entry edge.";
    } else if (pr > 70) {
      insight = "strong pass odds — current sizing looks workable.";
    } else {
      insight = "marginal pass odds — small sizing tweaks could materially change outcomes.";
    }
  } else if (phase === "funded") {
    const pr = v.payout_rate != null ? Number(v.payout_rate) : null;
    const monthly = v.expected_monthly_usd != null ? Number(v.expected_monthly_usd) : null;
    const lifeMo = v.expected_lifetime_months != null ? Number(v.expected_lifetime_months) : null;
    const breach = v.breach_rate != null ? Number(v.breach_rate) : null;
    const dom = v.dominant_breach ? String(v.dominant_breach) : null;
    headline = pr != null ? `💰  ${pr.toFixed(1)}% reach at least one payout` : "💰  Funded outlook";
    const parts: string[] = [];
    if (monthly != null) parts.push(`Expected $${Math.round(monthly).toLocaleString()}/mo`);
    if (lifeMo != null) parts.push(`avg lifetime ${lifeMo.toFixed(1)} mo`);
    if (breach != null) parts.push(`breach rate ${breach.toFixed(1)}%`);
    secondary = parts.join(" · ");
    if (breach != null && breach > 60) {
      insight = `most accounts breach via ${dom ?? "DD limits"} — sizing too large for these guardrails.`;
    } else if (monthly != null && monthly <= 0) {
      insight = "expected monthly P&L is non-positive — fee will not be recovered.";
    } else if (lifeMo != null && lifeMo < 3) {
      insight = "short expected lifetime — survivorship risk dominates returns.";
    } else {
      insight = `payout rate is the lever; ${dom ? `dominant breach is ${dom}` : "monitor breach distribution"}.`;
    }
  } else if (phase === "longterm") {
    const r1 = v.p_ruin_1y != null ? Number(v.p_ruin_1y) : null;
    const r5 = v.p_ruin_5y != null ? Number(v.p_ruin_5y) : null;
    const eq = v.median_equity != null ? Number(v.median_equity) : null;
    const sh = v.median_sharpe != null ? Number(v.median_sharpe) : null;
    headline = "📈  Long-term outlook";
    const parts: string[] = [];
    if (r1 != null) parts.push(`P(ruin 1y) ${(r1 * 100).toFixed(1)}%`);
    if (r5 != null) parts.push(`P(ruin 5y) ${(r5 * 100).toFixed(1)}%`);
    if (eq != null) parts.push(`median equity $${Math.round(eq).toLocaleString()}`);
    if (sh != null) parts.push(`Sharpe ${sh.toFixed(2)}`);
    secondary = parts.join(" · ");
    if (r1 != null && r1 > 0.5) {
      insight = "P(ruin) > 50% within a year — strategy unlikely to survive; reduce risk.";
    } else if (sh != null && sh < 0) {
      insight = "negative Sharpe — strategy loses money on average; do not deploy.";
    } else if (sh != null && sh > 1) {
      insight = "healthy risk-adjusted return.";
    } else {
      insight = "marginal Sharpe — improvements to entry edge would compound substantially.";
    }
  }

  // Optional Kelly nudge applies across phases
  if (globalV?.kelly_fraction != null && Number(globalV.kelly_fraction) < 0) {
    insight = `Kelly is negative — strategy has negative expected value. ${insight}`;
  }

  return (
    <div className="mc-verdict" style={verdictStyle()}>
      <div className="mc-verdict-headline" style={{ fontSize: 15, fontWeight: 600 }}>{headline}</div>
      {secondary && <div className="mc-verdict-secondary" style={{ fontSize: 13, marginTop: 2, opacity: 0.85 }}>{secondary}</div>}
      <div className="mc-verdict-insight" style={{ fontSize: 13, marginTop: 4, fontStyle: "italic" }}>
        <strong>Verdict:</strong> {insight}
      </div>
    </div>
  );
}

function verdictStyle(): React.CSSProperties {
  return {
    borderLeft: "4px solid var(--accent2, #1f6feb)",
    padding: "10px 14px",
    marginBottom: 10,
    background: "var(--bg2, rgba(255,255,255,0.03))",
    borderRadius: 4,
  };
}

// ────────────────────────────────────────────────────────────────────────────
//  Lot-Size Sweep Chart
// ────────────────────────────────────────────────────────────────────────────

function LotSweepChart({ sweep, theme: t }: {
  sweep: Array<Record<string, any>>; theme: ChartTheme;
}) {
  const xs = sweep.map((r) => Number(r.lot ?? r.lot_multiplier ?? 0));
  const passRates = sweep.map((r) => Number(r.pass_rate ?? 0));
  // Backend computes median DAYS to pass (not earnings) — show that on the
  // secondary axis since it's the actual data the lot_size_sweep helper
  // produces. Smaller is better (faster to pass).
  const medianDays = sweep.map((r) => {
    const d = r.median_days ?? r.median_earnings;
    return d == null || Number.isNaN(Number(d)) ? null : Number(d);
  });

  const traces: Data[] = [
    {
      x: xs, y: passRates, type: "scatter", mode: "lines+markers",
      name: "Pass Rate %",
      line: { color: t.pass, width: 2 },
      marker: { color: t.pass, size: 7 },
      yaxis: "y",
      hovertemplate: "Lot %{x}x: %{y:.1f}%<extra>Pass</extra>",
    } as Data,
    {
      x: xs, y: medianDays, type: "scatter", mode: "lines+markers",
      name: "Median Days to Pass",
      line: { color: t.accent2, width: 2, dash: "dot" },
      marker: { color: t.accent2, size: 7 },
      yaxis: "y2",
      connectgaps: false,
      hovertemplate: "Lot %{x}x: %{y:.0f} days<extra>Speed</extra>",
    } as Data,
  ];

  const l = baseLayout(t, "Position Sizing Sensitivity");
  const layout: Partial<Layout> = {
    ...l,
    height: 340,
    xaxis: { ...l.xaxis, title: { text: "Lot Multiplier", font: { color: t.text2 } } },
    yaxis: { ...l.yaxis, title: { text: "Pass Rate (%)", font: { color: t.pass } },
             tickfont: { color: t.pass } },
    yaxis2: {
      title: { text: "Median Days to Pass", font: { color: t.accent2 } },
      tickfont: { color: t.accent2 },
      overlaying: "y", side: "right", showgrid: false,
    },
    legend: { orientation: "h", y: -0.2 },
  };
  return <Plot data={traces} layout={layout} config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler />;
}

// ────────────────────────────────────────────────────────────────────────────
//  Payout Cadence Chart
// ────────────────────────────────────────────────────────────────────────────

function PayoutCadenceChart({ sweep, theme: t }: {
  sweep: Array<Record<string, any>>; theme: ChartTheme;
}) {
  const xs = sweep.map((r) => Number(r.cadence_days ?? r.days ?? 0));
  const earnings = sweep.map((r) => Number(r.total_earnings ?? r.earnings ?? 0));
  const breachR  = sweep.map((r) => Number(r.breach_rate ?? 0));

  const traces: Data[] = [
    {
      x: xs, y: earnings, type: "bar", name: "Total Earnings $",
      marker: { color: t.pass, opacity: 0.8 },
      yaxis: "y",
      hovertemplate: "%{x}d cadence: $%{y:,.0f}<extra>Earnings</extra>",
    } as Data,
    {
      x: xs, y: breachR, type: "scatter", mode: "lines+markers",
      name: "Breach Rate %",
      line: { color: t.fail, width: 2 },
      marker: { color: t.fail, size: 8 },
      yaxis: "y2",
      hovertemplate: "%{x}d: %{y:.1f}%<extra>Breach</extra>",
    } as Data,
  ];

  const l = baseLayout(t, "Payout Cadence Optimizer — find the sweet spot");
  const layout: Partial<Layout> = {
    ...l,
    height: 360,
    xaxis: { ...l.xaxis, title: { text: "Cadence (days)", font: { color: t.text2 } } },
    yaxis: { ...l.yaxis, title: { text: "Total Earnings ($)", font: { color: t.pass } },
             tickfont: { color: t.pass } },
    yaxis2: {
      title: { text: "Breach Rate (%)", font: { color: t.fail } },
      tickfont: { color: t.fail },
      overlaying: "y", side: "right", showgrid: false,
      range: [0, 100],
    },
    legend: { orientation: "h", y: -0.2 },
    bargap: 0.25,
  };
  return <Plot data={traces} layout={layout} config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler />;
}

// ────────────────────────────────────────────────────────────────────────────
//  Risk-of-Ruin Curve
// ────────────────────────────────────────────────────────────────────────────

function RuinHorizonChart({ horizons, theme: t }: {
  horizons: Array<Record<string, any>>; theme: ChartTheme;
}) {
  const xs = horizons.map((r) => Number(r.days ?? 0));
  const ys = horizons.map((r) => Number(r.p_ruin ?? 0) * 100);

  const traces: Data[] = [
    {
      x: xs, y: ys, type: "scatter", mode: "lines+markers",
      name: "P(ruin) %",
      line: { color: t.fail, width: 2.5 },
      marker: { color: t.fail, size: 8 },
      hovertemplate: "%{x} days: %{y:.2f}%<extra></extra>",
    } as Data,
  ];

  const l = baseLayout(t, "Risk of Ruin by Horizon");
  const layout: Partial<Layout> = {
    ...l,
    height: 340,
    xaxis: {
      ...l.xaxis,
      title: { text: "Days (log scale)", font: { color: t.text2 } },
      type: "log",
    },
    yaxis: { ...l.yaxis, title: { text: "P(ruin) (%)", font: { color: t.text2 } },
             range: [0, 100] },
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 50, y1: 50,
        line: { color: t.fail, dash: "dash", width: 1.5 } },
    ],
    annotations: [
      { xref: "paper", x: 0.99, y: 50, xanchor: "right", yanchor: "bottom",
        text: "50% ruin", font: { color: t.fail, size: 11 }, showarrow: false },
    ],
  };
  return <Plot data={traces} layout={layout} config={PLOT_CONFIG} style={{ width: "100%" }} useResizeHandler />;
}

function hexA(hex: string, a: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6 && h.length !== 3) return `rgba(255,255,255,${a})`;
  const r = parseInt(h.length === 3 ? h[0] + h[0] : h.slice(0, 2), 16);
  const g = parseInt(h.length === 3 ? h[1] + h[1] : h.slice(2, 4), 16);
  const b = parseInt(h.length === 3 ? h[2] + h[2] : h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${a})`;
}
