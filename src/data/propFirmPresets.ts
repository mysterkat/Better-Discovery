/**
 * Prop firm preset library.
 *
 * Each preset bundles the parameter overrides for all four MC phases plus
 * top-level metadata (challenge fee, refund policy). When the user picks
 * a preset, the MonteCarloTab merges these `overrides` into its own
 * `overrides` state — they are keyed by `MC_PARAM_META` keys (e.g.
 * "P1_BALANCE", "FD_PAYOUT_SCHEDULE") which is exactly what the form expects.
 *
 * `CHALLENGE_FEE` and `FEE_REFUNDED_ON_FIRST_PAYOUT` are pseudo-parameters
 * that don't exist in the backend's MC_PARAM_META registry — they are passed
 * through as `global_params` on the run request so the dashboard's verdict
 * block can compute fee-aware ROI.
 */

export interface PropFirmPreset {
  id: string;
  name: string;
  description: string;
  challengeFee: number;
  feeRefundedOnFirstPayout: boolean;
  /**
   * Override values keyed by MC_PARAM_META key (e.g. "P1_BALANCE").
   * Numbers are stored as decimals (0.10 = 10%) matching the backend's
   * fractional convention. Currency-denominated DDs (futures-style) are
   * stored as USD on the FD_* keys — the dashboard / backend treats those
   * fields as "near-flat" trailing limits.
   */
  overrides: Record<string, number | string | boolean>;
}

