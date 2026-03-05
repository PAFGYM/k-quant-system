"""v9.0: 한국형 리스크 팩터 통합 분석.

신용잔고, ETF 레버리지, 개인 과열, 만기일, 프로그램 매매 등
한국 시장 특수 리스크 요인을 종합하여 시장 위험도 판단.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class KoreaRiskAssessment:
    """한국 시장 리스크 종합 평가."""

    total_risk: int = 0  # 0-100 (높을수록 위험)
    risk_level: str = "안전"  # 안전/주의/위험/극위험
    factors: list[dict] = field(default_factory=list)
    score_adjustment: int = 0  # 포트폴리오 스코어 조정
    action_guide: str = ""


def assess_korea_risk(
    credit_data: list[dict] | None = None,
    etf_data: list[dict] | None = None,
    program_data: list[dict] | None = None,
    vix: float = 0,
    usdkrw: float = 0,
    usdkrw_change_pct: float = 0,
    days_to_expiry: int = 999,
    month: int = 0,
    day: int = 0,
) -> KoreaRiskAssessment:
    """한국 시장 종합 리스크 평가.

    Args:
        credit_data: 신용잔고 데이터 (DB).
        etf_data: ETF 흐름 데이터 (DB).
        program_data: 프로그램 매매 데이터 (DB).
        vix: 현재 VIX.
        usdkrw: 현재 환율.
        usdkrw_change_pct: 환율 변동률 (%).
        days_to_expiry: 선물 만기일까지 남은 일수.
        month: 현재 월.
        day: 현재 일.

    Returns:
        KoreaRiskAssessment with 0-100 total risk score.
    """
    result = KoreaRiskAssessment()
    risk = 0

    # 1. 신용잔고 위험 (max 20)
    if credit_data:
        c = credit_data[0]
        credit_tril = c.get("credit", 0) / 10000
        credit_chg = c.get("credit_change", 0)

        if credit_tril >= 20:
            risk += 20
            result.factors.append({
                "name": "신용잔고 과열",
                "score": 20,
                "detail": f"신용잔고 {credit_tril:.1f}조 (20조+ 극위험)",
            })
        elif credit_tril >= 18:
            risk += 12
            result.factors.append({
                "name": "신용잔고 주의",
                "score": 12,
                "detail": f"신용잔고 {credit_tril:.1f}조 (18조+ 위험)",
            })
        elif credit_chg > 2000:
            risk += 5
            result.factors.append({
                "name": "신용 일일 급증",
                "score": 5,
                "detail": f"신용잔고 일일 {credit_chg:+,.0f}억 증가",
            })

    # 2. ETF 레버리지 과열 (max 15)
    if etf_data:
        lev_cap = sum(d.get("market_cap", 0) for d in etf_data if d.get("etf_type") == "leverage")
        inv_cap = sum(d.get("market_cap", 0) for d in etf_data if d.get("etf_type") == "inverse")

        lev_tril = lev_cap / 10000
        if lev_tril >= 6:
            risk += 15
            result.factors.append({
                "name": "레버리지 ETF 과열",
                "score": 15,
                "detail": f"레버리지 ETF {lev_tril:.1f}조 (개인 탐욕 극단)",
            })
        elif lev_tril >= 4:
            risk += 8
            result.factors.append({
                "name": "레버리지 ETF 주의",
                "score": 8,
                "detail": f"레버리지 ETF {lev_tril:.1f}조",
            })

        # 인버스 급증 = 공포 (역발상 기회)
        inv_tril = inv_cap / 10000
        if inv_tril >= 3:
            result.factors.append({
                "name": "인버스 ETF 급증 (역발상)",
                "score": 0,
                "detail": f"인버스 ETF {inv_tril:.1f}조 → 극공포, 역발상 매수 기회",
            })

    # 3. 프로그램 매매 (max 10)
    if program_data:
        p = program_data[0]
        total_net = p.get("total_net", 0)
        if total_net < -5000:
            risk += 10
            result.factors.append({
                "name": "프로그램 대규모 매도",
                "score": 10,
                "detail": f"프로그램 순매도 {total_net:,.0f}억",
            })
        elif total_net < -3000:
            risk += 5
            result.factors.append({
                "name": "프로그램 매도 압력",
                "score": 5,
                "detail": f"프로그램 순매도 {total_net:,.0f}억",
            })

    # 4. 환율 위험 (max 10)
    if usdkrw >= 1400:
        risk += 10
        result.factors.append({
            "name": "환율 고위험",
            "score": 10,
            "detail": f"USD/KRW {usdkrw:,.0f}원 (1,400원+ 자본유출 위험)",
        })
    elif usdkrw >= 1350:
        risk += 5
        result.factors.append({
            "name": "환율 주의",
            "score": 5,
            "detail": f"USD/KRW {usdkrw:,.0f}원",
        })

    if usdkrw_change_pct >= 1.0:
        risk += 5
        result.factors.append({
            "name": "환율 급등",
            "score": 5,
            "detail": f"환율 {usdkrw_change_pct:+.1f}% 급등",
        })

    # 5. VIX 위험 (max 15)
    if vix >= 30:
        risk += 15
        result.factors.append({
            "name": "VIX 패닉",
            "score": 15,
            "detail": f"VIX {vix:.1f} (30+ 패닉)",
        })
    elif vix >= 25:
        risk += 8
        result.factors.append({
            "name": "VIX 공포",
            "score": 8,
            "detail": f"VIX {vix:.1f} (25+ 공포)",
        })

    # 6. 만기일 효과 (max 10)
    if days_to_expiry <= 1:
        risk += 10
        result.factors.append({
            "name": "만기일 당일",
            "score": 10,
            "detail": "선물옵션 만기일! 변동성 극대화",
        })
    elif days_to_expiry <= 3:
        risk += 5
        result.factors.append({
            "name": "만기일 접근",
            "score": 5,
            "detail": f"선물만기 D-{days_to_expiry}",
        })

    # 7. 계절 리스크 (max 10)
    if month == 12 and day >= 15:
        risk += 5
        result.factors.append({
            "name": "대주주 양도세 매도",
            "score": 5,
            "detail": "12월 중후반 대주주 양도세 회피 매도 시즌",
        })
    elif month == 12 and day < 15:
        risk += 3
        result.factors.append({
            "name": "연말 양도세 시즌",
            "score": 3,
            "detail": "12월 대주주 양도세 매도 압력",
        })

    # 윈도우드레싱 (분기말)
    if month in (3, 6, 9, 12) and day >= 20:
        result.factors.append({
            "name": "윈도우드레싱 기간",
            "score": 0,
            "detail": f"{month}월 말 기관 결산 수익률 관리 → 대형 우량주 일시적 매수세",
        })

    # 종합 판정
    result.total_risk = min(100, risk)

    if result.total_risk >= 60:
        result.risk_level = "극위험"
        result.score_adjustment = -15
        result.action_guide = "신규 매수 중단, 포지션 50% 축소, 손절 기준 -5% 강화"
    elif result.total_risk >= 40:
        result.risk_level = "위험"
        result.score_adjustment = -10
        result.action_guide = "포지션 -20%, 분할 매수만, 레버리지 종목 회피"
    elif result.total_risk >= 20:
        result.risk_level = "주의"
        result.score_adjustment = -5
        result.action_guide = "포지션 사이징 보수적, 급등주 추격 자제"
    else:
        result.risk_level = "안전"
        result.score_adjustment = 0
        result.action_guide = "정상 운영"

    return result


def format_korea_risk(assessment: KoreaRiskAssessment) -> str:
    """텔레그램 표시용 한국 리스크 포맷."""
    emoji = {
        "안전": "🟢",
        "주의": "🟡",
        "위험": "🟠",
        "극위험": "🔴",
    }.get(assessment.risk_level, "⚪")

    lines = [
        f"{emoji} 한국 시장 리스크: {assessment.total_risk}점 [{assessment.risk_level}]",
    ]

    if assessment.action_guide and assessment.risk_level != "안전":
        lines.append(f"  📋 {assessment.action_guide}")

    for f in assessment.factors[:5]:
        if f["score"] > 0:
            lines.append(f"  • {f['name']}: {f['detail']}")

    return "\n".join(lines)
