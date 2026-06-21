"""Parse MT5 Strategy Tester HTML into a stable metric contract."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from .models import ReportMetrics


def _read_report(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    encodings = ("utf-16-le", "utf-8-sig", "utf-8") if b"\x00" in raw[:200] else ("utf-8-sig", "utf-8")
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _number(value: str) -> float | None:
    text = value.replace("\xa0", " ").strip()
    match = re.search(r"[-+]?\d[\d\s]*(?:[.,]\d+)?", text)
    if not match:
        return None
    normalized = re.sub(r"\s", "", match.group(0)).replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _percent(value: str) -> float | None:
    matches = re.findall(r"([-+]?\d+(?:[.,]\d+)?)\s*%", value)
    return float(matches[-1].replace(",", ".")) if matches else None


def _summarize(values: list[float]) -> dict[str, float | int | None]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(values),
        "wins": len(wins),
        "win_rate_pct": round(100.0 * len(wins) / len(values), 4) if values else None,
        "net_profit": round(sum(values), 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "average_profit": round(sum(values) / len(values), 4) if values else None,
    }


def _deal_segments(soup: BeautifulSoup) -> tuple[int, dict[str, dict[str, dict[str, float | int | None]]]]:
    rows = soup.find_all("tr")
    in_deals = False
    columns: list[str] = []
    pending: list[dict[str, str]] = []
    trades: list[dict[str, str | float]] = []
    for row in rows:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if cells == ["Deals"]:
            in_deals = True
            continue
        if not in_deals or not cells:
            continue
        if cells[0] == "Time" and "Direction" in cells:
            columns = [cell.lower() for cell in cells]
            continue
        if not columns or len(cells) < len(columns):
            continue
        deal = dict(zip(columns, cells))
        direction = deal.get("direction", "").lower()
        if direction == "in":
            pending.append(deal)
            continue
        if direction != "out" or not pending:
            continue
        entry = pending.pop(0)
        regime_match = re.search(r"R:(\d+)", entry.get("comment", ""))
        entry_cost = (_number(entry.get("commission", "")) or 0) + (_number(entry.get("swap", "")) or 0)
        exit_cost = (_number(deal.get("commission", "")) or 0) + (_number(deal.get("swap", "")) or 0)
        profit = (_number(deal.get("profit", "")) or 0) + entry_cost + exit_cost
        trades.append(
            {
                "time": entry.get("time", ""),
                "direction": entry.get("type", "unknown").lower(),
                "regime": regime_match.group(1) if regime_match else "unknown",
                "profit": profit,
            }
        )

    groups: dict[str, dict[str, list[float]]] = {
        "regime": {}, "direction": {}, "month": {}, "entry_hour": {}
    }
    for trade in trades:
        profit = float(trade["profit"])
        timestamp = str(trade["time"])
        try:
            dt = datetime.strptime(timestamp, "%Y.%m.%d %H:%M:%S")
            month, hour = dt.strftime("%Y-%m"), dt.strftime("%H")
        except ValueError:
            month, hour = "unknown", "unknown"
        keys = {
            "regime": str(trade["regime"]),
            "direction": str(trade["direction"]),
            "month": month,
            "entry_hour": hour,
        }
        for group, key in keys.items():
            groups[group].setdefault(key, []).append(profit)
    return len(trades), {
        group: {key: _summarize(values) for key, values in sorted(items.items())}
        for group, items in groups.items()
    }


def parse_mt5_report(report_path: str | Path) -> ReportMetrics:
    path = Path(report_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"MT5 report not found: {path}")
    soup = BeautifulSoup(_read_report(path), "html.parser")

    raw: dict[str, str] = {}
    inputs: dict[str, str] = {}
    current_section = ""
    for row in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if not cells:
            continue
        joined = " ".join(cells)
        if joined in {"Settings", "Results", "Orders", "Deals"}:
            current_section = joined
            continue
        if current_section == "Settings" and "=" in joined and not cells[0].endswith(":"):
            key, value = joined.split("=", 1)
            inputs[key.strip()] = value.strip()
        label_indexes = [i for i, cell in enumerate(cells) if cell.endswith(":")]
        for label_index in label_indexes:
            next_label = next((i for i in label_indexes if i > label_index), len(cells))
            value = next((c for c in cells[label_index + 1 : next_label] if c), "")
            label = cells[label_index].rstrip(":").strip()
            if label == "Inputs" and "=" in value:
                key, input_value = value.split("=", 1)
                inputs[key.strip()] = input_value.strip()
            else:
                raw[label] = value

    total = int(_number(raw.get("Total Trades", "")) or 0)
    won = int(_number(raw.get("Profit Trades (% of total)", "")) or 0)
    win_rate = (100.0 * won / total) if total else None
    closed_trades, segments = _deal_segments(soup)
    return ReportMetrics(
        expert=raw.get("Expert", ""),
        symbol=raw.get("Symbol", ""),
        period=raw.get("Period", ""),
        net_profit=_number(raw.get("Total Net Profit", "")),
        profit_factor=_number(raw.get("Profit Factor", "")),
        expected_payoff=_number(raw.get("Expected Payoff", "")),
        total_trades=total,
        win_rate_pct=win_rate,
        maximal_balance_drawdown_pct=_percent(raw.get("Balance Drawdown Maximal", "")),
        maximal_equity_drawdown_pct=_percent(raw.get("Equity Drawdown Maximal", "")),
        gross_profit=_number(raw.get("Gross Profit", "")),
        gross_loss=_number(raw.get("Gross Loss", "")),
        closed_trades_parsed=closed_trades,
        segments=segments,
        inputs=inputs,
        raw=raw,
    )
