"""Surge stock detection system (급등주 포착).

Detects rapid price/volume movements and classifies their health.

Scan times: 09:10, 11:30, 13:00, 15:00

Surge conditions (OR):
  1. price_surge: +5% or more
  2. volume_explosion: 3x average volume
  3. combined: +3% AND 2x volume
  4. limit_approach: +25% or more (near limit up 29.9%)

Exclude filters:
  - Market cap < 500억 (junk stocks)
  - Daily trading value < 5억
  - Managed/warning stocks
  - Listed < 90 days (IPO volatility)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Surge condition thresholds (OR — any single trigger fires)
# ---------------------------------------------------------------------------
SURGE_CONDITIONS: dict[str, dict] = {
    "price_surge": {
        "label": "가격 급등 (+5% 이상)",
        "min_change": 5.0,
        "min_volume_ratio": 0.0,
    },
    "volume_explosion": {
        "label": "거래량 폭발 (3배 이상)",
        "min_change": 0.0,
        "min_volume_ratio": 3.0,
    },
    "combined": {
        "label": "복합 급등 (+3% & 2배 거래량)",
        "min_change": 3.0,
        "min_volume_ratio": 2.0,
    },
    "limit_approach": {
        "label": "상한가 접근 (+25% 이상)",
        "min_change": 25.0,
        "min_volume_ratio": 0.0,
    },
}

# ---------------------------------------------------------------------------
# Exclusion filters — stocks that fail any of these are dropped
# ---------------------------------------------------------------------------
EXCLUDE_FILTERS: dict[str, object] = {
    "min_market_cap": 500_0000_0000,       # 500억 원
    "min_daily_volume": 5_0000_0000,       # 5억 원
    "exclude_managed": True,
    "exclude_warning": True,
    "min_listing_days": 90,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class SurgeHealth:
    """급등 건전성 평가 결과."""

    grade: str                              # HEALTHY / CAUTION / DANGER
    label: str                              # 한글 라벨
    score: int                              # 건전성 점수 (높을수록 양호)
    reasons: list[str] = field(default_factory=list)
    action: str = ""                        # 주호님 맞춤 행동 가이드


@dataclass
class SurgeStock:
    """급등 감지된 종목."""

    ticker: str
    name: str
    price: float
    change_pct: float
    volume_ratio: float
    triggers: list[str] = field(default_factory=list)
    market_cap: float = 0.0
    scan_time: str = ""
    health: SurgeHealth = field(default_factory=lambda: SurgeHealth(
        grade="CAUTION", label="주의", score=0,
    ))
    ai_analysis: str = ""


# ---------------------------------------------------------------------------
# 1. check_surge_conditions
# ---------------------------------------------------------------------------
def check_surge_conditions(change_pct: float, volume_ratio: float) -> list[str]:
    """Return list of triggered surge condition names."""
    try:
        triggered: list[str] = []
        for key, cond in SURGE_CONDITIONS.items():
            min_chg = cond["min_change"]
            min_vol = cond["min_volume_ratio"]
            change_ok = change_pct >= min_chg if min_chg > 0 else True
            volume_ok = volume_ratio >= min_vol if min_vol > 0 else True
            if change_ok and volume_ok:
                triggered.append(key)
        return triggered
    except Exception:
        logger.exception("급등 조건 판별 중 오류 발생")
        return []


# ---------------------------------------------------------------------------
# 2. passes_exclude_filter
# ---------------------------------------------------------------------------
def passes_exclude_filter(
    market_cap: float,
    daily_volume: float,
    is_managed: bool,
    is_warning: bool,
    listing_days: int,
) -> bool:
    """Return True if the stock passes all exclusion filters."""
    try:
        if market_cap < EXCLUDE_FILTERS["min_market_cap"]:
            return False
        if daily_volume < EXCLUDE_FILTERS["min_daily_volume"]:
            return False
        if EXCLUDE_FILTERS["exclude_managed"] and is_managed:
            return False
        if EXCLUDE_FILTERS["exclude_warning"] and is_warning:
            return False
        if listing_days < EXCLUDE_FILTERS["min_listing_days"]:
            return False
        return True
    except Exception:
        logger.exception("필터 판별 중 오류 발생")
        return False


# ---------------------------------------------------------------------------
# 3. classify_surge_health
# ---------------------------------------------------------------------------
def classify_surge_health(
    change_pct: float,
    volume_ratio: float,
    has_news: bool,
    has_disclosure: bool,
    inst_net: float,
    foreign_net: float,
    retail_net: float,
    prev_vol_ratio: float,
    detected_time: str,
    past_suspicious_count: int,
) -> SurgeHealth:
    """Classify surge health into HEALTHY / CAUTION / DANGER.

    Score system:
        공시 +30 | 뉴스 +20, 뉴스 없음 -30
        기관+외인 동시 매수 +25, 한쪽 +10, 없음 -20
        전일 거래량 >= 1.5배 +10 | 거래량 >= 5배 갑작스러운 -15
        장 초반 공시 없이 급등 -10 | 과거 의심 >= 3회 -15
    Grade: score >= 30 HEALTHY, >= 0 CAUTION, < 0 DANGER
    """
    try:
        score = 0
        reasons: list[str] = []

        # 공시
        if has_disclosure:
            score += 30
            reasons.append("공시 확인됨 (+30)")

        # 뉴스
        if has_news:
            score += 20
            reasons.append("관련 뉴스 존재 (+20)")
        else:
            score -= 30
            reasons.append("관련 뉴스 없음 (-30)")

        # 기관/외인 수급
        inst_buy = inst_net > 0
        foreign_buy = foreign_net > 0
        if inst_buy and foreign_buy:
            score += 25
            reasons.append("기관+외인 동시 순매수 (+25)")
        elif inst_buy or foreign_buy:
            score += 10
            buyer = "기관" if inst_buy else "외인"
            reasons.append(f"{buyer} 순매수 (+10)")
        else:
            score -= 20
            reasons.append("기관/외인 순매수 없음 (-20)")

        # 전일 거래량 사전 조짐
        if prev_vol_ratio >= 1.5:
            score += 10
            reasons.append(f"전일 거래량 비율 {prev_vol_ratio:.1f}배 — 사전 조짐 (+10)")

        # 갑작스러운 거래량 폭발
        if volume_ratio >= 5.0:
            score -= 15
            reasons.append(f"거래량 {volume_ratio:.1f}배 갑작스러운 폭발 (-15)")

        # 장 초반 공시 없이 급등
        try:
            hour = int(detected_time.split(":")[0])
        except (ValueError, IndexError):
            hour = -1
        if hour == 9 and not has_disclosure:
            score -= 10
            reasons.append("장 초반 급등인데 공시 없음 (-10)")

        # 과거 의심 이력
        if past_suspicious_count >= 3:
            score -= 15
            reasons.append(f"과거 의심 이력 {past_suspicious_count}회 (-15)")

        # 등급 판정
        if score >= 30:
            grade = "HEALTHY"
            label = "건전"
            action = "주호님, 추세 편승 매매 고려해보세요."
        elif score >= 0:
            grade = "CAUTION"
            label = "주의"
            action = "주호님, 추가 확인 후 소량 진입 고려하세요."
        else:
            grade = "DANGER"
            label = "위험"
            action = "주호님, 이 급등은 위험 신호가 많습니다. 관망하세요."

        return SurgeHealth(
            grade=grade, label=label, score=score,
            reasons=reasons, action=action,
        )
    except Exception:
        logger.exception("급등 건전성 분류 중 오류 발생")
        return SurgeHealth(
            grade="DANGER", label="분류 실패", score=-100,
            reasons=["건전성 분류 중 예외 발생"],
            action="주호님, 시스템 오류로 판별 불가합니다. 매매를 보류하세요.",
        )


# ---------------------------------------------------------------------------
# 4. scan_stocks
# ---------------------------------------------------------------------------
def scan_stocks(stocks_data: list[dict]) -> list[SurgeStock]:
    """Scan stock data dicts and return up to 10 surge stocks by change_pct desc.

    Each dict must have: ticker, name, price, change_pct, volume, avg_volume_20,
    market_cap, daily_volume, is_managed, is_warning, listing_days, has_news,
    has_disclosure, inst_net, foreign_net, retail_net, prev_vol_ratio,
    detected_time, past_suspicious_count.
    """
    try:
        surges: list[SurgeStock] = []

        for sd in stocks_data:
            if not passes_exclude_filter(
                market_cap=sd["market_cap"],
                daily_volume=sd["daily_volume"],
                is_managed=sd.get("is_managed", False),
                is_warning=sd.get("is_warning", False),
                listing_days=sd.get("listing_days", 999),
            ):
                continue

            avg_vol = sd.get("avg_volume_20", 0)
            volume_ratio = sd["volume"] / avg_vol if avg_vol > 0 else 0.0

            triggers = check_surge_conditions(sd["change_pct"], volume_ratio)
            if not triggers:
                continue

            health = classify_surge_health(
                change_pct=sd["change_pct"],
                volume_ratio=volume_ratio,
                has_news=sd.get("has_news", False),
                has_disclosure=sd.get("has_disclosure", False),
                inst_net=sd.get("inst_net", 0),
                foreign_net=sd.get("foreign_net", 0),
                retail_net=sd.get("retail_net", 0),
                prev_vol_ratio=sd.get("prev_vol_ratio", 0),
                detected_time=sd.get("detected_time", ""),
                past_suspicious_count=sd.get("past_suspicious_count", 0),
            )

            surges.append(SurgeStock(
                ticker=sd["ticker"],
                name=sd["name"],
                price=sd["price"],
                change_pct=sd["change_pct"],
                volume_ratio=volume_ratio,
                triggers=triggers,
                market_cap=sd["market_cap"],
                scan_time=sd.get("detected_time", ""),
                health=health,
                ai_analysis="",
            ))

        surges.sort(key=lambda s: s.change_pct, reverse=True)
        return surges[:10]
    except Exception:
        logger.exception("급등주 스캔 중 오류 발생")
        return []


# ---------------------------------------------------------------------------
# 5. format_surge_alert
# ---------------------------------------------------------------------------
def format_surge_alert(surges: list[SurgeStock], scan_time: str) -> str:
    """Format surge stocks into a Telegram alert (no ** bold markers)."""
    try:
        if not surges:
            return f"[{scan_time}] 급등주 감지 없음"

        grade_icons = {
            "HEALTHY": "\u2705",
            "CAUTION": "\u26a0\ufe0f",
            "DANGER": "\U0001f6d1",
        }

        lines: list[str] = [
            f"\U0001f4c8 급등주 포착 [{scan_time}]",
            f"감지 종목: {len(surges)}개",
            "",
        ]

        for idx, s in enumerate(surges, 1):
            icon = grade_icons.get(s.health.grade, "\u2753")
            trigger_labels = [
                SURGE_CONDITIONS[t]["label"] if t in SURGE_CONDITIONS else t
                for t in s.triggers
            ]
            lines.append(f"{idx}. {icon} {s.name} ({s.ticker})")
            lines.append(
                f"   현재가 {s.price:,.0f}원  {s.change_pct:+.1f}%  "
                f"거래량 {s.volume_ratio:.1f}배"
            )
            lines.append(f"   시총 {s.market_cap / 1_0000_0000:.0f}억원")
            lines.append(f"   조건: {', '.join(trigger_labels)}")
            lines.append(f"   건전성: {s.health.label} ({s.health.score}점)")
            for reason in s.health.reasons:
                lines.append(f"     - {reason}")
            lines.append(f"   {s.health.action}")
            if s.ai_analysis:
                lines.append(f"   AI: {s.ai_analysis}")
            lines.append("")

        return "\n".join(lines)
    except Exception:
        logger.exception("급등주 알림 포맷 중 오류 발생")
        return "급등주 알림 생성 실패"


# ---------------------------------------------------------------------------
# 6. format_holding_surge_alert
# ---------------------------------------------------------------------------
def format_holding_surge_alert(
    surge: SurgeStock,
    holding_profit_pct: float,
    holding_profit_amount: float,
) -> str:
    """Alert when an owned stock surges — helps decide take-profit vs hold."""
    try:
        grade_icons = {
            "HEALTHY": "\u2705",
            "CAUTION": "\u26a0\ufe0f",
            "DANGER": "\U0001f6d1",
        }
        icon = grade_icons.get(surge.health.grade, "\u2753")

        lines: list[str] = [
            "\U0001f4a1 보유 종목 급등 알림",
            "",
            f"종목: {surge.name} ({surge.ticker})",
            f"현재가: {surge.price:,.0f}원  {surge.change_pct:+.1f}%",
            f"거래량: 평균 대비 {surge.volume_ratio:.1f}배",
            "",
            f"보유 수익률: {holding_profit_pct:+.1f}%",
            f"평가 손익: {holding_profit_amount:+,.0f}원",
            "",
            f"건전성: {icon} {surge.health.label} ({surge.health.score}점)",
        ]
        for reason in surge.health.reasons:
            lines.append(f"  - {reason}")
        lines.append("")

        if surge.health.grade == "DANGER":
            lines.append(
                "주호님, 이 급등은 의심 신호가 많습니다. "
                "일부 익절을 고려하세요."
            )
        elif surge.health.grade == "CAUTION":
            lines.append(
                "주호님, 급등 원인을 확인 중입니다. "
                "트레일링 스탑을 설정해두세요."
            )
        else:
            lines.append(
                "주호님, 건전한 급등입니다. "
                "목표가까지 홀딩하되 트레일링 스탑을 조정하세요."
            )

        return "\n".join(lines)
    except Exception:
        logger.exception("보유 종목 급등 알림 포맷 중 오류 발생")
        return "보유 종목 급등 알림 생성 실패"


# ---------------------------------------------------------------------------
# 7. link_surge_to_smallcap
# ---------------------------------------------------------------------------
def link_surge_to_smallcap(
    surge: SurgeStock,
    smallcap_score: int,
) -> str | None:
    """Return alert if surge stock is in small-cap sweet spot (1000억-5000억, score>=60)."""
    try:
        cap_lower = 1000_0000_0000   # 1,000억
        cap_upper = 5000_0000_0000   # 5,000억

        if not (cap_lower <= surge.market_cap <= cap_upper):
            return None
        if smallcap_score < 60:
            return None

        cap_bil = surge.market_cap / 1_0000_0000
        lines: list[str] = [
            "\U0001f517 급등주-소형주 연계 발견",
            "",
            f"종목: {surge.name} ({surge.ticker})",
            f"시총: {cap_bil:,.0f}억원 (소형주 구간)",
            f"급등률: {surge.change_pct:+.1f}%  거래량 {surge.volume_ratio:.1f}배",
            f"소형주 점수: {smallcap_score}점",
            "",
        ]

        if surge.health.grade == "HEALTHY":
            lines.append(
                "주호님, 건전한 급등 + 소형주 고점수입니다. "
                "깊이 분석해볼 가치가 있습니다."
            )
        elif surge.health.grade == "CAUTION":
            lines.append(
                "주호님, 소형주 점수는 높지만 급등 건전성이 주의 단계입니다. "
                "공시/뉴스 확인 후 판단하세요."
            )
        else:
            lines.append(
                "주호님, 소형주 점수는 높으나 급등 건전성이 위험입니다. "
                "관망을 권합니다."
            )

        return "\n".join(lines)
    except Exception:
        logger.exception("급등주-소형주 연계 분석 중 오류 발생")
        return None
