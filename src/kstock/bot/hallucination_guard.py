"""AI response hallucination guard (AI 응답 환각 검증).

Verifies numeric claims (target prices), date-event references, and
tags unverified content with [미확인]. All functions are pure computation
with no external API calls.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

# Regex patterns
_TARGET_PRICE_PATTERN = re.compile(
    r"목표가\s*(\d{1,3}(?:,\d{3})*)\s*원"
)
_TARGET_PRICE_WITH_TICKER_PATTERN = re.compile(
    r"([가-힣A-Za-z0-9]+?)(?:의)?\s*목표가\s*(\d{1,3}(?:,\d{3})*)\s*원"
)
_EVENT_DATE_PATTERN = re.compile(
    r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+([가-힣A-Za-z0-9\s]{2,20})"
)
_NUMERIC_CLAIM_PATTERN = re.compile(
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:원|%|조|억)"
)

# Tolerance for target price verification
TARGET_PRICE_TOLERANCE = 0.10  # 10%


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TargetPriceClaim:
    """목표가 주장 정보."""

    ticker_or_name: str = ""
    claimed_price: float = 0.0
    verified: bool = False
    source_text: str = ""


@dataclass
class EventClaim:
    """이벤트 주장 정보."""

    date_str: str = ""
    event_desc: str = ""
    verified: bool = False
    source_text: str = ""


@dataclass
class GuardResult:
    """환각 검증 결과."""

    modified_response: str = ""
    unverified_claims: list[str] = field(default_factory=list)
    total_claims_checked: int = 0
    verified_count: int = 0


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------

def extract_target_prices(text: str) -> list[tuple[str, float]]:
    """텍스트에서 '목표가 XX,XXX원' 패턴을 추출합니다.

    Returns:
        list of (ticker_or_name, price) tuples
    """
    try:
        results: list[tuple[str, float]] = []

        # Try pattern with ticker/name first
        for match in _TARGET_PRICE_WITH_TICKER_PATTERN.finditer(text):
            name = match.group(1).strip()
            price_str = match.group(2).replace(",", "")
            price = float(price_str)
            results.append((name, price))

        # If no named matches, try standalone pattern
        if not results:
            for match in _TARGET_PRICE_PATTERN.finditer(text):
                price_str = match.group(1).replace(",", "")
                price = float(price_str)
                results.append(("", price))

        logger.info("목표가 %d건 추출", len(results))
        return results

    except Exception as e:
        logger.error("목표가 추출 실패: %s", e, exc_info=True)
        return []


def extract_events(text: str) -> list[dict]:
    """텍스트에서 날짜-이벤트 패턴을 추출합니다.

    '2026.03.15 실적발표' 같은 패턴을 찾습니다.

    Returns:
        list of {"date": str, "event": str, "source_text": str}
    """
    try:
        results: list[dict] = []

        for match in _EVENT_DATE_PATTERN.finditer(text):
            year = match.group(1)
            month = match.group(2).zfill(2)
            day = match.group(3).zfill(2)
            event = match.group(4).strip()

            date_str = f"{year}.{month}.{day}"
            results.append({
                "date": date_str,
                "event": event,
                "source_text": match.group(0).strip(),
            })

        logger.info("이벤트 %d건 추출", len(results))
        return results

    except Exception as e:
        logger.error("이벤트 추출 실패: %s", e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Verification functions
# ---------------------------------------------------------------------------

def verify_target_price(
    ticker: str,
    claimed_price: float,
    db_prices: dict[str, float],
) -> bool:
    """목표가가 알려진 컨센서스 범위(10%) 내인지 검증합니다.

    Args:
        ticker: 종목코드 또는 이름
        claimed_price: 주장된 목표가
        db_prices: {ticker_or_name: consensus_target_price} 데이터

    Returns:
        True if within tolerance, False otherwise
    """
    try:
        if not ticker or ticker not in db_prices:
            logger.info("[%s] 컨센서스 데이터 없음, 미확인 처리", ticker)
            return False

        consensus = db_prices[ticker]
        if consensus <= 0:
            return False

        diff_pct = abs(claimed_price - consensus) / consensus

        verified = diff_pct <= TARGET_PRICE_TOLERANCE
        if not verified:
            logger.warning(
                "[%s] 목표가 불일치: 주장=%.0f, 컨센서스=%.0f, 차이=%.1f%%",
                ticker, claimed_price, consensus, diff_pct * 100,
            )

        return verified

    except Exception as e:
        logger.error("[%s] 목표가 검증 실패: %s", ticker, e, exc_info=True)
        return False


def _verify_event(
    event: dict,
    known_events: list[dict],
) -> bool:
    """이벤트가 알려진 일정에 포함되어 있는지 검증합니다."""
    try:
        if not known_events:
            return False

        event_date = event.get("date", "")
        event_desc = event.get("event", "")

        for known in known_events:
            known_date = known.get("date", "")
            known_desc = known.get("event", "")

            if event_date == known_date and event_desc in known_desc:
                return True
            if event_date == known_date and known_desc in event_desc:
                return True

        return False

    except Exception as e:
        logger.error("이벤트 검증 실패: %s", e, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Main guard function
# ---------------------------------------------------------------------------

def guard_response(
    response: str,
    known_prices: dict[str, float] | None = None,
    known_events: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """AI 응답을 검증하고 미확인 항목에 태그를 붙입니다.

    Args:
        response: 원본 AI 응답 텍스트
        known_prices: {ticker_or_name: consensus_price} 검증용 데이터
        known_events: [{"date": ..., "event": ...}] 검증용 데이터

    Returns:
        (수정된 응답, 미확인 주장 목록)
    """
    try:
        known_prices = known_prices or {}
        known_events = known_events or []
        unverified: list[str] = []
        modified = response

        # Check target prices
        target_claims = extract_target_prices(response)
        for name, price in target_claims:
            verified = verify_target_price(name, price, known_prices) if name else False

            if not verified:
                price_str = f"{price:,.0f}"
                claim_text = f"목표가 {price_str}원"
                tagged = f"목표가 {price_str}원 [미확인]"

                if claim_text in modified:
                    modified = modified.replace(claim_text, tagged, 1)

                label = f"{name} " if name else ""
                unverified.append(f"{label}목표가 {price_str}원")

        # Check events
        event_claims = extract_events(response)
        for event in event_claims:
            verified = _verify_event(event, known_events)

            if not verified:
                source_text = event.get("source_text", "")
                if source_text and source_text in modified:
                    modified = modified.replace(
                        source_text, f"{source_text} [미확인]", 1
                    )
                unverified.append(source_text)

        # Append footer if there are unverified claims
        if unverified:
            modified += "\n\n---\n"
            modified += f"위 응답에 [미확인] 표시된 {len(unverified)}건은 "
            modified += "실시간 데이터로 검증되지 않은 내용입니다. "
            modified += "투자 판단 전 반드시 직접 확인해 주세요."

        logger.info(
            "환각 검증 완료: 총 %d건 검사, 미확인 %d건",
            len(target_claims) + len(event_claims),
            len(unverified),
        )

        return modified, unverified

    except Exception as e:
        logger.error("환각 검증 실패: %s", e, exc_info=True)
        return response, []


# ---------------------------------------------------------------------------
# Logging format
# ---------------------------------------------------------------------------

def format_hallucination_log(
    query: str,
    response: str,
    unverified: list[str],
) -> str:
    """환각 검증 로그를 포맷합니다."""
    try:
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M:%S")
        lines = [
            f"[환각 검증 로그] {now}",
            f"질의: {query[:100]}{'...' if len(query) > 100 else ''}",
            f"응답 길이: {len(response)}자",
            f"미확인 항목: {len(unverified)}건",
        ]

        if unverified:
            lines.append("")
            for i, claim in enumerate(unverified, 1):
                lines.append(f"  {i}. {claim}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("환각 로그 생성 실패: %s", e, exc_info=True)
        return f"환각 로그 생성 오류: {e}"
