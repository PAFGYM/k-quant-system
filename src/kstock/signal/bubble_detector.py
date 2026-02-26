"""거품 판별 엔진.

PER/PEG/성장률/적정주가 기반 종합 밸류에이션 분석.
@ai_frontier의 7단계 분석 프레임워크 기반.

사용:
    from kstock.signal.bubble_detector import analyze_bubble, format_bubble_analysis
    result = analyze_bubble(ticker="000660", name="SK하이닉스", ...)
    text = format_bubble_analysis(result)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BubbleAnalysis:
    """거품 판별 결과."""
    ticker: str
    name: str
    current_price: float

    # PER 분석
    trailing_per: float          # 현재 Trailing PER
    forward_per: float           # Forward PER (예상 실적 기준)
    sector_avg_per: float        # 섹터 평균 PER
    kospi_avg_per: float         # 코스피 평균 PER (약 12~13)

    # 성장률
    revenue_yoy: float           # 매출 YoY 성장률 (%)
    op_profit_yoy: float         # 영업이익 YoY 성장률 (%)
    earnings_cagr_2y: float      # 향후 2년 이익 CAGR (%)
    growth_decelerating: bool    # 이익 성장 둔화 여부

    # PEG 비율
    peg_ratio: float             # PER / 이익성장률
    peg_zone: str                # "저평가" (<1) | "적정" (1~1.5) | "고평가" (>1.5)

    # 적정주가 3가지 기준
    fair_price_kospi: float      # 코스피 평균 PER 기준 적정주가
    fair_price_sector: float     # 섹터 평균 PER 기준 적정주가
    fair_price_peg1: float       # PEG=1 기준 적정주가
    deviation_kospi_pct: float   # 코스피 기준 괴리율
    deviation_sector_pct: float  # 섹터 기준 괴리율
    deviation_peg1_pct: float    # PEG1 기준 괴리율

    # 종합 판단
    valuation: str               # "과열" | "적정" | "저평가"
    bubble_probability: float    # 거품 확률 (0~100%)
    correction_6m_prob: float    # 6개월 내 조정 확률 (0~100%)
    summary: str                 # 한줄 요약


def calculate_peg(per: float, growth_rate: float) -> float:
    """PEG 비율 계산. growth_rate가 0 이하면 999 반환."""
    if growth_rate <= 0:
        return 999.0
    return round(per / growth_rate, 2)


def classify_peg(peg: float) -> str:
    """PEG 구간 분류."""
    if peg < 1.0:
        return "저평가"
    elif peg <= 1.5:
        return "적정"
    else:
        return "고평가"


def calculate_fair_prices(
    eps: float,
    kospi_per: float = 12.5,
    sector_per: float = 15.0,
    growth_rate: float = 10.0,
) -> dict:
    """3가지 기준 적정주가 계산."""
    return {
        "kospi": round(eps * kospi_per, 0),
        "sector": round(eps * sector_per, 0),
        "peg1": round(eps * growth_rate, 0),  # PEG=1이면 PER=성장률
    }


def analyze_bubble(
    ticker: str,
    name: str,
    current_price: float,
    trailing_per: float,
    forward_per: float,
    eps: float,
    sector_avg_per: float = 15.0,
    kospi_avg_per: float = 12.5,
    revenue_yoy: float = 0.0,
    op_profit_yoy: float = 0.0,
    earnings_cagr_2y: float = 0.0,
    prev_growth: float = 0.0,
) -> BubbleAnalysis:
    """종합 거품 판별.

    Args:
        ticker: 종목 코드
        name: 종목명
        current_price: 현재 주가
        trailing_per: Trailing PER
        forward_per: Forward PER
        eps: 주당순이익 (EPS)
        sector_avg_per: 섹터 평균 PER
        kospi_avg_per: 코스피 평균 PER
        revenue_yoy: 매출 YoY 성장률 (%)
        op_profit_yoy: 영업이익 YoY 성장률 (%)
        earnings_cagr_2y: 향후 2년 이익 CAGR (%)
        prev_growth: 이전 기간 성장률 (둔화 판단용)

    Returns:
        BubbleAnalysis 결과 객체
    """
    # 1. 성장 둔화 판단
    growth_decelerating = (
        earnings_cagr_2y > 0
        and prev_growth > 0
        and earnings_cagr_2y < prev_growth * 0.7  # 성장률 30% 이상 둔화
    )

    # 2. PEG 계산
    growth_for_peg = max(earnings_cagr_2y, 1.0)
    peg = calculate_peg(forward_per, growth_for_peg)
    peg_zone = classify_peg(peg)

    # 3. 적정주가 3가지
    fair = calculate_fair_prices(eps, kospi_avg_per, sector_avg_per, growth_for_peg)

    dev_kospi = (
        (current_price - fair["kospi"]) / fair["kospi"] * 100
        if fair["kospi"] > 0 else 0
    )
    dev_sector = (
        (current_price - fair["sector"]) / fair["sector"] * 100
        if fair["sector"] > 0 else 0
    )
    dev_peg1 = (
        (current_price - fair["peg1"]) / fair["peg1"] * 100
        if fair["peg1"] > 0 else 0
    )

    # 4. 종합 판단 (100점 스코어링)
    bubble_score = 0

    # PEG 기반 (40%)
    if peg > 2.0:
        bubble_score += 40
    elif peg > 1.5:
        bubble_score += 25
    elif peg > 1.0:
        bubble_score += 10

    # 섹터 PER 대비 (25%)
    if forward_per > sector_avg_per * 1.5:
        bubble_score += 25
    elif forward_per > sector_avg_per * 1.2:
        bubble_score += 15
    elif forward_per > sector_avg_per:
        bubble_score += 5

    # 성장 둔화 (20%)
    if growth_decelerating:
        bubble_score += 20
    elif earnings_cagr_2y < 5:
        bubble_score += 10

    # 적정주가 괴리 (15%)
    avg_deviation = (dev_kospi + dev_sector + dev_peg1) / 3
    if avg_deviation > 50:
        bubble_score += 15
    elif avg_deviation > 30:
        bubble_score += 10
    elif avg_deviation > 15:
        bubble_score += 5

    # 밸류에이션 등급
    if bubble_score >= 60:
        valuation = "과열"
    elif bubble_score >= 30:
        valuation = "적정"
    else:
        valuation = "저평가"

    # 6개월 조정 확률
    correction_prob = min(bubble_score * 1.2, 95)

    summary = (
        f"{name}: {valuation} (거품 {bubble_score}%) | "
        f"PEG {peg:.1f} ({peg_zone}) | "
        f"적정가 {fair['sector']:,.0f}원 (괴리 {dev_sector:+.1f}%)"
    )

    return BubbleAnalysis(
        ticker=ticker,
        name=name,
        current_price=current_price,
        trailing_per=trailing_per,
        forward_per=forward_per,
        sector_avg_per=sector_avg_per,
        kospi_avg_per=kospi_avg_per,
        revenue_yoy=revenue_yoy,
        op_profit_yoy=op_profit_yoy,
        earnings_cagr_2y=earnings_cagr_2y,
        growth_decelerating=growth_decelerating,
        peg_ratio=peg,
        peg_zone=peg_zone,
        fair_price_kospi=fair["kospi"],
        fair_price_sector=fair["sector"],
        fair_price_peg1=fair["peg1"],
        deviation_kospi_pct=round(dev_kospi, 1),
        deviation_sector_pct=round(dev_sector, 1),
        deviation_peg1_pct=round(dev_peg1, 1),
        valuation=valuation,
        bubble_probability=bubble_score,
        correction_6m_prob=round(correction_prob, 1),
        summary=summary,
    )


def format_bubble_analysis(b: BubbleAnalysis) -> str:
    """거품 판별 결과 텔레그램 표시."""
    icon = "🔴" if b.valuation == "과열" else "🟢" if b.valuation == "저평가" else "🟡"

    return (
        f"{icon} {b.name} 밸류에이션 분석\n"
        f"{'━' * 22}\n\n"
        f"현재가: {b.current_price:,.0f}원\n"
        f"Trailing PER: {b.trailing_per:.1f} | Forward PER: {b.forward_per:.1f}\n"
        f"섹터 평균 PER: {b.sector_avg_per:.1f}\n\n"
        f"📈 성장률\n"
        f"  매출 YoY: {b.revenue_yoy:+.1f}%\n"
        f"  영업이익 YoY: {b.op_profit_yoy:+.1f}%\n"
        f"  2년 CAGR: {b.earnings_cagr_2y:.1f}%\n"
        f"  {'⚠️ 성장 둔화 감지' if b.growth_decelerating else '✅ 성장 지속'}\n\n"
        f"📊 PEG: {b.peg_ratio:.2f} → {b.peg_zone}\n\n"
        f"💰 적정주가 (3가지 기준)\n"
        f"  코스피 PER 기준: {b.fair_price_kospi:,.0f}원 ({b.deviation_kospi_pct:+.1f}%)\n"
        f"  섹터 PER 기준: {b.fair_price_sector:,.0f}원 ({b.deviation_sector_pct:+.1f}%)\n"
        f"  PEG=1 기준: {b.fair_price_peg1:,.0f}원 ({b.deviation_peg1_pct:+.1f}%)\n\n"
        f"{'━' * 22}\n"
        f"{icon} 판정: {b.valuation}\n"
        f"🎯 거품 확률: {b.bubble_probability:.0f}%\n"
        f"📉 6개월 조정 확률: {b.correction_6m_prob:.0f}%\n"
    )


async def get_bubble_data_from_yfinance(ticker: str, yf_client=None) -> dict:
    """yfinance에서 거품 판별에 필요한 데이터 수집.

    Returns:
        dict with keys: trailing_per, forward_per, eps, sector_avg_per,
        revenue_yoy, op_profit_yoy, earnings_cagr_2y, current_price
    """
    result = {
        "trailing_per": 0.0,
        "forward_per": 0.0,
        "eps": 0.0,
        "sector_avg_per": 15.0,  # 기본값
        "kospi_avg_per": 12.5,
        "revenue_yoy": 0.0,
        "op_profit_yoy": 0.0,
        "earnings_cagr_2y": 0.0,
        "current_price": 0.0,
    }

    try:
        import yfinance as yf

        # 한국 주식은 .KS 접미사
        yf_ticker = ticker if "." in ticker else f"{ticker}.KS"
        stock = yf.Ticker(yf_ticker)
        info = stock.info or {}

        result["trailing_per"] = info.get("trailingPE", 0) or 0
        result["forward_per"] = info.get("forwardPE", 0) or 0
        result["eps"] = info.get("trailingEps", 0) or 0
        result["current_price"] = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0

        # 성장률
        result["revenue_yoy"] = (info.get("revenueGrowth", 0) or 0) * 100
        result["earnings_cagr_2y"] = (info.get("earningsGrowth", 0) or 0) * 100

        # 영업이익 YoY (없으면 earnings growth로 대체)
        result["op_profit_yoy"] = result["earnings_cagr_2y"]

        logger.debug("Bubble data for %s: PER=%.1f, EPS=%.0f", ticker, result["trailing_per"], result["eps"])

    except Exception as e:
        logger.debug("Bubble data fetch error %s: %s", ticker, e)

    return result
