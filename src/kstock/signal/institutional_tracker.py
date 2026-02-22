"""Institutional/program trading pattern detector for K-Quant.

Detects institutional trading patterns by entity type:
- Pension funds (국민연금, 사학연금 등)
- Asset managers (자산운용사)
- Securities firms (증권사 자기매매)
- Foreign investors (외국인)
- Insurance companies (보험사)

Also detects program trading signals, window dressing periods,
and year-end tax selling risk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InstitutionalPattern:
    """Single institutional trading pattern entry."""

    institution_type: str  # e.g., "pension_fund", "foreign"
    behavior: str  # e.g., "대형주 순매수 지속"
    signal: str  # e.g., "긍정 - 하방 지지"
    weight: str  # e.g., "높음", "중간", "낮음"


@dataclass
class InstitutionalSignal:
    """Aggregated institutional signal across all entity types."""

    pension_signal: str
    asset_mgr_signal: str
    securities_signal: str
    foreign_signal: str
    insurance_signal: str
    program_net_buy: float  # 억 원
    summary: str


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

INSTITUTIONAL_PATTERNS: dict[str, list[InstitutionalPattern]] = {
    "pension_fund": [
        InstitutionalPattern(
            institution_type="pension_fund",
            behavior="대형주 순매수 지속",
            signal="긍정 - 하방 지지",
            weight="높음",
        ),
        InstitutionalPattern(
            institution_type="pension_fund",
            behavior="순매도 전환",
            signal="주의 - 리밸런싱 가능성",
            weight="중간",
        ),
        InstitutionalPattern(
            institution_type="pension_fund",
            behavior="특정 섹터 집중 매수",
            signal="긍정 - 섹터 방향성 확인",
            weight="높음",
        ),
    ],
    "asset_manager": [
        InstitutionalPattern(
            institution_type="asset_manager",
            behavior="순매수 전환",
            signal="긍정 - 펀드 자금 유입",
            weight="중간",
        ),
        InstitutionalPattern(
            institution_type="asset_manager",
            behavior="순매도 지속",
            signal="부정 - 환매 압력",
            weight="중간",
        ),
        InstitutionalPattern(
            institution_type="asset_manager",
            behavior="소형주 집중 매수",
            signal="긍정 - 개별 종목 모멘텀",
            weight="낮음",
        ),
    ],
    "securities": [
        InstitutionalPattern(
            institution_type="securities",
            behavior="자기매매 순매수 급증",
            signal="주의 - 단기 트레이딩 수급",
            weight="낮음",
        ),
        InstitutionalPattern(
            institution_type="securities",
            behavior="자기매매 순매도 급증",
            signal="부정 - 리스크 축소",
            weight="중간",
        ),
        InstitutionalPattern(
            institution_type="securities",
            behavior="ELW 헤지 물량 출회",
            signal="중립 - 기계적 매도",
            weight="낮음",
        ),
    ],
    "foreign": [
        InstitutionalPattern(
            institution_type="foreign",
            behavior="5일 연속 순매수",
            signal="강한 긍정 - 추세 전환",
            weight="높음",
        ),
        InstitutionalPattern(
            institution_type="foreign",
            behavior="선물 순매수 + 현물 순매도",
            signal="주의 - 방향 전환 임박",
            weight="높음",
        ),
        InstitutionalPattern(
            institution_type="foreign",
            behavior="3일 이상 순매도 + 환율 급등",
            signal="부정 - 위험 신호",
            weight="높음",
        ),
    ],
    "insurance": [
        InstitutionalPattern(
            institution_type="insurance",
            behavior="채권형 자산 축소 + 주식 비중 확대",
            signal="긍정 - 자산배분 전환",
            weight="중간",
        ),
        InstitutionalPattern(
            institution_type="insurance",
            behavior="배당주 집중 매수",
            signal="긍정 - 장기 수급 안정",
            weight="중간",
        ),
        InstitutionalPattern(
            institution_type="insurance",
            behavior="전반적 매도",
            signal="부정 - ALM 리밸런싱",
            weight="낮음",
        ),
    ],
}


# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------


def detect_program_trading(program_net_buy_krw: float) -> dict:
    """Detect program trading signal based on net buy amount.

    Args:
        program_net_buy_krw: 비차익거래 순매수 금액 (억 원 단위).
            양수 = 순매수, 음수 = 순매도.

    Returns:
        dict with keys: signal, amount, description.
    """
    if program_net_buy_krw >= 500:
        return {
            "signal": "단기 상승 시그널",
            "amount": program_net_buy_krw,
            "description": (
                f"비차익 순매수 {program_net_buy_krw:,.0f}억원 → "
                "프로그램 매수세 유입, 단기 상승 압력"
            ),
        }
    elif program_net_buy_krw <= -500:
        return {
            "signal": "단기 하락 시그널",
            "amount": program_net_buy_krw,
            "description": (
                f"비차익 순매도 {abs(program_net_buy_krw):,.0f}억원 → "
                "프로그램 매도세 출회, 단기 하락 압력"
            ),
        }
    else:
        return {
            "signal": "중립",
            "amount": program_net_buy_krw,
            "description": (
                f"비차익 순매수 {program_net_buy_krw:+,.0f}억원 → "
                "프로그램 수급 중립"
            ),
        }


def detect_foreign_turning(
    foreign_consecutive_days: int,
    usdkrw_change_pct: float,
    foreign_futures_net: float,
) -> dict:
    """Detect foreign investor direction change signals.

    Args:
        foreign_consecutive_days: 외국인 연속 순매수/순매도 일수.
            양수 = 연속 순매수 일수, 음수 = 연속 순매도 일수.
        usdkrw_change_pct: USD/KRW 환율 변동률 (%).
        foreign_futures_net: 외국인 선물 순매수 금액 (억 원).
            양수 = 선물 순매수, 음수 = 선물 순매도.

    Returns:
        dict with keys: signal, risk_level, description.
    """
    # 5일 연속 순매수 → 추세 전환
    if foreign_consecutive_days >= 5:
        return {
            "signal": "추세 전환",
            "risk_level": "긍정",
            "description": (
                f"외국인 {foreign_consecutive_days}일 연속 순매수 → "
                "매수 추세 전환 신호"
            ),
        }

    # 3일+ 순매도 + 환율 급등 → 위험
    if foreign_consecutive_days <= -3 and usdkrw_change_pct >= 1.0:
        return {
            "signal": "위험",
            "risk_level": "부정",
            "description": (
                f"외국인 {abs(foreign_consecutive_days)}일 연속 순매도 + "
                f"환율 {usdkrw_change_pct:+.1f}% 급등 → 자금 이탈 위험"
            ),
        }

    # 선물 순매수 + 현물 순매도 → 방향 전환 임박
    if foreign_futures_net > 0 and foreign_consecutive_days < 0:
        return {
            "signal": "방향 전환 임박",
            "risk_level": "주의",
            "description": (
                f"외국인 선물 순매수 {foreign_futures_net:+,.0f}억원 + "
                f"현물 {abs(foreign_consecutive_days)}일 순매도 → "
                "선물 선행, 현물 매수 전환 임박 가능"
            ),
        }

    # 기본: 중립
    if foreign_consecutive_days > 0:
        desc = f"외국인 {foreign_consecutive_days}일 연속 순매수 중 (추세 전환 미확인)"
    elif foreign_consecutive_days < 0:
        desc = f"외국인 {abs(foreign_consecutive_days)}일 연속 순매도 중"
    else:
        desc = "외국인 수급 방향성 미확정"

    return {
        "signal": "중립",
        "risk_level": "중립",
        "description": desc,
    }


def detect_window_dressing(month: int, day: int) -> dict:
    """Detect window dressing periods by institution type.

    Window dressing = 기관이 결산기를 앞두고 수익률 관리 목적으로
    보유 종목 가격을 끌어올리는 행위.

    Args:
        month: 현재 월 (1-12).
        day: 현재 일 (1-31).

    Returns:
        dict with keys: is_window_dressing, institutions, description.
    """
    active: list[dict] = []

    # 연기금: 12월 결산 (12월 중순~말)
    if month == 12 and day >= 15:
        active.append({
            "institution": "연기금",
            "deadline": "12월 31일",
            "note": "연간 수익률 관리",
        })

    # 자산운용사: 분기 결산 (3/6/9/12월 말)
    if month in (3, 6, 9, 12) and day >= 20:
        active.append({
            "institution": "자산운용사",
            "deadline": f"{month}월 말",
            "note": "분기 수익률 보고 대비",
        })

    # 증권사: 3월 결산 (3월 중순~말) - 대다수 증권사 3월 결산
    if month == 3 and day >= 15:
        active.append({
            "institution": "증권사",
            "deadline": "3월 31일",
            "note": "회계연도 결산 수익률 관리",
        })

    # 보험사: 3월 결산 (3월 중순~말)
    if month == 3 and day >= 15:
        active.append({
            "institution": "보험사",
            "deadline": "3월 31일",
            "note": "결산기 포트폴리오 정리",
        })

    # 외국인: 12월 결산 (11월 말 ~ 12월)
    if (month == 11 and day >= 25) or month == 12:
        active.append({
            "institution": "외국인 (글로벌 펀드)",
            "deadline": "12월 31일",
            "note": "연말 NAV 관리 및 세금 최적화",
        })

    if active:
        inst_names = [a["institution"] for a in active]
        return {
            "is_window_dressing": True,
            "institutions": active,
            "description": (
                f"윈도우드레싱 기간: {', '.join(inst_names)} 결산 대비 → "
                "대형 우량주 단기 매수세 유입 가능"
            ),
        }

    return {
        "is_window_dressing": False,
        "institutions": [],
        "description": "윈도우드레싱 비해당 기간",
    }


def detect_tax_selling_risk(
    market: str,
    market_cap: float,
    yearly_return_pct: float,
    month: int,
) -> dict:
    """Detect year-end tax selling risk for individual stocks.

    대주주 양도세 회피를 위한 연말 매도 압력을 탐지.
    코스닥 시총 5,000억 이하 + 연간 수익률 100% 이상 + 12월 → 고위험.

    Args:
        market: "KOSPI" or "KOSDAQ".
        market_cap: 시가총액 (억 원).
        yearly_return_pct: 연초 대비 수익률 (%).
        month: 현재 월 (1-12).

    Returns:
        dict with keys: risk_level, factors, description.
    """
    factors: list[str] = []
    risk_score = 0

    # 코스닥 소형주 여부
    is_kosdaq_small = (
        market.upper() in ("KOSDAQ", "코스닥") and market_cap <= 5000
    )
    if is_kosdaq_small:
        factors.append(f"코스닥 시총 {market_cap:,.0f}억원 (5,000억 이하)")
        risk_score += 1

    # 높은 연간 수익률
    if yearly_return_pct >= 100:
        factors.append(f"연간 수익률 {yearly_return_pct:+.0f}% (100% 이상)")
        risk_score += 1
    elif yearly_return_pct >= 50:
        factors.append(f"연간 수익률 {yearly_return_pct:+.0f}% (50% 이상)")
        risk_score += 0.5

    # 시기 판단
    if month == 12:
        factors.append("12월 - 대주주 양도세 회피 매도 시기")
        risk_score += 1
    elif month == 11:
        factors.append("11월 - 선제적 매도 시작 가능")
        risk_score += 0.5

    # 리스크 레벨 판정
    if risk_score >= 3:
        risk_level = "높음"
        desc = (
            "대주주 양도세 회피 매도 위험 높음 → "
            "12월 중 급락 가능성, 매수 자제 권장"
        )
    elif risk_score >= 2:
        risk_level = "중간"
        desc = (
            "세금 매도 위험 중간 → "
            "12월 초까지 일부 물량 출회 가능"
        )
    elif risk_score >= 1:
        risk_level = "낮음"
        desc = "세금 매도 위험 제한적"
    else:
        risk_level = "해당없음"
        desc = "세금 매도 위험 미해당"

    return {
        "risk_level": risk_level,
        "factors": factors,
        "description": desc,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def analyze_institutional(
    program_net_buy_krw: float = 0.0,
    foreign_consecutive_days: int = 0,
    usdkrw_change_pct: float = 0.0,
    foreign_futures_net: float = 0.0,
    month: int = 1,
    day: int = 1,
    market: str = "KOSPI",
    market_cap: float = 0.0,
    yearly_return_pct: float = 0.0,
    pension_net_buy: float = 0.0,
    asset_mgr_net_buy: float = 0.0,
    securities_net_buy: float = 0.0,
    insurance_net_buy: float = 0.0,
) -> InstitutionalSignal:
    """Aggregate all institutional signals into a single result.

    Args:
        program_net_buy_krw: 비차익 프로그램 순매수 (억 원).
        foreign_consecutive_days: 외국인 연속 순매수/순매도 일수.
        usdkrw_change_pct: USD/KRW 변동률 (%).
        foreign_futures_net: 외국인 선물 순매수 (억 원).
        month: 현재 월.
        day: 현재 일.
        market: 시장 구분.
        market_cap: 시가총액 (억 원).
        yearly_return_pct: 연간 수익률 (%).
        pension_net_buy: 연기금 순매수 (억 원).
        asset_mgr_net_buy: 자산운용사 순매수 (억 원).
        securities_net_buy: 증권사 자기매매 순매수 (억 원).
        insurance_net_buy: 보험사 순매수 (억 원).

    Returns:
        InstitutionalSignal with per-entity signals and summary.
    """
    # 연기금 시그널
    if pension_net_buy > 100:
        pension_signal = "순매수 (하방 지지)"
    elif pension_net_buy < -100:
        pension_signal = "순매도 (리밸런싱 가능)"
    else:
        pension_signal = "중립"

    # 자산운용사 시그널
    if asset_mgr_net_buy > 50:
        asset_mgr_signal = "순매수 (펀드 자금 유입)"
    elif asset_mgr_net_buy < -50:
        asset_mgr_signal = "순매도 (환매 압력)"
    else:
        asset_mgr_signal = "중립"

    # 증권사 시그널
    if securities_net_buy > 200:
        securities_signal = "자기매매 순매수 급증 (단기 트레이딩)"
    elif securities_net_buy < -200:
        securities_signal = "자기매매 순매도 급증 (리스크 축소)"
    else:
        securities_signal = "중립"

    # 외국인 시그널
    foreign_result = detect_foreign_turning(
        foreign_consecutive_days, usdkrw_change_pct, foreign_futures_net
    )
    foreign_signal = foreign_result["signal"]

    # 보험사 시그널
    if insurance_net_buy > 30:
        insurance_signal = "순매수 (자산배분 확대)"
    elif insurance_net_buy < -30:
        insurance_signal = "순매도 (ALM 리밸런싱)"
    else:
        insurance_signal = "중립"

    # 프로그램 매매
    program_result = detect_program_trading(program_net_buy_krw)

    # 윈도우드레싱
    wd_result = detect_window_dressing(month, day)

    # 세금 매도
    tax_result = detect_tax_selling_risk(
        market, market_cap, yearly_return_pct, month
    )

    # 요약 생성
    summary_parts: list[str] = []

    # 프로그램 매매 요약
    if program_result["signal"] != "중립":
        summary_parts.append(program_result["description"])

    # 외국인 요약
    if foreign_result["signal"] != "중립":
        summary_parts.append(foreign_result["description"])

    # 연기금 요약
    if pension_signal != "중립":
        direction = "순매수" if pension_net_buy > 0 else "순매도"
        summary_parts.append(
            f"연기금 {direction} {abs(pension_net_buy):,.0f}억원"
        )

    # 윈도우드레싱
    if wd_result["is_window_dressing"]:
        summary_parts.append(wd_result["description"])

    # 세금 매도
    if tax_result["risk_level"] in ("높음", "중간"):
        summary_parts.append(tax_result["description"])

    if not summary_parts:
        summary = "기관 수급 특이사항 없음"
    else:
        summary = " / ".join(summary_parts)

    logger.debug(
        "institutional signal: pension=%s foreign=%s program=%s",
        pension_signal,
        foreign_signal,
        program_result["signal"],
    )

    return InstitutionalSignal(
        pension_signal=pension_signal,
        asset_mgr_signal=asset_mgr_signal,
        securities_signal=securities_signal,
        foreign_signal=foreign_signal,
        insurance_signal=insurance_signal,
        program_net_buy=program_net_buy_krw,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------


def format_institutional_summary(signal: InstitutionalSignal) -> str:
    """Format institutional signal for Telegram (주호님 style).

    No ** bold markdown. Uses emojis for visual hierarchy.

    Args:
        signal: Aggregated institutional signal.

    Returns:
        Telegram-friendly formatted string.
    """
    lines: list[str] = []
    lines.append("\U0001f3e6 기관/외국인 수급 분석")
    lines.append("")

    # 외국인
    f_emoji = _signal_emoji(signal.foreign_signal)
    lines.append(f"{f_emoji} 외국인: {signal.foreign_signal}")

    # 연기금
    p_emoji = _signal_emoji(signal.pension_signal)
    lines.append(f"{p_emoji} 연기금: {signal.pension_signal}")

    # 자산운용사
    a_emoji = _signal_emoji(signal.asset_mgr_signal)
    lines.append(f"{a_emoji} 자산운용: {signal.asset_mgr_signal}")

    # 증권사
    s_emoji = _signal_emoji(signal.securities_signal)
    lines.append(f"{s_emoji} 증권사: {signal.securities_signal}")

    # 보험사
    i_emoji = _signal_emoji(signal.insurance_signal)
    lines.append(f"{i_emoji} 보험: {signal.insurance_signal}")

    # 프로그램 매매
    lines.append("")
    prog_emoji = (
        "\U0001f7e2" if signal.program_net_buy >= 500
        else "\U0001f534" if signal.program_net_buy <= -500
        else "\u26aa"
    )
    lines.append(
        f"{prog_emoji} 프로그램: "
        f"비차익 {signal.program_net_buy:+,.0f}억원"
    )

    # 종합 요약
    lines.append("")
    lines.append(f"\u2192 {signal.summary}")

    return "\n".join(lines)


def _signal_emoji(signal_text: str) -> str:
    """Map signal text to an emoji indicator."""
    text = signal_text.lower() if signal_text else ""
    if any(k in text for k in ("순매수", "긍정", "전환", "지지", "유입", "확대")):
        return "\U0001f7e2"
    if any(k in text for k in ("순매도", "부정", "위험", "압력", "축소", "리밸런싱")):
        return "\U0001f534"
    if any(k in text for k in ("주의", "임박")):
        return "\U0001f7e1"
    return "\u26aa"
