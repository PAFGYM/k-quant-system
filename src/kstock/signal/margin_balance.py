"""Margin balance analysis module (신용/대출 잔고 분석).

Detects 4 patterns from margin (credit) trading data:
1. 반대매매 폭탄: High credit ratio + price declining → forced liquidation risk
2. 신용 청산 완료: Credit ratio dropping after being high → recovery signal
3. 개인 빚투 과열: Credit buying surging → overheated retail leverage
4. 양방향 레버리지 극단: Both short and margin extreme → high volatility

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
class MarginData:
    """Single day's margin (credit) balance data for a ticker."""

    ticker: str
    name: str
    date: str                  # "YYYY-MM-DD"
    credit_buy: int = 0        # 신용 매수 (주)
    credit_sell: int = 0       # 신용 매도 (주)
    credit_balance: int = 0    # 신용 잔고 (주)
    credit_ratio: float = 0.0  # 신용 비율 (%)
    collateral_balance: int = 0  # 담보 대출 잔고 (주)


@dataclass
class MarginPattern:
    """A detected margin balance pattern."""

    name: str
    code: str
    description: str
    detected: bool = False
    score_adj: int = 0
    severity: str = ""  # "warning", "danger", "positive"


@dataclass
class MarginSignal:
    """Aggregated margin analysis signal."""

    ticker: str
    name: str
    patterns: list[MarginPattern] = field(default_factory=list)
    total_score_adj: int = 0
    message: str = ""
    credit_ratio: float = 0.0  # Latest credit ratio
    is_dangerous: bool = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FORCED_LIQUIDATION_CREDIT_RATIO = 5.0    # Credit ratio >= 5% → high risk
_FORCED_LIQUIDATION_PRICE_DROP_PCT = -5.0  # Price drop >= 5% while high credit

_CREDIT_CLEARING_PEAK_RATIO = 5.0         # Peak credit ratio threshold
_CREDIT_CLEARING_DROP_PCT = -30.0         # Credit ratio dropped >= 30% from peak

_RETAIL_OVERHEATED_SURGE_PCT = 50.0       # Credit balance surge >= 50% over 5 days
_RETAIL_OVERHEATED_MIN_RATIO = 3.0        # Minimum credit ratio for overheated

_DUAL_LEVERAGE_SHORT_RATIO = 5.0          # Short ratio threshold for dual leverage
_DUAL_LEVERAGE_CREDIT_RATIO = 3.0         # Credit ratio threshold for dual leverage

_SCORE_CAP = 15


# ---------------------------------------------------------------------------
# Pattern detection functions
# ---------------------------------------------------------------------------

def _detect_forced_liquidation(
    margin_history: list[dict],
    price_history: list[dict],
) -> MarginPattern:
    """Detect 반대매매 폭탄: High credit ratio + price declining.

    When credit balance is high and the stock price drops significantly,
    forced liquidation (반대매매) may occur, causing cascading sell pressure.
    """
    pattern = MarginPattern(
        name="반대매매 폭탄",
        code="forced_liquidation",
        description="높은 신용 잔고 + 주가 하락 → 반대매매 위험",
    )

    if not margin_history or len(price_history) < 5:
        return pattern

    latest_margin = margin_history[-1]
    credit_ratio = latest_margin.get("credit_ratio", 0.0)

    if credit_ratio < _FORCED_LIQUIDATION_CREDIT_RATIO:
        return pattern

    # Price change over 5 days
    start_price = price_history[-5].get("close", 0)
    end_price = price_history[-1].get("close", 0)
    if start_price <= 0:
        return pattern

    price_change_pct = (end_price - start_price) / start_price * 100

    if price_change_pct <= _FORCED_LIQUIDATION_PRICE_DROP_PCT:
        pattern.detected = True
        pattern.score_adj = -12
        pattern.severity = "danger"
        pattern.description = (
            f"신용 비율 {credit_ratio:.1f}% + "
            f"주가 {price_change_pct:+.1f}% → 반대매매 연쇄 위험"
        )

    return pattern


