"""Event-driven signal detection (Section 58 - 이벤트 드리븐 시그널).

Detects actionable events such as earnings surprises, target price upgrade
chains, share buybacks, major stake changes, and policy-benefit overlaps.
All functions are pure computation with no external API calls at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARNINGS_SURPRISE_THRESHOLD = 0.15
"""Operating income must beat consensus by 15% to qualify as a surprise."""

TARGET_UPGRADE_MIN_BROKERS = 3
"""Minimum number of brokers raising target in a week."""

BUYBACK_MIN_PCT = 0.01
"""Buyback must exceed 1% of market cap."""

STAKE_THRESHOLD_PCT = 5.0
"""Minimum stake percentage for a major stake acquisition alert."""

SCORE_ADJ_MAP: dict[str, int] = {
    "earnings_surprise": 15,
    "target_upgrade_chain": 10,
    "buyback": 10,
    "stake_change": 8,
    "policy_benefit": 5,
}
"""Default score adjustments by event type."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class EventSignal:
    """Represents a detected event-driven signal.

    Attributes:
        event_type: One of "earnings_surprise", "target_upgrade_chain",
            "buyback", "stake_change", "policy_benefit".
        ticker: Stock ticker code.
        name: Human-readable stock name.
        score_adj: Score adjustment to apply.
        description: Korean description of the event.
        action: Recommended action in Korean.
        details: Additional event-specific data.
        message: Pre-formatted Telegram message.
    """

    event_type: str
    ticker: str
    name: str
    score_adj: int
    description: str
    action: str
    details: dict = field(default_factory=dict)
    message: str = ""


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def detect_earnings_surprise(
    ticker: str,
    name: str,
    op_income: float,
    consensus: float,
    current_price: float,
) -> EventSignal | None:
    """Detect an earnings surprise event.

    An earnings surprise occurs when actual operating income exceeds
    the consensus estimate by 15% or more.  When detected, the signal
    suggests a buy with a target of +5-10% and a stop-loss of -3%.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        op_income: Actual operating income (KRW).
        consensus: Consensus operating income estimate (KRW).
        current_price: Current stock price (KRW).

    Returns:
        EventSignal if surprise detected, None otherwise.
    """
    if consensus <= 0:
        logger.debug("Earnings surprise skip %s(%s): consensus <= 0", name, ticker)
        return None

    surprise_pct = (op_income - consensus) / abs(consensus)

    if surprise_pct < EARNINGS_SURPRISE_THRESHOLD:
        logger.debug(
            "Earnings surprise skip %s(%s): surprise %.1f%% < threshold %.0f%%",
            name, ticker, surprise_pct * 100, EARNINGS_SURPRISE_THRESHOLD * 100,
        )
        return None

    # Compute target and stop prices
    target_pct = min(10.0, max(5.0, surprise_pct * 30))
    target_price = round(current_price * (1 + target_pct / 100))
    stop_price = round(current_price * 0.97)

    score_adj = SCORE_ADJ_MAP["earnings_surprise"]
    description = (
        f"영업이익 {op_income / 1e8:,.0f}억원, "
        f"컨센서스 대비 +{surprise_pct * 100:.1f}% 서프라이즈"
    )

    details = {
        "op_income": op_income,
        "consensus": consensus,
        "surprise_pct": round(surprise_pct * 100, 1),
        "target_price": target_price,
        "stop_price": stop_price,
        "target_pct": round(target_pct, 1),
    }

    signal = EventSignal(
        event_type="earnings_surprise",
        ticker=ticker,
        name=name,
        score_adj=score_adj,
        description=description,
        action="매수 추천",
        details=details,
    )
    signal.message = format_event_alert(signal)

    logger.info(
        "Earnings surprise %s(%s): +%.1f%% vs consensus, score_adj=%+d",
        name, ticker, surprise_pct * 100, score_adj,
    )

    return signal


def detect_target_upgrade_chain(
    ticker: str,
    name: str,
    recent_reports: list[dict],
) -> EventSignal | None:
    """Detect target price upgrade chain.

    When 3 or more brokers have raised target prices within a 7-day
    window, this signals momentum acceleration.  Each report dict
    should contain keys: "broker", "prev_target", "new_target", "date".

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        recent_reports: List of broker report dicts with upgrade info.

    Returns:
        EventSignal if upgrade chain detected, None otherwise.
    """
    if not recent_reports:
        return None

    upgrades = [
        r for r in recent_reports
        if r.get("new_target", 0) > r.get("prev_target", 0)
    ]

    if len(upgrades) < TARGET_UPGRADE_MIN_BROKERS:
        logger.debug(
            "Target upgrade chain skip %s(%s): %d upgrades < %d required",
            name, ticker, len(upgrades), TARGET_UPGRADE_MIN_BROKERS,
        )
        return None

    broker_names = [r.get("broker", "?") for r in upgrades]
    avg_raise_pct = 0.0
    for r in upgrades:
        prev = r.get("prev_target", 0)
        new = r.get("new_target", 0)
        if prev > 0:
            avg_raise_pct += ((new - prev) / prev) * 100
    if upgrades:
        avg_raise_pct /= len(upgrades)

    score_adj = SCORE_ADJ_MAP["target_upgrade_chain"]
    description = (
        f"{len(upgrades)}개 증권사 목표가 상향 "
        f"(평균 +{avg_raise_pct:.1f}%): {', '.join(broker_names[:5])}"
    )

    details = {
        "upgrade_count": len(upgrades),
        "broker_names": broker_names,
        "avg_raise_pct": round(avg_raise_pct, 1),
        "reports": upgrades,
    }

    signal = EventSignal(
        event_type="target_upgrade_chain",
        ticker=ticker,
        name=name,
        score_adj=score_adj,
        description=description,
        action="매수 추천",
        details=details,
    )
    signal.message = format_event_alert(signal)

    logger.info(
        "Target upgrade chain %s(%s): %d brokers, avg +%.1f%%",
        name, ticker, len(upgrades), avg_raise_pct,
    )

    return signal


