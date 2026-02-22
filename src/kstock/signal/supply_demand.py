"""Supply and demand pattern detector (Sections 56-57 - 수급 패턴 분석).

Detects institutional, foreign, and retail trading patterns from daily
supply/demand history. Produces score adjustments and Telegram alerts.
All functions are pure computation with no external API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SupplyDemandData:
    """Single day's supply/demand data for a ticker."""

    ticker: str
    name: str
    date: str                 # "YYYY-MM-DD"
    foreign_net: float        # KRW (억원), positive = net buy
    institution_net: float    # KRW (억원)
    retail_net: float         # KRW (억원)
    program_net: float        # KRW (억원)
    short_balance: float      # shares (주)
    short_ratio: float        # % of outstanding shares


@dataclass
class SupplyDemandSignal:
    """Aggregated supply/demand signal with detected patterns."""

    ticker: str
    name: str
    patterns: list[str] = field(default_factory=list)
    score_adj: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Pattern detection thresholds
# ---------------------------------------------------------------------------

_FOREIGN_CONSEC_BUY_DAYS = 3     # consecutive days for foreign buy signal
_FOREIGN_CONSEC_SELL_DAYS = 5    # consecutive days for foreign sell signal
_SHORT_CHANGE_THRESHOLD = 50.0   # % change in short balance for alert
_RETAIL_DOMINANCE_RATIO = 3.0    # retail buy >> foreign sell ratio

_SCORE_CAP = 20


# ---------------------------------------------------------------------------
# Internal pattern detection helpers
# ---------------------------------------------------------------------------


def _count_consecutive_foreign_buy(history: list[dict]) -> int:
    """Count consecutive days of foreign net buying from most recent.

    Positive return = consecutive buy days.
    Negative return = consecutive sell days.
    """
    if not history:
        return 0

    # History assumed sorted by date ascending; check from the end
    count = 0
    direction = 0  # 0=unset, 1=buy, -1=sell

    for entry in reversed(history):
        foreign_net = entry.get("foreign_net", 0.0)
        if foreign_net > 0:
            if direction == 0:
                direction = 1
            if direction == 1:
                count += 1
            else:
                break
        elif foreign_net < 0:
            if direction == 0:
                direction = -1
            if direction == -1:
                count += 1
            else:
                break
        else:
            break

    return count * direction if direction != 0 else 0


def _count_consecutive_institution_buy(history: list[dict]) -> int:
    """Count consecutive days of institutional net buying from most recent."""
    if not history:
        return 0

    count = 0
    direction = 0

    for entry in reversed(history):
        inst_net = entry.get("institution_net", 0.0)
        if inst_net > 0:
            if direction == 0:
                direction = 1
            if direction == 1:
                count += 1
            else:
                break
        elif inst_net < 0:
            if direction == 0:
                direction = -1
            if direction == -1:
                count += 1
            else:
                break
        else:
            break

    return count * direction if direction != 0 else 0


def _check_dual_buy(history: list[dict], days: int = 3) -> bool:
    """Check if both foreign and institution are net buyers for recent N days.

    This is the "쌍끌이 매수" pattern.
    """
    if len(history) < days:
        return False

    recent = history[-days:]
    for entry in recent:
        if entry.get("foreign_net", 0.0) <= 0:
            return False
        if entry.get("institution_net", 0.0) <= 0:
            return False
    return True


def _check_retail_trap(history: list[dict], days: int = 3) -> bool:
    """Check for retail heavy buy + foreign sell pattern.

    Retail buying dominates while foreign investors are selling — often
    a negative signal ("개미 물림" pattern).
    """
    if len(history) < days:
        return False

    recent = history[-days:]
    for entry in recent:
        retail = entry.get("retail_net", 0.0)
        foreign = entry.get("foreign_net", 0.0)
        # Retail buying significantly while foreign selling
        if retail <= 0 or foreign >= 0:
            return False
        if abs(foreign) > 0 and retail / abs(foreign) < _RETAIL_DOMINANCE_RATIO:
            return False
    return True


