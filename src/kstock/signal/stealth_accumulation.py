"""Stealth accumulation detection (기관/외인 스텔스 매집 탐지).

Detects patterns where institutions/foreigners quietly buy small amounts
over consecutive days while price hasn't moved significantly yet.

Patterns:
  1. Institutional streak: 5+ consecutive days net buying, 1-500억/day
  2. Foreign streak: 5+ consecutive days net buying, 1-500억/day
  3. Dual accumulation: Both inst+foreign buying 3+ days simultaneously
  4. Pension entry: National pension 5% report (DART trigger)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern configuration
# ---------------------------------------------------------------------------

ACCUMULATION_PATTERNS: dict[str, dict] = {
    "institutional_streak": {
        "name": "기관 연속 매수",
        "min_consecutive_days": 5,
        "min_daily_amount": 1e8,       # 1억 원
        "max_daily_amount": 5e10,      # 500억 원
        "max_price_change": 0.10,      # 10%
        "score_weight": 30,
    },
    "foreign_streak": {
        "name": "외인 연속 매수",
        "min_consecutive_days": 5,
        "min_daily_amount": 1e8,       # 1억 원
        "max_daily_amount": 5e10,      # 500억 원
        "max_price_change": 0.10,      # 10%
        "score_weight": 30,
    },
    "dual_accumulation": {
        "name": "기관+외인 동시 매수",
        "min_consecutive_days": 3,
        "min_daily_amount": 5e7,       # 5천만 원
        "max_daily_amount": None,
        "max_price_change": 0.10,
        "score_weight": 50,
    },
    "pension_entry": {
        "name": "연기금 진입 감지",
        "entities": ["국민연금", "공무원연금", "사학연금", "군인공제회"],
        "score_weight": 40,
    },
}

PENSION_ENTITIES: list[str] = ["국민연금", "공무원연금", "사학연금", "군인공제회"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StreakResult:
    """Single pattern detection result."""

    pattern_name: str
    streak_days: int
    total_amount: float
    avg_daily: float
    score: int


@dataclass
class AccumulationDetection:
    """Aggregated stealth accumulation detection for a single stock."""

    ticker: str
    name: str
    patterns: list[StreakResult] = field(default_factory=list)
    total_score: int = 0
    price_change_20d: float = 0.0
    inst_total: float = 0.0
    foreign_total: float = 0.0


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def count_streak(
    daily_amounts: list[float],
    min_daily: float,
) -> tuple[int, float]:
    """가장 최근부터 연속 순매수 일수와 합계를 반환합니다.

    Parameters
    ----------
    daily_amounts:
        일별 순매수 금액 리스트 (오래된 날짜 -> 최근 순서).
    min_daily:
        하루 최소 순매수 금액 기준.

    Returns
    -------
    tuple[int, float]
        (연속 순매수 일수, 연속 구간 총 매수 금액).
    """
    try:
        if not daily_amounts:
            return 0, 0.0

        streak_days = 0
        total_amount = 0.0

        # 최근 날짜부터 역순으로 탐색
        for amount in reversed(daily_amounts):
            if amount >= min_daily:
                streak_days += 1
                total_amount += amount
            else:
                break

        return streak_days, total_amount
    except Exception:
        logger.exception("count_streak 계산 중 오류 발생")
        return 0, 0.0


# ---------------------------------------------------------------------------
# Individual pattern detectors
# ---------------------------------------------------------------------------


def detect_institutional_streak(
    daily_inst_amounts: list[float],
    config: dict | None = None,
) -> StreakResult | None:
    """기관 연속 순매수 패턴을 감지합니다.

    Parameters
    ----------
    daily_inst_amounts:
        기관 일별 순매수 금액 리스트 (오래된 날짜 -> 최근).
    config:
        패턴 설정 오버라이드. None이면 기본값 사용.

    Returns
    -------
    StreakResult | None
        감지 결과. 조건 미충족 시 None.
    """
    try:
        cfg = config or ACCUMULATION_PATTERNS["institutional_streak"]
        min_days: int = cfg.get("min_consecutive_days", 5)
        min_daily: float = cfg.get("min_daily_amount", 1e8)
        max_daily: float | None = cfg.get("max_daily_amount", 5e10)
        weight: int = cfg.get("score_weight", 30)

        streak_days, total_amount = count_streak(daily_inst_amounts, min_daily)

        if streak_days < min_days:
            return None

        avg_daily = total_amount / streak_days if streak_days > 0 else 0.0

        # max_daily 초과 시 스텔스가 아닌 대놓고 매수이므로 제외
        if max_daily is not None and avg_daily > max_daily:
            logger.info(
                "기관 일평균 매수 %.0f 이 상한 %.0f 초과 - 스텔스 매집이 아님",
                avg_daily,
                max_daily,
            )
            return None

        # 스코어: 기본 weight + 일수에 비례한 보너스 (최대 weight * 2)
        bonus = min(streak_days - min_days, min_days) * (weight // min_days)
        score = min(weight + bonus, weight * 2)

        return StreakResult(
            pattern_name=cfg.get("name", "기관 연속 매수"),
            streak_days=streak_days,
            total_amount=total_amount,
            avg_daily=avg_daily,
            score=score,
        )
    except Exception:
        logger.exception("기관 연속 매수 감지 중 오류 발생")
        return None


def detect_foreign_streak(
    daily_foreign_amounts: list[float],
    config: dict | None = None,
) -> StreakResult | None:
    """외인 연속 순매수 패턴을 감지합니다.

    Parameters
    ----------
    daily_foreign_amounts:
        외인 일별 순매수 금액 리스트 (오래된 날짜 -> 최근).
    config:
        패턴 설정 오버라이드. None이면 기본값 사용.

    Returns
    -------
    StreakResult | None
        감지 결과. 조건 미충족 시 None.
    """
    try:
        cfg = config or ACCUMULATION_PATTERNS["foreign_streak"]
        min_days: int = cfg.get("min_consecutive_days", 5)
        min_daily: float = cfg.get("min_daily_amount", 1e8)
        max_daily: float | None = cfg.get("max_daily_amount", 5e10)
        weight: int = cfg.get("score_weight", 30)

        streak_days, total_amount = count_streak(daily_foreign_amounts, min_daily)

        if streak_days < min_days:
            return None

        avg_daily = total_amount / streak_days if streak_days > 0 else 0.0

        if max_daily is not None and avg_daily > max_daily:
            logger.info(
                "외인 일평균 매수 %.0f 이 상한 %.0f 초과 - 스텔스 매집이 아님",
                avg_daily,
                max_daily,
            )
            return None

        bonus = min(streak_days - min_days, min_days) * (weight // min_days)
        score = min(weight + bonus, weight * 2)

        return StreakResult(
            pattern_name=cfg.get("name", "외인 연속 매수"),
            streak_days=streak_days,
            total_amount=total_amount,
            avg_daily=avg_daily,
            score=score,
        )
    except Exception:
        logger.exception("외인 연속 매수 감지 중 오류 발생")
        return None


def detect_dual_accumulation(
    daily_inst: list[float],
    daily_foreign: list[float],
    config: dict | None = None,
) -> StreakResult | None:
    """기관+외인 동시 매수 패턴을 감지합니다.

    Parameters
    ----------
    daily_inst:
        기관 일별 순매수 금액 리스트 (오래된 날짜 -> 최근).
    daily_foreign:
        외인 일별 순매수 금액 리스트 (오래된 날짜 -> 최근).
    config:
        패턴 설정 오버라이드. None이면 기본값 사용.

    Returns
    -------
    StreakResult | None
        감지 결과. 조건 미충족 시 None.
    """
    try:
        cfg = config or ACCUMULATION_PATTERNS["dual_accumulation"]
        min_days: int = cfg.get("min_consecutive_days", 3)
        min_daily: float = cfg.get("min_daily_amount", 5e7)
        weight: int = cfg.get("score_weight", 50)

        # 두 리스트 중 짧은 쪽 기준
        length = min(len(daily_inst), len(daily_foreign))
        if length == 0:
            return None

        # 최근부터 역순으로 동시 매수 일수 계산
        streak_days = 0
        total_inst = 0.0
        total_foreign = 0.0

        for i in range(1, length + 1):
            inst_amt = daily_inst[-i]
            foreign_amt = daily_foreign[-i]
            if inst_amt >= min_daily and foreign_amt >= min_daily:
                streak_days += 1
                total_inst += inst_amt
                total_foreign += foreign_amt
            else:
                break

        if streak_days < min_days:
            return None

        total_amount = total_inst + total_foreign
        avg_daily = total_amount / streak_days if streak_days > 0 else 0.0

        # 동시 매수는 가장 강력한 신호이므로 높은 점수
        bonus = min(streak_days - min_days, min_days) * (weight // (min_days * 2))
        score = min(weight + bonus, weight * 2)

        return StreakResult(
            pattern_name=cfg.get("name", "기관+외인 동시 매수"),
            streak_days=streak_days,
            total_amount=total_amount,
            avg_daily=avg_daily,
            score=score,
        )
    except Exception:
        logger.exception("기관+외인 동시 매수 감지 중 오류 발생")
        return None


def detect_pension_entry(
    disclosure_text: str,
) -> StreakResult | None:
    """연기금 진입 공시를 감지합니다.

    DART 스타일 공시 텍스트에서 연기금 관련 엔티티 언급을 확인합니다.

    Parameters
    ----------
    disclosure_text:
        DART 공시 텍스트 (5% 대량보유 보고서 등).

    Returns
    -------
    StreakResult | None
        감지 결과. 연기금 언급이 없으면 None.
    """
    try:
        if not disclosure_text or not disclosure_text.strip():
            return None

        cfg = ACCUMULATION_PATTERNS["pension_entry"]
        entities: list[str] = cfg.get("entities", PENSION_ENTITIES)
        weight: int = cfg.get("score_weight", 40)

        found_entities: list[str] = []
        for entity in entities:
            if entity in disclosure_text:
                found_entities.append(entity)

        if not found_entities:
            return None

        logger.info("연기금 진입 감지: %s", ", ".join(found_entities))

        # 연기금 수에 따라 점수 조정
        entity_bonus = (len(found_entities) - 1) * 5
        score = min(weight + entity_bonus, weight * 2)

        return StreakResult(
            pattern_name=f"연기금 진입 ({', '.join(found_entities)})",
            streak_days=0,
            total_amount=0.0,
            avg_daily=0.0,
            score=score,
        )
    except Exception:
        logger.exception("연기금 진입 감지 중 오류 발생")
        return None


# ---------------------------------------------------------------------------
# Stock-level scanning
# ---------------------------------------------------------------------------


def scan_stock(
    ticker: str,
    name: str,
    daily_inst: list[float],
    daily_foreign: list[float],
    price_change_20d: float,
    disclosure_text: str = "",
) -> AccumulationDetection | None:
    """단일 종목의 스텔스 매집 패턴을 종합 분석합니다.

    Parameters
    ----------
    ticker:
        종목 코드 (예: "005930").
    name:
        종목명 (예: "삼성전자").
    daily_inst:
        기관 일별 순매수 금액 리스트 (오래된 날짜 -> 최근).
    daily_foreign:
        외인 일별 순매수 금액 리스트 (오래된 날짜 -> 최근).
    price_change_20d:
        최근 20일 주가 변화율 (0.05 = +5%).
    disclosure_text:
        DART 공시 텍스트 (선택).

    Returns
    -------
    AccumulationDetection | None
        패턴이 하나라도 감지되면 결과 반환, 없으면 None.
    """
    try:
        # 가격이 이미 많이 올랐으면 스텔스가 아님
        if price_change_20d > 0.10:
            logger.debug(
                "%s(%s) 20일 변화 %.1f%% > 10%% - 스텔스 매집 대상 아님",
                name,
                ticker,
                price_change_20d * 100,
            )
            return None

        patterns_found: list[StreakResult] = []

        # 1. 기관 연속 매수
        inst_result = detect_institutional_streak(daily_inst)
        if inst_result is not None:
            patterns_found.append(inst_result)

        # 2. 외인 연속 매수
        foreign_result = detect_foreign_streak(daily_foreign)
        if foreign_result is not None:
            patterns_found.append(foreign_result)

        # 3. 기관+외인 동시 매수
        dual_result = detect_dual_accumulation(daily_inst, daily_foreign)
        if dual_result is not None:
            patterns_found.append(dual_result)

        # 4. 연기금 진입
        pension_result = detect_pension_entry(disclosure_text)
        if pension_result is not None:
            patterns_found.append(pension_result)

        if not patterns_found:
            return None

        total_score = sum(p.score for p in patterns_found)
        inst_total = sum(daily_inst) if daily_inst else 0.0
        foreign_total = sum(daily_foreign) if daily_foreign else 0.0

        detection = AccumulationDetection(
            ticker=ticker,
            name=name,
            patterns=patterns_found,
            total_score=total_score,
            price_change_20d=price_change_20d,
            inst_total=inst_total,
            foreign_total=foreign_total,
        )

        logger.info(
            "스텔스 매집 감지: %s(%s) - 패턴 %d개, 총점 %d",
            name,
            ticker,
            len(patterns_found),
            total_score,
        )

        return detection
    except Exception:
        logger.exception("scan_stock 처리 중 오류: %s(%s)", name, ticker)
        return None


def scan_all_stocks(
    stocks_data: list[dict],
) -> list[AccumulationDetection]:
    """전체 종목을 스캔하여 스텔스 매집 종목을 찾습니다.

    Parameters
    ----------
    stocks_data:
        종목 데이터 리스트. 각 항목은 다음 키를 포함:
        - ticker (str): 종목 코드
        - name (str): 종목명
        - daily_inst (list[float]): 기관 일별 순매수
        - daily_foreign (list[float]): 외인 일별 순매수
        - price_change_20d (float): 20일 주가 변화율
        - disclosure_text (str, optional): DART 공시 텍스트

    Returns
    -------
    list[AccumulationDetection]
        총점 기준 내림차순 정렬, 상위 20개.
    """
    try:
        detections: list[AccumulationDetection] = []

        for stock in stocks_data:
            try:
                result = scan_stock(
                    ticker=stock.get("ticker", ""),
                    name=stock.get("name", ""),
                    daily_inst=stock.get("daily_inst", []),
                    daily_foreign=stock.get("daily_foreign", []),
                    price_change_20d=stock.get("price_change_20d", 0.0),
                    disclosure_text=stock.get("disclosure_text", ""),
                )
                if result is not None:
                    detections.append(result)
            except Exception:
                logger.exception(
                    "개별 종목 스캔 실패: %s(%s)",
                    stock.get("name", "?"),
                    stock.get("ticker", "?"),
                )

        # 총점 기준 내림차순 정렬, 상위 20개
        detections.sort(key=lambda d: d.total_score, reverse=True)
        top_detections = detections[:20]

        logger.info(
            "스텔스 매집 스캔 완료: 전체 %d종목 중 %d종목 감지 (상위 %d개 반환)",
            len(stocks_data),
            len(detections),
            len(top_detections),
        )

        return top_detections
    except Exception:
        logger.exception("scan_all_stocks 처리 중 오류 발생")
        return []


# ---------------------------------------------------------------------------
# Score integration
# ---------------------------------------------------------------------------


def integrate_accumulation_score(
    base_score: int,
    detection: AccumulationDetection | None,
) -> int:
    """기존 종합 점수에 스텔스 매집 보너스를 추가합니다.

    Parameters
    ----------
    base_score:
        기존 종합 점수.
    detection:
        스텔스 매집 감지 결과. None이면 보너스 없음.

    Returns
    -------
    int
        보너스 적용 후 점수 (최대 250).
    """
    try:
        if detection is None:
            return base_score

        bonus = 0
        pattern_names = [p.pattern_name for p in detection.patterns]

        for p_name in pattern_names:
            if "동시" in p_name or "dual" in p_name.lower():
                bonus += 15
            elif "기관" in p_name:
                bonus += 8
            elif "외인" in p_name:
                bonus += 8
            elif "연기금" in p_name:
                bonus += 10

        result = min(base_score + bonus, 250)

        if bonus > 0:
            logger.info(
                "스텔스 매집 보너스 적용: %d -> %d (+%d)",
                base_score,
                result,
                bonus,
            )

        return result
    except Exception:
        logger.exception("스텔스 매집 점수 통합 중 오류 발생")
        return base_score


# ---------------------------------------------------------------------------
# Formatting (Telegram alerts)
# ---------------------------------------------------------------------------


def _format_amount_korean(amount: float) -> str:
    """금액을 한국식 억 단위로 포맷합니다."""
    try:
        eok = amount / 1e8
        if eok >= 1.0:
            return f"{eok:.0f}억"
        man = amount / 1e4
        if man >= 1.0:
            return f"{man:.0f}만"
        return f"{amount:.0f}원"
    except Exception:
        return "N/A"


def format_accumulation_alert(
    detections: list[AccumulationDetection],
) -> str:
    """스텔스 매집 감지 결과를 텔레그램 알림 형식으로 포맷합니다.

    Parameters
    ----------
    detections:
        감지 결과 리스트 (총점 기준 정렬 권장).

    Returns
    -------
    str
        텔레그램 발송용 한글 메시지.
    """
    try:
        if not detections:
            return (
                "[스텔스 매집 감지] 일일 스캔\n"
                "주호님, 오늘은 스텔스 매집 패턴이 감지되지 않았습니다."
            )

        lines: list[str] = [
            "[스텔스 매집 감지] 일일 스캔",
            "주호님, 큰손이 조용히 모으는 종목이 감지되었습니다.",
            "",
        ]

        for idx, det in enumerate(detections, start=1):
            lines.append(
                f"{idx}. {det.name} ({det.ticker}) 스코어 {det.total_score}"
            )

            # 개별 패턴 상세
            has_dual = False
            dual_days = 0
            for pattern in det.patterns:
                if "동시" in pattern.pattern_name:
                    has_dual = True
                    dual_days = pattern.streak_days
                elif "기관" in pattern.pattern_name and "연기금" not in pattern.pattern_name:
                    avg_str = _format_amount_korean(pattern.avg_daily)
                    lines.append(
                        f"  기관 {pattern.streak_days}일 연속 순매수 "
                        f"(일평균 {avg_str})"
                    )
                elif "외인" in pattern.pattern_name:
                    avg_str = _format_amount_korean(pattern.avg_daily)
                    lines.append(
                        f"  외인 {pattern.streak_days}일 연속 순매수 "
                        f"(일평균 {avg_str})"
                    )
                elif "연기금" in pattern.pattern_name:
                    lines.append(f"  {pattern.pattern_name}")

            if has_dual:
                lines.append(
                    f"  -> 기관+외인 동시 매수 {dual_days}일! (최강 신호)"
                )

            # 주가 변화
            pct = det.price_change_20d * 100
            if pct >= 0:
                change_str = f"+{pct:.1f}%"
            else:
                change_str = f"{pct:.1f}%"

            if abs(det.price_change_20d) <= 0.03:
                stage = "아직 초기"
            elif abs(det.price_change_20d) <= 0.07:
                stage = "초기 움직임"
            else:
                stage = "주의 관찰"

            lines.append(f"  20일 주가 변화: {change_str} ({stage})")
            lines.append("")

        return "\n".join(lines).rstrip()
    except Exception:
        logger.exception("스텔스 매집 알림 포맷 중 오류 발생")
        return "[스텔스 매집 감지] 주호님, 알림 생성 중 오류가 발생했습니다."


def format_pension_alert(
    detection: AccumulationDetection,
    entity_name: str,
) -> str:
    """연기금 진입 특별 알림을 포맷합니다.

    Parameters
    ----------
    detection:
        해당 종목의 스텔스 매집 감지 결과.
    entity_name:
        감지된 연기금 이름 (예: "국민연금").

    Returns
    -------
    str
        텔레그램 발송용 연기금 특별 알림 메시지.
    """
    try:
        pct = detection.price_change_20d * 100
        if pct >= 0:
            change_str = f"+{pct:.1f}%"
        else:
            change_str = f"{pct:.1f}%"

        inst_str = _format_amount_korean(detection.inst_total)
        foreign_str = _format_amount_korean(detection.foreign_total)

        lines: list[str] = [
            f"[연기금 진입 알림] {entity_name}",
            f"주호님, {entity_name}이(가) {detection.name}({detection.ticker})에 진입했습니다.",
            "",
            f"종목: {detection.name} ({detection.ticker})",
            f"감지 연기금: {entity_name}",
            f"스코어: {detection.total_score}",
            "",
            f"기관 누적 순매수: {inst_str}",
            f"외인 누적 순매수: {foreign_str}",
            f"20일 주가 변화: {change_str}",
            "",
        ]

        # 동반 패턴 요약
        other_patterns = [
            p for p in detection.patterns if "연기금" not in p.pattern_name
        ]
        if other_patterns:
            lines.append("동반 신호:")
            for p in other_patterns:
                if p.streak_days > 0:
                    avg_str = _format_amount_korean(p.avg_daily)
                    lines.append(
                        f"  - {p.pattern_name}: {p.streak_days}일 "
                        f"(일평균 {avg_str})"
                    )
            lines.append("")

        lines.append(
            f"주호님, {entity_name} 진입은 중장기 긍정 신호입니다. "
            "분할 매수를 검토하세요."
        )

        return "\n".join(lines).rstrip()
    except Exception:
        logger.exception("연기금 진입 알림 포맷 중 오류 발생")
        return (
            f"[연기금 진입 알림] 주호님, {entity_name} 관련 알림 생성 중 "
            "오류가 발생했습니다."
        )