def detect_buyback(
    ticker: str,
    name: str,
    buyback_amount: float,
    market_cap: float,
) -> EventSignal | None:
    """Detect significant share buyback.

    A buyback exceeding 1% of market capitalization is treated as a
    strong buy signal, reflecting management confidence.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        buyback_amount: Announced buyback amount (KRW).
        market_cap: Current market capitalization (KRW).

    Returns:
        EventSignal if significant buyback detected, None otherwise.
    """
    if market_cap <= 0:
        logger.debug("Buyback skip %s(%s): market_cap <= 0", name, ticker)
        return None

    buyback_pct = buyback_amount / market_cap

    if buyback_pct < BUYBACK_MIN_PCT:
        logger.debug(
            "Buyback skip %s(%s): %.2f%% < 1.0%% threshold",
            name, ticker, buyback_pct * 100,
        )
        return None

    score_adj = SCORE_ADJ_MAP["buyback"]
    description = (
        f"자사주 매입 {buyback_amount / 1e8:,.0f}억원 "
        f"(시총 대비 {buyback_pct * 100:.2f}%)"
    )

    details = {
        "buyback_amount": buyback_amount,
        "market_cap": market_cap,
        "buyback_pct": round(buyback_pct * 100, 2),
    }

    signal = EventSignal(
        event_type="buyback",
        ticker=ticker,
        name=name,
        score_adj=score_adj,
        description=description,
        action="매수 추천",
        details=details,
    )
    signal.message = format_event_alert(signal)

    logger.info(
        "Buyback detected %s(%s): %,.0f원 (%.2f%% of market cap)",
        name, ticker, buyback_amount, buyback_pct * 100,
    )

    return signal


def detect_stake_change(
    ticker: str,
    name: str,
    investor_name: str,
    stake_pct: float,
    investor_type: str = "",
) -> EventSignal | None:
    """Detect significant stake acquisition.

    A 5%+ stake acquisition by a major institution signals strategic
    interest and is treated as a buy signal.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        investor_name: Name of the acquiring investor/institution.
        stake_pct: Acquired stake percentage.
        investor_type: Type of investor (e.g. "국민연금", "행동주의 펀드").

    Returns:
        EventSignal if significant stake change detected, None otherwise.
    """
    if stake_pct < STAKE_THRESHOLD_PCT:
        logger.debug(
            "Stake change skip %s(%s): %.1f%% < %.1f%% threshold",
            name, ticker, stake_pct, STAKE_THRESHOLD_PCT,
        )
        return None

    # Determine action based on investor type
    if investor_type in ("행동주의 펀드", "사모펀드"):
        action = "모니터링"
        score_adj = SCORE_ADJ_MAP["stake_change"] - 3
    elif stake_pct >= 10.0:
        action = "매수 추천"
        score_adj = SCORE_ADJ_MAP["stake_change"] + 5
    else:
        action = "매수 추천"
        score_adj = SCORE_ADJ_MAP["stake_change"]

    type_label = f" ({investor_type})" if investor_type else ""
    description = (
        f"{investor_name}{type_label} {stake_pct:.1f}% 지분 취득"
    )

    details = {
        "investor_name": investor_name,
        "stake_pct": stake_pct,
        "investor_type": investor_type,
    }

    signal = EventSignal(
        event_type="stake_change",
        ticker=ticker,
        name=name,
        score_adj=score_adj,
        description=description,
        action=action,
        details=details,
    )
    signal.message = format_event_alert(signal)

    logger.info(
        "Stake change %s(%s): %s %.1f%% -> %s",
        name, ticker, investor_name, stake_pct, action,
    )

    return signal


