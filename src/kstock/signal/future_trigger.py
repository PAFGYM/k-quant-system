"""Future technology trigger monitoring and entry evaluation - K-Quant v3.5.

Monitors news keywords, classifies trigger types (policy, corporate, global,
earnings), matches beneficiary stocks, and evaluates entry conditions.

Rules:
- No ** bold, no Markdown parse_mode
- Korean responses only
- "주호님" personalized greeting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from kstock.signal.future_tech import (
    FUTURE_SECTORS,
    EntrySignal,
    FutureStockScore,
    get_sector_watchlist,
    get_ticker_info,
    score_future_stock,
)

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Trigger types and classification
# ---------------------------------------------------------------------------

TRIGGER_TYPES = {
    "policy": {
        "label": "정책 트리거",
        "impact": "HIGH",
        "description": "정부 규제완화/지원 정책",
        "keywords": [
            "허용", "규제완화", "법안", "예산", "의무화", "특별법",
            "국가전략", "국책사업", "정부지원", "우주항공청",
        ],
    },
    "corporate": {
        "label": "기업 트리거",
        "impact": "MEDIUM",
        "description": "기업 성과/이벤트",
        "keywords": [
            "수주", "계약", "납품", "양산", "시범운행", "출시",
            "성공", "발사", "인증", "특허",
        ],
    },
    "global": {
        "label": "글로벌 트리거",
        "impact": "HIGH",
        "description": "글로벌 기술/시장 이벤트",
        "keywords": [
            "테슬라", "스페이스X", "구글", "아이온큐", "엔비디아",
            "FSD", "스타십", "양자우월성", "CES", "MWC",
        ],
    },
    "earnings": {
        "label": "실적 트리거",
        "impact": "MEDIUM",
        "description": "실적 서프라이즈/ETF 거래량 급증",
        "keywords": [
            "실적", "서프라이즈", "어닝", "매출", "영업이익",
            "흑자전환", "거래량 급증", "신고가",
        ],
    },
}


@dataclass
class TriggerEvent:
    """A detected trigger event."""

    sector: str = ""
    trigger_type: str = ""  # policy, corporate, global, earnings
    impact: str = "LOW"  # HIGH, MEDIUM, LOW
    title: str = ""
    source: str = ""
    date: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    beneficiary_tickers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def match_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return list of keywords found in text."""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def classify_trigger_type(text: str) -> tuple[str, str]:
    """Classify a news/event text into trigger type.

    Returns (trigger_type, impact).
    """
    best_type = ""
    best_impact = "LOW"
    best_count = 0

    for ttype, cfg in TRIGGER_TYPES.items():
        matches = match_keywords(text, cfg["keywords"])
        if len(matches) > best_count:
            best_count = len(matches)
            best_type = ttype
            best_impact = cfg["impact"]

    if not best_type:
        return "unknown", "LOW"
    return best_type, best_impact


def detect_sector_for_text(text: str) -> list[str]:
    """Detect which future tech sectors a text relates to."""
    sectors_found: list[str] = []
    for sector_key, sector in FUTURE_SECTORS.items():
        keywords = sector.get("trigger_keywords", [])
        if match_keywords(text, keywords):
            sectors_found.append(sector_key)
    return sectors_found


def match_beneficiaries(
    text: str,
    sector_key: str,
) -> list[dict[str, Any]]:
    """Find beneficiary stocks in a sector based on trigger text.

    Returns list of {ticker, name, tier, reason, relevance_score}.
    """
    sector = FUTURE_SECTORS.get(sector_key, {})
    if not sector:
        return []

    text_lower = text.lower()
    beneficiaries: list[dict[str, Any]] = []

    for tier_key in ["tier1_platform", "tier2_core", "tier3_emerging"]:
        stocks = sector["watchlist"].get(tier_key, {})
        for name, info in stocks.items():
            # Score relevance by name/reason keyword overlap
            relevance = 0
            if name.lower() in text_lower or name in text:
                relevance += 10
            reason_words = info["reason"].replace(",", " ").split()
            for word in reason_words:
                if len(word) >= 2 and word.lower() in text_lower:
                    relevance += 2

            # Tier 1 gets base bonus
            tier_bonus = {"tier1_platform": 3, "tier2_core": 2, "tier3_emerging": 1}
            relevance += tier_bonus.get(tier_key, 0)

            if relevance > 0:
                beneficiaries.append({
                    "ticker": info["ticker"],
                    "name": name,
                    "tier": tier_key,
                    "reason": info["reason"],
                    "relevance_score": relevance,
                })

    # Sort by relevance
    beneficiaries.sort(key=lambda x: x["relevance_score"], reverse=True)
    return beneficiaries


# ---------------------------------------------------------------------------
# Trigger event creation
# ---------------------------------------------------------------------------

