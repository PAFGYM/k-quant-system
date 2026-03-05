"""Strategy optimization engine — GA, Bayesian, NSGA-II, Robustness.

Provides four optimization methods for trading strategy parameter tuning:
  1. Genetic Algorithm with BLX-alpha crossover and Latin Hypercube init
  2. Bayesian Optimization with GP surrogate and Expected Improvement
  3. NSGA-II multi-objective optimization with Pareto front
  4. Monte Carlo robustness testing with parameter sensitivity

Dependencies: numpy, scipy (scipy.stats only), pandas — no new packages.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Individual:
    """A single candidate solution in the population."""

    genes: List[float] = field(default_factory=list)
    fitness: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown: float = 0.0
    turnover: float = 0.0
    generation: int = 0


@dataclass
class GAConfig:
    """Configuration for Genetic Algorithm optimizer."""

    population_size: int = 50
    n_generations: int = 100
    crossover_rate: float = 0.8
    mutation_rate: float = 0.1
    elite_count: int = 5
    tournament_size: int = 3


@dataclass
class BayesianPoint:
    """A single evaluated point in Bayesian optimization."""

    params: Dict[str, float] = field(default_factory=dict)
    expected_improvement: float = 0.0
    mean: float = 0.0
    std: float = 0.0


@dataclass
class BayesianConfig:
    """Configuration for Bayesian optimizer."""

    n_initial: int = 10
    n_iterations: int = 50
    exploration_weight: float = 2.0  # kappa for EI


@dataclass
class MultiObjectiveResult:
    """Result of NSGA-II multi-objective optimization."""

    pareto_front: List[Individual] = field(default_factory=list)
    dominated: List[Individual] = field(default_factory=list)
    hypervolume: float = 0.0


@dataclass
class RobustnessResult:
    """Result of Monte Carlo robustness testing."""

    base_params: Dict[str, float] = field(default_factory=dict)
    base_sharpe: float = 0.0
    perturbed_sharpes: List[float] = field(default_factory=list)
    stability_score: float = 0.0  # 0~1
    worst_case_sharpe: float = 0.0
    parameter_sensitivity: Dict[str, float] = field(default_factory=dict)


@dataclass
class OptimizationResult:
    """Unified result container for all optimization methods."""

    method: str = ""
    best_params: Dict[str, float] = field(default_factory=dict)
    best_fitness: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    convergence_history: List[float] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    robustness: Optional[RobustnessResult] = None


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------


def _evaluate_strategy(
    params: dict,
    ohlcv: pd.DataFrame,
    strategy_fn: Callable,
) -> Individual:
    """Evaluate a strategy with given parameters on OHLCV data.

    The strategy_fn(ohlcv, **params) must return a list of dicts:
      [{"date": idx, "side": "buy"/"sell", "price": float}, ...]

    Returns an Individual with computed metrics.
    """
    close = ohlcv["close"].astype(float).values
    n = len(close)

    if n < 20:
        return Individual(genes=list(params.values()), fitness=-999.0)

    # Execute strategy
    try:
        signals = strategy_fn(ohlcv, **params)
    except Exception:
        logger.debug("Strategy evaluation failed for params=%s", params)
        return Individual(genes=list(params.values()), fitness=-999.0)

    if not signals:
        return Individual(genes=list(params.values()), fitness=-999.0)

    # Pair buy/sell signals into trades
    trades_pnl: List[float] = []
    holding = False
    entry_price = 0.0

    for sig in signals:
        side = sig.get("side", "")
        price = sig.get("price", 0.0)
        if price <= 0:
            continue

        if side == "buy" and not holding:
            entry_price = price
            holding = True
        elif side == "sell" and holding:
            pnl = (price - entry_price) / entry_price
            trades_pnl.append(pnl)
            holding = False

    # Close open position at last price
    if holding and close[-1] > 0:
        pnl = (close[-1] - entry_price) / entry_price
        trades_pnl.append(pnl)

    if not trades_pnl:
        return Individual(genes=list(params.values()), fitness=-999.0)

    pnl_arr = np.array(trades_pnl, dtype=float)
    mean_ret = float(np.mean(pnl_arr))
    std_ret = float(np.std(pnl_arr))

    # Sharpe (annualized approx: sqrt(252 / avg_trade_days))
    sharpe = mean_ret / std_ret * np.sqrt(252 / max(10, n // max(len(trades_pnl), 1))) if std_ret > 1e-12 else 0.0

    # Sortino
    downside = pnl_arr[pnl_arr < 0]
    down_std = float(np.std(downside)) if len(downside) > 0 else 1e-12
    sortino = mean_ret / down_std * np.sqrt(252 / max(10, n // max(len(trades_pnl), 1))) if down_std > 1e-12 else 0.0

    # Max drawdown from cumulative returns
    cum = np.cumprod(1 + pnl_arr)
    running_max = np.maximum.accumulate(cum)
    dd = (cum - running_max) / np.where(running_max > 0, running_max, 1.0)
    max_drawdown = float(np.min(dd)) if len(dd) > 0 else 0.0  # negative

    # Calmar = annualized return / abs(max_drawdown)
    total_ret = float(cum[-1] - 1) if len(cum) > 0 else 0.0
    ann_ret = total_ret * (252 / max(n, 1))
    calmar = ann_ret / abs(max_drawdown) if abs(max_drawdown) > 1e-12 else 0.0

    # Turnover = number of round-trip trades / trading days
    turnover = len(trades_pnl) / max(n, 1)

    # Composite fitness
    fitness = 0.4 * sharpe + 0.3 * sortino + 0.2 * calmar - 0.1 * abs(max_drawdown)

    ind = Individual(
        genes=list(params.values()),
        fitness=round(fitness, 6),
        sharpe=round(sharpe, 4),
        sortino=round(sortino, 4),
        calmar=round(calmar, 4),
        max_drawdown=round(max_drawdown, 4),
        turnover=round(turnover, 6),
    )
    return ind


# ---------------------------------------------------------------------------
# Helpers — parameter encoding
# ---------------------------------------------------------------------------


def _param_names(param_ranges: dict) -> List[str]:
    """Sorted parameter names for consistent gene ordering."""
    return sorted(param_ranges.keys())


def _genes_to_params(genes: List[float], param_ranges: dict) -> dict:
    """Convert gene list to parameter dict, clipping to ranges."""
    names = _param_names(param_ranges)
    params: Dict[str, float] = {}
    for i, name in enumerate(names):
        lo, hi = param_ranges[name]
        val = float(np.clip(genes[i], lo, hi))
        params[name] = val
    return params


def _params_to_genes(params: dict, param_ranges: dict) -> List[float]:
    """Convert parameter dict to gene list."""
    names = _param_names(param_ranges)
    return [params.get(name, (param_ranges[name][0] + param_ranges[name][1]) / 2) for name in names]


# ---------------------------------------------------------------------------
# Latin Hypercube Sampling
# ---------------------------------------------------------------------------


def _latin_hypercube_sample(
    param_ranges: dict,
    n_samples: int,
    rng: np.random.RandomState,
) -> List[List[float]]:
    """Generate Latin Hypercube samples for initial population."""
    names = _param_names(param_ranges)
    n_dims = len(names)
    result: List[List[float]] = []

    # Create stratified intervals per dimension
    intervals = np.zeros((n_dims, n_samples))
    for d in range(n_dims):
        perm = rng.permutation(n_samples)
        for i in range(n_samples):
            lo, hi = param_ranges[names[d]]
            low_frac = perm[i] / n_samples
            high_frac = (perm[i] + 1) / n_samples
            u = rng.uniform(low_frac, high_frac)
            intervals[d, i] = lo + u * (hi - lo)

    for i in range(n_samples):
        genes = [float(intervals[d, i]) for d in range(n_dims)]
        result.append(genes)

    return result


# ---------------------------------------------------------------------------
# Sobol-like low-discrepancy sequence
# ---------------------------------------------------------------------------


def _sobol_like_sample(
    param_ranges: dict,
    n_samples: int,
    rng: np.random.RandomState,
) -> List[dict]:
    """Generate quasi-random low-discrepancy initial points.

    Uses a simple additive recurrence (R-sequence by Martin Roberts)
    as a numpy-only Sobol approximation.
    """
    names = _param_names(param_ranges)
    n_dims = len(names)
    points: List[dict] = []

    # Golden ratio generalization for d dimensions
    phi = 1.0
    for _ in range(20):  # Newton iterations to find phi_d
        phi = pow(1 + phi, 1.0 / (n_dims + 1))
    alphas = np.array([pow(1.0 / phi, d + 1) for d in range(n_dims)])

    seed_offset = rng.uniform(0, 1, size=n_dims)

    for i in range(n_samples):
        raw = (seed_offset + alphas * (i + 1)) % 1.0
        params: Dict[str, float] = {}
        for d, name in enumerate(names):
            lo, hi = param_ranges[name]
            params[name] = lo + raw[d] * (hi - lo)
        points.append(params)

    return points


# ---------------------------------------------------------------------------
# 1. Genetic Algorithm
# ---------------------------------------------------------------------------


def optimize_genetic(
    param_ranges: dict,
    ohlcv: pd.DataFrame,
    strategy_fn: Callable,
    config: Optional[GAConfig] = None,
    seed: int = 42,
) -> OptimizationResult:
    """Optimize strategy parameters using a Genetic Algorithm.

    Features:
      - Latin Hypercube Sampling for initial population
      - Tournament selection (k=3)
      - BLX-alpha crossover (alpha=0.5) for continuous params
      - Gaussian mutation (sigma = 0.1 * range)
      - Elitism: top N preserved
      - Early stopping: 20 generations without improvement
    """
    t0 = time.time()
    cfg = config or GAConfig()
    rng = np.random.RandomState(seed)
    names = _param_names(param_ranges)
    n_dims = len(names)

    # ---- Initial population via LHS ----
    lhs_genes = _latin_hypercube_sample(param_ranges, cfg.population_size, rng)
    population: List[Individual] = []
    for genes in lhs_genes:
        params = _genes_to_params(genes, param_ranges)
        ind = _evaluate_strategy(params, ohlcv, strategy_fn)
        ind.genes = genes
        ind.generation = 0
        population.append(ind)

    convergence: List[float] = []
    best_ever_fitness = max(ind.fitness for ind in population)
    convergence.append(best_ever_fitness)
    stale_count = 0

    for gen in range(1, cfg.n_generations + 1):
        # Sort by fitness descending
        population.sort(key=lambda x: x.fitness, reverse=True)

        # Elitism
        next_gen: List[Individual] = []
        for elite in population[: cfg.elite_count]:
            elite_copy = Individual(
                genes=list(elite.genes),
                fitness=elite.fitness,
                sharpe=elite.sharpe,
                sortino=elite.sortino,
                calmar=elite.calmar,
                max_drawdown=elite.max_drawdown,
                turnover=elite.turnover,
                generation=elite.generation,
            )
            next_gen.append(elite_copy)

        # Fill rest via selection + crossover + mutation
        while len(next_gen) < cfg.population_size:
            # Tournament selection
            parent_a = _tournament_select(population, cfg.tournament_size, rng)
            parent_b = _tournament_select(population, cfg.tournament_size, rng)

            # BLX-alpha crossover
            if rng.random() < cfg.crossover_rate:
                child_genes = _blx_alpha_crossover(
                    parent_a.genes, parent_b.genes, param_ranges, rng, alpha=0.5,
                )
            else:
                child_genes = list(parent_a.genes)

            # Gaussian mutation
            child_genes = _gaussian_mutate(
                child_genes, param_ranges, cfg.mutation_rate, rng,
            )

            params = _genes_to_params(child_genes, param_ranges)
            child = _evaluate_strategy(params, ohlcv, strategy_fn)
            child.genes = child_genes
            child.generation = gen
            next_gen.append(child)

        population = next_gen

        gen_best = max(ind.fitness for ind in population)
        convergence.append(gen_best)

        if gen_best > best_ever_fitness + 1e-8:
            best_ever_fitness = gen_best
            stale_count = 0
        else:
            stale_count += 1

        # Early stopping
        if stale_count >= 20:
            logger.info("GA early stopping at generation %d (20 stale)", gen)
            break

    # Best individual
    population.sort(key=lambda x: x.fitness, reverse=True)
    best = population[0]
    best_params = _genes_to_params(best.genes, param_ranges)

    return OptimizationResult(
        method="genetic",
        best_params=best_params,
        best_fitness=best.fitness,
        metrics={
            "sharpe": best.sharpe,
            "sortino": best.sortino,
            "calmar": best.calmar,
            "max_drawdown": best.max_drawdown,
            "turnover": best.turnover,
        },
        convergence_history=convergence,
        elapsed_seconds=max(time.time() - t0, 0.001),
    )


def _tournament_select(
    population: List[Individual],
    k: int,
    rng: np.random.RandomState,
) -> Individual:
    """Tournament selection: pick k random individuals, return the best."""
    indices = rng.choice(len(population), size=min(k, len(population)), replace=False)
    candidates = [population[i] for i in indices]
    return max(candidates, key=lambda x: x.fitness)


def _blx_alpha_crossover(
    parent_a: List[float],
    parent_b: List[float],
    param_ranges: dict,
    rng: np.random.RandomState,
    alpha: float = 0.5,
) -> List[float]:
    """BLX-alpha crossover for continuous parameters."""
    names = _param_names(param_ranges)
    child: List[float] = []
    for i in range(len(parent_a)):
        lo_gene = min(parent_a[i], parent_b[i])
        hi_gene = max(parent_a[i], parent_b[i])
        d = hi_gene - lo_gene
        new_lo = lo_gene - alpha * d
        new_hi = hi_gene + alpha * d
        # Clip to param range
        p_lo, p_hi = param_ranges[names[i]]
        new_lo = max(new_lo, p_lo)
        new_hi = min(new_hi, p_hi)
        child.append(float(rng.uniform(new_lo, new_hi)))
    return child


def _gaussian_mutate(
    genes: List[float],
    param_ranges: dict,
    mutation_rate: float,
    rng: np.random.RandomState,
) -> List[float]:
    """Gaussian perturbation mutation."""
    names = _param_names(param_ranges)
    mutated: List[float] = []
    for i, gene in enumerate(genes):
        if rng.random() < mutation_rate:
            lo, hi = param_ranges[names[i]]
            sigma = 0.1 * (hi - lo)
            new_val = gene + rng.normal(0, sigma)
            new_val = float(np.clip(new_val, lo, hi))
            mutated.append(new_val)
        else:
            mutated.append(gene)
    return mutated


# ---------------------------------------------------------------------------
# 2. Bayesian Optimization (GP surrogate, numpy-only)
# ---------------------------------------------------------------------------


def optimize_bayesian(
    param_ranges: dict,
    ohlcv: pd.DataFrame,
    strategy_fn: Callable,
    config: Optional[BayesianConfig] = None,
    seed: int = 42,
) -> OptimizationResult:
    """Optimize using Bayesian Optimization with GP surrogate.

    Features:
      - RBF kernel Gaussian Process (numpy implementation)
      - Expected Improvement acquisition function
      - Sobol-like quasi-random initial points
      - Convergence: stop when EI < 1e-6
    """
    t0 = time.time()
    cfg = config or BayesianConfig()
    rng = np.random.RandomState(seed)
    names = _param_names(param_ranges)

    # ---- Initial points via Sobol-like sequence ----
    initial_points = _sobol_like_sample(param_ranges, cfg.n_initial, rng)
    X_observed: List[np.ndarray] = []
    y_observed: List[float] = []

    for params in initial_points:
        ind = _evaluate_strategy(params, ohlcv, strategy_fn)
        genes = _params_to_genes(params, param_ranges)
        X_observed.append(np.array(genes))
        y_observed.append(ind.fitness)

    convergence: List[float] = [max(y_observed)]

    # ---- Iterative optimization ----
    for iteration in range(cfg.n_iterations):
        X = np.array(X_observed)
        y = np.array(y_observed)
        f_best = float(np.max(y))

        # Fit GP
        K = _rbf_kernel(X, X) + 1e-6 * np.eye(len(X))
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            K += 1e-4 * np.eye(len(X))
            L = np.linalg.cholesky(K)

        alpha_gp = np.linalg.solve(L.T, np.linalg.solve(L, y))

        # Find next point by maximizing EI over random candidates
        n_candidates = 500
        best_ei = -1.0
        best_candidate = None

        for _ in range(n_candidates):
            cand_genes = []
            for name in names:
                lo, hi = param_ranges[name]
                cand_genes.append(rng.uniform(lo, hi))
            x_cand = np.array(cand_genes)

            # GP prediction
            k_star = _rbf_kernel(X, x_cand.reshape(1, -1)).flatten()
            mu = float(np.dot(k_star, alpha_gp))
            v = np.linalg.solve(L, k_star)
            k_ss = float(_rbf_kernel(x_cand.reshape(1, -1), x_cand.reshape(1, -1))[0, 0])
            sigma2 = max(k_ss - np.dot(v, v), 1e-12)
            sigma = np.sqrt(sigma2)

            # Expected Improvement
            ei = _expected_improvement(mu, sigma, f_best, kappa=cfg.exploration_weight)

            if ei > best_ei:
                best_ei = ei
                best_candidate = cand_genes

        # Convergence check
        if best_ei < 1e-6:
            logger.info("Bayesian opt converged at iteration %d (EI=%.2e)", iteration, best_ei)
            break

        # Evaluate best candidate
        params = _genes_to_params(best_candidate, param_ranges)
        ind = _evaluate_strategy(params, ohlcv, strategy_fn)
        X_observed.append(np.array(best_candidate))
        y_observed.append(ind.fitness)
        convergence.append(max(y_observed))

    # Best result
    best_idx = int(np.argmax(y_observed))
    best_params = _genes_to_params(list(X_observed[best_idx]), param_ranges)
    best_ind = _evaluate_strategy(best_params, ohlcv, strategy_fn)

    return OptimizationResult(
        method="bayesian",
        best_params=best_params,
        best_fitness=best_ind.fitness,
        metrics={
            "sharpe": best_ind.sharpe,
            "sortino": best_ind.sortino,
            "calmar": best_ind.calmar,
            "max_drawdown": best_ind.max_drawdown,
            "turnover": best_ind.turnover,
        },
        convergence_history=convergence,
        elapsed_seconds=max(time.time() - t0, 0.001),
    )


def _rbf_kernel(
    X1: np.ndarray,
    X2: np.ndarray,
    length_scale: float = 1.0,
    variance: float = 1.0,
) -> np.ndarray:
    """Radial Basis Function (squared exponential) kernel."""
    # X1: (n, d), X2: (m, d) -> (n, m)
    if X1.ndim == 1:
        X1 = X1.reshape(1, -1)
    if X2.ndim == 1:
        X2 = X2.reshape(1, -1)
    sqdist = np.sum(X1 ** 2, axis=1, keepdims=True) \
        - 2 * X1 @ X2.T \
        + np.sum(X2 ** 2, axis=1, keepdims=True).T
    sqdist = np.maximum(sqdist, 0.0)
    return variance * np.exp(-0.5 * sqdist / (length_scale ** 2))


def _expected_improvement(
    mu: float,
    sigma: float,
    f_best: float,
    kappa: float = 2.0,
) -> float:
    """Expected Improvement acquisition function.

    EI = (mu - f_best) * Phi(z) + sigma * phi(z)
    where z = (mu - f_best) / sigma
    """
    if sigma < 1e-12:
        return 0.0
    z = (mu - f_best) / sigma
    ei = (mu - f_best) * norm.cdf(z) + sigma * norm.pdf(z)
    return max(float(ei), 0.0)


# ---------------------------------------------------------------------------
# 3. NSGA-II Multi-Objective Optimization
# ---------------------------------------------------------------------------


def optimize_multi_objective(
    param_ranges: dict,
    ohlcv: pd.DataFrame,
    strategy_fn: Callable,
    objectives: Optional[List[str]] = None,
    population_size: int = 50,
    n_generations: int = 80,
    seed: int = 42,
) -> MultiObjectiveResult:
    """Multi-objective optimization using NSGA-II.

    Features:
      - Fast non-dominated sorting
      - Crowding distance assignment
      - Pareto front extraction
      - Hypervolume indicator computation
    """
    if objectives is None:
        objectives = ["sharpe", "sortino", "calmar"]

    rng = np.random.RandomState(seed)
    names = _param_names(param_ranges)

    def _get_objectives(ind: Individual) -> List[float]:
        """Extract objective values (all maximized)."""
        vals = []
        for obj in objectives:
            vals.append(getattr(ind, obj, 0.0))
        return vals

    # ---- Initial population via LHS ----
    lhs_genes = _latin_hypercube_sample(param_ranges, population_size, rng)
    population: List[Individual] = []
    for genes in lhs_genes:
        params = _genes_to_params(genes, param_ranges)
        ind = _evaluate_strategy(params, ohlcv, strategy_fn)
        ind.genes = genes
        ind.generation = 0
        population.append(ind)

    for gen in range(1, n_generations + 1):
        # Generate offspring
        offspring: List[Individual] = []
        while len(offspring) < population_size:
            p1 = _tournament_select(population, 3, rng)
            p2 = _tournament_select(population, 3, rng)
            if rng.random() < 0.8:
                child_genes = _blx_alpha_crossover(
                    p1.genes, p2.genes, param_ranges, rng,
                )
            else:
                child_genes = list(p1.genes)
            child_genes = _gaussian_mutate(child_genes, param_ranges, 0.1, rng)
            params = _genes_to_params(child_genes, param_ranges)
            child = _evaluate_strategy(params, ohlcv, strategy_fn)
            child.genes = child_genes
            child.generation = gen
            offspring.append(child)

        # Combine parent + offspring
        combined = population + offspring

        # Fast non-dominated sorting
        fronts = _fast_non_dominated_sort(combined, _get_objectives)

        # Select next population
        next_pop: List[Individual] = []
        for front in fronts:
            if len(next_pop) + len(front) <= population_size:
                next_pop.extend(front)
            else:
                # Fill remaining with crowding distance
                remaining = population_size - len(next_pop)
                distances = _crowding_distance(front, _get_objectives)
                ranked = sorted(
                    zip(front, distances),
                    key=lambda x: x[1],
                    reverse=True,
                )
                for ind, _dist in ranked[:remaining]:
                    next_pop.append(ind)
                break

        population = next_pop

    # Final non-dominated sorting
    fronts = _fast_non_dominated_sort(population, _get_objectives)
    pareto_front = fronts[0] if fronts else []
    dominated = [ind for front in fronts[1:] for ind in front]

    # Hypervolume with reference point at origin (0, 0, ...)
    ref_point = [0.0] * len(objectives)
    hv = _compute_hypervolume(pareto_front, _get_objectives, ref_point)

    return MultiObjectiveResult(
        pareto_front=pareto_front,
        dominated=dominated,
        hypervolume=round(hv, 6),
    )


def _fast_non_dominated_sort(
    population: List[Individual],
    get_objectives: Callable[[Individual], List[float]],
) -> List[List[Individual]]:
    """NSGA-II fast non-dominated sorting.

    Sorts population into fronts where front[0] is the Pareto front.
    All objectives are maximized.
    """
    n = len(population)
    if n == 0:
        return []

    # domination_count[i] = number of individuals that dominate i
    domination_count = [0] * n
    # dominated_set[i] = set of individuals that i dominates
    dominated_set: List[List[int]] = [[] for _ in range(n)]
    obj_values = [get_objectives(ind) for ind in population]

    for i in range(n):
        for j in range(i + 1, n):
            dom_ij = _dominates(obj_values[i], obj_values[j])
            dom_ji = _dominates(obj_values[j], obj_values[i])
            if dom_ij:
                dominated_set[i].append(j)
                domination_count[j] += 1
            elif dom_ji:
                dominated_set[j].append(i)
                domination_count[i] += 1

    # Build fronts
    fronts: List[List[Individual]] = []
    current_front_idx = [i for i in range(n) if domination_count[i] == 0]

    while current_front_idx:
        front = [population[i] for i in current_front_idx]
        fronts.append(front)
        next_front_idx: List[int] = []
        for i in current_front_idx:
            for j in dominated_set[i]:
                domination_count[j] -= 1
                if domination_count[j] == 0:
                    next_front_idx.append(j)
        current_front_idx = next_front_idx

    return fronts


def _dominates(obj_a: List[float], obj_b: List[float]) -> bool:
    """Check if a dominates b (all objectives maximized).

    a dominates b iff a[i] >= b[i] for all i AND a[j] > b[j] for at least one j.
    """
    at_least_one_better = False
    for a_val, b_val in zip(obj_a, obj_b):
        if a_val < b_val:
            return False
        if a_val > b_val:
            at_least_one_better = True
    return at_least_one_better


def _crowding_distance(
    front: List[Individual],
    get_objectives: Callable[[Individual], List[float]],
) -> List[float]:
    """Compute crowding distance for individuals in a front."""
    n = len(front)
    if n <= 2:
        return [float("inf")] * n

    distances = [0.0] * n
    obj_values = [get_objectives(ind) for ind in front]
    n_obj = len(obj_values[0])

    for m in range(n_obj):
        # Sort by objective m
        sorted_idx = sorted(range(n), key=lambda i: obj_values[i][m])
        distances[sorted_idx[0]] = float("inf")
        distances[sorted_idx[-1]] = float("inf")

        obj_min = obj_values[sorted_idx[0]][m]
        obj_max = obj_values[sorted_idx[-1]][m]
        obj_range = obj_max - obj_min

        if obj_range < 1e-12:
            continue

        for i in range(1, n - 1):
            diff = obj_values[sorted_idx[i + 1]][m] - obj_values[sorted_idx[i - 1]][m]
            distances[sorted_idx[i]] += diff / obj_range

    return distances


def _compute_hypervolume(
    pareto_front: List[Individual],
    get_objectives: Callable[[Individual], List[float]],
    ref_point: List[float],
) -> float:
    """Compute hypervolume indicator using inclusion-exclusion for 2-3 objectives.

    For higher dimensions, falls back to Monte Carlo estimation.
    """
    if not pareto_front:
        return 0.0

    obj_values = [get_objectives(ind) for ind in pareto_front]
    n_obj = len(obj_values[0])

    # Filter points that dominate the reference point
    valid = [ov for ov in obj_values if all(o > r for o, r in zip(ov, ref_point))]
    if not valid:
        return 0.0

    if n_obj == 2:
        return _hypervolume_2d(valid, ref_point)
    else:
        return _hypervolume_mc(valid, ref_point, n_samples=5000)


def _hypervolume_2d(
    points: List[List[float]],
    ref_point: List[float],
) -> float:
    """Exact 2D hypervolume via sweep line."""
    sorted_pts = sorted(points, key=lambda p: p[0], reverse=True)
    hv = 0.0
    prev_y = ref_point[1]
    for pt in sorted_pts:
        if pt[1] > prev_y:
            hv += (pt[0] - ref_point[0]) * (pt[1] - prev_y)
            prev_y = pt[1]
    return hv


def _hypervolume_mc(
    points: List[List[float]],
    ref_point: List[float],
    n_samples: int = 5000,
) -> float:
    """Monte Carlo hypervolume estimation for 3+ objectives."""
    pts = np.array(points)
    ref = np.array(ref_point)
    upper = np.max(pts, axis=0)

    # Volume of bounding box
    box_vol = float(np.prod(upper - ref))
    if box_vol <= 0:
        return 0.0

    rng = np.random.RandomState(123)
    samples = ref + rng.uniform(size=(n_samples, len(ref_point))) * (upper - ref)

    # Count samples dominated by at least one point
    dominated_count = 0
    for s in samples:
        for p in pts:
            if np.all(p >= s):
                dominated_count += 1
                break

    return box_vol * dominated_count / n_samples


# ---------------------------------------------------------------------------
# 4. Robustness Testing
# ---------------------------------------------------------------------------


def test_robustness(
    params: dict,
    param_ranges: dict,
    ohlcv: pd.DataFrame,
    strategy_fn: Callable,
    n_perturbations: int = 50,
    seed: int = 42,
) -> RobustnessResult:
    """Test parameter robustness via Monte Carlo perturbation.

    Features:
      - Uniform +-10% perturbation of each parameter
      - Stability score = 1 - (std/mean) of Sharpe ratios
      - Per-parameter sensitivity via finite-difference partial derivatives
    """
    rng = np.random.RandomState(seed)
    names = _param_names(param_ranges)

    # Base evaluation
    base_ind = _evaluate_strategy(params, ohlcv, strategy_fn)
    base_sharpe = base_ind.sharpe

    # Monte Carlo perturbation
    perturbed_sharpes: List[float] = []
    for _ in range(n_perturbations):
        perturbed = {}
        for name in names:
            lo, hi = param_ranges[name]
            base_val = params[name]
            delta = 0.1 * (hi - lo)
            new_val = base_val + rng.uniform(-delta, delta)
            new_val = float(np.clip(new_val, lo, hi))
            perturbed[name] = new_val

        ind = _evaluate_strategy(perturbed, ohlcv, strategy_fn)
        perturbed_sharpes.append(ind.sharpe)

    # Stability score
    sharpe_arr = np.array(perturbed_sharpes)
    mean_sharpe = float(np.mean(sharpe_arr))
    std_sharpe = float(np.std(sharpe_arr))
    if abs(mean_sharpe) > 1e-12:
        stability_score = float(np.clip(1 - std_sharpe / abs(mean_sharpe), 0.0, 1.0))
    else:
        stability_score = 0.0

    worst_case = float(np.min(sharpe_arr)) if len(sharpe_arr) > 0 else 0.0

    # Parameter sensitivity: partial derivative via central finite difference
    sensitivity: Dict[str, float] = {}
    for name in names:
        lo, hi = param_ranges[name]
        h = 0.01 * (hi - lo)
        if h < 1e-12:
            sensitivity[name] = 0.0
            continue

        # f(x + h)
        params_plus = dict(params)
        params_plus[name] = float(np.clip(params[name] + h, lo, hi))
        ind_plus = _evaluate_strategy(params_plus, ohlcv, strategy_fn)

        # f(x - h)
        params_minus = dict(params)
        params_minus[name] = float(np.clip(params[name] - h, lo, hi))
        ind_minus = _evaluate_strategy(params_minus, ohlcv, strategy_fn)

        actual_h = params_plus[name] - params_minus[name]
        if abs(actual_h) > 1e-12:
            deriv = (ind_plus.sharpe - ind_minus.sharpe) / actual_h
        else:
            deriv = 0.0
        sensitivity[name] = round(abs(deriv), 6)

    return RobustnessResult(
        base_params=dict(params),
        base_sharpe=base_sharpe,
        perturbed_sharpes=perturbed_sharpes,
        stability_score=round(stability_score, 4),
        worst_case_sharpe=round(worst_case, 4),
        parameter_sensitivity=sensitivity,
    )


# ---------------------------------------------------------------------------
# 5. Formatting
# ---------------------------------------------------------------------------


def format_optimization_result(result: OptimizationResult) -> str:
    """Format optimization result for Telegram (plain text + emoji)."""
    method_labels = {
        "genetic": "유전 알고리즘",
        "bayesian": "베이지안 최적화",
        "nsga2": "NSGA-II 다중목표",
    }
    method_name = method_labels.get(result.method, result.method)

    lines = [
        "\u2699\ufe0f 전략 최적화 결과",
        "\u2500" * 25,
        "",
        f"\U0001f50d 방법  {method_name}",
        f"\u23f1 소요  {result.elapsed_seconds:.1f}초",
        "",
        "\U0001f4ca 최적 파라미터",
    ]

    for k, v in result.best_params.items():
        if isinstance(v, float) and v == int(v):
            lines.append(f"  {k}: {int(v)}")
        else:
            lines.append(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")

    lines.append("")
    lines.append(f"\U0001f3af Fitness  {result.best_fitness:.4f}")

    metrics = result.metrics
    if metrics:
        lines.append("")
        if "sharpe" in metrics:
            lines.append(f"Sharpe  {metrics['sharpe']:.4f}")
        if "sortino" in metrics:
            lines.append(f"Sortino  {metrics['sortino']:.4f}")
        if "calmar" in metrics:
            lines.append(f"Calmar  {metrics['calmar']:.4f}")
        if "max_drawdown" in metrics:
            lines.append(f"MDD  {metrics['max_drawdown']:.4f}")

    # Convergence summary
    if len(result.convergence_history) >= 2:
        initial = result.convergence_history[0]
        final = result.convergence_history[-1]
        if abs(initial) > 1e-12:
            improvement = (final - initial) / abs(initial) * 100
            lines.append("")
            lines.append(
                f"\U0001f4c8 수렴  {initial:.4f} -> {final:.4f} ({improvement:+.1f}%)",
            )

    # Robustness
    if result.robustness:
        rob = result.robustness
        stability_emoji = "\u2705" if rob.stability_score >= 0.7 else "\u26a0\ufe0f"
        lines.append("")
        lines.append(f"\U0001f6e1 견고성  {stability_emoji} {rob.stability_score:.2f}")
        lines.append(f"  최악 Sharpe  {rob.worst_case_sharpe:.4f}")

        # Top 2 most sensitive params
        if rob.parameter_sensitivity:
            sorted_sens = sorted(
                rob.parameter_sensitivity.items(),
                key=lambda x: abs(x[1]),
                reverse=True,
            )
            top = sorted_sens[:2]
            sens_str = ", ".join(f"{k}({v:.3f})" for k, v in top)
            lines.append(f"  민감도  {sens_str}")

    return "\n".join(lines)