def _detect_credit_clearing(margin_history: list[dict]) -> MarginPattern:
    """Detect 신용 청산 완료: Credit ratio dropping after being high.

    When credit ratio significantly decreases from a peak, forced selling
    pressure is relieved — often a recovery signal.
    """
    pattern = MarginPattern(
        name="신용 청산 완료",
        code="credit_clearing",
        description="신용 잔고 대량 청산 → 매도 압력 해소",
    )

    if len(margin_history) < 10:
        return pattern

    # Find peak credit ratio in last 20 entries
    window = margin_history[-20:] if len(margin_history) >= 20 else margin_history
    peak_ratio = max(e.get("credit_ratio", 0.0) for e in window)
    current_ratio = margin_history[-1].get("credit_ratio", 0.0)

    if peak_ratio < _CREDIT_CLEARING_PEAK_RATIO:
        return pattern

    if peak_ratio > 0:
        drop_pct = (current_ratio - peak_ratio) / peak_ratio * 100
    else:
        drop_pct = 0.0

    if drop_pct <= _CREDIT_CLEARING_DROP_PCT:
        pattern.detected = True
        pattern.score_adj = 8
        pattern.severity = "positive"
        pattern.description = (
            f"신용 비율 {peak_ratio:.1f}% → {current_ratio:.1f}% "
            f"({drop_pct:+.0f}%) → 매도 압력 해소"
        )

    return pattern


def _detect_retail_overheated(margin_history: list[dict]) -> MarginPattern:
    """Detect 개인 빚투 과열: Credit buying surging.

    Rapid increase in credit balance indicates retail investors borrowing
    heavily to buy — a negative contrarian signal.
    """
    pattern = MarginPattern(
        name="개인 빚투 과열",
        code="retail_overheated",
        description="신용 매수 급증 → 개인 레버리지 과열",
    )

    if len(margin_history) < 5:
        return pattern

    # Credit balance change over 5 days
    start_balance = margin_history[-5].get("credit_balance", 0)
    end_balance = margin_history[-1].get("credit_balance", 0)
    current_ratio = margin_history[-1].get("credit_ratio", 0.0)

    if start_balance <= 0:
        return pattern

    balance_change_pct = (end_balance - start_balance) / start_balance * 100

    if balance_change_pct >= _RETAIL_OVERHEATED_SURGE_PCT and current_ratio >= _RETAIL_OVERHEATED_MIN_RATIO:
        pattern.detected = True
        pattern.score_adj = -8
        pattern.severity = "warning"
        pattern.description = (
            f"신용 잔고 5일간 {balance_change_pct:+.0f}% 급증 "
            f"(비율 {current_ratio:.1f}%) → 빚투 과열 주의"
        )

    return pattern


def _detect_dual_leverage(
    margin_history: list[dict],
    short_history: list[dict],
) -> MarginPattern:
    """Detect 양방향 레버리지 극단: Both short and margin at extreme.

    When both short sellers and credit buyers are heavily positioned,
    the stock becomes a tug-of-war that can break violently either way.
    """
    pattern = MarginPattern(
        name="양방향 레버리지 극단",
        code="dual_leverage",
        description="공매도 + 신용 양쪽 극단 → 급변동 예상",
    )

    if not margin_history or not short_history:
        return pattern

    credit_ratio = margin_history[-1].get("credit_ratio", 0.0)
    short_ratio = short_history[-1].get("short_ratio", 0.0)

    if credit_ratio >= _DUAL_LEVERAGE_CREDIT_RATIO and short_ratio >= _DUAL_LEVERAGE_SHORT_RATIO:
        pattern.detected = True
        pattern.score_adj = -5
        pattern.severity = "warning"
        pattern.description = (
            f"공매도 {short_ratio:.1f}% + 신용 {credit_ratio:.1f}% "
            f"→ 양방향 레버리지 극단, 급변동 예상"
        )

    return pattern


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_margin_patterns(
    margin_history: list[dict],
    price_history: list[dict] | None = None,
    short_history: list[dict] | None = None,
    ticker: str = "",
    name: str = "",
) -> MarginSignal:
    """Detect all 4 margin balance patterns.

    Args:
        margin_history: Daily margin data sorted by date ascending.
        price_history: Daily price data sorted by date ascending.
        short_history: Daily short selling data sorted by date ascending.
        ticker: Stock ticker code.
        name: Stock name.

    Returns:
        MarginSignal with all detected patterns and total score adjustment.
    """
    price_history = price_history or []
    short_history = short_history or []

    all_patterns = [
        _detect_forced_liquidation(margin_history, price_history),
        _detect_credit_clearing(margin_history),
        _detect_retail_overheated(margin_history),
        _detect_dual_leverage(margin_history, short_history),
    ]

    detected = [p for p in all_patterns if p.detected]
    total_score = sum(p.score_adj for p in detected)
    total_score = max(-_SCORE_CAP, min(_SCORE_CAP, total_score))

    credit_ratio = margin_history[-1].get("credit_ratio", 0.0) if margin_history else 0.0
    is_dangerous = any(p.severity == "danger" for p in detected)

    if detected:
        names = [p.name for p in detected]
        message = f"{name} 레버리지 신호: " + ", ".join(names)
    else:
        message = f"{name} 레버리지 특이사항 없음"

    signal = MarginSignal(
        ticker=ticker,
        name=name,
        patterns=detected,
        total_score_adj=total_score,
        message=message,
        credit_ratio=credit_ratio,
        is_dangerous=is_dangerous,
    )

    logger.info(
        "Margin patterns %s (%s): detected=%d score=%d dangerous=%s",
        ticker, name, len(detected), total_score, is_dangerous,
    )

    return signal


