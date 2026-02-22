"""Short selling pattern detection engine (공매도 패턴 엔진).

Detects 5 patterns from short selling and price/volume data:
1. Real Buy (실매수): Short balance dropping + price rising → genuine buying
2. Short Covering Rally (숏커버링 랠리): Sharp short balance decrease + volume spike
3. Short Build-up (공매도 빌드업): Gradual short ratio increase over N days
4. Short Squeeze (숏스퀴즈): Very high short ratio + sudden price increase
5. Inverse Contrarian (인버스 역발상): Inverse ETF volume spike without market decline

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
class ShortPattern:
    """A detected short selling pattern."""

    name: str            # Pattern name (Korean)
    code: str            # Pattern code (English identifier)
    description: str     # Human-readable description
    detected: bool = False
    score_adj: int = 0   # Score adjustment when detected
    confidence: float = 0.0  # 0.0 ~ 1.0


@dataclass
class ShortPatternResult:
    """Result of all pattern detection."""

    ticker: str
    name: str
    patterns: list[ShortPattern] = field(default_factory=list)
    total_score_adj: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Real Buy thresholds
_REAL_BUY_BALANCE_DROP_PCT = -10.0  # Short balance drop >= 10%
_REAL_BUY_PRICE_RISE_PCT = 3.0     # Price rise >= 3%

# Short Covering Rally thresholds
_COVERING_BALANCE_DROP_PCT = -20.0  # Sharp short balance drop >= 20%
_COVERING_VOLUME_SURGE = 1.5       # Volume >= 1.5x average

# Short Build-up thresholds
_BUILDUP_DAYS = 5                   # Consecutive days of short ratio increase
_BUILDUP_MIN_INCREASE = 2.0         # Minimum cumulative ratio increase (pp)

# Short Squeeze thresholds
_SQUEEZE_MIN_SHORT_RATIO = 10.0     # Short ratio >= 10%
_SQUEEZE_PRICE_SURGE_PCT = 5.0      # Single day price surge >= 5%

# Inverse Contrarian thresholds
_INVERSE_VOLUME_SURGE = 2.0         # Inverse ETF volume >= 2x average

_SCORE_CAP = 15


# ---------------------------------------------------------------------------
# Pattern detection functions
# ---------------------------------------------------------------------------

def _detect_real_buy(
    short_history: list[dict],
    price_history: list[dict],
) -> ShortPattern:
    """Detect Real Buy pattern: short balance dropping + price rising.

    When short sellers cover (buy back) and the price still rises,
    it indicates genuine institutional/foreign buying.
    """
    pattern = ShortPattern(
        name="실매수",
        code="real_buy",
        description="공매도 잔고 감소 + 가격 상승 → 실제 매수세 유입",
    )

    if len(short_history) < 5 or len(price_history) < 5:
        return pattern

    # Short balance change over 5 days
    start_balance = short_history[-5].get("short_balance", 0)
    end_balance = short_history[-1].get("short_balance", 0)
    if start_balance <= 0:
        return pattern

    balance_change_pct = (end_balance - start_balance) / start_balance * 100

    # Price change over 5 days
    start_price = price_history[-5].get("close", 0)
    end_price = price_history[-1].get("close", 0)
    if start_price <= 0:
        return pattern

    price_change_pct = (end_price - start_price) / start_price * 100

    if balance_change_pct <= _REAL_BUY_BALANCE_DROP_PCT and price_change_pct >= _REAL_BUY_PRICE_RISE_PCT:
        pattern.detected = True
        pattern.score_adj = 10
        confidence = min(1.0, abs(balance_change_pct) / 30 + price_change_pct / 10)
        pattern.confidence = round(confidence, 2)
        pattern.description = (
            f"공매도 잔고 {balance_change_pct:+.1f}% 감소 중 "
            f"가격 {price_change_pct:+.1f}% 상승 → 실매수 유입"
        )

    return pattern


def _detect_short_covering_rally(
    short_history: list[dict],
    price_history: list[dict],
) -> ShortPattern:
    """Detect Short Covering Rally: sharp short balance decrease + volume spike.

    When shorts are forced to cover rapidly with high volume, it often
    triggers a short-term rally.
    """
    pattern = ShortPattern(
        name="숏커버링 랠리",
        code="short_covering",
        description="공매도 잔고 급감 + 거래량 폭증 → 숏커버 매수세",
    )

    if len(short_history) < 3 or len(price_history) < 6:
        return pattern

    # Short balance change over 3 days
    start_balance = short_history[-3].get("short_balance", 0)
    end_balance = short_history[-1].get("short_balance", 0)
    if start_balance <= 0:
        return pattern

    balance_change_pct = (end_balance - start_balance) / start_balance * 100

    # Volume surge: latest vs 5-day average
    vol_window = price_history[-6:-1] if len(price_history) > 5 else price_history[:-1]
    avg_vol = sum(e.get("volume", 0) for e in vol_window) / max(1, len(vol_window))
    latest_vol = price_history[-1].get("volume", 0)
    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

    if balance_change_pct <= _COVERING_BALANCE_DROP_PCT and vol_ratio >= _COVERING_VOLUME_SURGE:
        pattern.detected = True
        pattern.score_adj = 8
        confidence = min(1.0, abs(balance_change_pct) / 40 + vol_ratio / 4)
        pattern.confidence = round(confidence, 2)
        pattern.description = (
            f"공매도 잔고 {balance_change_pct:+.1f}% 급감 + "
            f"거래량 {vol_ratio:.1f}배 → 숏커버링 랠리 가능성"
        )

    return pattern


def _detect_short_buildup(short_history: list[dict]) -> ShortPattern:
    """Detect Short Build-up: gradual short ratio increase over N days.

    Consistent increase in short ratio signals bearish institutional sentiment.
    """
    pattern = ShortPattern(
        name="공매도 빌드업",
        code="short_buildup",
        description="공매도 비중 점진적 증가 → 하락 압력 축적",
    )

    if len(short_history) < _BUILDUP_DAYS:
        return pattern

    recent = short_history[-_BUILDUP_DAYS:]
    increasing_days = 0
    total_increase = 0.0

    for i in range(1, len(recent)):
        prev_ratio = recent[i - 1].get("short_ratio", 0.0)
        curr_ratio = recent[i].get("short_ratio", 0.0)
        if curr_ratio > prev_ratio:
            increasing_days += 1
            total_increase += curr_ratio - prev_ratio

    if increasing_days >= _BUILDUP_DAYS - 1 and total_increase >= _BUILDUP_MIN_INCREASE:
        pattern.detected = True
        pattern.score_adj = -8
        confidence = min(1.0, total_increase / 5)
        pattern.confidence = round(confidence, 2)
        pattern.description = (
            f"공매도 비중 {_BUILDUP_DAYS}일 연속 증가 "
            f"(+{total_increase:.1f}%p) → 하락 압력 빌드업"
        )

    return pattern


def _detect_short_squeeze(
    short_history: list[dict],
    price_history: list[dict],
) -> ShortPattern:
    """Detect Short Squeeze: high short ratio + sudden price increase.

    When a stock with high short interest suddenly surges, shorts are
    forced to cover, amplifying the move.
    """
    pattern = ShortPattern(
        name="숏스퀴즈",
        code="short_squeeze",
        description="높은 공매도 비중 + 급등 → 숏스퀴즈 가능성",
    )

    if not short_history or not price_history:
        return pattern

    latest_short = short_history[-1]
    short_ratio = latest_short.get("short_ratio", 0.0)

    if short_ratio < _SQUEEZE_MIN_SHORT_RATIO:
        return pattern

    # Check for price surge
    if len(price_history) < 2:
        return pattern

    prev_price = price_history[-2].get("close", 0)
    curr_price = price_history[-1].get("close", 0)
    if prev_price <= 0:
        return pattern

    price_change_pct = (curr_price - prev_price) / prev_price * 100

    if price_change_pct >= _SQUEEZE_PRICE_SURGE_PCT:
        pattern.detected = True
        pattern.score_adj = 12
        confidence = min(1.0, short_ratio / 20 + price_change_pct / 15)
        pattern.confidence = round(confidence, 2)
        pattern.description = (
            f"공매도 비중 {short_ratio:.1f}% + "
            f"가격 {price_change_pct:+.1f}% 급등 → 숏스퀴즈 진행"
        )

    return pattern


def _detect_inverse_contrarian(
    inverse_etf_history: list[dict],
    market_change_pct: float = 0.0,
) -> ShortPattern:
    """Detect Inverse Contrarian: inverse ETF volume spike without market decline.

    When inverse ETF volume surges but market is not actually declining,
    it may indicate excessive fear → contrarian buy opportunity.
    """
    pattern = ShortPattern(
        name="인버스 역발상",
        code="inverse_contrarian",
        description="인버스 ETF 거래량 급증 + 시장 미하락 → 역발상 매수 기회",
    )

    if len(inverse_etf_history) < 6:
        return pattern

    # Volume surge for inverse ETF
    avg_window = inverse_etf_history[-6:-1]
    avg_vol = sum(e.get("volume", 0) for e in avg_window) / max(1, len(avg_window))
    latest_vol = inverse_etf_history[-1].get("volume", 0)
    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 1.0

    # Market is not declining (or slightly positive)
    if vol_ratio >= _INVERSE_VOLUME_SURGE and market_change_pct >= -0.5:
        pattern.detected = True
        pattern.score_adj = 5
        confidence = min(1.0, vol_ratio / 4)
        pattern.confidence = round(confidence, 2)
        pattern.description = (
            f"인버스 ETF 거래량 {vol_ratio:.1f}배 급증 + "
            f"시장 {market_change_pct:+.1f}% → 과도한 공포, 역발상 기회"
        )

    return pattern


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_all_patterns(
    short_history: list[dict],
    price_history: list[dict] | None = None,
    inverse_etf_history: list[dict] | None = None,
    market_change_pct: float = 0.0,
    ticker: str = "",
    name: str = "",
) -> ShortPatternResult:
    """Detect all 5 short selling patterns.

    Args:
        short_history: Daily short selling data sorted by date ascending.
        price_history: Daily price/volume data sorted by date ascending.
            Expected keys: date, close, volume.
        inverse_etf_history: Daily inverse ETF data sorted by date ascending.
        market_change_pct: Recent market (KOSPI) change percentage.
        ticker: Stock ticker code.
        name: Stock name.

    Returns:
        ShortPatternResult with all detected patterns and total score adjustment.
    """
    price_history = price_history or []
    inverse_etf_history = inverse_etf_history or []

    all_patterns = [
        _detect_real_buy(short_history, price_history),
        _detect_short_covering_rally(short_history, price_history),
        _detect_short_buildup(short_history),
        _detect_short_squeeze(short_history, price_history),
        _detect_inverse_contrarian(inverse_etf_history, market_change_pct),
    ]

    detected = [p for p in all_patterns if p.detected]
    total_score = sum(p.score_adj for p in detected)
    total_score = max(-_SCORE_CAP, min(_SCORE_CAP, total_score))

    if detected:
        names = [p.name for p in detected]
        message = f"{name} 공매도 패턴: " + ", ".join(names)
    else:
        message = f"{name} 공매도 패턴 미감지"

    result = ShortPatternResult(
        ticker=ticker,
        name=name,
        patterns=detected,
        total_score_adj=total_score,
        message=message,
    )

    logger.info(
        "Short patterns %s (%s): detected=%d score=%d",
        ticker, name, len(detected), total_score,
    )

    return result


def format_pattern_report(result: ShortPatternResult) -> str:
    """Format short pattern detection results for Telegram.

    No ** bold markers. Korean text throughout.
    """
    lines: list[str] = []

    if result.total_score_adj >= 5:
        emoji = "\U0001f7e2"
    elif result.total_score_adj <= -5:
        emoji = "\U0001f534"
    else:
        emoji = "\U0001f4ca"

    lines.append(f"{emoji} {result.name} ({result.ticker}) 공매도 패턴 분석")
    lines.append("")

    if result.patterns:
        for p in result.patterns:
            conf_bar = "\u2588" * int(p.confidence * 5) + "\u2591" * (5 - int(p.confidence * 5))
            p_emoji = "\U0001f7e2" if p.score_adj > 0 else "\U0001f534"
            lines.append(f"  {p_emoji} {p.name} ({p.score_adj:+d}점)")
            lines.append(f"     {p.description}")
            lines.append(f"     신뢰도: {conf_bar} {p.confidence:.0%}")
            lines.append("")
    else:
        lines.append("  감지된 패턴 없음")
        lines.append("")

    if result.total_score_adj > 0:
        lines.append(f"패턴 종합 스코어: +{result.total_score_adj}점")
    elif result.total_score_adj < 0:
        lines.append(f"패턴 종합 스코어: {result.total_score_adj}점")
    else:
        lines.append("패턴 종합 스코어: 0점 (중립)")

    return "\n".join(lines)