export const PROP_FIRM_PRESETS: PropFirmPreset[] = [
  {
    id: "ftmo_2step_10k",
    name: "FTMO 2-Step ($10k)",
    description: "Classic 2-step FTMO challenge: 10% / 5% targets, 5% daily, 10% total DD, 4 min trading days.",
    challengeFee: 89,
    feeRefundedOnFirstPayout: true,
    overrides: {
      P1_BALANCE: 10000,
      P1_PROFIT_TARGET: 0.10,
      P1_MAX_DAILY_DD: 0.05,
      P1_MAX_TOTAL_DD: 0.10,
      P1_MIN_DAYS: 4,
      P2_BALANCE: 10000,
      P2_PROFIT_TARGET: 0.05,
      P2_MAX_DAILY_DD: 0.05,
      P2_MAX_TOTAL_DD: 0.10,
      P2_MIN_DAYS: 4,
      FD_BALANCE: 10000,
      FD_MAX_DAILY_DD: 0.05,
      FD_MAX_TOTAL_DD: 0.10,
      FD_PROFIT_SPLIT: 0.80,
      FD_PAYOUT_MODE: "schedule",
      FD_PAYOUT_SCHEDULE: 30,
      FD_MIN_DAYS_PAYOUT: 4,
    },
  },
  {
    id: "ftmo_swing_10k",
    name: "FTMO Swing ($10k)",
    description: "Swing variant — same DD limits as 2-Step but no overnight/weekend restrictions.",
    challengeFee: 99,
    feeRefundedOnFirstPayout: true,
    overrides: {
      P1_BALANCE: 10000,
      P1_PROFIT_TARGET: 0.10,
      P1_MAX_DAILY_DD: 0.05,
      P1_MAX_TOTAL_DD: 0.10,
      P1_MIN_DAYS: 4,
      P2_BALANCE: 10000,
      P2_PROFIT_TARGET: 0.05,
      P2_MAX_DAILY_DD: 0.05,
      P2_MAX_TOTAL_DD: 0.10,
      P2_MIN_DAYS: 4,
      FD_BALANCE: 10000,
      FD_MAX_DAILY_DD: 0.05,
      FD_MAX_TOTAL_DD: 0.10,
      FD_PROFIT_SPLIT: 0.80,
      FD_PAYOUT_MODE: "schedule",
      FD_PAYOUT_SCHEDULE: 30,
      FD_MIN_DAYS_PAYOUT: 4,
    },
  },
  {
    id: "mff_rapid_10k",
    name: "MyForexFunds Rapid ($10k)",
    description: "MFF Rapid 2-step: 8% / 5% targets, 4% daily, 6% trailing total DD.",
    challengeFee: 84,
    feeRefundedOnFirstPayout: true,
    overrides: {
      P1_BALANCE: 10000,
      P1_PROFIT_TARGET: 0.08,
      P1_MAX_DAILY_DD: 0.04,
      P1_MAX_TOTAL_DD: 0.06,
      P1_MIN_DAYS: 5,
      P2_BALANCE: 10000,
      P2_PROFIT_TARGET: 0.05,
      P2_MAX_DAILY_DD: 0.04,
      P2_MAX_TOTAL_DD: 0.06,
      P2_MIN_DAYS: 5,
      FD_BALANCE: 10000,
      FD_MAX_DAILY_DD: 0.04,
      FD_MAX_TOTAL_DD: 0.06,
      FD_PROFIT_SPLIT: 0.80,
      FD_PAYOUT_MODE: "schedule",
      FD_PAYOUT_SCHEDULE: 30,
      FD_MIN_DAYS_PAYOUT: 5,
    },
  },
  {
    id: "apex_25k",
    name: "Apex Trader Funding ($25k)",
    description: "Futures-style 1-step: $1500 trailing DD, $1500 profit target, 5 min trading days.",
    challengeFee: 137,
    feeRefundedOnFirstPayout: true,
    overrides: {
      P1_BALANCE: 25000,
      P1_PROFIT_TARGET: 0.06,    // $1500 / $25k
      P1_MAX_DAILY_DD: 0.06,     // futures: trailing only — set daily ≈ trailing
      P1_MAX_TOTAL_DD: 0.06,     // $1500 / $25k trailing
      P1_MIN_DAYS: 5,
      // No phase 2 — Apex is 1-step. Mirror P1 so phase2 is effectively a no-op
      // pass-through if the user runs it.
      P2_BALANCE: 25000,
      P2_PROFIT_TARGET: 0.001,
      P2_MAX_DAILY_DD: 0.06,
      P2_MAX_TOTAL_DD: 0.06,
      P2_MIN_DAYS: 1,
      FD_BALANCE: 25000,
      FD_MAX_DAILY_DD: 0.06,
      FD_MAX_TOTAL_DD: 0.06,
      FD_PROFIT_SPLIT: 1.0,      // first $25k 100% to trader (Apex policy)
      FD_PAYOUT_MODE: "threshold",
      FD_PAYOUT_THRESHOLD: 0.02,
      FD_MIN_DAYS_PAYOUT: 8,
    },
  },
  {
    id: "topstep_50k",
    name: "Topstep Trading Combine ($50k)",
    description: "Futures combine: $3k profit target, $2k max loss, $1k daily loss, 5 min days.",
    challengeFee: 165,
    feeRefundedOnFirstPayout: false,
    overrides: {
      P1_BALANCE: 50000,
      P1_PROFIT_TARGET: 0.06,    // $3000 / $50k
      P1_MAX_DAILY_DD: 0.02,     // $1000 / $50k
      P1_MAX_TOTAL_DD: 0.04,     // $2000 / $50k
      P1_MIN_DAYS: 5,
      P2_BALANCE: 50000,
      P2_PROFIT_TARGET: 0.001,
      P2_MAX_DAILY_DD: 0.02,
      P2_MAX_TOTAL_DD: 0.04,
      P2_MIN_DAYS: 1,
      FD_BALANCE: 50000,
      FD_MAX_DAILY_DD: 0.02,
      FD_MAX_TOTAL_DD: 0.04,
      FD_PROFIT_SPLIT: 0.90,
      FD_PAYOUT_MODE: "threshold",
      FD_PAYOUT_THRESHOLD: 0.01,
      FD_MIN_DAYS_PAYOUT: 5,
    },
  },
  {
    id: "the5ers_bootcamp",
    name: "The5%ers Bootcamp",
    description: "Bootcamp: 6% target, 4% loss limit, low fee.",
    challengeFee: 39,
    feeRefundedOnFirstPayout: true,
    overrides: {
      P1_BALANCE: 10000,
      P1_PROFIT_TARGET: 0.06,
      P1_MAX_DAILY_DD: 0.04,
      P1_MAX_TOTAL_DD: 0.04,
      P1_MIN_DAYS: 1,
      P2_BALANCE: 10000,
      P2_PROFIT_TARGET: 0.001,
      P2_MAX_DAILY_DD: 0.04,
      P2_MAX_TOTAL_DD: 0.04,
      P2_MIN_DAYS: 1,
      FD_BALANCE: 10000,
      FD_MAX_DAILY_DD: 0.04,
      FD_MAX_TOTAL_DD: 0.05,
      FD_PROFIT_SPLIT: 0.80,
      FD_PAYOUT_MODE: "schedule",
      FD_PAYOUT_SCHEDULE: 30,
      FD_MIN_DAYS_PAYOUT: 1,
    },
  },
  {
    id: "fundednext_express_10k",
    name: "FundedNext Express ($10k)",
    description: "1-step express: 8% target, 5% daily, 10% total, no min trading days.",
    challengeFee: 59,
    feeRefundedOnFirstPayout: true,
    overrides: {
      P1_BALANCE: 10000,
      P1_PROFIT_TARGET: 0.08,
      P1_MAX_DAILY_DD: 0.05,
      P1_MAX_TOTAL_DD: 0.10,
      P1_MIN_DAYS: 1,
      // No phase 2 — pass-through.
      P2_BALANCE: 10000,
      P2_PROFIT_TARGET: 0.001,
      P2_MAX_DAILY_DD: 0.05,
      P2_MAX_TOTAL_DD: 0.10,
      P2_MIN_DAYS: 1,
      FD_BALANCE: 10000,
      FD_MAX_DAILY_DD: 0.05,
      FD_MAX_TOTAL_DD: 0.10,
      FD_PROFIT_SPLIT: 0.80,
      FD_PAYOUT_MODE: "schedule",
      FD_PAYOUT_SCHEDULE: 14,
      FD_MIN_DAYS_PAYOUT: 1,
    },
  },
];

export function findPreset(id: string): PropFirmPreset | undefined {
  return PROP_FIRM_PRESETS.find((p) => p.id === id);
}
