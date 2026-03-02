"""ETF 분석 모듈.

추적오차·구성·비용·스크리닝·종합분석 기능 제공.

사용:
    from kstock.signal.etf_analyzer import analyze_etf, screen_etfs
    report = analyze_etf("069500", ohlcv_map)
    text = format_etf_analysis(report)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ETF_UNIVERSE: dict[str, dict] = {
    # 레버리지
    "122630": {
        "name": "KODEX 레버리지",
        "benchmark": "069500",
        "category": "leverage",
        "expense": 0.64,
    },
    "252670": {
        "name": "KODEX 200선물인버스2X",
        "benchmark": "069500",
        "category": "inverse",
        "expense": 0.64,
    },
    # 지수
    "069500": {
        "name": "KODEX 200",
        "benchmark": "^KS11",
        "category": "index",
        "expense": 0.15,
    },
    "102110": {
        "name": "TIGER 200",
        "benchmark": "^KS11",
        "category": "index",
        "expense": 0.05,
    },
    "229200": {
        "name": "KODEX 코스닥150",
        "benchmark": "^KQ11",
        "category": "index",
        "expense": 0.25,
    },
    # 섹터
    "091160": {
        "name": "KODEX 반도체",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "305540": {
        "name": "KODEX 2차전지산업",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "091170": {
        "name": "KODEX 은행",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "266370": {
        "name": "KODEX IT",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "117460": {
        "name": "KODEX 에너지화학",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "140700": {
        "name": "KODEX 건설",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "091180": {
        "name": "KODEX 자동차",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    "117680": {
        "name": "KODEX 철강",
        "benchmark": None,
        "category": "sector",
        "expense": 0.45,
    },
    # 배당
    "211560": {
        "name": "TIGER 배당성장",
        "benchmark": "069500",
        "category": "dividend",
        "expense": 0.24,
    },
    "104530": {
        "name": "KODEX 고배당",
        "benchmark": "069500",
        "category": "dividend",
        "expense": 0.30,
    },
    # 글로벌
    "360750": {
        "name": "TIGER 미국S&P500",
        "benchmark": None,
        "category": "global",
        "expense": 0.07,
    },
    "133690": {
        "name": "TIGER 미국나스닥100",
        "benchmark": None,
        "category": "global",
        "expense": 0.07,
    },
    "195930": {
        "name": "TIGER 유로스탁스50",
        "benchmark": None,
        "category": "global",
        "expense": 0.24,
    },
    "192090": {
        "name": "TIGER 차이나CSI300",
        "benchmark": None,
        "category": "global",
        "expense": 0.25,
    },
    # 채권
    "148070": {
        "name": "KOSEF 국고채10년",
        "benchmark": None,
        "category": "bond",
        "expense": 0.15,
    },
    "114260": {
        "name": "KODEX 국고채3년",
        "benchmark": None,
        "category": "bond",
        "expense": 0.07,
    },
    # 원자재
    "132030": {
        "name": "KODEX 골드선물(H)",
        "benchmark": None,
        "category": "commodity",
        "expense": 0.68,
    },
    "130680": {
        "name": "TIGER 원유선물Enhanced(H)",
        "benchmark": None,
        "category": "commodity",
        "expense": 0.70,
    },
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TrackingError:
    """추적오차 분석 결과."""

    etf_code: str
    benchmark_code: str
    tracking_error_annual_pct: float
    tracking_difference_pct: float
    correlation: float
    beta: float
    r_squared: float
    quality_grade: str  # A / B / C / D


@dataclass
class ETFComposition:
    """ETF 구성 분석 결과."""

    etf_code: str
    name: str
    category: str
    top_holdings: list[dict] = field(default_factory=list)
    sector_weights: dict = field(default_factory=dict)
    concentration_hhi: float = 0.0  # 0~1
    effective_n: float = 0.0  # 1/HHI


@dataclass
class ETFCostComparison:
    """ETF 비용 비교 결과."""

    etf_code: str
    name: str
    expense_ratio_pct: float
    bid_ask_spread_pct: float
    tracking_error_pct: float
    total_cost_pct: float
    cost_grade: str  # A / B / C / D


@dataclass
class ETFScreenResult:
    """ETF 스크리닝 결과."""

    etf_code: str
    name: str
    category: str
    return_1m_pct: float
    return_3m_pct: float
    return_6m_pct: float
    volatility_pct: float
    sharpe_ratio: float
    tracking_error_pct: float
    expense_ratio_pct: float
    score: float  # 0~100


@dataclass
class ETFAnalysisReport:
    """ETF 종합 분석 리포트."""

    etf_code: str
    name: str
    tracking: TrackingError | None = None
    composition: ETFComposition | None = None
    cost: ETFCostComparison | None = None
    peer_comparison: list[ETFScreenResult] = field(default_factory=list)
    recommendation: str = "Hold"  # Buy / Hold / Avoid
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _daily_returns(ohlcv: pd.DataFrame) -> pd.Series:
    """Close 컬럼에서 일일 수익률 계산."""
    close = ohlcv["Close"] if "Close" in ohlcv.columns else ohlcv["close"]
    return close.pct_change().dropna()


def _cumulative_return(ohlcv: pd.DataFrame) -> float:
    """누적 수익률 계산."""
    close = ohlcv["Close"] if "Close" in ohlcv.columns else ohlcv["close"]
    if len(close) < 2:
        return 0.0
    return (close.iloc[-1] / close.iloc[0]) - 1.0


def _te_quality_grade(te: float) -> str:
    """추적오차 기반 등급 산정."""
    if te < 0.5:
        return "A"
    if te < 1.0:
        return "B"
    if te < 2.0:
        return "C"
    return "D"


def _cost_grade(total_cost: float) -> str:
    """총 비용 기반 등급 산정."""
    if total_cost < 0.3:
        return "A"
    if total_cost < 0.6:
        return "B"
    if total_cost < 1.0:
        return "C"
    return "D"


def _normalize_0_100(values: list[float]) -> list[float]:
    """리스트를 0~100 범위로 정규화."""
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mx == mn:
        return [50.0] * len(values)
    return [(v - mn) / (mx - mn) * 100.0 for v in values]


def _safe_sharpe(returns: pd.Series, rf_annual: float = 0.035) -> float:
    """연환산 Sharpe ratio (안전 버전)."""
    if len(returns) < 20:
        return 0.0
    ann_ret = returns.mean() * 252
    ann_vol = returns.std() * math.sqrt(252)
    if ann_vol < 1e-10:
        return 0.0
    return (ann_ret - rf_annual) / ann_vol


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------


def compute_tracking_error(
    etf_ohlcv: pd.DataFrame,
    bench_ohlcv: pd.DataFrame,
    etf_code: str,
    bench_code: str,
    lookback: int = 252,
) -> TrackingError:
    """추적오차 계산.

    Parameters
    ----------
    etf_ohlcv : 종가 포함 OHLCV DataFrame
    bench_ohlcv : 벤치마크 OHLCV DataFrame
    etf_code : ETF 종목코드
    bench_code : 벤치마크 종목코드
    lookback : 분석 기간 (거래일 수, 기본 252)

    Returns
    -------
    TrackingError 데이터클래스
    """
    etf_ret = _daily_returns(etf_ohlcv).tail(lookback)
    bench_ret = _daily_returns(bench_ohlcv).tail(lookback)

    # 날짜 인덱스 정렬
    aligned = pd.DataFrame({"etf": etf_ret, "bench": bench_ret}).dropna()

    if len(aligned) < 20:
        logger.warning(
            "추적오차 계산 데이터 부족: %s vs %s (%d rows)",
            etf_code, bench_code, len(aligned),
        )
        return TrackingError(
            etf_code=etf_code,
            benchmark_code=bench_code,
            tracking_error_annual_pct=0.0,
            tracking_difference_pct=0.0,
            correlation=0.0,
            beta=0.0,
            r_squared=0.0,
            quality_grade="D",
        )

    diff = aligned["etf"] - aligned["bench"]

    # 연환산 추적오차
    te_annual = float(diff.std() * math.sqrt(252)) * 100  # percent

    # 추적차이 (누적 수익률 갭)
    cum_etf = float((1 + aligned["etf"]).prod() - 1)
    cum_bench = float((1 + aligned["bench"]).prod() - 1)
    td = (cum_etf - cum_bench) * 100  # percent

    # 상관계수, 베타, R²
    corr = float(aligned["etf"].corr(aligned["bench"]))
    bench_var = float(aligned["bench"].var())
    if bench_var > 1e-15:
        beta = float(aligned["etf"].cov(aligned["bench"]) / bench_var)
    else:
        beta = 1.0
    r_sq = corr ** 2

    grade = _te_quality_grade(te_annual)

    return TrackingError(
        etf_code=etf_code,
        benchmark_code=bench_code,
        tracking_error_annual_pct=round(te_annual, 4),
        tracking_difference_pct=round(td, 4),
        correlation=round(corr, 4),
        beta=round(beta, 4),
        r_squared=round(r_sq, 4),
        quality_grade=grade,
    )


def analyze_composition(
    etf_code: str,
    holdings_data: list[dict] | None = None,
) -> ETFComposition:
    """ETF 구성 분석.

    Parameters
    ----------
    etf_code : ETF 종목코드
    holdings_data : 보유종목 리스트 [{"name": ..., "weight": 0~1, "sector": ...}, ...]
                    없으면 ETF_UNIVERSE 기본 정보만 사용

    Returns
    -------
    ETFComposition 데이터클래스
    """
    info = ETF_UNIVERSE.get(etf_code, {})
    name = info.get("name", etf_code)
    category = info.get("category", "unknown")

    if not holdings_data:
        return ETFComposition(
            etf_code=etf_code,
            name=name,
            category=category,
            top_holdings=[],
            sector_weights={},
            concentration_hhi=0.0,
            effective_n=0.0,
        )

    # 상위 보유 종목 (weight 기준 내림차순)
    sorted_holdings = sorted(
        holdings_data, key=lambda h: h.get("weight", 0), reverse=True,
    )
    top = sorted_holdings[:10]

    # 섹터 비중 합산
    sector_w: dict[str, float] = {}
    for h in holdings_data:
        sec = h.get("sector", "기타")
        sector_w[sec] = sector_w.get(sec, 0.0) + h.get("weight", 0.0)

    # HHI (Herfindahl-Hirschman Index)
    weights = [h.get("weight", 0.0) for h in holdings_data]
    total_w = sum(weights)
    if total_w > 0:
        fractions = [w / total_w for w in weights]
    else:
        fractions = []

    hhi = sum(f ** 2 for f in fractions)
    eff_n = 1.0 / hhi if hhi > 1e-10 else 0.0

    return ETFComposition(
        etf_code=etf_code,
        name=name,
        category=category,
        top_holdings=top,
        sector_weights=sector_w,
        concentration_hhi=round(hhi, 6),
        effective_n=round(eff_n, 2),
    )


def compare_costs(etf_codes: list[str]) -> list[ETFCostComparison]:
    """ETF 비용 비교.

    total_cost = expense + tracking_error_estimate + spread_estimate

    Parameters
    ----------
    etf_codes : 비교할 ETF 코드 리스트

    Returns
    -------
    총 비용 오름차순 정렬된 ETFCostComparison 리스트
    """
    results: list[ETFCostComparison] = []

    for code in etf_codes:
        info = ETF_UNIVERSE.get(code)
        if not info:
            logger.warning("ETF_UNIVERSE에 없는 코드: %s", code)
            continue

        expense = info["expense"]

        # 카테고리별 추적오차/스프레드 추정치
        cat = info["category"]
        if cat in ("leverage", "inverse"):
            te_est = 1.5
            spread_est = 0.10
        elif cat == "index":
            te_est = 0.3
            spread_est = 0.02
        elif cat == "sector":
            te_est = 0.8
            spread_est = 0.05
        elif cat in ("dividend",):
            te_est = 0.6
            spread_est = 0.04
        elif cat == "global":
            te_est = 0.5
            spread_est = 0.03
        elif cat == "bond":
            te_est = 0.2
            spread_est = 0.02
        elif cat == "commodity":
            te_est = 1.0
            spread_est = 0.08
        else:
            te_est = 0.5
            spread_est = 0.05

        total = expense + te_est + spread_est
        grade = _cost_grade(total)

        results.append(ETFCostComparison(
            etf_code=code,
            name=info["name"],
            expense_ratio_pct=expense,
            bid_ask_spread_pct=spread_est,
            tracking_error_pct=te_est,
            total_cost_pct=round(total, 4),
            cost_grade=grade,
        ))

    results.sort(key=lambda c: c.total_cost_pct)
    return results


def screen_etfs(
    ohlcv_map: dict[str, pd.DataFrame],
    category_filter: str | None = None,
) -> list[ETFScreenResult]:
    """ETF 스크리닝 (수익·변동성·비용 종합 점수).

    Score = Sharpe(40%) + return_3m_norm(20%) + inv_vol_norm(20%) + inv_cost_norm(20%)
    모든 항목 0~100으로 정규화 후 가중합산.

    Parameters
    ----------
    ohlcv_map : { ETF코드: OHLCV DataFrame } 매핑
    category_filter : 특정 카테고리만 필터링 (None이면 전체)

    Returns
    -------
    score 내림차순 정렬된 ETFScreenResult 리스트
    """
    candidates: list[dict] = []

    for code, info in ETF_UNIVERSE.items():
        if category_filter and info["category"] != category_filter:
            continue

        df = ohlcv_map.get(code)
        if df is None or len(df) < 22:
            continue

        close_col = "Close" if "Close" in df.columns else "close"
        close = df[close_col]

        # 수익률 계산
        n = len(close)
        ret_1m = (close.iloc[-1] / close.iloc[max(-22, -n)] - 1) * 100 if n >= 22 else 0.0
        ret_3m = (close.iloc[-1] / close.iloc[max(-66, -n)] - 1) * 100 if n >= 22 else 0.0
        ret_6m = (close.iloc[-1] / close.iloc[max(-132, -n)] - 1) * 100 if n >= 22 else 0.0

        rets = _daily_returns(df)
        vol = float(rets.std() * math.sqrt(252) * 100) if len(rets) > 1 else 0.0
        sharpe = _safe_sharpe(rets)

        te_pct = 0.0
        bench_code = info.get("benchmark")
        if bench_code and bench_code in ohlcv_map:
            bench_df = ohlcv_map[bench_code]
            te_result = compute_tracking_error(df, bench_df, code, bench_code)
            te_pct = te_result.tracking_error_annual_pct

        candidates.append({
            "code": code,
            "name": info["name"],
            "category": info["category"],
            "ret_1m": ret_1m,
            "ret_3m": ret_3m,
            "ret_6m": ret_6m,
            "vol": vol,
            "sharpe": sharpe,
            "te": te_pct,
            "expense": info["expense"],
        })

    if not candidates:
        return []

    # 정규화 → 스코어
    sharpes = [c["sharpe"] for c in candidates]
    ret3ms = [c["ret_3m"] for c in candidates]
    vols = [c["vol"] for c in candidates]
    expenses = [c["expense"] for c in candidates]

    norm_sharpe = _normalize_0_100(sharpes)
    norm_ret3m = _normalize_0_100(ret3ms)

    # 변동성·비용은 낮을수록 좋으므로 역정규화
    norm_inv_vol = _normalize_0_100([-v for v in vols])
    norm_inv_cost = _normalize_0_100([-e for e in expenses])

    results: list[ETFScreenResult] = []

    for i, c in enumerate(candidates):
        score = (
            norm_sharpe[i] * 0.40
            + norm_ret3m[i] * 0.20
            + norm_inv_vol[i] * 0.20
            + norm_inv_cost[i] * 0.20
        )
        score = max(0.0, min(100.0, score))

        results.append(ETFScreenResult(
            etf_code=c["code"],
            name=c["name"],
            category=c["category"],
            return_1m_pct=round(c["ret_1m"], 2),
            return_3m_pct=round(c["ret_3m"], 2),
            return_6m_pct=round(c["ret_6m"], 2),
            volatility_pct=round(c["vol"], 2),
            sharpe_ratio=round(c["sharpe"], 4),
            tracking_error_pct=round(c["te"], 4),
            expense_ratio_pct=c["expense"],
            score=round(score, 2),
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def analyze_etf(
    etf_code: str,
    ohlcv_map: dict[str, pd.DataFrame],
    holdings_data: list[dict] | None = None,
) -> ETFAnalysisReport:
    """ETF 종합 분석.

    추적오차 + 구성 + 비용 + 동일 카테고리 피어 비교.

    Parameters
    ----------
    etf_code : ETF 종목코드
    ohlcv_map : { 종목코드: OHLCV DataFrame } 매핑
    holdings_data : 보유종목 리스트 (선택)

    Returns
    -------
    ETFAnalysisReport 데이터클래스
    """
    info = ETF_UNIVERSE.get(etf_code)
    if not info:
        logger.warning("ETF_UNIVERSE에 없는 코드: %s", etf_code)
        return ETFAnalysisReport(
            etf_code=etf_code,
            name=etf_code,
            recommendation="Avoid",
            reasons=["ETF_UNIVERSE에 등록되지 않은 종목"],
        )

    name = info["name"]
    category = info["category"]
    reasons: list[str] = []

    # 1. 추적오차
    tracking: TrackingError | None = None
    bench_code = info.get("benchmark")
    etf_df = ohlcv_map.get(etf_code)

    if bench_code and etf_df is not None and bench_code in ohlcv_map:
        bench_df = ohlcv_map[bench_code]
        tracking = compute_tracking_error(etf_df, bench_df, etf_code, bench_code)

    # 2. 구성 분석
    composition = analyze_composition(etf_code, holdings_data)

    # 3. 비용 비교 (동일 카테고리 ETF)
    peer_codes = [
        c for c, inf in ETF_UNIVERSE.items()
        if inf["category"] == category
    ]
    costs = compare_costs(peer_codes)
    my_cost: ETFCostComparison | None = None
    for c in costs:
        if c.etf_code == etf_code:
            my_cost = c
            break

    # 4. 피어 스크리닝
    peers = screen_etfs(ohlcv_map, category_filter=category)

    # 5. 추천 로직
    recommendation = "Hold"

    # Sharpe 기반
    if etf_df is not None and len(etf_df) >= 22:
        rets = _daily_returns(etf_df)
        sharpe = _safe_sharpe(rets)
        if sharpe > 0.5:
            reasons.append(f"Sharpe {sharpe:.2f} (양호)")
            recommendation = "Buy"
        elif sharpe < -0.3:
            reasons.append(f"Sharpe {sharpe:.2f} (부진)")
            recommendation = "Avoid"
        else:
            reasons.append(f"Sharpe {sharpe:.2f} (보통)")

    # 추적오차 기반
    if tracking:
        if tracking.quality_grade == "A":
            reasons.append(f"추적오차 {tracking.tracking_error_annual_pct:.2f}% (우수)")
        elif tracking.quality_grade in ("C", "D"):
            reasons.append(
                f"추적오차 {tracking.tracking_error_annual_pct:.2f}% (불량)"
            )
            if recommendation == "Buy":
                recommendation = "Hold"

    # 비용 기반
    if my_cost:
        if my_cost.cost_grade == "D":
            reasons.append(f"총비용 {my_cost.total_cost_pct:.2f}% (고비용)")
            if recommendation == "Buy":
                recommendation = "Hold"
        elif my_cost.cost_grade == "A":
            reasons.append(f"총비용 {my_cost.total_cost_pct:.2f}% (저비용)")

    # 카테고리 특성
    if category in ("leverage", "inverse"):
        reasons.append("레버리지/인버스: 단기 전용, 장기 보유 부적합")
        if recommendation == "Buy":
            recommendation = "Hold"

    if not reasons:
        reasons.append("분석 데이터 부족")

    return ETFAnalysisReport(
        etf_code=etf_code,
        name=name,
        tracking=tracking,
        composition=composition,
        cost=my_cost,
        peer_comparison=peers,
        recommendation=recommendation,
        reasons=reasons,
    )


def get_etf_info(etf_code: str) -> dict | None:
    """ETF_UNIVERSE에서 ETF 정보 조회.

    Parameters
    ----------
    etf_code : ETF 종목코드

    Returns
    -------
    dict 또는 None (미등록 종목)
    """
    return ETF_UNIVERSE.get(etf_code)


# ---------------------------------------------------------------------------
# Formatters (텔레그램 plain text + emoji)
# ---------------------------------------------------------------------------


def format_etf_analysis(report: ETFAnalysisReport) -> str:
    """ETF 종합 분석 리포트 텔레그램 포맷.

    plain text + emoji, parse_mode 없음.
    """
    rec_emoji = {"Buy": "🟢", "Hold": "🟡", "Avoid": "🔴"}.get(
        report.recommendation, "⚪"
    )

    lines = [
        f"📊 ETF 분석: {report.name} ({report.etf_code})",
        f"{rec_emoji} 추천: {report.recommendation}",
        "",
    ]

    # 사유
    if report.reasons:
        lines.append("📋 분석 요약")
        for r in report.reasons:
            lines.append(f"  • {r}")
        lines.append("")

    # 추적오차
    if report.tracking:
        t = report.tracking
        lines.append("🎯 추적오차")
        lines.append(f"  TE: {t.tracking_error_annual_pct:.2f}% (등급 {t.quality_grade})")
        lines.append(f"  TD: {t.tracking_difference_pct:+.2f}%")
        lines.append(f"  상관: {t.correlation:.4f} / Beta: {t.beta:.4f}")
        lines.append(f"  R²: {t.r_squared:.4f}")
        lines.append("")

    # 구성
    if report.composition and report.composition.top_holdings:
        comp = report.composition
        lines.append("🧩 구성")
        lines.append(f"  HHI: {comp.concentration_hhi:.4f}")
        lines.append(f"  유효 종목수: {comp.effective_n:.1f}")
        for h in comp.top_holdings[:5]:
            w_pct = h.get("weight", 0) * 100
            lines.append(f"  • {h.get('name', '?')} {w_pct:.1f}%")
        lines.append("")

    # 비용
    if report.cost:
        c = report.cost
        lines.append("💰 비용")
        lines.append(f"  보수: {c.expense_ratio_pct:.2f}%")
        lines.append(f"  총비용: {c.total_cost_pct:.2f}% (등급 {c.cost_grade})")
        lines.append("")

    # 피어 비교 (상위 5개)
    if report.peer_comparison:
        lines.append("📈 동일 카테고리 순위")
        for i, p in enumerate(report.peer_comparison[:5], 1):
            marker = " ◀" if p.etf_code == report.etf_code else ""
            lines.append(
                f"  {i}. {p.name} "
                f"점수 {p.score:.0f} "
                f"Sharpe {p.sharpe_ratio:.2f}{marker}"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


def format_etf_screen(
    results: list[ETFScreenResult],
    top_n: int = 10,
) -> str:
    """ETF 스크리닝 결과 텔레그램 포맷.

    plain text + emoji, parse_mode 없음.
    """
    if not results:
        return "📊 ETF 스크리닝 결과가 없습니다."

    lines = [
        f"📊 ETF 스크리닝 TOP {min(top_n, len(results))}",
        "",
    ]

    for i, r in enumerate(results[:top_n], 1):
        cat_emoji = {
            "leverage": "⚡",
            "inverse": "📉",
            "index": "📊",
            "sector": "🏭",
            "dividend": "💰",
            "global": "🌍",
            "bond": "🏛",
            "commodity": "🥇",
        }.get(r.category, "📋")

        lines.append(
            f"{i}. {cat_emoji} {r.name} ({r.etf_code})"
        )
        lines.append(
            f"   점수 {r.score:.0f} | "
            f"1M {r.return_1m_pct:+.1f}% | "
            f"3M {r.return_3m_pct:+.1f}% | "
            f"6M {r.return_6m_pct:+.1f}%"
        )
        lines.append(
            f"   변동성 {r.volatility_pct:.1f}% | "
            f"Sharpe {r.sharpe_ratio:.2f} | "
            f"보수 {r.expense_ratio_pct:.2f}%"
        )
        lines.append("")

    return "\n".join(lines).rstrip()