def _compute_short_change(history: list[dict]) -> float:
    """Compute short balance change percentage between first and last entry.

    Returns percentage change. Positive = short balance increased.
    """
    if len(history) < 2:
        return 0.0

    first_short = history[0].get("short_balance", 0.0)
    last_short = history[-1].get("short_balance", 0.0)

    if first_short <= 0:
        return 0.0

    return round((last_short - first_short) / first_short * 100, 2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_patterns(
    history: list[dict],
    ticker: str = "",
    name: str = "",
) -> SupplyDemandSignal:
    """Detect supply/demand patterns from daily history.

    Patterns detected:
        1. Foreign 3-day consecutive buy: +10
        2. Foreign 5-day consecutive sell: -15
        3. Foreign + Institution both buy (쌍끌이 매수): +15
        4. Retail heavy buy + Foreign sell (개미 물림): -10
        5. Short balance increase >= 50%: -5
        6. Short balance decrease >= 50%: +5

    Score adjustment is capped at +-20.

    Args:
        history: List of daily supply/demand dicts sorted by date ascending.
            Expected keys: date, foreign_net, institution_net, retail_net,
            program_net, short_balance, short_ratio.
        ticker: Stock ticker code (for labeling).
        name: Stock name (for labeling).

    Returns:
        SupplyDemandSignal with detected patterns, score adjustment, and message.
    """
    patterns: list[str] = []
    score_adj = 0

    if not history:
        return SupplyDemandSignal(
            ticker=ticker,
            name=name,
            patterns=[],
            score_adj=0,
            message="수급 데이터 없음",
        )

    # --- Pattern 1 & 2: Foreign consecutive buy/sell ---
    foreign_consec = _count_consecutive_foreign_buy(history)

    if foreign_consec >= _FOREIGN_CONSEC_BUY_DAYS:
        patterns.append(f"외국인 {foreign_consec}일 연속 순매수")
        score_adj += 10

    if foreign_consec <= -_FOREIGN_CONSEC_SELL_DAYS:
        patterns.append(f"외국인 {abs(foreign_consec)}일 연속 순매도")
        score_adj -= 15

    # --- Pattern 3: Dual buy (쌍끌이) ---
    if _check_dual_buy(history, days=3):
        patterns.append("외국인+기관 쌍끌이 매수 (3일)")
        score_adj += 15

    # --- Pattern 4: Retail trap ---
    if _check_retail_trap(history, days=3):
        patterns.append("개인 대량 매수 + 외국인 매도 (개미 물림 주의)")
        score_adj -= 10

    # --- Pattern 5 & 6: Short balance change ---
    short_change = _compute_short_change(history)

    if short_change >= _SHORT_CHANGE_THRESHOLD:
        patterns.append(f"공매도 잔고 {short_change:+.0f}% 급증")
        score_adj -= 5

    if short_change <= -_SHORT_CHANGE_THRESHOLD:
        patterns.append(f"공매도 잔고 {short_change:+.0f}% 감소")
        score_adj += 5

    # Cap score
    score_adj = max(-_SCORE_CAP, min(_SCORE_CAP, score_adj))

    # Build summary message
    if patterns:
        message = f"{name} 수급 신호: " + ", ".join(patterns)
    else:
        message = f"{name} 수급 특이사항 없음"

    signal = SupplyDemandSignal(
        ticker=ticker,
        name=name,
        patterns=patterns,
        score_adj=score_adj,
        message=message,
    )

    logger.info(
        "Supply/demand %s (%s): patterns=%d score_adj=%d",
        ticker, name, len(patterns), score_adj,
    )

    return signal


def compute_supply_score(signal: SupplyDemandSignal) -> int:
    """Return the score adjustment from the signal, capped at +-20.

    Convenience accessor that re-validates the cap.

    Args:
        signal: Precomputed SupplyDemandSignal.

    Returns:
        Integer score adjustment in range [-20, 20].
    """
    return max(-_SCORE_CAP, min(_SCORE_CAP, signal.score_adj))


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def _format_krw(amount: float) -> str:
    """Format KRW amount in human-readable form."""
    if abs(amount) >= 10000:
        return f"{amount / 10000:,.1f}조원"
    if abs(amount) >= 1:
        return f"{amount:,.0f}억원"
    return f"{amount * 100:,.0f}백만원"


def _net_emoji(value: float) -> str:
    """Return emoji for net buy/sell."""
    if value > 0:
        return "\U0001f7e2"  # green
    if value < 0:
        return "\U0001f534"  # red
    return "\u26aa"          # white


def format_supply_alert(
    signal: SupplyDemandSignal,
    history: list[dict] | None = None,
) -> str:
    """Format supply/demand alert for Telegram.

    No ** bold markers. Korean text throughout.

    Args:
        signal: Detected supply/demand signal.
        history: Optional recent history for additional context.

    Returns:
        Multi-line formatted Telegram message.
    """
    lines: list[str] = []

    # Header
    if signal.score_adj >= 10:
        header_emoji = "\U0001f7e2"
    elif signal.score_adj <= -10:
        header_emoji = "\U0001f534"
    else:
        header_emoji = "\U0001f4ca"

    lines.append(f"{header_emoji} {signal.name} ({signal.ticker}) 수급 분석")
    lines.append("")

    # Patterns
    if signal.patterns:
        lines.append("감지된 패턴:")
        for pattern in signal.patterns:
            # Determine emoji per pattern
            if any(pos in pattern for pos in ("순매수", "쌍끌이", "감소")):
                p_emoji = "\U0001f7e2"
            elif any(neg in pattern for neg in ("순매도", "물림", "급증")):
                p_emoji = "\U0001f534"
            else:
                p_emoji = "\u26aa"
            lines.append(f"  {p_emoji} {pattern}")
        lines.append("")
    else:
        lines.append("수급 특이사항 없음")
        lines.append("")

    # Recent daily breakdown (last 5 days)
    if history and len(history) > 0:
        recent = history[-5:]
        lines.append("최근 수급 현황:")
        for entry in recent:
            date = entry.get("date", "")
            foreign = entry.get("foreign_net", 0.0)
            inst = entry.get("institution_net", 0.0)
            retail = entry.get("retail_net", 0.0)

            f_emoji = _net_emoji(foreign)
            i_emoji = _net_emoji(inst)

            lines.append(
                f"  {date} | 외인 {f_emoji}{foreign:+,.0f}억 "
                f"기관 {i_emoji}{inst:+,.0f}억 "
                f"개인 {retail:+,.0f}억"
            )
        lines.append("")

    # Short info from latest entry
    if history and len(history) > 0:
        latest = history[-1]
        short_ratio = latest.get("short_ratio", 0.0)
        short_balance = latest.get("short_balance", 0.0)
        if short_balance > 0 or short_ratio > 0:
            short_emoji = "\U0001f534" if short_ratio >= 5.0 else "\u26aa"
            lines.append(
                f"공매도: {short_emoji} 잔고 {short_balance:,.0f}주 "
                f"(비율 {short_ratio:.1f}%)"
            )
            lines.append("")

    # Score adjustment
    if signal.score_adj > 0:
        lines.append(f"수급 스코어: +{signal.score_adj}점")
    elif signal.score_adj < 0:
        lines.append(f"수급 스코어: {signal.score_adj}점")
    else:
        lines.append("수급 스코어: 0점 (중립)")

    return "\n".join(lines)
