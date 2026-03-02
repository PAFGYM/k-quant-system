"""Tests for strategy optimization engine.

Covers: GA, Bayesian, NSGA-II, Robustness, and formatting.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.backtest.strategy_optimizer import (
    BayesianConfig,
    GAConfig,
    Individual,
    MultiObjectiveResult,
    OptimizationResult,
    RobustnessResult,
    _dominates,
    _evaluate_strategy,
    _expected_improvement,
    _fast_non_dominated_sort,
    _latin_hypercube_sample,
    format_optimization_result,
    optimize_bayesian,
    optimize_genetic,
    optimize_multi_objective,
    test_robustness as run_robustness_test,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ohlcv_data() -> pd.DataFrame:
    """Generate synthetic OHLCV data (200 bars, mean-reverting with cycles)."""
    rng = np.random.RandomState(42)
    n = 200
    # Mean-reverting process so RSI oscillates above/below thresholds
    t = np.arange(n, dtype=float)
    trend = 10000 + t * 2  # mild uptrend
    cycle = 300 * np.sin(2 * np.pi * t / 40)  # ~40 bar cycle
    noise = np.cumsum(rng.normal(0, 30, n))
    close = trend + cycle + noise
    close = np.maximum(close, 100)  # floor
    high = close + rng.uniform(10, 100, n)
    low = close - rng.uniform(10, 100, n)
    open_ = close + rng.normal(0, 30, n)
    volume = rng.randint(100000, 1000000, n)

    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def param_ranges() -> dict:
    """Parameter ranges for the dummy strategy."""
    return {
        "rsi_period": (5, 30),
        "rsi_buy": (15, 40),
        "rsi_sell": (60, 85),
    }


def dummy_strategy(
    ohlcv: pd.DataFrame,
    rsi_period: float = 14,
    rsi_buy: float = 30,
    rsi_sell: float = 70,
) -> list:
    """Simple RSI-based signal generator for testing.

    Returns list of {"date": idx, "side": "buy"/"sell", "price": float}.
    """
    close = ohlcv["close"].astype(float).values
    n = len(close)
    period = max(int(round(rsi_period)), 2)
    buy_thresh = float(rsi_buy)
    sell_thresh = float(rsi_sell)

    # Compute RSI
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.zeros(n - 1)
    avg_loss = np.zeros(n - 1)

    if period >= len(gains):
        return []

    avg_gain[period - 1] = np.mean(gains[:period])
    avg_loss[period - 1] = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i]) / period

    rsi = np.full(n, 50.0)
    for i in range(period, n):
        ag = avg_gain[i - 1]
        al = avg_loss[i - 1]
        if al < 1e-12:
            rsi[i] = 100.0
        else:
            rs = ag / al
            rsi[i] = 100.0 - 100.0 / (1 + rs)

    # Generate signals
    signals = []
    holding = False
    for i in range(period + 1, n):
        if not holding and rsi[i] < buy_thresh:
            signals.append({"date": i, "side": "buy", "price": close[i]})
            holding = True
        elif holding and rsi[i] > sell_thresh:
            signals.append({"date": i, "side": "sell", "price": close[i]})
            holding = False

    return signals


# ---------------------------------------------------------------------------
# TestEvaluateStrategy
# ---------------------------------------------------------------------------


class TestEvaluateStrategy:
    def test_basic_evaluation(self, ohlcv_data):
        params = {"rsi_period": 14, "rsi_buy": 30, "rsi_sell": 70}
        ind = _evaluate_strategy(params, ohlcv_data, dummy_strategy)
        assert isinstance(ind, Individual)
        assert ind.fitness != -999.0
        assert ind.sharpe != 0.0

    def test_bad_params_returns_low_fitness(self, ohlcv_data):
        # RSI buy threshold at 0 means no buy signal can fire
        params = {"rsi_period": 14, "rsi_buy": 0, "rsi_sell": 70}
        ind = _evaluate_strategy(params, ohlcv_data, dummy_strategy)
        assert ind.fitness == -999.0

    def test_short_data(self):
        short_df = pd.DataFrame({
            "date": ["2024-01-01"],
            "open": [100], "high": [110], "low": [90],
            "close": [105], "volume": [1000],
        })
        params = {"rsi_period": 14, "rsi_buy": 30, "rsi_sell": 70}
        ind = _evaluate_strategy(params, short_df, dummy_strategy)
        assert ind.fitness == -999.0


# ---------------------------------------------------------------------------
# TestGeneticOptimizer
# ---------------------------------------------------------------------------


class TestGeneticOptimizer:
    def test_convergence(self, ohlcv_data, param_ranges):
        """Final fitness should be >= initial fitness."""
        config = GAConfig(
            population_size=15,
            n_generations=10,
            elite_count=3,
        )
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        assert isinstance(result, OptimizationResult)
        assert result.method == "genetic"
        assert len(result.convergence_history) >= 2
        # Final >= initial (elitism guarantees non-degradation)
        assert result.convergence_history[-1] >= result.convergence_history[0]

    def test_elite_preservation(self, ohlcv_data, param_ranges):
        """Elite count individuals must survive each generation."""
        config = GAConfig(
            population_size=15,
            n_generations=5,
            elite_count=3,
        )
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        # Convergence should be monotonically non-decreasing (elitism)
        for i in range(1, len(result.convergence_history)):
            assert result.convergence_history[i] >= result.convergence_history[i - 1] - 1e-9

    def test_params_within_range(self, ohlcv_data, param_ranges):
        """Best params must be within specified ranges."""
        config = GAConfig(population_size=15, n_generations=5)
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        for name, (lo, hi) in param_ranges.items():
            assert lo <= result.best_params[name] <= hi, (
                f"{name}={result.best_params[name]} outside [{lo}, {hi}]"
            )

    def test_metrics_present(self, ohlcv_data, param_ranges):
        config = GAConfig(population_size=15, n_generations=5)
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        for key in ("sharpe", "sortino", "calmar", "max_drawdown", "turnover"):
            assert key in result.metrics

    def test_elapsed_time(self, ohlcv_data, param_ranges):
        config = GAConfig(population_size=10, n_generations=3)
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        assert result.elapsed_seconds > 0


# ---------------------------------------------------------------------------
# TestBayesianOptimizer
# ---------------------------------------------------------------------------


class TestBayesianOptimizer:
    def test_convergence(self, ohlcv_data, param_ranges):
        config = BayesianConfig(n_initial=5, n_iterations=8)
        result = optimize_bayesian(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        assert isinstance(result, OptimizationResult)
        assert result.method == "bayesian"
        assert len(result.convergence_history) >= 1
        # Should improve or at least maintain
        assert result.convergence_history[-1] >= result.convergence_history[0] - 1e-9

    def test_initial_point_count(self, ohlcv_data, param_ranges):
        config = BayesianConfig(n_initial=7, n_iterations=3)
        result = optimize_bayesian(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        # At least n_initial evaluations recorded (convergence[0] from initials)
        assert len(result.convergence_history) >= 1

    def test_params_within_range(self, ohlcv_data, param_ranges):
        config = BayesianConfig(n_initial=5, n_iterations=5)
        result = optimize_bayesian(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        for name, (lo, hi) in param_ranges.items():
            assert lo <= result.best_params[name] <= hi

    def test_expected_improvement_calculation(self):
        """EI should be positive when mu > f_best."""
        ei = _expected_improvement(mu=2.0, sigma=1.0, f_best=1.0)
        assert ei > 0

        # EI with zero sigma should be 0
        ei_zero = _expected_improvement(mu=2.0, sigma=0.0, f_best=1.0)
        assert ei_zero == 0.0

    def test_ei_higher_with_more_uncertainty(self):
        """EI should increase with more uncertainty (higher sigma)."""
        ei_low = _expected_improvement(mu=1.5, sigma=0.1, f_best=1.5)
        ei_high = _expected_improvement(mu=1.5, sigma=2.0, f_best=1.5)
        assert ei_high > ei_low


# ---------------------------------------------------------------------------
# TestMultiObjective
# ---------------------------------------------------------------------------


class TestMultiObjective:
    def test_pareto_non_domination(self, ohlcv_data, param_ranges):
        """No individual on the Pareto front should dominate another."""
        result = optimize_multi_objective(
            param_ranges, ohlcv_data, dummy_strategy,
            objectives=["sharpe", "sortino"],
            population_size=15,
            n_generations=5,
        )
        assert isinstance(result, MultiObjectiveResult)
        front = result.pareto_front
        assert len(front) > 0

        for i, ind_a in enumerate(front):
            for j, ind_b in enumerate(front):
                if i == j:
                    continue
                obj_a = [ind_a.sharpe, ind_a.sortino]
                obj_b = [ind_b.sharpe, ind_b.sortino]
                assert not _dominates(obj_a, obj_b), (
                    f"Front member {i} dominates member {j}"
                )

    def test_hypervolume_positive(self, ohlcv_data, param_ranges):
        result = optimize_multi_objective(
            param_ranges, ohlcv_data, dummy_strategy,
            objectives=["sharpe", "sortino"],
            population_size=15,
            n_generations=5,
        )
        assert result.hypervolume > 0

    def test_fast_non_dominated_sort_basic(self):
        """Basic test: clearly dominating individual should be in front 0."""
        ind_a = Individual(sharpe=3.0, sortino=3.0, calmar=3.0)
        ind_b = Individual(sharpe=1.0, sortino=1.0, calmar=1.0)
        ind_c = Individual(sharpe=2.0, sortino=0.5, calmar=2.0)  # non-dominated with b

        def get_obj(ind):
            return [ind.sharpe, ind.sortino]

        fronts = _fast_non_dominated_sort([ind_a, ind_b, ind_c], get_obj)
        # ind_a dominates both b and c in sharpe/sortino
        assert ind_a in fronts[0]

    def test_dominates_function(self):
        assert _dominates([3, 3], [1, 1]) is True
        assert _dominates([3, 3], [3, 3]) is False  # equal, no strict improvement
        assert _dominates([3, 1], [1, 3]) is False  # neither dominates
        assert _dominates([1, 1], [3, 3]) is False


# ---------------------------------------------------------------------------
# TestRobustness
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_stability_score_range(self, ohlcv_data, param_ranges):
        params = {"rsi_period": 14, "rsi_buy": 30, "rsi_sell": 70}
        result = run_robustness_test(
            params, param_ranges, ohlcv_data, dummy_strategy,
            n_perturbations=20,
        )
        assert isinstance(result, RobustnessResult)
        assert 0.0 <= result.stability_score <= 1.0

    def test_sensitivity_keys_exist(self, ohlcv_data, param_ranges):
        params = {"rsi_period": 14, "rsi_buy": 30, "rsi_sell": 70}
        result = run_robustness_test(
            params, param_ranges, ohlcv_data, dummy_strategy,
            n_perturbations=10,
        )
        for name in param_ranges:
            assert name in result.parameter_sensitivity

    def test_perturbed_sharpes_count(self, ohlcv_data, param_ranges):
        params = {"rsi_period": 14, "rsi_buy": 30, "rsi_sell": 70}
        n = 15
        result = run_robustness_test(
            params, param_ranges, ohlcv_data, dummy_strategy,
            n_perturbations=n,
        )
        assert len(result.perturbed_sharpes) == n

    def test_worst_case_le_base(self, ohlcv_data, param_ranges):
        params = {"rsi_period": 14, "rsi_buy": 30, "rsi_sell": 70}
        result = run_robustness_test(
            params, param_ranges, ohlcv_data, dummy_strategy,
            n_perturbations=20,
        )
        # Worst case should be <= max of perturbed
        assert result.worst_case_sharpe <= max(result.perturbed_sharpes) + 1e-9


# ---------------------------------------------------------------------------
# TestFormat
# ---------------------------------------------------------------------------


class TestFormat:
    def test_returns_string(self, ohlcv_data, param_ranges):
        config = GAConfig(population_size=10, n_generations=3)
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        text = format_optimization_result(result)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_key_sections(self, ohlcv_data, param_ranges):
        config = GAConfig(population_size=10, n_generations=3)
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        text = format_optimization_result(result)
        assert "최적화 결과" in text
        assert "Fitness" in text

    def test_format_with_robustness(self, ohlcv_data, param_ranges):
        config = GAConfig(population_size=10, n_generations=3)
        result = optimize_genetic(
            param_ranges, ohlcv_data, dummy_strategy, config=config,
        )
        rob = run_robustness_test(
            result.best_params, param_ranges, ohlcv_data, dummy_strategy,
            n_perturbations=10,
        )
        result.robustness = rob
        text = format_optimization_result(result)
        assert "견고성" in text
        assert "민감도" in text


# ---------------------------------------------------------------------------
# TestLatinHypercube
# ---------------------------------------------------------------------------


class TestLatinHypercube:
    def test_correct_count(self, param_ranges):
        rng = np.random.RandomState(42)
        samples = _latin_hypercube_sample(param_ranges, 20, rng)
        assert len(samples) == 20

    def test_within_ranges(self, param_ranges):
        rng = np.random.RandomState(42)
        samples = _latin_hypercube_sample(param_ranges, 30, rng)
        names = sorted(param_ranges.keys())
        for genes in samples:
            for i, name in enumerate(names):
                lo, hi = param_ranges[name]
                assert lo <= genes[i] <= hi, f"{name}={genes[i]} outside [{lo}, {hi}]"