def detect_policy_benefit(
    ticker: str,
    name: str,
    sector: str,
    policy_keywords: list[str],
) -> EventSignal | None:
    """Detect policy-benefit overlap for a sector.

    Matches sector keywords against policy announcement keywords.
    Produces a signal when the sector stands to benefit from a
    recently announced or active policy.

    Args:
        ticker: Stock ticker code.
        name: Stock name.
        sector: Stock sector name.
        policy_keywords: Keywords from the relevant policy announcement.

    Returns:
        EventSignal if policy benefit detected, None otherwise.
    """
    if not policy_keywords:
        return None

    # Sector-keyword mapping for benefit detection
    sector_keywords_map: dict[str, list[str]] = {
        "2차전지": ["배터리", "전기차", "IRA", "보조금", "핵심광물"],
        "반도체": ["반도체", "칩스법", "AI", "HBM", "파운드리"],
        "바이오": ["바이오", "신약", "의료", "건강보험", "헬스케어"],
        "자동차": ["자동차", "전기차", "수소차", "모빌리티"],
        "에너지": ["태양광", "풍력", "수소", "원전", "에너지"],
        "AI": ["AI", "인공지능", "데이터센터", "클라우드", "GPU"],
        "로봇": ["로봇", "자동화", "스마트팩토리"],
        "방산": ["방산", "국방", "방위", "무기"],
        "조선": ["조선", "LNG", "해운"],
        "건설": ["건설", "인프라", "SOC", "부동산"],
    }

    sector_kw = sector_keywords_map.get(sector, [])
    if not sector_kw:
        logger.debug(
            "Policy benefit skip %s(%s): sector '%s' not in keyword map",
            name, ticker, sector,
        )
        return None

    matched = [kw for kw in policy_keywords if kw in sector_kw]
    if not matched:
        # Also check if any policy keyword appears as substring in sector keywords
        matched = [
            kw for kw in policy_keywords
            if any(kw in sk or sk in kw for sk in sector_kw)
        ]

    if not matched:
        logger.debug(
            "Policy benefit skip %s(%s): no keyword overlap",
            name, ticker,
        )
        return None

    score_adj = SCORE_ADJ_MAP["policy_benefit"]
    description = (
        f"{sector} 섹터 정책 수혜 감지 "
        f"(키워드: {', '.join(matched[:3])})"
    )

    details = {
        "sector": sector,
        "matched_keywords": matched,
        "all_policy_keywords": policy_keywords,
    }

    signal = EventSignal(
        event_type="policy_benefit",
        ticker=ticker,
        name=name,
        score_adj=score_adj,
        description=description,
        action="모니터링",
        details=details,
    )
    signal.message = format_event_alert(signal)

    logger.info(
        "Policy benefit %s(%s): sector=%s, matched=%s",
        name, ticker, sector, matched,
    )

    return signal


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_EVENT_TYPE_LABEL: dict[str, str] = {
    "earnings_surprise": "어닝 서프라이즈",
    "target_upgrade_chain": "목표가 상향 릴레이",
    "buyback": "자사주 매입",
    "stake_change": "지분 변동",
    "policy_benefit": "정책 수혜",
}

_ACTION_EMOJI: dict[str, str] = {
    "매수 추천": "",
    "주의": "",
    "모니터링": "",
}


def format_event_alert(signal: EventSignal) -> str:
    """Format an EventSignal as a Telegram alert message.

    Produces clean Korean text without any bold (**) formatting.
    Uses "주호님" for the user greeting.

    Args:
        signal: EventSignal to format.

    Returns:
        Multi-line formatted string suitable for Telegram.

    Example output::

        [이벤트] 어닝 서프라이즈
        종목: 삼성전자 (005930)
        내용: 영업이익 15,230억원, 컨센서스 대비 +22.3% 서프라이즈
        판단: 매수 추천 (스코어 +15)

        주호님, 어닝 서프라이즈 발생했습니다. 확인해보세요!
    """
    type_label = _EVENT_TYPE_LABEL.get(signal.event_type, signal.event_type)

    lines = [
        f"[이벤트] {type_label}",
        f"종목: {signal.name} ({signal.ticker})",
        f"내용: {signal.description}",
        f"판단: {signal.action} (스코어 {signal.score_adj:+d})",
    ]

    # Add event-specific detail lines
    if signal.event_type == "earnings_surprise":
        target = signal.details.get("target_price", 0)
        stop = signal.details.get("stop_price", 0)
        if target and stop:
            lines.append(
                f"목표가: {target:,.0f}원 / 손절가: {stop:,.0f}원"
            )

    elif signal.event_type == "target_upgrade_chain":
        count = signal.details.get("upgrade_count", 0)
        avg = signal.details.get("avg_raise_pct", 0)
        lines.append(f"상향 증권사: {count}개, 평균 상향폭: +{avg:.1f}%")

    elif signal.event_type == "buyback":
        pct = signal.details.get("buyback_pct", 0)
        lines.append(f"시총 대비 비율: {pct:.2f}%")

    elif signal.event_type == "stake_change":
        investor = signal.details.get("investor_name", "")
        pct = signal.details.get("stake_pct", 0)
        lines.append(f"투자자: {investor}, 지분율: {pct:.1f}%")

    lines.append("")

    # User-facing closing with 주호님
    if signal.action == "매수 추천":
        lines.append(f"주호님, {type_label} 발생했습니다. 확인해보세요!")
    elif signal.action == "주의":
        lines.append(f"주호님, {type_label} 관련 주의가 필요합니다.")
    else:
        lines.append(f"주호님, {type_label} 감지되었습니다. 추적 중입니다.")

    return "\n".join(lines)