def compute_combined_leverage_score(
    short_score: int,
    margin_score: int,
) -> int:
    """Compute combined short + margin score adjustment.

    Cap at ±30 for the combined leverage score.

    Args:
        short_score: Score adjustment from short selling analysis.
        margin_score: Score adjustment from margin balance analysis.

    Returns:
        Combined score capped at ±30.
    """
    combined = short_score + margin_score
    return max(-30, min(30, combined))


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_margin_alert(
    signal: MarginSignal,
    margin_history: list[dict] | None = None,
) -> str:
    """Format margin balance alert for Telegram.

    No ** bold markers. Korean text throughout.
    """
    lines: list[str] = []

    if signal.is_dangerous:
        header_emoji = "\U0001f6a8"
    elif signal.total_score_adj <= -5:
        header_emoji = "\U0001f534"
    elif signal.total_score_adj >= 5:
        header_emoji = "\U0001f7e2"
    else:
        header_emoji = "\U0001f4ca"

    lines.append(f"{header_emoji} {signal.name} ({signal.ticker}) 레버리지 분석")
    lines.append("")

    if signal.is_dangerous:
        lines.append("\U0001f6a8 위험: 반대매매 가능성 높음!")
        lines.append("")

    # Detected patterns
    if signal.patterns:
        lines.append("감지된 패턴:")
        for p in signal.patterns:
            if p.severity == "danger":
                p_emoji = "\U0001f6a8"
            elif p.severity == "warning":
                p_emoji = "\U0001f534"
            elif p.severity == "positive":
                p_emoji = "\U0001f7e2"
            else:
                p_emoji = "\u26aa"
            lines.append(f"  {p_emoji} {p.name} ({p.score_adj:+d}점)")
            lines.append(f"     {p.description}")
            lines.append("")
    else:
        lines.append("레버리지 특이사항 없음")
        lines.append("")

    # Recent margin data
    if margin_history and len(margin_history) > 0:
        recent = margin_history[-5:]
        lines.append("최근 신용 잔고 현황:")
        for entry in recent:
            date = entry.get("date", "")
            credit_ratio = entry.get("credit_ratio", 0.0)
            credit_balance = entry.get("credit_balance", 0)
            r_emoji = "\U0001f534" if credit_ratio >= _FORCED_LIQUIDATION_CREDIT_RATIO else "\u26aa"
            lines.append(
                f"  {date} | {r_emoji} 신용비율 {credit_ratio:.1f}% "
                f"잔고 {credit_balance:,.0f}주"
            )
        lines.append("")

    # Score
    if signal.total_score_adj > 0:
        lines.append(f"레버리지 스코어: +{signal.total_score_adj}점")
    elif signal.total_score_adj < 0:
        lines.append(f"레버리지 스코어: {signal.total_score_adj}점")
    else:
        lines.append("레버리지 스코어: 0점 (중립)")

    return "\n".join(lines)
