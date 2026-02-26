"""고급 리스크 엔진: VaR, Monte Carlo, 스트레스 테스트.

기존 risk_manager.py의 기본 리스크 체크를 보완하는 고급 분석 모듈.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# scipy z-score fallback
_Z_SCORES = {0.90: -1.2816, 0.95: -1.6449, 0.99: -2.3263}

try:
    from scipy import stats as scipy_stats
    _HAS_SCIPY = True
except ImportError:
    scipy_stats = None
    _HAS_SCIPY = False


# ── Dataclasses ──────────────────────────────────────────

@dataclass
class VaRResult:
    var_95: float
    var_99: float
    var_95_pct: float
    var_99_pct: float
    cvar_95: float
    cvar_95_pct: float
    method: str
    holding_period_days: int
    confidence_text: str


@dataclass
class MonteCarloResult:
    var_95: float
    var_99: float
    var_95_pct: float
    var_99_pct: float
    cvar_95_pct: float
    expected_return_pct: float
    best_case_pct: float
    worst_case_pct: float
    simulations: int
    distribution: list[float] = field(default_factory=list)


@dataclass
class StressTestResult:
    scenario_name: str
    portfolio_impact_pct: float
    portfolio_impact_amount: float
    per_stock_impact: list[dict] = field(default_factory=list)
    recovery_days_estimate: int = 0
    historical_reference: str = ""


@dataclass
class AdvancedRiskReport:
    date: str
    portfolio_value: float
    historical_var: VaRResult | None = None
    parametric_var: VaRResult | None = None
    monte_carlo: MonteCarloResult | None = None
    correlation_matrix: dict | None = None
    high_correlation_pairs: list = field(default_factory=list)
    stress_results: list[StressTestResult] = field(default_factory=list)
    risk_grade: str = "C"
    risk_score: int = 50


# ── Historical stress scenarios ──────────────────────────

HISTORICAL_STRESS_SCENARIOS = {
    "covid_crash": {
        "name": "코로나 폭락 (2020.03)",
        "market_impact": -0.33,
        "sector_multiplier": {
            "반도체": 0.8, "2차전지": 1.2, "자동차": 1.1,
            "바이오": 0.6, "금융": 1.3, "통신": 0.7,
            "엔터": 1.5, "조선": 1.0, "방산": 0.8,
            "기타": 1.0,
        },
        "recovery_days": 120,
    },
    "lehman_crisis": {
        "name": "리먼 사태 (2008)",
        "market_impact": -0.45,
        "sector_multiplier": {
            "금융": 1.8, "반도체": 1.2, "자동차": 1.5,
            "기타": 1.0,
        },
        "recovery_days": 365,
    },
    "china_shock": {
        "name": "중국 경기 둔화",
        "market_impact": -0.15,
        "sector_multiplier": {
            "2차전지": 1.5, "반도체": 1.3, "철강": 1.8, "화학": 1.6,
            "기타": 0.8,
        },
        "recovery_days": 60,
    },
    "rate_surge": {
        "name": "미국 금리 급등 (+1%p)",
        "market_impact": -0.12,
        "sector_multiplier": {
            "반도체": 1.3, "바이오": 1.5, "금융": 0.5,
            "기타": 1.0,
        },
        "recovery_days": 90,
    },
    "won_crisis": {
        "name": "원화 급락 (USD/KRW 1,500원)",
        "market_impact": -0.18,
        "sector_multiplier": {
            "자동차": 0.5, "조선": 0.4,
            "바이오": 1.2, "통신": 0.8,
            "기타": 1.0,
        },
        "recovery_days": 45,
    },
}


# ── Correlation ──────────────────────────────────────────

def calculate_real_correlation(
    price_histories: dict[str, pd.Series],
    window: int = 60,
) -> pd.DataFrame:
    """실제 가격 데이터 기반 상관관계 행렬 계산."""
    if not price_histories or len(price_histories) < 2:
        return pd.DataFrame()

    returns_df = pd.DataFrame()
    for ticker, prices in price_histories.items():
        if len(prices) < window:
            continue
        r = prices.pct_change().dropna().tail(window)
        returns_df[ticker] = r

    if returns_df.empty or len(returns_df.columns) < 2:
        return pd.DataFrame()

    return returns_df.corr()


async def _fetch_price_histories(
    tickers: list[dict],
    period: str = "6mo",
) -> dict[str, pd.Series]:
    """yfinance에서 종목별 종가 히스토리 가져오기 (비동기)."""
    import yfinance as yf

    loop = asyncio.get_event_loop()
    result = {}
    for t in tickers:
        ticker = t.get("ticker", "")
        market = t.get("market", "KOSPI")
        suffix = ".KS" if market.upper() == "KOSPI" else ".KQ"
        symbol = f"{ticker}{suffix}"
        try:
            hist = await loop.run_in_executor(
                None, lambda s=symbol: yf.Ticker(s).history(period=period)
            )
            if not hist.empty and len(hist) >= 20:
                result[ticker] = hist["Close"]
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", symbol, e)
    return result


# ── Historical VaR ───────────────────────────────────────

def calculate_historical_var(
    portfolio_value: float,
    holdings: list[dict],
    confidence: float = 0.95,
    holding_period: int = 1,
) -> VaRResult:
    """역사적 시뮬레이션 VaR."""
    weights = np.array([h.get("weight", 0) for h in holdings], dtype=np.float64)
    return_arrays = [np.array(h.get("returns", [0.0]), dtype=np.float64) for h in holdings]

    if not return_arrays or len(return_arrays[0]) == 0:
        return VaRResult(
            var_95=0, var_99=0, var_95_pct=0, var_99_pct=0,
            cvar_95=0, cvar_95_pct=0, method="historical",
            holding_period_days=holding_period,
            confidence_text="데이터 부족",
        )

    # Align lengths
    min_len = min(len(r) for r in return_arrays)
    return_arrays = [r[:min_len] for r in return_arrays]

    # Portfolio daily returns
    portfolio_returns = np.zeros(min_len)
    for w, r in zip(weights, return_arrays):
        portfolio_returns += w * r

    var_95_pct = float(np.percentile(portfolio_returns, (1 - 0.95) * 100))
    var_99_pct = float(np.percentile(portfolio_returns, (1 - 0.99) * 100))

    # CVaR
    cvar_mask = portfolio_returns <= var_95_pct
    cvar_95_pct = float(np.mean(portfolio_returns[cvar_mask])) if cvar_mask.any() else var_95_pct

    # Scale for holding period
    if holding_period > 1:
        scale = np.sqrt(holding_period)
        var_95_pct *= scale
        var_99_pct *= scale
        cvar_95_pct *= scale

    var_95 = portfolio_value * var_95_pct
    var_99 = portfolio_value * var_99_pct
    cvar_95 = portfolio_value * cvar_95_pct

    return VaRResult(
        var_95=round(var_95, 0),
        var_99=round(var_99, 0),
        var_95_pct=round(var_95_pct * 100, 2),
        var_99_pct=round(var_99_pct * 100, 2),
        cvar_95=round(cvar_95, 0),
        cvar_95_pct=round(cvar_95_pct * 100, 2),
        method="historical",
        holding_period_days=holding_period,
        confidence_text=(
            f"95% 확률로 {holding_period}일 최대 "
            f"{abs(var_95):,.0f}원 손실"
        ),
    )


# ── Parametric VaR ───────────────────────────────────────

def calculate_parametric_var(
    portfolio_value: float,
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    confidence: float = 0.95,
    holding_period: int = 1,
) -> VaRResult:
    """분산-공분산(Parametric) VaR."""
    if _HAS_SCIPY:
        z_95 = scipy_stats.norm.ppf(1 - 0.95)
        z_99 = scipy_stats.norm.ppf(1 - 0.99)
    else:
        z_95 = _Z_SCORES.get(0.95, -1.6449)
        z_99 = _Z_SCORES.get(0.99, -2.3263)

    portfolio_std = float(np.sqrt(weights @ cov_matrix @ weights))

    var_95_pct = z_95 * portfolio_std * np.sqrt(holding_period)
    var_99_pct = z_99 * portfolio_std * np.sqrt(holding_period)
    # CVaR for normal: E[X | X < VaR] = mu - sigma * phi(z) / (1-confidence)
    cvar_95_pct = var_95_pct * 1.2  # approximation

    var_95 = portfolio_value * var_95_pct
    var_99 = portfolio_value * var_99_pct
    cvar_95 = portfolio_value * cvar_95_pct

    return VaRResult(
        var_95=round(var_95, 0),
        var_99=round(var_99, 0),
        var_95_pct=round(var_95_pct * 100, 2),
        var_99_pct=round(var_99_pct * 100, 2),
        cvar_95=round(cvar_95, 0),
        cvar_95_pct=round(cvar_95_pct * 100, 2),
        method="parametric",
        holding_period_days=holding_period,
        confidence_text=(
            f"95% 확률로 {holding_period}일 최대 "
            f"{abs(var_95):,.0f}원 손실 (정규분포 가정)"
        ),
    )


# ── Monte Carlo ──────────────────────────────────────────

def run_monte_carlo(
    portfolio_value: float,
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    days: int = 20,
    simulations: int = 10000,
) -> MonteCarloResult:
    """Monte Carlo 시뮬레이션으로 포트폴리오 수익 분포 예측."""
    n_stocks = len(weights)

    # Ensure positive definite covariance matrix
    try:
        cov_pd = cov_matrix.copy()
        eigvals = np.linalg.eigvalsh(cov_pd)
        if np.min(eigvals) < 0:
            cov_pd += np.eye(n_stocks) * (abs(np.min(eigvals)) + 1e-8)

        # Vectorized simulation
        random_returns = np.random.multivariate_normal(
            mean_returns, cov_pd, (simulations, days),
        )
        portfolio_daily = np.tensordot(random_returns, weights, axes=(2, 0))
        cumulative = np.prod(1 + portfolio_daily, axis=1)
        results = (cumulative - 1) * 100
    except Exception as e:
        logger.warning("Monte Carlo fallback to simple: %s", e)
        portfolio_mean = float(np.dot(weights, mean_returns))
        portfolio_std = float(np.sqrt(weights @ cov_matrix @ weights))
        results = np.random.normal(
            portfolio_mean * days, portfolio_std * np.sqrt(days), simulations,
        ) * 100

    var_95_pct = float(np.percentile(results, 5))
    var_99_pct = float(np.percentile(results, 1))
    cvar_mask = results <= var_95_pct
    cvar_95_pct = float(np.mean(results[cvar_mask])) if cvar_mask.any() else var_95_pct

    # Distribution histogram (100 bins)
    hist_vals, _ = np.histogram(results, bins=100)
    distribution = hist_vals.tolist()

    return MonteCarloResult(
        var_95=round(portfolio_value * var_95_pct / 100, 0),
        var_99=round(portfolio_value * var_99_pct / 100, 0),
        var_95_pct=round(var_95_pct, 2),
        var_99_pct=round(var_99_pct, 2),
        cvar_95_pct=round(cvar_95_pct, 2),
        expected_return_pct=round(float(np.median(results)), 2),
        best_case_pct=round(float(np.percentile(results, 95)), 2),
        worst_case_pct=round(float(np.percentile(results, 5)), 2),
        simulations=simulations,
        distribution=distribution,
    )


# ── Stress Test ──────────────────────────────────────────

def run_stress_test(
    portfolio_value: float,
    holdings: list[dict],
    scenario_key: str = "all",
) -> list[StressTestResult]:
    """과거 위기 시나리오로 포트폴리오 스트레스 테스트."""
    scenarios = HISTORICAL_STRESS_SCENARIOS
    if scenario_key != "all" and scenario_key in scenarios:
        scenarios = {scenario_key: scenarios[scenario_key]}

    results = []
    total_weight = sum(h.get("weight", 1.0) for h in holdings) or 1.0

    for key, scenario in scenarios.items():
        market_impact = scenario["market_impact"]
        sector_mult = scenario["sector_multiplier"]
        per_stock = []
        portfolio_impact = 0.0

        for h in holdings:
            weight = h.get("weight", 1.0) / total_weight
            sector = h.get("sector", "기타")
            mult = sector_mult.get(sector, sector_mult.get("기타", 1.0))
            stock_impact = market_impact * mult
            portfolio_impact += stock_impact * weight

            per_stock.append({
                "ticker": h.get("ticker", ""),
                "name": h.get("name", ""),
                "impact_pct": round(stock_impact * 100, 1),
            })

        results.append(StressTestResult(
            scenario_name=scenario["name"],
            portfolio_impact_pct=round(portfolio_impact * 100, 1),
            portfolio_impact_amount=round(portfolio_value * portfolio_impact, 0),
            per_stock_impact=per_stock,
            recovery_days_estimate=scenario["recovery_days"],
            historical_reference=scenario["name"],
        ))

    return results


# ── Risk Grade ───────────────────────────────────────────

def _calculate_risk_grade(
    var_95_pct: float = 0,
    max_dd_pct: float = 0,
    concentration: float = 0,
    max_corr: float = 0,
    worst_stress_pct: float = 0,
) -> tuple[str, int]:
    """종합 리스크 등급과 점수 계산 (0~100, 높을수록 위험)."""
    score = 0

    # VaR 95% (0~25)
    score += min(25, int(abs(var_95_pct) * 10))

    # MDD (0~25)
    score += min(25, int(abs(max_dd_pct) * 2.5))

    # 집중도 (0~15): weight of top stock
    score += min(15, int(concentration * 15))

    # 상관관계 (0~15)
    score += min(15, int(max_corr * 15))

    # 스트레스 최악 (0~20)
    score += min(20, int(abs(worst_stress_pct) * 0.5))

    score = min(100, max(0, score))

    if score <= 20:
        grade = "A"
    elif score <= 40:
        grade = "B"
    elif score <= 60:
        grade = "C"
    elif score <= 80:
        grade = "D"
    else:
        grade = "F"

    return grade, score


# ── Integrated Report ────────────────────────────────────

async def generate_advanced_risk_report(
    portfolio_value: float,
    holdings: list[dict],
    yf_client=None,
) -> AdvancedRiskReport:
    """고급 리스크 통합 리포트 생성."""
    report = AdvancedRiskReport(
        date=datetime.now().strftime("%Y-%m-%d"),
        portfolio_value=portfolio_value,
    )

    if not holdings or portfolio_value <= 0:
        return report

    # Fetch price histories
    try:
        price_histories = await _fetch_price_histories(
            [{"ticker": h.get("ticker", ""), "market": h.get("market", "KOSPI")} for h in holdings],
        )
    except Exception as e:
        logger.error("Failed to fetch price histories: %s", e)
        price_histories = {}

    # Correlation
    if len(price_histories) >= 2:
        corr_df = calculate_real_correlation(price_histories)
        if not corr_df.empty:
            report.correlation_matrix = corr_df.to_dict()
            # Find high correlation pairs
            for i, t1 in enumerate(corr_df.columns):
                for j, t2 in enumerate(corr_df.columns):
                    if i < j:
                        c = corr_df.iloc[i, j]
                        if abs(c) > 0.7:
                            report.high_correlation_pairs.append((t1, t2, round(c, 2)))

    # Prepare returns and weights
    total_val = sum(h.get("eval_amount", 0) or h.get("weight", 1) for h in holdings) or 1
    weights = []
    return_data = []
    for h in holdings:
        w = (h.get("eval_amount", 0) or h.get("weight", 1)) / total_val
        weights.append(w)
        ticker = h.get("ticker", "")
        if ticker in price_histories:
            returns = price_histories[ticker].pct_change().dropna().values
            return_data.append(returns)
        else:
            return_data.append(np.zeros(60))

    weights = np.array(weights)

    # Historical VaR
    try:
        min_len = min(len(r) for r in return_data) if return_data else 0
        if min_len > 10:
            h_data = [{"weight": w, "returns": r[:min_len]} for w, r in zip(weights, return_data)]
            report.historical_var = calculate_historical_var(portfolio_value, h_data)
    except Exception as e:
        logger.error("Historical VaR error: %s", e)

    # Parametric VaR
    try:
        if len(return_data) > 0:
            min_len = min(len(r) for r in return_data)
            returns_matrix = np.array([r[:min_len] for r in return_data])
            mean_returns = np.mean(returns_matrix, axis=1)
            cov_matrix = np.cov(returns_matrix)
            if cov_matrix.ndim == 0:
                cov_matrix = np.array([[float(cov_matrix)]])
            report.parametric_var = calculate_parametric_var(
                portfolio_value, weights, mean_returns, cov_matrix,
            )
            # Monte Carlo
            report.monte_carlo = run_monte_carlo(
                portfolio_value, weights, mean_returns, cov_matrix,
            )
    except Exception as e:
        logger.error("Parametric VaR / Monte Carlo error: %s", e)

    # Stress Test
    try:
        from kstock.core.risk_manager import SECTOR_MAP
        for h in holdings:
            if "sector" not in h:
                h["sector"] = SECTOR_MAP.get(h.get("ticker", ""), "기타")
            if "weight" not in h:
                h["weight"] = (h.get("eval_amount", 0) or 1)
        report.stress_results = run_stress_test(portfolio_value, holdings)
    except Exception as e:
        logger.error("Stress test error: %s", e)

    # Risk Grade
    var_pct = report.historical_var.var_95_pct if report.historical_var else 0
    max_corr = max((c for _, _, c in report.high_correlation_pairs), default=0)
    worst_stress = min((s.portfolio_impact_pct for s in report.stress_results), default=0)
    concentration = max(weights) if len(weights) > 0 else 0
    report.risk_grade, report.risk_score = _calculate_risk_grade(
        var_95_pct=var_pct,
        max_dd_pct=0,
        concentration=concentration,
        max_corr=max_corr,
        worst_stress_pct=worst_stress,
    )

    return report


# ── Telegram Format ──────────────────────────────────────

def format_advanced_risk_report(report: AdvancedRiskReport) -> str:
    """고급 리스크 리포트 텔레그램 포맷."""
    lines = [
        f"🛡️ 고급 리스크 리포트 ({report.date})",
        "━" * 22,
        "",
        f"📊 리스크 등급: {report.risk_grade} — {report.risk_score}점/100",
        "",
    ]

    if report.historical_var:
        v = report.historical_var
        lines.extend([
            f"💰 VaR (1일, 95%): {v.var_95:,.0f}원 ({v.var_95_pct:+.2f}%)",
            f'   "{v.confidence_text}"',
            f"💰 CVaR (95%): {v.cvar_95:,.0f}원 ({v.cvar_95_pct:+.2f}%)",
            "",
        ])

    if report.monte_carlo:
        mc = report.monte_carlo
        lines.extend([
            f"🎲 Monte Carlo (20일, {mc.simulations:,}회)",
            f"   기대 수익: {mc.expected_return_pct:+.1f}%",
            f"   최선: {mc.best_case_pct:+.1f}% | 최악: {mc.worst_case_pct:+.1f}%",
            "",
        ])

    if report.stress_results:
        lines.append("📉 스트레스 테스트")
        for s in report.stress_results:
            lines.append(
                f"   {s.scenario_name}: {s.portfolio_impact_pct:+.0f}%"
                f" ({s.portfolio_impact_amount:,.0f}원)"
            )
        lines.append("")

    if report.high_correlation_pairs:
        pairs_text = ", ".join(
            f"{a}↔{b} ({c:.2f})"
            for a, b, c in report.high_correlation_pairs[:3]
        )
        lines.append(f"🔗 고상관 종목: {pairs_text}")

    return "\n".join(lines)
