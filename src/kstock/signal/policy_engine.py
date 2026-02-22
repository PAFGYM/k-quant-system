"""Policy/political event calendar engine for K-Quant v3.0.

Supports both legacy events format and enhanced yearly_patterns + annual_policies.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("config/policy_calendar.yaml")


def _load_config(path: Path | None = None) -> dict:
    p = path or _DEFAULT_PATH
    if not p.exists():
        return {"events": [], "leading_sectors": {"tier1": [], "tier2": []}}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _pattern_to_event(pattern: dict, year: int) -> dict:
    """Convert a yearly_pattern entry to an event with concrete dates."""
    sm = pattern.get("start_month", 1)
    sd = pattern.get("start_day", 1)
    em = pattern.get("end_month", 12)
    ed = pattern.get("end_day", 31)
    start = f"{year}-{sm:02d}-{sd:02d}"
    end = f"{year}-{em:02d}-{ed:02d}"
    ev = dict(pattern)
    ev["start"] = start
    ev["end"] = end
    return ev


def _annual_to_event(policy: dict) -> dict:
    """Convert an annual_policy entry to an event with concrete dates."""
    year = policy.get("year", date.today().year)
    ev = dict(policy)
    ev["start"] = f"{year}-01-01"
    ev["end"] = f"{year}-12-31"
    return ev


def get_active_events(today: date | None = None, config: dict | None = None) -> list[dict]:
    """Return events active on the given date.

    Supports three config formats:
    - Legacy: config["events"] with start/end date strings
    - Enhanced: config["yearly_patterns"] with start_month/day, end_month/day
    - Annual: config["annual_policies"] with year
    """
    if config is None:
        config = _load_config()
    if today is None:
        today = date.today()
    today_str = today.isoformat()

    active = []

    # Legacy events (start/end date strings)
    for ev in config.get("events", []):
        start = ev.get("start", "")
        end = ev.get("end", "")
        if start <= today_str <= end:
            active.append(ev)

    # Yearly patterns (month/day based)
    for pattern in config.get("yearly_patterns", []):
        ev = _pattern_to_event(pattern, today.year)
        start = ev.get("start", "")
        end = ev.get("end", "")
        if start <= today_str <= end:
            active.append(ev)

    # Annual policies (year based)
    for policy in config.get("annual_policies", []):
        year = policy.get("year", today.year)
        if year == today.year:
            ev = _annual_to_event(policy)
            active.append(ev)

    return active


def get_adjustments(today: date | None = None, config: dict | None = None) -> dict:
    """Merge adjustments from all active events."""
    events = get_active_events(today, config)
    merged: dict = {}
    for ev in events:
        adj = ev.get("adjustments", {})
        for k, v in adj.items():
            if k in merged:
                if isinstance(v, bool):
                    merged[k] = merged[k] and v
                elif isinstance(v, (int, float)):
                    if k.endswith("_weight"):
                        merged[k] = max(merged[k], v)
                    elif k.endswith("_pct"):
                        merged[k] = max(merged[k], v)
                    else:
                        merged[k] = merged.get(k, 0) + v
            else:
                merged[k] = v
    return merged


def get_score_bonus(
    ticker: str,
    sector: str = "",
    market: str = "KOSPI",
    today: date | None = None,
    config: dict | None = None,
) -> int:
    """Compute policy-driven score bonus for a ticker.

    Returns bonus points (can be 0).
    """
    if config is None:
        config = _load_config()
    events = get_active_events(today, config)
    bonus = 0

    for ev in events:
        effect = ev.get("effect", "")
        adj = ev.get("adjustments", {})

        # Bullish sector: target sectors get bonus
        if effect == "bullish_sector":
            targets = ev.get("target_sectors", [])
            if sector in targets:
                bonus += adj.get("sector_bonus", 0)

        # Bullish KOSDAQ
        if effect == "bullish_kosdaq" and market in ("KOSDAQ", "kosdaq"):
            bonus += adj.get("kosdaq_bonus", 0)

        # Bullish value: PBR-based
        if effect == "bullish_value":
            bonus += adj.get("value_bonus", 0)

    # Leading sector bonus
    leading = config.get("leading_sectors", {})
    tier1 = leading.get("tier1", [])
    tier2 = leading.get("tier2", [])
    if sector in tier1:
        bonus += 5
    elif sector in tier2:
        bonus += 2

    return min(bonus, 20)  # cap at 20


def has_bullish_policy(today: date | None = None, config: dict | None = None) -> bool:
    """Check if any bullish policy event is active."""
    events = get_active_events(today, config)
    return any(ev.get("effect", "").startswith("bullish") for ev in events)


def get_policy_summary(today: date | None = None, config: dict | None = None) -> str:
    """Alias for get_telegram_summary for backward compatibility."""
    return get_telegram_summary(today, config)


def get_telegram_summary(today: date | None = None, config: dict | None = None) -> str:
    """Generate Telegram-friendly summary of active policy events."""
    events = get_active_events(today, config)
    if not events:
        return ""

    lines = ["\U0001f3db\ufe0f 정책/정치 이벤트"]
    effect_emoji = {
        "bullish": "\U0001f7e2",
        "bullish_sector": "\U0001f7e2",
        "bullish_kosdaq": "\U0001f7e2",
        "bullish_value": "\U0001f7e2",
        "cautious": "\U0001f7e1",
        "neutral": "\u26aa",
        "volatile": "\U0001f534",
        "mixed": "\U0001f7e0",
        "bearish_selective": "\U0001f534",
    }

    for ev in events:
        emoji = effect_emoji.get(ev.get("effect", ""), "\u26aa")
        name = ev.get("name", "")
        desc = ev.get("description", "")
        line = f"{emoji} {name}"
        if desc:
            first_line = desc.split("\n")[0].strip()
            line += f"\n  {first_line}"
        lines.append(line)

    adj = get_adjustments(today, config)
    if adj:
        notes = []
        if adj.get("leverage_etf_ok") is False:
            notes.append("레버리지 ETF 비추천")
        cash_min = adj.get("cash_min_pct")
        if cash_min and cash_min >= 15:
            notes.append(f"현금 최소 {cash_min}% 유지")
        mom_w = adj.get("momentum_weight")
        if mom_w and mom_w >= 1.3:
            notes.append("모멘텀 강화")
        if notes:
            lines.append("\u2192 " + ", ".join(notes))

    return "\n".join(lines)
