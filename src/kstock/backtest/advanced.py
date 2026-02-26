"""전략 백테스트 고도화 - Phase 3-2.

기존 백테스트 엔진에 추가되는 고급 분석 기능:
  1. Monte Carlo 시뮬레이션: 수익률 분포의 통계적 검증
  2. Walk-Forward 분석: 시계열 교차검증으로 과적합 방지
  3. 전략별 성과 비교표: A~G 전략 동시 비교
  4. 리스크 조정 수익률: Sortino, Calmar 비율
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ── 데이터 구조 ───────────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    """Monte Carlo 시뮬레이션 결과."""
    n_simulations: int
    n_trades: int
    median_return_pct: float
    mean_return_pct: float
    std_return_pct: float
    percentile_5: float       # 5% 최악 시나리오
    percentile_25: float      # 25% 약세 시나리오
    percentile_75: float      # 75% 강세 시나리오
    percentile_95: float      # 95% 최선 시나리오
    probability_positive: float  # 수익 확률 (%)
    probability_target: float    # 목표 수익률 달성 확률 (%)
    max_drawdown_median: float
    var_95: float              # Value at Risk (95%)


@dataclass
class WalkForwardResult:
    """Walk-Forward 분석 결과."""
    n_windows: int
    window_results: list[dict]   # 각 윈도우별 성과
    avg_train_sharpe: float
    avg_test_sharpe: float
    sharpe_decay_pct: float      # 학습→검증 Sharpe 감소율
    consistency_score: float     # 일관성 점수 (0~1)
    robustness: str             # "robust", "moderate", "fragile"


@dataclass
class StrategyComparison:
    """전략 비교 결과."""
    strategies: dict[str, dict]  # strategy_code → metrics
    best_strategy: str
    best_sharpe: float
    ranking: list[tuple[str, float]]  # (strategy_code, sharpe)


@dataclass
class RiskAdjustedMetrics:
    """리스크 조정 수익률 지표."""
    sharpe_ratio: float
    sortino_ratio: float    # 하방 리스크만 고려
    calmar_ratio: float     # 수익/최대낙폭
    omega_ratio: float      # 이익/손실 확률 비율
    information_ratio: float  # 벤치마크 대비 초과수익
    max_consecutive_losses: int
    recovery_factor: float   # 총수익/최대낙폭


class AdvancedBacktester:
    """고급 백테스트 분석 엔진."""

    # ── Monte Carlo 시뮬레이션 ───────────────────────────────────

    def run_monte_carlo(
        self,
        trade_pnls: list[float],
        n_simulations: int = 5000,
        n_trades: int = 0,
        target_return_pct: float = 10.0,
        seed: int | None = None,
    ) -> MonteCarloResult:
        """Monte Carlo 시뮬레이션으로 수익률 분포 추정.

        Args:
            trade_pnls: 개별 거래 수익률(%) 리스트
            n_simulations: 시뮬레이션 횟수
            n_trades: 시뮬레이션당 거래 수 (0이면 원본과 동일)
            target_return_pct: 목표 수익률 (확률 계산용)
            seed: 랜덤 시드

        Returns:
            MonteCarloResult
        """
        if not trade_pnls or len(trade_pnls) < 3:
            return MonteCarloResult(
                n_simulations=0, n_trades=0,
                median_return_pct=0, mean_return_pct=0, std_return_pct=0,
                percentile_5=0, percentile_25=0,
                percentile_75=0, percentile_95=0,
                probability_positive=0, probability_target=0,
                max_drawdown_median=0, var_95=0,
            )

        rng = np.random.default_rng(seed)
        pnls = np.array(trade_pnls)
        n = n_trades if n_trades > 0 else len(pnls)

        total_returns = []
        max_drawdowns = []

        for _ in range(n_simulations):
            # 복원 추출 (bootstrap)
            sampled = rng.choice(pnls, size=n, replace=True)

            # 누적 수익률
            cumulative = np.cumprod(1 + sampled / 100)
            total_ret = (cumulative[-1] - 1) * 100
            total_returns.append(total_ret)

            # 최대 낙폭
            peak = np.maximum.accumulate(cumulative)
            dd = (cumulative - peak) / peak * 100
            max_drawdowns.append(float(np.min(dd)))

        returns_arr = np.array(total_returns)
        dd_arr = np.array(max_drawdowns)

        return MonteCarloResult(
            n_simulations=n_simulations,
            n_trades=n,
            median_return_pct=round(float(np.median(returns_arr)), 2),
            mean_return_pct=round(float(np.mean(returns_arr)), 2),
            std_return_pct=round(float(np.std(returns_arr)), 2),
            percentile_5=round(float(np.percentile(returns_arr, 5)), 2),
            percentile_25=round(float(np.percentile(returns_arr, 25)), 2),
            percentile_75=round(float(np.percentile(returns_arr, 75)), 2),
            percentile_95=round(float(np.percentile(returns_arr, 95)), 2),
            probability_positive=round(
                float(np.sum(returns_arr > 0) / n_simulations * 100), 1,
            ),
            probability_target=round(
                float(np.sum(returns_arr >= target_return_pct) / n_simulations * 100), 1,
            ),
            max_drawdown_median=round(float(np.median(dd_arr)), 2),
            var_95=round(float(np.percentile(returns_arr, 5)), 2),
        )

    # ── Walk-Forward 분석 ────────────────────────────────────────

    def run_walk_forward(
        self,
        trade_pnls: list[float],
        n_windows: int = 5,
        train_ratio: float = 0.7,
    ) -> WalkForwardResult:
        """Walk-Forward 교차검증 분석.

        시계열을 n_windows개의 윈도우로 나누어
        각 윈도우에서 학습/검증 성과를 비교.

        Args:
            trade_pnls: 시간순 거래 수익률 리스트
            n_windows: 분석 윈도우 수
            train_ratio: 학습 비율 (0.5~0.8)
        """
        if len(trade_pnls) < 10:
            return WalkForwardResult(
                n_windows=0, window_results=[],
                avg_train_sharpe=0, avg_test_sharpe=0,
                sharpe_decay_pct=0, consistency_score=0,
                robustness="fragile",
            )

        pnls = np.array(trade_pnls)
        total = len(pnls)
        window_size = total // n_windows
        if window_size < 5:
            n_windows = max(2, total // 5)
            window_size = total // n_windows

        window_results = []
        train_sharpes = []
        test_sharpes = []

        for i in range(n_windows):
            start = i * window_size
            end = min(start + window_size, total)
            window = pnls[start:end]

            split = int(len(window) * train_ratio)
            if split < 3 or len(window) - split < 2:
                continue

            train = window[:split]
            test = window[split:]

            train_sharpe = self._compute_sharpe(train)
            test_sharpe = self._compute_sharpe(test)

            train_sharpes.append(train_sharpe)
            test_sharpes.append(test_sharpe)

            window_results.append({
                "window": i + 1,
                "train_size": len(train),
                "test_size": len(test),
                "train_sharpe": round(train_sharpe, 2),
                "test_sharpe": round(test_sharpe, 2),
                "train_return": round(float(np.sum(train)), 2),
                "test_return": round(float(np.sum(test)), 2),
            })

        avg_train = np.mean(train_sharpes) if train_sharpes else 0
        avg_test = np.mean(test_sharpes) if test_sharpes else 0

        if avg_train > 0:
            decay = (avg_train - avg_test) / avg_train * 100
        else:
            decay = 100 if avg_test <= 0 else 0

        # 일관성: 검증 Sharpe가 양수인 윈도우 비율
        positive_tests = sum(1 for s in test_sharpes if s > 0)
        consistency = positive_tests / len(test_sharpes) if test_sharpes else 0

        # 로버스트니스 판단
        if consistency >= 0.8 and decay < 20:
            robustness = "robust"
        elif consistency >= 0.5:
            robustness = "moderate"
        else:
            robustness = "fragile"

        return WalkForwardResult(
            n_windows=len(window_results),
            window_results=window_results,
            avg_train_sharpe=round(float(avg_train), 2),
            avg_test_sharpe=round(float(avg_test), 2),
            sharpe_decay_pct=round(float(decay), 1),
            consistency_score=round(consistency, 2),
            robustness=robustness,
        )

    # ── 리스크 조정 지표 ─────────────────────────────────────────

    def compute_risk_metrics(
        self,
        trade_pnls: list[float],
        benchmark_pnls: list[float] | None = None,
        risk_free_rate: float = 3.5,  # 한국 기준금리
    ) -> RiskAdjustedMetrics:
        """리스크 조정 수익률 지표 계산.

        Args:
            trade_pnls: 거래 수익률 리스트 (%)
            benchmark_pnls: 벤치마크 수익률 리스트 (%)
            risk_free_rate: 무위험 수익률 (연 %)
        """
        if not trade_pnls or len(trade_pnls) < 2:
            return RiskAdjustedMetrics(
                sharpe_ratio=0, sortino_ratio=0, calmar_ratio=0,
                omega_ratio=0, information_ratio=0,
                max_consecutive_losses=0, recovery_factor=0,
            )

        pnls = np.array(trade_pnls)
        rf_per_trade = risk_free_rate / 252  # 거래당 무위험 수익률

        # Sharpe
        sharpe = self._compute_sharpe(pnls, rf_per_trade)

        # Sortino (하방 표준편차만 사용)
        excess = pnls - rf_per_trade
        downside = pnls[pnls < 0]
        downside_std = np.std(downside) if len(downside) > 0 else 1e-6
        sortino = float(np.mean(excess) / downside_std * np.sqrt(252 / 10))

        # Max Drawdown
        cumulative = np.cumprod(1 + pnls / 100)
        peak = np.maximum.accumulate(cumulative)
        dd = (cumulative - peak) / peak * 100
        max_dd = abs(float(np.min(dd))) if len(dd) > 0 else 1e-6

        # Calmar (연환산 수익 / 최대낙폭)
        total_return = (cumulative[-1] - 1) * 100
        calmar = total_return / max_dd if max_dd > 0 else 0

        # Omega (이익합 / 손실합)
        gains = pnls[pnls > 0]
        losses_abs = np.abs(pnls[pnls < 0])
        omega = (
            float(np.sum(gains) / np.sum(losses_abs))
            if len(losses_abs) > 0 and np.sum(losses_abs) > 0
            else float(np.sum(gains)) if len(gains) > 0 else 0
        )

        # Information Ratio (벤치마크 대비)
        if benchmark_pnls and len(benchmark_pnls) == len(pnls):
            excess_returns = pnls - np.array(benchmark_pnls)
            ir_std = np.std(excess_returns)
            information_ratio = (
                float(np.mean(excess_returns) / ir_std * np.sqrt(252 / 10))
                if ir_std > 0 else 0
            )
        else:
            information_ratio = 0

        # 최대 연속 손실
        max_consec = self._max_consecutive_losses(pnls)

        # Recovery Factor
        recovery = total_return / max_dd if max_dd > 0 else 0

        return RiskAdjustedMetrics(
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            calmar_ratio=round(calmar, 2),
            omega_ratio=round(omega, 2),
            information_ratio=round(information_ratio, 2),
            max_consecutive_losses=max_consec,
            recovery_factor=round(recovery, 2),
        )

    # ── 전략 비교 ────────────────────────────────────────────────

    def compare_strategies(
        self,
        strategy_results: dict[str, list[float]],
    ) -> StrategyComparison:
        """전략별 성과 비교표 생성.

        Args:
            strategy_results: 전략코드 → 거래 수익률 리스트

        Returns:
            StrategyComparison
        """
        strategies: dict[str, dict] = {}
        sharpes: list[tuple[str, float]] = []

        strategy_names = {
            "A": "단기반등", "B": "ETF레버리지", "C": "장기우량주",
            "D": "섹터로테이션", "E": "글로벌분산", "F": "모멘텀", "G": "돌파",
        }

        for code, pnls in strategy_results.items():
            if not pnls:
                continue

            arr = np.array(pnls)
            wins = arr[arr > 0]
            losses = arr[arr < 0]
            win_rate = len(wins) / len(arr) * 100 if len(arr) > 0 else 0

            sharpe = self._compute_sharpe(arr)
            total_ret = float(np.prod(1 + arr / 100) - 1) * 100

            # 최대 낙폭
            cumul = np.cumprod(1 + arr / 100)
            peak = np.maximum.accumulate(cumul)
            dd = (cumul - peak) / peak * 100
            max_dd = abs(float(np.min(dd))) if len(dd) > 0 else 0

            strategies[code] = {
                "name": strategy_names.get(code, code),
                "trades": len(arr),
                "win_rate": round(win_rate, 1),
                "avg_pnl": round(float(np.mean(arr)), 2),
                "total_return": round(total_ret, 2),
                "sharpe": round(sharpe, 2),
                "max_drawdown": round(max_dd, 2),
                "profit_factor": round(
                    float(np.sum(wins) / np.sum(np.abs(losses)))
                    if len(losses) > 0 and np.sum(np.abs(losses)) > 0
                    else float(np.sum(wins)),
                    2,
                ),
            }
            sharpes.append((code, sharpe))

        sharpes.sort(key=lambda x: x[1], reverse=True)
        best = sharpes[0] if sharpes else ("", 0)

        return StrategyComparison(
            strategies=strategies,
            best_strategy=best[0],
            best_sharpe=round(best[1], 2),
            ranking=[(c, round(s, 2)) for c, s in sharpes],
        )

    # ── 유틸리티 ─────────────────────────────────────────────────

    def _compute_sharpe(
        self, pnls: np.ndarray, rf: float = 0.0,
    ) -> float:
        """Sharpe ratio 계산."""
        if len(pnls) < 2:
            return 0.0
        excess = pnls - rf
        std = np.std(excess)
        if std <= 0:
            return 0.0
        return float(np.mean(excess) / std * np.sqrt(252 / 10))

    def _max_consecutive_losses(self, pnls: np.ndarray) -> int:
        """최대 연속 손실 횟수."""
        max_streak = 0
        current = 0
        for p in pnls:
            if p < 0:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak


# ── 텔레그램 포맷 ─────────────────────────────────────────────────

def format_monte_carlo(result: MonteCarloResult) -> str:
    """Monte Carlo 결과 텔레그램 포맷."""
    if result.n_simulations == 0:
        return "⚠️ Monte Carlo 시뮬레이션 실행 불가 (거래 데이터 부족)"

    prob_emoji = "🟢" if result.probability_positive >= 60 else "🟡" if result.probability_positive >= 40 else "🔴"

    return "\n".join([
        "🎲 Monte Carlo 시뮬레이션",
        "━" * 25,
        f"시뮬레이션: {result.n_simulations:,}회 × {result.n_trades}거래",
        "",
        f"📊 수익률 분포",
        f"  중앙값: {result.median_return_pct:+.1f}%",
        f"  평균:   {result.mean_return_pct:+.1f}% (±{result.std_return_pct:.1f}%)",
        "",
        f"📈 시나리오 분석",
        f"  🔴 최악 (5%):  {result.percentile_5:+.1f}%",
        f"  🟡 약세 (25%): {result.percentile_25:+.1f}%",
        f"  🟢 강세 (75%): {result.percentile_75:+.1f}%",
        f"  🚀 최선 (95%): {result.percentile_95:+.1f}%",
        "",
        f"{prob_emoji} 수익 확률: {result.probability_positive:.0f}%",
        f"🎯 목표 달성 확률: {result.probability_target:.0f}%",
        f"📉 VaR(95%): {result.var_95:+.1f}%",
    ])


def format_walk_forward(result: WalkForwardResult) -> str:
    """Walk-Forward 결과 텔레그램 포맷."""
    if result.n_windows == 0:
        return "⚠️ Walk-Forward 분석 불가 (거래 데이터 부족)"

    robustness_emoji = {
        "robust": "🟢 견고함",
        "moderate": "🟡 보통",
        "fragile": "🔴 취약",
    }

    lines = [
        "🔄 Walk-Forward 교차검증",
        "━" * 25,
        f"윈도우: {result.n_windows}개",
        "",
        f"학습 Sharpe: {result.avg_train_sharpe:.2f}",
        f"검증 Sharpe: {result.avg_test_sharpe:.2f}",
        f"Sharpe 감소: {result.sharpe_decay_pct:.0f}%",
        f"일관성: {result.consistency_score:.0%}",
        f"로버스트니스: {robustness_emoji.get(result.robustness, result.robustness)}",
    ]

    # 윈도우별 상세
    if result.window_results:
        lines.extend(["", "📊 윈도우별 성과"])
        for w in result.window_results[:5]:
            test_emoji = "✅" if w["test_sharpe"] > 0 else "❌"
            lines.append(
                f"  {test_emoji} W{w['window']}: "
                f"학습 {w['train_sharpe']:.2f} → 검증 {w['test_sharpe']:.2f} "
                f"({w['test_return']:+.1f}%)"
            )

    return "\n".join(lines)


def format_strategy_comparison(comp: StrategyComparison) -> str:
    """전략 비교표 텔레그램 포맷."""
    strategy_names = {
        "A": "🔥 단기반등", "B": "⚡ ETF레버리지", "C": "🏦 장기우량주",
        "D": "🔄 섹터로테이션", "E": "🌎 글로벌분산", "F": "🚀 모멘텀", "G": "💥 돌파",
    }

    lines = [
        "📊 전략 성과 비교",
        "━" * 25,
    ]

    for rank, (code, sharpe) in enumerate(comp.ranking, 1):
        s = comp.strategies.get(code, {})
        name = strategy_names.get(code, code)
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"{rank}."

        lines.append(
            f"{medal} {name}\n"
            f"   승률 {s.get('win_rate', 0):.0f}% | "
            f"Sharpe {s.get('sharpe', 0):.2f} | "
            f"수익 {s.get('total_return', 0):+.1f}%"
        )

    lines.extend([
        "",
        f"🏆 최적 전략: {strategy_names.get(comp.best_strategy, comp.best_strategy)}",
        f"   Sharpe {comp.best_sharpe:.2f}",
    ])

    return "\n".join(lines)


def format_risk_metrics(metrics: RiskAdjustedMetrics) -> str:
    """리스크 지표 텔레그램 포맷."""
    return "\n".join([
        "📐 리스크 조정 수익률",
        "━" * 25,
        f"Sharpe:   {metrics.sharpe_ratio:.2f}",
        f"Sortino:  {metrics.sortino_ratio:.2f}",
        f"Calmar:   {metrics.calmar_ratio:.2f}",
        f"Omega:    {metrics.omega_ratio:.2f}",
        f"Recovery: {metrics.recovery_factor:.2f}",
        "",
        f"📉 최대 연속 손실: {metrics.max_consecutive_losses}회",
    ])