def analyze_trigger(
    title: str,
    source: str = "",
    date: str = "",
) -> list[TriggerEvent]:
    """Analyze a news/event and create TriggerEvent objects.

    Returns a list of TriggerEvent, one per matched sector.
    """
    sectors = detect_sector_for_text(title)
    if not sectors:
        return []

    trigger_type, impact = classify_trigger_type(title)
    events: list[TriggerEvent] = []

    for sector_key in sectors:
        sector_keywords = FUTURE_SECTORS[sector_key]["trigger_keywords"]
        matched = match_keywords(title, sector_keywords)
        beneficiaries = match_beneficiaries(title, sector_key)

        event = TriggerEvent(
            sector=sector_key,
            trigger_type=trigger_type,
            impact=impact,
            title=title,
            source=source,
            date=date or datetime.now(KST).strftime("%Y-%m-%d"),
            matched_keywords=matched,
            beneficiary_tickers=[b["ticker"] for b in beneficiaries[:5]],
        )
        events.append(event)

    return events


# ---------------------------------------------------------------------------
# Entry evaluation
# ---------------------------------------------------------------------------

def evaluate_entry(
    ticker: str,
    sector_key: str,
    future_score: int = 0,
    existing_score: float = 0.0,
    rsi: float = 50.0,
    volume_ratio: float = 1.0,
    has_recent_trigger: bool = False,
) -> EntrySignal:
    """Evaluate whether to enter a future tech stock.

    Checks 5 conditions:
      1. future_score >= 60
      2. existing_score >= 130
      3. RSI < 70
      4. volume_ratio > 1.5
      5. has_recent_trigger

    Returns EntrySignal with STRONG_BUY (>=4), WATCH (>=3), WAIT (<3).
    """
    info = get_ticker_info(ticker)
    name = info["name"] if info else ticker

    conditions = {
        "future_score_min": future_score >= 60,
        "existing_score_min": existing_score >= 130,
        "not_overbought": rsi < 70,
        "volume_confirm": volume_ratio > 1.5,
        "trigger_exists": has_recent_trigger,
    }

    met = sum(conditions.values())

    if met >= 4:
        signal = "STRONG_BUY"
        message = f"{USER_NAME}, {name} 진입 조건 충족. 소액 매수 추천합니다."
    elif met >= 3:
        signal = "WATCH"
        message = f"{USER_NAME}, {name} 관심 유지. 추가 조건 대기하세요."
    else:
        signal = "WAIT"
        message = f"{USER_NAME}, {name} 아직 이릅니다. 워치리스트 유지하세요."

    return EntrySignal(
        ticker=ticker,
        name=name,
        sector=sector_key,
        signal=signal,
        conditions_met=met,
        conditions_total=5,
        conditions_detail=conditions,
        message=message,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_trigger_alert(event: TriggerEvent) -> str:
    """Format a trigger event alert for Telegram."""
    sector = FUTURE_SECTORS.get(event.sector, {})
    sector_name = sector.get("name", event.sector)
    emoji = sector.get("emoji", "\U0001f680")
    type_label = TRIGGER_TYPES.get(event.trigger_type, {}).get("label", event.trigger_type)

    lines: list[str] = [
        f"[미래기술 트리거] {emoji} {sector_name}",
        "",
        f"뉴스: {event.title}",
    ]
    if event.source:
        lines.append(f"출처: {event.source}")
    lines.append(f"유형: {type_label}")
    lines.append(f"영향도: {'높음' if event.impact == 'HIGH' else '보통' if event.impact == 'MEDIUM' else '낮음'}")

    if event.beneficiary_tickers:
        lines.append("")
        lines.append("수혜 종목:")
        all_tickers = get_sector_watchlist(event.sector)
        for t in event.beneficiary_tickers[:5]:
            info = all_tickers.get(t, {})
            name = info.get("name", t)
            lines.append(f"  {name} ({t})")

    lines.append("")
    lines.append(f"{USER_NAME}, {sector_name} 섹터 트리거가 감지되었습니다.")
    lines.append("워치리스트 종목 중 진입 조건을 확인하세요.")

    return "\n".join(lines)


def format_entry_signal(entry: EntrySignal) -> str:
    """Format entry signal for a stock."""
    signal_emoji = {
        "STRONG_BUY": "\U0001f7e2",
        "WATCH": "\U0001f7e1",
        "WAIT": "\u26aa",
    }
    emoji = signal_emoji.get(entry.signal, "\u26aa")
    lines: list[str] = [
        f"{emoji} {entry.name} ({entry.ticker}) - {entry.signal}",
        f"  조건 충족: {entry.conditions_met}/{entry.conditions_total}",
    ]

    for cond_name, met in entry.conditions_detail.items():
        mark = "\u2705" if met else "\u274c"
        cond_labels = {
            "future_score_min": "미래기술 스코어 60+",
            "existing_score_min": "기존 스코어 130+",
            "not_overbought": "RSI 70 미만",
            "volume_confirm": "거래량 1.5배+",
            "trigger_exists": "최근 트리거 존재",
        }
        label = cond_labels.get(cond_name, cond_name)
        lines.append(f"    {mark} {label}")

    lines.append("")
    lines.append(entry.message)

    return "\n".join(lines)
