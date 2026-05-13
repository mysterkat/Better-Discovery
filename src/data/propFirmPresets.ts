/**
 * Prop firm preset library.
 *
 * Currently FTMO-only (Normal 2-Step + Swing 2-Step, all 5 account sizes).
 * Other firms (MFF / Apex / Topstep / etc.) were removed in v0.4.0.1 — they
 * had different DD mechanics (trailing / EOD / static) that the sim only
 * partially modelled, so showing them was misleading.
 *
 * Each preset bundles parameter overrides for all four MC phases plus
 * top-level metadata (challenge fee, refund policy). When the user picks
 * a preset, MonteCarloTab merges these `overrides` into its own state.
 *
 * `CHALLENGE_FEE` + `FEE_REFUNDED_ON_FIRST_PAYOUT` are pseudo-parameters
 * passed through as `global_params` on the run request so the verdict
 * block can compute fee-aware ROI.
 */

export interface PropFirmPreset {
  id: string;
  name: string;
  description: string;
  challengeFee: number;
  feeRefundedOnFirstPayout: boolean;
  overrides: Record<string, number | string | boolean>;
}

/** Shared rule set for both Normal + Swing variants — only fees differ
 *  in published FTMO pricing, plus Swing allows overnight/news holding. */
const FTMO_BASE_RULES = {
  // Phase 1 — Challenge
  P1_PROFIT_TARGET: 0.10,
  P1_MAX_DAILY_DD:  0.05,
  P1_MAX_TOTAL_DD:  0.10,
  P1_MIN_DAYS:      4,
  P1_MAX_SIM_DAYS:  365,
  // Phase 2 — Verification
  P2_PROFIT_TARGET: 0.05,
  P2_MAX_DAILY_DD:  0.05,
  P2_MAX_TOTAL_DD:  0.10,
  P2_MIN_DAYS:      4,
  P2_MAX_SIM_DAYS:  365,
  // Funded
  FD_MAX_DAILY_DD:    0.05,
  FD_MAX_TOTAL_DD:    0.10,
  FD_PROFIT_SPLIT:    0.80,         // 80% trader / 20% firm (default)
  FD_PAYOUT_MODE:     "schedule",   // FTMO: bi-weekly request, monthly default
  FD_PAYOUT_SCHEDULE: 14,           // FTMO's bi-weekly cadence
  FD_MIN_DAYS_PAYOUT: 4,
  FD_BALANCE_RESET:   true,
  FD_MAX_SIM_DAYS:    365,
} as const;

/**
 * FTMO fee table (current as of late 2024 — verify on FTMO.com before relying on these).
 * Normal and Swing have identical fees per published pricing.
 */
const FTMO_FEES: Record<number, number> = {
  10000:  89,
  25000: 189,
  50000: 289,
  100000: 539,
  200000: 989,
};

function buildFtmoPreset(
  variant: "normal" | "swing",
  balance: number,
): PropFirmPreset {
  const label = variant === "swing" ? "Swing" : "Normal";
  const fee   = FTMO_FEES[balance] ?? 0;
  const balanceLabel =
    balance >= 1000 ? `$${Math.round(balance / 1000)}k` : `$${balance}`;
  return {
    id: `ftmo_2step_${variant}_${balance}`,
    name: `FTMO 2-Step ${label} (${balanceLabel})`,
    description:
      variant === "swing"
        ? "FTMO 2-Step Swing — same DD rules, no overnight/news restrictions."
        : "FTMO 2-Step Normal — 10% / 5% targets, 5% daily, 10% total DD, 4 min days.",
    challengeFee:               fee,
    feeRefundedOnFirstPayout:   true,
    overrides: {
      ...FTMO_BASE_RULES,
      P1_BALANCE: balance,
      P2_BALANCE: balance,
      FD_BALANCE: balance,
    },
  };
}

const FTMO_SIZES = [10000, 25000, 50000, 100000, 200000] as const;

export const PROP_FIRM_PRESETS: PropFirmPreset[] = [
  ...FTMO_SIZES.map((bal) => buildFtmoPreset("normal", bal)),
  ...FTMO_SIZES.map((bal) => buildFtmoPreset("swing",  bal)),
];

export const NO_PRESET_ID = "custom";

/** Look up a preset by id; returns undefined when not found (custom mode). */
export function findPreset(id: string): PropFirmPreset | undefined {
  return PROP_FIRM_PRESETS.find((p) => p.id === id);
}
