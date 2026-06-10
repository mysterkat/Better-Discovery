"""Set → MQL5 converter bridge.

The template EA bundled at `backend/ea/PatternDiscoveryEA.mq5` is the source
of truth (originally ported from PatternDiscovery_Converter.html). It is
treated as READ-ONLY by this bridge — only its `input` block is replaced.

Public API
----------
export(set_content, template_path=None, output_name=None) -> str
    Merges a .set file with the EA template and writes the result to
    userdata/mql/<name>.mq5.  Returns the absolute path.

default_template_path() -> str
    Returns the path to the bundled PatternDiscoveryEA.mq5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import USER_DATA

# ── Paths ─────────────────────────────────────────────────────────────────────
# EA template lives bundled inside the repo at backend/ea/. Resolved relative
# to this file so it works on any machine without a hard-coded user path.
#   __file__ = backend/app/bridge/set_to_mql.py  → parents[2] = backend/
_DEFAULT_TEMPLATE = Path(__file__).resolve().parents[2] / "ea" / "PatternDiscoveryEA.mq5"
_MQL_OUTPUT_DIR = USER_DATA / "mql"

# ── Column order (must match COLUMN INDEX TABLE in EA header) ─────────────────
_COLS: list[str] = [
    "rsi14", "macd_norm", "atr_pct", "bb_width", "trend", "mtf_bull_score",
    "body_pct", "rng_atr", "vol_ratio", "vol_body_conf", "regime",
    "vol_price_div", "bb_expanding", "prev_sess_bias", "poc_dist", "bull",
    "uwk_pct", "lwk_pct", "stoch_k", "stoch_d", "pin_bar", "inside_bar",
    "outside_bar", "htf_div", "rolling_sharpe", "sd_zone", "vwap_dist",
]

_COL_LABELS: dict[str, str] = {
    "rsi14":          "RSI(14) [0-100]",
    "macd_norm":      "MACD_hist/ATR [normalised]",
    "atr_pct":        "ATR/Close [volatility%]",
    "bb_width":       "BB width/midline [squeeze<0.01]",
    "trend":          "EMA trend [-1=dn 0=range 1=up]",
    "mtf_bull_score": "MTF bull score [0..N where N=primary+signal TFs]",
    "body_pct":       "Body/Range [0-1]",
    "rng_atr":        "Range/ATR [1=avg bar]",
    "vol_ratio":      "Volume/MA20 [1=avg vol]",
    "vol_body_conf":  "Vol x Body [confirmation]",
    "regime":         "Market regime [0=TrendUp..4=Choppy]",
    "vol_price_div":  "Vol-price diverge [+1=accum -1=distrib]",
    "bb_expanding":   "BB expanding [0=no 1=yes]",
    "prev_sess_bias": "Prev session bias [-1=bear 0=flat 1=bull]",
    "poc_dist":       "Dist from POC [% of price]",
    "bull":           "Bullish candle [0=bear 1=bull]",
    "uwk_pct":        "Upper wick/Range [0-1]",
    "lwk_pct":        "Lower wick/Range [0-1]",
    "stoch_k":        "Stochastic %K [0-100]",
    "stoch_d":        "Stochastic %D [0-100]",
    "pin_bar":        "Pin-bar score [0-1]",
    "inside_bar":     "Inside bar [0=no 1=yes]",
    "outside_bar":    "Outside bar [0=no 1=yes]",
    "htf_div":        "HTF RSI divergence [+1=bull -1=bear 0=none]",
    "rolling_sharpe": "Rolling Sharpe(20) [risk-adj momentum]",
    "sd_zone":        "S/D zone proximity [+1=supp -1=res]",
    "vwap_dist":      "VWAP distance [% from VWAP]",
}

# ── Defaults (mirror DEFAULTS in PatternDiscovery_Converter.html) ─────────────
_DEFAULTS: dict[str, Any] = {
    "MagicNumber": 10001,
    # Multi-TF inputs declared in PatternDiscoveryEA.mq5. ENUM_TIMEFRAMES
    # values must be left as raw identifiers (not quoted, not numeric-formatted).
    # Defaults match the template; the .set file may override any of them.
    "SignalTF1": "PERIOD_M15",
    "SignalTF2": "PERIOD_H1",
    "SignalTF3": "PERIOD_CURRENT",
    "SignalTF4": "PERIOD_CURRENT",
    "DirectionMode": 1,
    "SL_Pct": 0.005220, "TP_Pct": 0.003630, "Lots": 0.10,
    "Commission_R": 0.0, "Swap_R_PerBar": 0.0,
    "CooldownBars": 3, "BreakevenAtR": 0.0, "UseTrailing": "false",
    "TrailingStart": 1.0, "TrailingStep": 0.5, "MaxHoldBars": 0,
    "TradeAsian": "true", "TradeLondon": "true", "TradeNY": "true",
    "TradeOverlap": "true", "TradeOff": "true",
    "Discrim_Col": 1, "Discrim_Thresh": 0.012000, "Discrim_Dir": 1,
    "MaxSpreadPoints": 30.0, "MaxDailyLossR": 0.0, "MaxOpenPositions": 1,
    "DebugMode": "false",
    "HoursBan": "", "EODCloseEnabled": "false", "EODCloseHour": 22,
    # rsi14 has natural [0-100] range
    "rsi14_lo": 0.0, "rsi14_hi": 100.0,
    # all other features use ±999 sentinels
    "macd_norm_lo": -999.0, "macd_norm_hi": 999.0,
    "atr_pct_lo": -999.0, "atr_pct_hi": 999.0,
    "bb_width_lo": -999.0, "bb_width_hi": 999.0,
    "trend_lo": -999.0, "trend_hi": 999.0,
    "mtf_bull_score_lo": -999.0, "mtf_bull_score_hi": 999.0,
    "body_pct_lo": -999.0, "body_pct_hi": 999.0,
    "rng_atr_lo": -999.0, "rng_atr_hi": 999.0,
    "vol_ratio_lo": -999.0, "vol_ratio_hi": 999.0,
    "vol_body_conf_lo": -999.0, "vol_body_conf_hi": 999.0,
    "regime_lo": -999.0, "regime_hi": 999.0,
    "vol_price_div_lo": -999.0, "vol_price_div_hi": 999.0,
    "bb_expanding_lo": -999.0, "bb_expanding_hi": 999.0,
    "prev_sess_bias_lo": -999.0, "prev_sess_bias_hi": 999.0,
    "poc_dist_lo": -999.0, "poc_dist_hi": 999.0,
    "bull_lo": -999.0, "bull_hi": 999.0,
    "uwk_pct_lo": -999.0, "uwk_pct_hi": 999.0,
    "lwk_pct_lo": -999.0, "lwk_pct_hi": 999.0,
    "stoch_k_lo": -999.0, "stoch_k_hi": 999.0,
    "stoch_d_lo": -999.0, "stoch_d_hi": 999.0,
    "pin_bar_lo": -999.0, "pin_bar_hi": 999.0,
    "inside_bar_lo": -999.0, "inside_bar_hi": 999.0,
    "outside_bar_lo": -999.0, "outside_bar_hi": 999.0,
    "htf_div_lo": -999.0, "htf_div_hi": 999.0,
    "rolling_sharpe_lo": -999.0, "rolling_sharpe_hi": 999.0,
    "sd_zone_lo": -999.0, "sd_zone_hi": 999.0,
    "vwap_dist_lo": -999.0, "vwap_dist_hi": 999.0,
}

# ── Filter group spec (mirrors FILTER_GROUPS in the HTML converter) ───────────
_FILTER_GROUPS: list[tuple[str, str]] = [
    ("rsi14",          "Entry filter: RSI(14) [0-100]"),
    ("macd_norm",      "Entry filter: MACD_hist/ATR [normalised]"),
    ("atr_pct",        "Entry filter: ATR/Close [volatility %]"),
    ("bb_width",       "Entry filter: BB width/midline [squeeze<0.01]"),
    ("trend",          "Entry filter: EMA trend [-1=dn 0=range 1=up]"),
    ("mtf_bull_score", "Entry filter: MTF bull score [0-2]"),
    ("body_pct",       "Entry filter: Body/Range [0-1]"),
    ("rng_atr",        "Entry filter: Range/ATR [1=avg bar]"),
    ("vol_ratio",      "Entry filter: Volume/MA20 [1=avg vol]"),
    ("vol_body_conf",  "Entry filter: Vol x Body [confirmation]"),
    ("regime",         "Entry filter: Market regime [0=TrendUp..4=Choppy]"),
    ("vol_price_div",  "Entry filter: Vol-price diverge [+1=accum -1=distrib]"),
    ("bb_expanding",   "Entry filter: BB expanding [0=no 1=yes]"),
    ("prev_sess_bias", "Entry filter: Prev session bias [-1=bear 0=flat 1=bull]"),
    ("poc_dist",       "Entry filter: Dist from POC [% of price]"),
    ("bull",           "Entry filter: Bullish candle [0=bear 1=bull]"),
    ("uwk_pct",        "Entry filter: Upper wick / Range [0-1]"),
    ("lwk_pct",        "Entry filter: Lower wick / Range [0-1]"),
    ("stoch_k",        "Entry filter: Stochastic %K [0-100]"),
    ("stoch_d",        "Entry filter: Stochastic %D [0-100]"),
    ("pin_bar",        "Entry filter: Pin-bar score [0-1]"),
    ("inside_bar",     "Entry filter: Inside bar [0=no 1=yes]"),
    ("outside_bar",    "Entry filter: Outside bar [0=no 1=yes]"),
    ("htf_div",        "Entry filter: HTF RSI divergence [+1=bull -1=bear 0=none]"),
    ("rolling_sharpe", "Entry filter: Rolling Sharpe(20) [risk-adj momentum]"),
    ("sd_zone",        "Entry filter: S/D zone proximity [+1=near supp -1=near res]"),
    ("vwap_dist",      "Entry filter: VWAP distance [% from VWAP, 0 if no vol]"),
]


# ── Input-block markers (must match PatternDiscoveryEA.mq5) ───────────────────
_BD_INPUT_BEGIN = "// @BD_INPUT_BEGIN"
_BD_INPUT_END = "// @BD_INPUT_END"

# Matches: input double Commission_R = 0.0;  (optional trailing comment)
_INPUT_LINE_RE = re.compile(
    r"^\s*input\s+(?P<type>\S+)\s+(?P<name>\w+)\s*=\s*(?P<val>[^;]+);\s*(?://.*)?\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class _TemplateInput:
    mql_type: str
    name: str
    default_raw: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_num(v: Any) -> str:
    """Format a value for MQL5 source (mirrors fmtNum() in the HTML converter)."""
    s = str(v)
    if s in ("true", "false"):
        return s
    try:
        n = float(s)
    except (ValueError, TypeError):
        return s
    if n == -999:
        return "-999.0"
    if n == 999:
        return "999.0"
    if n == int(n) and abs(n) < 10000:
        return str(int(n))
    return f"{n:.6f}"


def _suggest_filename(meta: dict[str, Any], params: dict[str, Any]) -> str:
    """Suggest an output filename from pattern metadata."""
    if meta.get("pattern_no") and meta.get("cluster") and meta.get("direction"):
        p = str(meta["pattern_no"]).zfill(2)
        c = str(meta["cluster"]).zfill(2)
        return f"pattern_{p}_C{c}_{meta['direction']}"
    magic = params.get("MagicNumber", "")
    return f"pattern_magic{magic}" if magic else "PatternDiscoveryEA_converted"


# ── Core functions ────────────────────────────────────────────────────────────

def parse_set_file(text: str) -> dict[str, Any]:
    """Parse a .set file into {'params': {...}, 'meta': {...}}.

    Mirrors parseSetFile() in PatternDiscovery_Converter.html.
    """
    params: dict[str, Any] = {}
    meta: dict[str, Any] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith(";"):
            # Extract performance metadata from comment headers
            m = re.search(
                r"Train:.*?WR=([\d.]+)%.*?Wilson=([\d.]+)%.*?PF=([\d.]+).*?Score=([\d.]+)",
                line,
            )
            if m:
                meta.update(train_wr=m[1], wilson_wr=m[2], pf=m[3], score=m[4])

            m = re.search(r"Test:.*?WR=([\d.]+)%.*?PF=([\d.]+).*?Trades=(\d+)", line)
            if m:
                meta.update(test_wr=m[1], test_pf=m[2], test_trades=m[3])

            m = re.search(r"SL=([\d.]+)%.*?TP=([\d.]+)%.*?RR=([\d.]+)", line)
            if m:
                meta.update(sl_pct=m[1], tp_pct=m[2], rr=m[3])

            m = re.search(
                r"Pattern\s+(\d+).*?Cluster\s+(\d+)\s+\[([A-Z]+)\]\s+\[([A-Z_]+)\]",
                line,
            )
            if m:
                meta.update(
                    pattern_no=m[1], cluster=m[2], direction=m[3], bidir_mode=m[4]
                )
            continue

        eq = line.find("=")
        if eq < 1:
            continue

        val = line[eq + 1:].strip()
        # Strip inline comments
        ci = val.find("//")
        if ci >= 0:
            val = val[:ci].strip()
        val = val.rstrip(";").strip()

        key = line[:eq].strip()
        # Handle 'input double rsi14_lo' style — take only the last token
        key = key.split()[-1]
        params[key] = val

    return {"params": params, "meta": meta}


def _detect_input_block(lines: list[str]) -> dict[str, int] | None:
    """Find the replaceable input block (0-indexed line numbers).

    Prefer @BD_INPUT_BEGIN / @BD_INPUT_END markers so modeled-cost inputs
    after @BD_INPUT_END are never stripped from generated pattern EAs.
    Falls back to first..last ``input`` line if markers are absent.
    """
    begin_idx = end_idx = -1
    for i, ln in enumerate(lines):
        if _BD_INPUT_BEGIN in ln:
            begin_idx = i
        if _BD_INPUT_END in ln:
            end_idx = i

    if begin_idx >= 0 and end_idx > begin_idx:
        start = begin_idx
        for i in range(begin_idx - 1, -1, -1):
            t = lines[i].strip()
            if t.startswith("//") or t == "":
                start = i
            else:
                break
        end = end_idx
        for i in range(end_idx + 1, len(lines)):
            t = lines[i].strip()
            if t == "" or t.startswith("//"):
                end = i
            else:
                break
        return {"start": start, "end": end}

    first = last = -1
    for i, ln in enumerate(lines):
        if re.match(r"^\s*input\s+", ln):
            if first == -1:
                first = i
            last = i
    if first == -1:
        return None

    start = first
    for i in range(first - 1, -1, -1):
        t = lines[i].strip()
        if t.startswith("//") or t == "":
            start = i
        else:
            break

    end = last
    for i in range(last + 1, len(lines)):
        t = lines[i].strip()
        if t == "" or t.startswith("//"):
            end = i
        else:
            break

    return {"start": start, "end": end}


def _parse_template_inputs(template_lines: list[str]) -> list[_TemplateInput]:
    """Every ``input`` between @BD_INPUT_BEGIN and @BD_INPUT_END in the EA template."""
    block = _detect_input_block(template_lines)
    if block is None:
        return []
    specs: list[_TemplateInput] = []
    for ln in template_lines[block["start"] + 1 : block["end"]]:
        m = _INPUT_LINE_RE.match(ln.rstrip("\r"))
        if m:
            specs.append(
                _TemplateInput(
                    mql_type=m.group("type").strip(),
                    name=m.group("name"),
                    default_raw=m.group("val").strip(),
                )
            )
    return specs


def _collect_input_names(mq5_text: str) -> set[str]:
    # Normalise CRLF so ``$``-anchored input-line parsing works on Windows.
    text = mq5_text.replace("\r\n", "\n").replace("\r", "\n")
    names: set[str] = set()
    for m in _INPUT_LINE_RE.finditer(text):
        names.add(m.group("name"))
    return names


def _build_merged_params(
    parsed: dict[str, Any], template_specs: list[_TemplateInput]
) -> dict[str, Any]:
    """Merge .set params onto defaults, then template defaults for any missing keys."""
    merged: dict[str, Any] = dict(_DEFAULTS)
    for spec in template_specs:
        if spec.name not in merged:
            merged[spec.name] = spec.default_raw
    merged.update(parsed.get("params", {}))
    return merged


def _format_input_value(spec: _TemplateInput, merged: dict[str, Any]) -> str:
    raw = merged.get(spec.name, spec.default_raw)
    if spec.mql_type == "string":
        s = str(raw).strip()
        if s.startswith('"') and s.endswith('"'):
            return s
        return f'"{s}"'
    if "ENUM_TIMEFRAMES" in spec.mql_type:
        return str(raw).strip()
    if spec.mql_type == "bool":
        return "true" if str(raw).lower() == "true" else "false"
    return _fmt_num(raw)


def _render_input_declaration(spec: _TemplateInput, merged: dict[str, Any]) -> str:
    val = _format_input_value(spec, merged)
    pad = (spec.name + " ").ljust(24)
    return f"input {spec.mql_type} {pad}= {val};"


def _inject_missing_template_inputs(
    mq5_text: str,
    template_specs: list[_TemplateInput],
    merged: dict[str, Any],
) -> str:
    """Insert any template ``input`` declarations missing from the generated file."""
    present = _collect_input_names(mq5_text)
    missing = [s for s in template_specs if s.name not in present]
    if not missing:
        return mq5_text

    lines = mq5_text.splitlines()
    end_idx = next((i for i, ln in enumerate(lines) if _BD_INPUT_END in ln), -1)
    if end_idx < 0:
        raise ValueError("Generated .mq5 is missing @BD_INPUT_END marker.")

    patch: list[str] = [
        "",
        "//--- Auto-synced from PatternDiscoveryEA.mq5 (required inputs)",
    ]
    for spec in missing:
        patch.append(_render_input_declaration(spec, merged))
    patch.append("")

    out_lines = lines[:end_idx] + patch + lines[end_idx:]
    return "\n".join(out_lines)


def _validate_export_inputs(mq5_text: str, template_specs: list[_TemplateInput]) -> None:
    """Fail export if any template input name is absent from the generated .mq5."""
    present = _collect_input_names(mq5_text)
    required = {s.name for s in template_specs}
    missing = sorted(required - present)
    if missing:
        raise ValueError(
            "Generated .mq5 is missing input declarations from the EA template: "
            + ", ".join(missing)
        )


def _build_input_block(parsed: dict[str, Any], merged: dict[str, Any]) -> str:
    """Generate the populated MQL5 input declaration block.

    Mirrors buildInputBlock() in PatternDiscovery_Converter.html.
    """
    meta = parsed.get("meta", {})

    PAD = 24
    lines: list[str] = []
    ln = lines.append

    def padded(name: str) -> str:
        return (name + " ").ljust(PAD)

    # ── Header comment ────────────────────────────────────────────────────────
    ln("//================================================================")
    if meta.get("pattern_no"):
        ln(
            f"// Pattern {meta['pattern_no']} — Cluster {meta['cluster']}"
            f" [{meta['direction']}] [{meta['bidir_mode']}]"
        )
    if meta.get("train_wr"):
        ln(
            f"// Train : WR={meta['train_wr']}%  Wilson={meta['wilson_wr']}%"
            f"  PF={meta['pf']}  Score={meta['score']}"
        )
    if meta.get("test_wr"):
        ln(
            f"// Test  : WR={meta['test_wr']}%  PF={meta['test_pf']}"
            f"  Trades={meta['test_trades']}"
        )
    if meta.get("sl_pct"):
        ln(f"// SL={meta['sl_pct']}%  TP={meta['tp_pct']}%  Implied RR={meta['rr']}")
    ln("// Injected by BETTER DISCOVERY / Pattern Discovery v6 Converter")
    ln("//================================================================")
    ln(_BD_INPUT_BEGIN)
    ln("")

    # ── Identity ──────────────────────────────────────────────────────────────
    ln("//--- Identity")
    ln(f"input long   MagicNumber         = {merged['MagicNumber']};")
    ln("")

    # ── Signal timeframes (multi-TF) ──────────────────────────────────────────
    # The EA body references SignalTF1..SignalTF4 directly (g_signalTFs[] is
    # built from them in OnInit), so these MUST be present in the input block
    # or the .mq5 fails to compile with "undeclared identifier 'SignalTFn'".
    ln("//--- Signal timeframes (multi-TF)")
    ln("//    Each non-PERIOD_CURRENT slot becomes an active signal TF whose")
    ln("//    trend (EMA20>50>200) contributes to mtf_bull_score. The first")
    ln("//    active slot also provides the RSI14 used by htf_div.")
    ln("//    Defaults match the discovery setup; PERIOD_CURRENT disables a slot.")
    for slot in (1, 2, 3, 4):
        key = f"SignalTF{slot}"
        ln(f"input ENUM_TIMEFRAMES {padded(key)}= {merged[key]};")
    ln("")

    # ── Direction ─────────────────────────────────────────────────────────────
    _dir_labels = {"0": "LONG ONLY", "1": "SHORT ONLY", "2": "AUTO (discriminator)"}
    dm = str(merged["DirectionMode"])
    ln("//--- Direction  (0=LongOnly  1=ShortOnly  2=Auto)")
    ln(f"input int    DirectionMode       = {merged['DirectionMode']};   // {_dir_labels.get(dm, dm)}")
    ln("")

    # ── Risk ──────────────────────────────────────────────────────────────────
    ln("//--- Risk")
    ln(f"input double SL_Pct              = {_fmt_num(merged['SL_Pct'])};")
    ln(f"input double TP_Pct              = {_fmt_num(merged['TP_Pct'])};")
    ln(f"input double Lots                = {_fmt_num(merged['Lots'])};")
    ln("")
    ln("//--- Trading costs (R multiples) — names match discovery .set file")
    ln(f"input double {padded('Commission_R')}= {_fmt_num(merged['Commission_R'])};")
    ln(f"input double {padded('Swap_R_PerBar')}= {_fmt_num(merged['Swap_R_PerBar'])};")
    ln("")

    # ── Trade management ──────────────────────────────────────────────────────
    ln("//--- Trade management")
    ln(f"input int    CooldownBars        = {merged['CooldownBars']};")
    ln(f"input double BreakevenAtR        = {_fmt_num(merged['BreakevenAtR'])};")
    ln(f"input bool   UseTrailing         = {merged['UseTrailing']};")
    ln(f"input double TrailingStart       = {_fmt_num(merged['TrailingStart'])};")
    ln(f"input double TrailingStep        = {_fmt_num(merged['TrailingStep'])};")
    ln(f"input int    {padded('MaxHoldBars')}= {merged['MaxHoldBars']};   // Force-close after N bars (0=hold to SL/TP only); matches sim MAX_HOLD_BARS")
    ln("")

    # ── Session filter ────────────────────────────────────────────────────────
    ln("//--- Session filter (UTC hours)")
    ln(f"input bool   TradeAsian          = {merged['TradeAsian']};")
    ln(f"input bool   TradeLondon         = {merged['TradeLondon']};")
    ln(f"input bool   TradeNY             = {merged['TradeNY']};")
    ln(f"input bool   TradeOverlap        = {merged['TradeOverlap']};")
    ln(f"input bool   TradeOff            = {merged['TradeOff']};")
    ln("")

    # ── Discriminator ─────────────────────────────────────────────────────────
    ln("//--- Direction discriminator (only used when DirectionMode == 2)")
    ln(f"input int    Discrim_Col         = {merged['Discrim_Col']};")
    ln(f"input double Discrim_Thresh      = {_fmt_num(merged['Discrim_Thresh'])};")
    ln(f"input int    Discrim_Dir         = {merged['Discrim_Dir']};   // 1=col>thresh->LONG | -1=col>thresh->SHORT")
    ln("")

    # ── Risk controls ─────────────────────────────────────────────────────────
    ln("//--- Risk controls")
    ln(f"input double {padded('MaxSpreadPoints')}= {_fmt_num(merged['MaxSpreadPoints'])};   // Skip entry if spread > this (0=disabled)")
    ln(f"input double {padded('MaxDailyLossR')}= {_fmt_num(merged['MaxDailyLossR'])};    // Max daily loss in R units (0=disabled)")
    ln(f"input int    {padded('MaxOpenPositions')}= {merged['MaxOpenPositions']};      // Max simultaneous positions for this magic")
    ln("")

    # ── Debug ─────────────────────────────────────────────────────────────────
    dbg = "true" if str(merged.get("DebugMode", "false")).lower() == "true" else "false"
    ln("//--- Debug")
    ln(f"input bool   {padded('DebugMode')}= {dbg};  // Enable per-bar filter diagnostics in terminal")
    ln("")

    # ── Hours ban ─────────────────────────────────────────────────────────────
    hours_ban = str(merged.get("HoursBan", ""))
    ln("//--- Hours ban (LOCAL PC time)")
    ln('//    Comma-separated local hours where NO new trades will be opened.')
    ln('//    Example: "1,4,14" bans 01-02, 04-05, 14-15 local time. Leave empty to disable.')
    ln(f'input string {padded("HoursBan")}= "{hours_ban}";')
    ln("")

    # ── EOD close ─────────────────────────────────────────────────────────────
    eod_en = "true" if str(merged.get("EODCloseEnabled", "false")).lower() == "true" else "false"
    ln("//--- End-of-day close (LOCAL PC time)")
    ln("//    When enabled, closes all positions once local hour >= EODCloseHour.")
    ln(f"input bool   {padded('EODCloseEnabled')}= {eod_en};")
    ln(f"input int    {padded('EODCloseHour')}= {merged.get('EODCloseHour', 22)};   // local hour (0-23)")
    ln("")

    # ── Entry filters ─────────────────────────────────────────────────────────
    ln("//--- Entry filters")
    ln("")

    for col, label in _FILTER_GROUPS:
        lo = merged.get(f"{col}_lo", -999.0)
        hi = merged.get(f"{col}_hi", 999.0)
        active = not (float(lo) <= -999 and float(hi) >= 999)
        short_label = label.replace("Entry filter: ", "")
        active_marker = "  \u2605 ACTIVE" if active else ""
        ln(f"//--- {label}{active_marker}")
        ln(f"input double {padded(col + '_lo')}= {_fmt_num(lo)};  // {short_label}")
        ln(f"input double {padded(col + '_hi')}= {_fmt_num(hi)};  // {short_label}")
        ln("")

    ln(_BD_INPUT_END)
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def export_report(mq5_path: str | Path) -> dict[str, Any]:
    """Summarise whether a generated .mq5 has every template input declared."""
    text = Path(mq5_path).read_text(encoding="utf-8", errors="replace")
    present = _collect_input_names(text)
    template_lines = Path(default_template_path()).read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    required = {s.name for s in _parse_template_inputs(template_lines)}
    missing = sorted(required - present)
    return {
        "path": str(Path(mq5_path).resolve()),
        "inputs_present": len(present),
        "inputs_required": len(required),
        "missing_inputs": missing,
        "has_commission_r": "Commission_R" in present,
        "has_swap_r_per_bar": "Swap_R_PerBar" in present,
    }


def export_from_set_path(
    set_path: str | Path,
    template_path: str | None = None,
    output_name: str | None = None,
    also_write_paths: list[str | Path] | None = None,
) -> str:
    """Read a .set from disk and export; optionally mirror the .mq5 to extra paths."""
    set_file = Path(set_path)
    if not set_file.is_file():
        raise FileNotFoundError(f".set file not found: {set_file}")
    content = set_file.read_text(encoding="utf-8", errors="replace")
    out = export(
        content,
        template_path=template_path,
        output_name=output_name,
        also_write_paths=also_write_paths,
    )
    return out


def export(
    set_content: str,
    template_path: str | None = None,
    output_name: str | None = None,
    also_write_paths: list[str | Path] | None = None,
) -> str:
    """Convert .set content + EA template into a ready-to-compile .mq5 file.

    Args:
        set_content:   Raw text of the .set file produced by pattern_discovery_v6.
        template_path: Path to the .mq5 template; None → bundled default.
        output_name:   Override output filename stem (no extension).
        also_write_paths: Optional extra paths to write the same .mq5 bytes
            (e.g. next to the source .set on the Desktop).

    Returns:
        Absolute path to the generated .mq5 in userdata/mql/.

    Raises:
        FileNotFoundError: template not found.
        ValueError: input block not found in template.
    """
    tpl_path = Path(template_path) if template_path else _DEFAULT_TEMPLATE
    if not tpl_path.is_file():
        raise FileNotFoundError(f"EA template not found: {tpl_path}")

    template_text = tpl_path.read_text(encoding="utf-8", errors="replace")
    template_lines = template_text.splitlines()

    parsed = parse_set_file(set_content)
    template_specs = _parse_template_inputs(template_lines)
    if not template_specs:
        raise ValueError(
            "EA template has no parsable inputs between "
            f"{_BD_INPUT_BEGIN} and {_BD_INPUT_END}."
        )

    merged = _build_merged_params(parsed, template_specs)
    input_block = _build_input_block(parsed, merged)

    block_info = _detect_input_block(template_lines)
    if block_info is None:
        raise ValueError("Could not locate 'input' block in the EA template.")

    before = template_lines[: block_info["start"]]
    after = template_lines[block_info["end"] + 1 :]
    merged_text = "\n".join(before) + "\n" + input_block + "\n" + "\n".join(after)
    merged_text = _inject_missing_template_inputs(merged_text, template_specs, merged)
    _validate_export_inputs(merged_text, template_specs)

    # Determine output filename
    if output_name is None:
        output_name = _suggest_filename(
            parsed.get("meta", {}), parsed.get("params", {})
        )

    _MQL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _MQL_OUTPUT_DIR / f"{output_name}.mq5"
    out_file.write_text(merged_text, encoding="utf-8", newline="\n")
    if also_write_paths:
        for extra in also_write_paths:
            dest = Path(extra)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(merged_text, encoding="utf-8", newline="\n")
    return str(out_file)


def default_template_path() -> str:
    """Return the path to the bundled PatternDiscoveryEA.mq5 template."""
    return str(_DEFAULT_TEMPLATE)
