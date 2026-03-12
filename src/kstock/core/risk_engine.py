"""고급 리스크 엔진: VaR, Monte Carlo, 스트레스 테스트 + 통합 리스크 평가.

기존 risk_manager.py의 기본 리스크 체크를 보완하는 고급 분석 모듈.
v12.5: RiskEngine.evaluate() 단일 진입점 + ManagerRiskPolicy.apply() 추가.
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


# =====================================================================
# v12.5: 통합 리스크 평가 진입점
# =====================================================================

@dataclass
class RiskContext:
    """RiskEngine에 전달하는 평가 컨텍스트.

    MacroSnapshot에서 필요한 필드만 추출.
    dict로도 생성 가능 (from_dict / from_macro_snapshot).
    """
    vix: float = 0.0
    vix_change_pct: float = 0.0
    usdkrw: float = 0.0
    usdkrw_change_pct: float = 0.0
    fear_greed: float = 50.0
    days_to_expiry: int = 999

    # 쇼크 (이미 계산된 경우)
    shock_grade: str = "NONE"
    global_shock_score: float = 0.0
    korea_open_risk_score: float = 0.0

    # 한국 리스크 (이미 계산된 경우)
    korea_risk_score: float = 0.0

    # 레짐 (이미 계산된 경우)
    regime_mode: str = ""  # bubble_attack/attack/balanced/defense

    # v13: FRED 신용 스트레스
    hy_spread: float = 0.0
    nfci: float = 0.0

    # alert mode
    alert_mode: str = "normal"  # normal/elevated/wartime

    @classmethod
    def from_dict(cls, d: dict) -> "RiskContext":
        """dict → RiskContext. 미지 키 무시."""
        return cls(
            vix=_safe_get(d, "vix", 0.0),
            vix_change_pct=_safe_get(d, "vix_change_pct", 0.0),
            usdkrw=_safe_get(d, "usdkrw", 0.0),
            usdkrw_change_pct=_safe_get(d, "usdkrw_change_pct", 0.0),
            fear_greed=_safe_get(d, "fear_greed", _safe_get(d, "fear_greed_score", 50.0)),
            days_to_expiry=int(_safe_get(d, "days_to_expiry", 999)),
            shock_grade=str(_safe_get(d, "shock_grade", "NONE")),
            global_shock_score=_safe_get(d, "global_shock_score", 0.0),
            korea_open_risk_score=_safe_get(d, "korea_open_risk_score", 0.0),
            korea_risk_score=_safe_get(d, "korea_risk_score", 0.0),
            regime_mode=str(_safe_get(d, "regime_mode", "")),
            alert_mode=str(_safe_get(d, "alert_mode", "normal")),
            hy_spread=_safe_get(d, "hy_spread", 0.0),
            nfci=_safe_get(d, "nfci", 0.0),
        )

    @classmethod
    def from_macro_snapshot(cls, snap) -> "RiskContext":
        """MacroSnapshot 객체 → RiskContext."""
        return cls(
            vix=getattr(snap, "vix", 0.0) or 0.0,
            vix_change_pct=getattr(snap, "vix_change_pct", 0.0) or 0.0,
            usdkrw=getattr(snap, "usdkrw", 0.0) or 0.0,
            usdkrw_change_pct=getattr(snap, "usdkrw_change_pct", 0.0) or 0.0,
            fear_greed=getattr(snap, "fear_greed_score", 50.0) or 50.0,
            hy_spread=getattr(snap, "hy_spread", 0.0) or 0.0,
            nfci=getattr(snap, "nfci", 0.0) or 0.0,
        )


def _safe_get(d, key, default=0.0):
    """dict 또는 object에서 안전하게 값 추출."""
    if isinstance(d, dict):
        v = d.get(key, default)
    else:
        v = getattr(d, key, default)
    return v if v is not None else default


# ── 매니저 액션 ────────────────────────────────────────────────

@dataclass
class ManagerAction:
    """매니저별 리스크 정책 적용 결과.

    Usage:
        action = ManagerRiskPolicy.apply("scalp", risk_decision)
        if not action.can_enter:
            print(action.block_reason)
    """
    manager_key: str = ""
    can_enter: bool = True
    block_reason: str = ""
    regime_weight: float = 1.0
    stop_tighten_pct: float = 0.0
    wartime_action: str = ""
    recommendations: list = field(default_factory=list)
    # v12.6: 보유 관리
    holding_action: str = "hold"        # hold / reduce / accumulate
    holding_reduce_pct: float = 0.0     # reduce 시 축소 비율 (%)
    holding_override_stop: bool = False  # True = 리스크 기반 손절 강화 안 함

    def __bool__(self) -> bool:
        return self.can_enter


# ── RiskEngine ─────────────────────────────────────────────────

class RiskEngine:
    """단일 리스크 평가 진입점.

    Usage:
        engine = RiskEngine()
        rd = engine.evaluate(ctx)
        action = ManagerRiskPolicy.apply("scalp", rd)
    """

    def evaluate(self, ctx: RiskContext) -> "RiskDecision":
        """RiskContext → RiskDecision.

        위임 체인:
        1. RiskDecision.from_market_state() — VIX/USDKRW/만기/쇼크
        2. Fear & Greed 보강
        3. 글로벌 쇼크 스코어 반영
        4. alert_mode 반영
        5. regime_mode 반영
        """
        from kstock.core.domain_types import RiskDecision

        # 1) 핵심 매크로 리스크
        rd = RiskDecision.from_market_state(
            vix=ctx.vix,
            usdkrw=ctx.usdkrw,
            usdkrw_change_pct=ctx.usdkrw_change_pct,
            days_to_expiry=ctx.days_to_expiry,
            shock_grade=ctx.shock_grade,
            korea_risk_score=ctx.korea_risk_score,
            source="risk_engine",
        )

        # 2) Fear & Greed 보강
        if ctx.fear_greed < 20:
            rd.reasons.append(f"극단 공포(F&G {ctx.fear_greed:.0f})")
            rd.source_flags.append("extreme_fear")
        elif ctx.fear_greed < 35:
            rd.reasons.append(f"공포(F&G {ctx.fear_greed:.0f})")

        # 3) 글로벌 쇼크 스코어
        if ctx.global_shock_score >= 70:
            rd.risk_score = max(rd.risk_score, ctx.global_shock_score)
            rd.source_flags.append("global_shock_high")
        if ctx.korea_open_risk_score >= 70:
            rd.source_flags.append("korea_open_risk_high")

        # 3.5) FRED 신용 스트레스 (HY Spread + NFCI)
        if ctx.hy_spread > 0 or ctx.nfci != 0:
            try:
                from kstock.core.risk_config import get_risk_thresholds
                cs = get_risk_thresholds().credit_stress
            except Exception:
                from kstock.core.risk_config import CreditStressThresholds
                cs = CreditStressThresholds()

            hy_up = ctx.hy_spread >= cs.hy_warning   # HY 상방 확정
            nfci_up = ctx.nfci >= cs.nfci_warning     # NFCI 상방 확정

            if hy_up and nfci_up:
                # 둘 다 상방 확정 → 매수 차단 + 현금 확보
                rd.block_new_buy = True
                rd.cash_floor_pct = max(rd.cash_floor_pct, 25.0)
                rd.max_position_pct = min(rd.max_position_pct, 75.0)
                rd.reasons.append(
                    f"신용 스트레스(HY {ctx.hy_spread:.1f}%/NFCI {ctx.nfci:+.2f})")
                rd.source_flags.append("credit_stress_confirmed")
            elif hy_up:
                rd.reasons.append(f"HY 스프레드 경고({ctx.hy_spread:.1f}%)")
                rd.source_flags.append("hy_spread_elevated")
            elif nfci_up:
                rd.reasons.append(f"NFCI 긴축 진입({ctx.nfci:+.2f})")
                rd.source_flags.append("nfci_tightening")
            elif (ctx.hy_spread > 0 and ctx.hy_spread < cs.hy_watch
                  and ctx.nfci < cs.nfci_watch):
                # 둘 다 안정 → 하락장이면 매수 기회
                rd.source_flags.append("credit_dip_opportunity")

        # 4) alert_mode 반영
        if ctx.alert_mode == "wartime":
            if not rd.block_new_buy:
                rd.reduce_position = True
                rd.cash_floor_pct = max(rd.cash_floor_pct, 30.0)
                rd.max_position_pct = min(rd.max_position_pct, 60.0)
            rd.source_flags.append("wartime")
        elif ctx.alert_mode == "elevated":
            rd.cash_floor_pct = max(rd.cash_floor_pct, 15.0)
            rd.source_flags.append("elevated")

        # 5) regime_mode 반영
        if ctx.regime_mode == "defense":
            rd.source_flags.append("defense_regime")
        elif ctx.regime_mode in ("attack", "bubble_attack"):
            rd.source_flags.append("attack_regime")

        return rd


# ── ManagerRiskPolicy ──────────────────────────────────────────

class ManagerRiskPolicy:
    """매니저별 리스크 정책 적용기.

    Usage:
        rd = RiskEngine().evaluate(ctx)
        action = ManagerRiskPolicy.apply("scalp", rd)
    """

    @staticmethod
    def apply(manager_key: str, rd) -> ManagerAction:
        """RiskDecision + 매니저 정책 → ManagerAction."""
        from kstock.bot.investment_managers import (
            get_manager_risk_policy,
            get_regime_weight,
            should_manager_enter,
        )

        policy = get_manager_risk_policy(manager_key)
        weight = get_regime_weight(manager_key, vix=rd.vix)

        can_enter, block_reason = should_manager_enter(
            manager_key, vix=rd.vix, shock_grade=rd.shock_grade,
        )

        # RiskDecision 매수 차단 → long_term / tenbagger(panic) 제외 차단
        _regime = getattr(rd, "regime", "")
        if rd.block_new_buy and manager_key not in ("long_term",):
            # tenbagger: panic에서는 VIX 한도(40) 이내면 매수 허용
            if manager_key == "tenbagger" and _regime == "panic":
                pass  # should_manager_enter()의 max_vix(40)로 자연 제어
            else:
                can_enter = False
                if not block_reason:
                    block_reason = rd.reason

        # wartime + 매니저 정책
        wartime_action = policy.get("wartime_action", "")
        if "wartime" in getattr(rd, "source_flags", []):
            if wartime_action == "disable":
                can_enter = False
                block_reason = block_reason or "전시 모드: 매니저 비활성"
            elif wartime_action == "restrict":
                can_enter = False
                block_reason = block_reason or "전시 모드: 신규 매수 제한"
            elif wartime_action == "event_hold":
                can_enter = False
                block_reason = block_reason or "전시 모드: 보유만 유지"

        # 손절 강화
        stop_tighten = 0.0
        if rd.risk_level in ("danger", "blocked", "warning"):
            stop_tighten = policy.get("stop_tighten_pct", 0)

        # 추천 사항
        recs = []
        if weight <= 0.3:
            recs.append("추천 보류 (레짐 가중치 극단)")
        elif weight < 0.7:
            recs.append("보수적 접근 권고")
        if rd.reduce_position:
            recs.append(f"포지션 축소 권고 (최대 {rd.max_position_pct:.0f}%)")
        if rd.cash_floor_pct > 0:
            recs.append(f"현금 {rd.cash_floor_pct:.0f}%+ 확보")

        # v12.6: 보유 관리 결정
        holding_action = "hold"
        holding_reduce_pct = 0.0
        holding_override_stop = False

        if manager_key == "tenbagger":
            # 텐배거: 가설 기반 -25% 손절, 시장 공포와 무관 → 손절 강화 안 함
            holding_override_stop = True
            stop_tighten = 0.0

            if rd.risk_level == "blocked" and _regime == "crisis":
                # crisis: C등급 축소 권고, A등급 코어 유지
                holding_action = "reduce"
                holding_reduce_pct = 50.0
                recs.append("C등급 옵션 50% 축소, A등급 코어 유지")
            elif rd.risk_level in ("danger", "blocked"):
                recs.append("텐배거 전량 보유 유지 (투자 논리 건재 전제)")

            # 환율 수혜 감지 (수출형 텐배거)
            _usdkrw = getattr(rd, "usdkrw", 0)
            _flags = getattr(rd, "source_flags", [])
            if _usdkrw >= 1350 and "foreign_outflow_pattern" not in _flags:
                recs.append("원화 약세 → 수출형 텐배거 수혜 가능")

        elif manager_key == "long_term":
            # 장기: panic/crisis에서 손절 강화 안 함
            if rd.risk_level in ("danger", "blocked"):
                holding_override_stop = True

        return ManagerAction(
            manager_key=manager_key,
            can_enter=can_enter,
            block_reason=block_reason,
            regime_weight=weight,
            stop_tighten_pct=stop_tighten,
            wartime_action=wartime_action,
            recommendations=recs,
            holding_action=holding_action,
            holding_reduce_pct=holding_reduce_pct,
            holding_override_stop=holding_override_stop,
        )

    @staticmethod
    def apply_all(rd) -> dict[str, ManagerAction]:
        """모든 매니저에 대해 일괄 적용."""
        managers = ["scalp", "swing", "position", "long_term", "tenbagger"]
        return {m: ManagerRiskPolicy.apply(m, rd) for m in managers}
