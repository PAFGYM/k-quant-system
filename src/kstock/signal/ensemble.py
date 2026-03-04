"""Strategy ensemble voting system — multi-strategy weighted consensus.

Aggregates signals from multiple trading strategies (Livermore, O'Neil,
Lynch, Buffett, etc.) into a single buy/sell/hold consensus using
weighted voting, entropy-based agreement metrics, and adaptive
performance weighting.

v6.5 — signal guard integration (holding protection + reliability grading).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)


# ───────────────────────── Dataclasses ─────────────────────────


@dataclass
class SignalVote:
    """A single strategy's vote for one ticker."""

    strategy: str = ""
    action: str = "HOLD"  # BUY / SELL / HOLD
    confidence: float = 0.5
    score: float = 50.0  # 0‑100
    weight: float = 1.0
    reasons: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.action = self.action.upper()
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.score = max(0.0, min(100.0, float(self.score)))
        self.weight = max(0.0, float(self.weight))


@dataclass
class VoteResult:
    """Ensemble consensus result for one ticker."""

    ticker: str = ""
    name: str = ""
    buy_votes: int = 0
    sell_votes: int = 0
    hold_votes: int = 0
    total_votes: int = 0
    consensus: str = "HOLD"  # STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL
    consensus_strength: float = 0.0  # 0‑1
    weighted_score: float = 50.0  # 0‑100
    confidence: float = 0.0  # 0‑1
    bullish_pct: float = 0.0
    bearish_pct: float = 0.0
    signal_agreement: float = 0.0  # 0‑1 (1 = unanimous)
    contributing_strategies: List[str] = field(default_factory=list)
    dissenting_strategies: List[str] = field(default_factory=list)
    # v6.5: Signal guard fields
    reliability_grade: str = ""       # A/B/C/D
    reliability_score: float = 0.0    # 0~100
    reliability_emoji: str = ""       # 🟢/🔵/🟡/🔴
    reliability_warning: str = ""     # 경고 메시지
    holding_suppressed: bool = False  # 장기보유 보호로 매도 억제됨
    original_consensus: str = ""      # 억제 전 원래 합의


@dataclass
class EnsembleConfig:
    """Tunable ensemble parameters."""

    min_votes: int = 3
    buy_threshold: float = 0.6
    sell_threshold: float = 0.6
    strong_threshold: float = 0.8
    use_performance_weighting: bool = True
    decay_factor: float = 0.95


@dataclass
class StrategyWeight:
    """Adaptive weight derived from recent performance."""

    strategy: str = ""
    base_weight: float = 1.0
    hit_rate_30d: float = 0.5
    avg_return_30d: float = 0.0
    adjusted_weight: float = 1.0
    confidence_penalty: float = 0.0


# ─────────────────── Default config singleton ───────────────────

_DEFAULT_CONFIG = EnsembleConfig()


# ───────────────────────── Helpers ──────────────────────────────


def _normalize_action(action: str) -> str:
    """Normalize action string to BUY/SELL/HOLD."""
    action = action.strip().upper()
    if action in ("BUY", "STRONG_BUY", "강력매수", "매수"):
        return "BUY"
    if action in ("SELL", "STRONG_SELL", "강력매도", "매도"):
        return "SELL"
    return "HOLD"


def _dict_to_signal_vote(d: dict) -> SignalVote:
    """Convert a plain dict to SignalVote for backward compatibility."""
    return SignalVote(
        strategy=d.get("strategy", d.get("name", "")),
        action=_normalize_action(str(d.get("action", d.get("signal", "HOLD")))),
        confidence=float(d.get("confidence", 0.5)),
        score=float(d.get("score", 50.0)),
        weight=float(d.get("weight", 1.0)),
        reasons=d.get("reasons", []),
    )


def _ensure_signal_votes(signals: Sequence[Union[SignalVote, dict]]) -> List[SignalVote]:
    """Accept mixed list of SignalVote and dict, return list of SignalVote."""
    result: List[SignalVote] = []
    for s in signals:
        if isinstance(s, SignalVote):
            result.append(s)
        elif isinstance(s, dict):
            result.append(_dict_to_signal_vote(s))
        else:
            # Best-effort: skip unknown types
            continue
    return result


# ───────────────────── Core Functions ───────────────────────────


def compute_signal_agreement(votes: List[SignalVote]) -> float:
    """Entropy-based signal agreement metric.

    Returns:
        1.0 = unanimous agreement, 0.0 = maximum disagreement.
        Uses Shannon entropy:  H = -sum(p * log(p)), H_max = log(3).
    """
    if not votes:
        return 0.0

    n = len(votes)
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    for v in votes:
        action = _normalize_action(v.action)
        counts[action] = counts.get(action, 0) + 1

    # Shannon entropy
    h = 0.0
    for c in counts.values():
        if c > 0:
            p = c / n
            h -= p * math.log(p)

    # Number of non-zero categories determines H_max
    num_categories = sum(1 for c in counts.values() if c > 0)
    if num_categories <= 1:
        return 1.0  # Unanimous

    h_max = math.log(3)  # max possible entropy (3 categories)
    if h_max == 0:
        return 1.0

    agreement = 1.0 - (h / h_max)
    return max(0.0, min(1.0, agreement))


def compute_weighted_agreement(votes: List[SignalVote]) -> float:
    """가중 엔트로피 기반 신호 일치도.

    confidence를 가중치로 반영하여 높은 확신의 동의가
    낮은 확신의 동의보다 중요하게 평가됨.
    """
    if not votes:
        return 0.0

    weighted_counts = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    total_weight = 0.0
    for v in votes:
        action = _normalize_action(v.action)
        w = v.confidence * v.weight
        weighted_counts[action] += w
        total_weight += w

    if total_weight <= 0:
        return 0.0

    h = 0.0
    for w in weighted_counts.values():
        if w > 0:
            p = w / total_weight
            h -= p * math.log(p)

    h_max = math.log(3)
    if h_max == 0:
        return 1.0

    return max(0.0, min(1.0, 1.0 - h / h_max))


def compute_strategy_diversity(
    ticker_votes: Dict[str, List[SignalVote]],
) -> dict:
    """전략 다양성 측정 — 전략 간 상관관계 및 독립성 분석.

    높은 다양성 = 앙상블의 예측력 향상.

    Args:
        ticker_votes: {ticker: [SignalVote, ...]} 여러 종목의 투표 데이터

    Returns:
        dict with: diversity_score (0-1), strategy_correlations,
        redundant_pairs, effective_n_strategies
    """
    if not ticker_votes:
        return {"diversity_score": 0.0, "strategy_correlations": {},
                "redundant_pairs": [], "effective_n_strategies": 0}

    # 전략별 action 벡터 생성 (BUY=1, HOLD=0, SELL=-1)
    action_map = {"BUY": 1, "HOLD": 0, "SELL": -1}
    strategies: Dict[str, List[int]] = {}

    tickers_list = list(ticker_votes.keys())
    for ticker in tickers_list:
        for v in ticker_votes[ticker]:
            strat = v.strategy
            if strat not in strategies:
                strategies[strat] = []
            action = _normalize_action(v.action)
            strategies[strat].append(action_map.get(action, 0))

    # 전략이 2개 미만이면 다양성 측정 불가
    strat_names = list(strategies.keys())
    n_strats = len(strat_names)
    if n_strats < 2:
        return {"diversity_score": 1.0, "strategy_correlations": {},
                "redundant_pairs": [], "effective_n_strategies": n_strats}

    # 전략 간 상관행렬
    import numpy as np

    # 모든 벡터를 동일 길이로 맞추기
    max_len = max(len(v) for v in strategies.values())
    matrix = np.zeros((n_strats, max_len))
    for i, name in enumerate(strat_names):
        vec = strategies[name]
        matrix[i, :len(vec)] = vec

    # 상관관계
    correlations = {}
    redundant = []

    for i in range(n_strats):
        for j in range(i + 1, n_strats):
            if max_len < 3:
                corr = 0.0
            else:
                std_i = np.std(matrix[i])
                std_j = np.std(matrix[j])
                if std_i < 1e-8 or std_j < 1e-8:
                    corr = 0.0
                else:
                    corr = float(np.corrcoef(matrix[i], matrix[j])[0, 1])
                    if np.isnan(corr):
                        corr = 0.0

            pair_key = f"{strat_names[i]}-{strat_names[j]}"
            correlations[pair_key] = round(corr, 4)

            if abs(corr) > 0.8:
                redundant.append(pair_key)

    # Diversity score: 1 - avg(|correlation|)
    if correlations:
        avg_corr = np.mean([abs(c) for c in correlations.values()])
        diversity = 1.0 - avg_corr
    else:
        diversity = 1.0

    # Effective N: N / (1 + (N-1) * avg_corr)
    avg_abs_corr = np.mean([abs(c) for c in correlations.values()]) if correlations else 0.0
    effective_n = n_strats / (1 + (n_strats - 1) * avg_abs_corr) if avg_abs_corr < 1.0 else 1.0

    return {
        "diversity_score": round(float(diversity), 4),
        "strategy_correlations": correlations,
        "redundant_pairs": redundant,
        "effective_n_strategies": round(float(effective_n), 2),
        "total_strategies": n_strats,
    }


def apply_signal_decay(
    votes: List[SignalVote],
    signal_ages_hours: Dict[str, float] | None = None,
    half_life_hours: float = 24.0,
) -> List[SignalVote]:
    """신호 감쇠: 시간 경과에 따라 확신도를 지수적으로 감소.

    신선한 신호일수록 높은 가중치, 오래된 신호는 감쇠.

    Args:
        votes: 원본 투표 리스트
        signal_ages_hours: {strategy: hours_since_generation}
        half_life_hours: 반감기 (시간). 기본 24시간.

    Returns:
        감쇠 적용된 새 투표 리스트 (원본 미수정)
    """
    if not signal_ages_hours:
        return votes

    decayed: List[SignalVote] = []
    for v in votes:
        age = signal_ages_hours.get(v.strategy, 0.0)
        if age <= 0:
            decayed.append(v)
            continue

        # 지수 감쇠: decay = 0.5^(age / half_life)
        decay_factor = 0.5 ** (age / half_life_hours)
        decay_factor = max(0.1, decay_factor)  # 최소 10% 유지

        new_vote = SignalVote(
            strategy=v.strategy,
            action=v.action,
            confidence=round(v.confidence * decay_factor, 4),
            score=round(v.score * decay_factor, 2),
            weight=v.weight,
            reasons=v.reasons + [f"신호감쇠 {decay_factor:.0%} ({age:.0f}h)"],
        )
        decayed.append(new_vote)

    return decayed


def compute_strategy_weights(
    performance_history: List[dict],
    decay_factor: float = 0.95,
) -> Dict[str, StrategyWeight]:
    """Compute adaptive strategy weights from recent performance.

    Args:
        performance_history: list of
            {strategy: str, date: str|datetime, hit: bool, return_pct: float}
        decay_factor: exponential decay for older records (0‑1).

    Returns:
        dict mapping strategy name → StrategyWeight.
    """
    if not performance_history:
        return {}

    # Parse dates and sort descending (newest first)
    records: List[dict] = []
    for rec in performance_history:
        d = rec.get("date")
        if isinstance(d, str):
            try:
                d = datetime.fromisoformat(d)
            except (ValueError, TypeError):
                d = datetime.now()
        elif not isinstance(d, datetime):
            d = datetime.now()
        records.append({**rec, "_dt": d})

    records.sort(key=lambda r: r["_dt"], reverse=True)

    # Cutoff: 30 calendar days
    cutoff = datetime.now() - timedelta(days=30)

    # Group by strategy
    strategy_records: Dict[str, List[dict]] = {}
    for rec in records:
        if rec["_dt"] < cutoff:
            continue
        strat = rec.get("strategy", "unknown")
        strategy_records.setdefault(strat, []).append(rec)

    weights: Dict[str, StrategyWeight] = {}
    for strat, recs in strategy_records.items():
        if not recs:
            continue

        # Apply time‑decay weighting
        total_w = 0.0
        hit_w = 0.0
        return_w = 0.0

        for i, rec in enumerate(recs):
            w = decay_factor ** i
            total_w += w
            if rec.get("hit", False):
                hit_w += w
            return_w += w * float(rec.get("return_pct", 0.0))

        hit_rate = (hit_w / total_w) if total_w > 0 else 0.5
        avg_return = (return_w / total_w) if total_w > 0 else 0.0

        # Adjusted weight:
        #   base * (hit_rate / 50%) * (1 + avg_return / 10)
        #   clipped to [0.2, 3.0]
        base = 1.0
        raw = base * (hit_rate / 0.5) * (1.0 + avg_return / 10.0)
        adjusted = max(0.2, min(3.0, raw))

        # Confidence penalty: penalize strategies with few data points
        n_points = len(recs)
        penalty = max(0.0, 1.0 - n_points / 10.0) * 0.3  # up to 0.3 penalty
        adjusted *= (1.0 - penalty)
        adjusted = max(0.2, min(3.0, adjusted))

        weights[strat] = StrategyWeight(
            strategy=strat,
            base_weight=base,
            hit_rate_30d=hit_rate,
            avg_return_30d=avg_return,
            adjusted_weight=round(adjusted, 4),
            confidence_penalty=round(penalty, 4),
        )

    return weights


def vote(
    signals: Sequence[Union[SignalVote, dict]],
    config: Optional[EnsembleConfig] = None,
    vix: float = 0,
) -> VoteResult:
    """Compute ensemble consensus from multiple strategy signals.

    Backward compatible: accepts list[dict] or list[SignalVote].

    Weighted voting:
        buy_score  = sum(v.weight * v.confidence  for BUY votes)
        sell_score = sum(v.weight * v.confidence  for SELL votes)
        hold_score = sum(v.weight * v.confidence  for HOLD votes)

    Consensus thresholds:
        buy_pct  >= strong_threshold → STRONG_BUY
        buy_pct  >= buy_threshold    → BUY
        sell_pct >= strong_threshold → STRONG_SELL
        sell_pct >= sell_threshold   → SELL
        else                          → HOLD
    """
    cfg = config or _DEFAULT_CONFIG
    votes = _ensure_signal_votes(signals)

    if not votes:
        return VoteResult(consensus="HOLD", confidence=0.0, weighted_score=50.0)

    buy_votes: List[SignalVote] = []
    sell_votes: List[SignalVote] = []
    hold_votes: List[SignalVote] = []

    for v in votes:
        action = _normalize_action(v.action)
        if action == "BUY":
            buy_votes.append(v)
        elif action == "SELL":
            sell_votes.append(v)
        else:
            hold_votes.append(v)

    # Weighted scores
    buy_score = sum(v.weight * v.confidence for v in buy_votes)
    sell_score = sum(v.weight * v.confidence for v in sell_votes)
    hold_score = sum(v.weight * v.confidence for v in hold_votes)
    total_weighted = buy_score + sell_score + hold_score

    if total_weighted == 0:
        total_weighted = 1.0  # avoid division by zero

    buy_pct = buy_score / total_weighted
    sell_pct = sell_score / total_weighted

    # v8.1: Regime-aware thresholds + dissent penalty
    buy_thresh = cfg.buy_threshold
    sell_thresh = cfg.sell_threshold
    strong_thresh = cfg.strong_threshold

    if vix > 30:  # panic: require higher agreement for BUY
        buy_thresh = min(0.85, buy_thresh + 0.15)
        strong_thresh = min(0.95, strong_thresh + 0.10)
    elif vix > 25:  # fear
        buy_thresh = min(0.80, buy_thresh + 0.10)
    elif vix < 15:  # calm: slightly relax
        buy_thresh = max(0.50, buy_thresh - 0.05)

    # Dissent penalty: if strongest dissenter has high confidence, reduce consensus
    dissent_penalty = 0.0
    if buy_pct > sell_pct and sell_votes:
        max_dissent = max(v.confidence * v.weight for v in sell_votes)
        net_strength = abs(buy_score - sell_score) / total_weighted
        if max_dissent > net_strength * 0.5:
            dissent_penalty = 0.05

    buy_pct_adj = buy_pct - dissent_penalty
    sell_pct_adj = sell_pct - dissent_penalty

    # Consensus determination
    if buy_pct_adj >= strong_thresh:
        consensus = "STRONG_BUY"
    elif buy_pct_adj >= buy_thresh:
        consensus = "BUY"
    elif sell_pct_adj >= strong_thresh:
        consensus = "STRONG_SELL"
    elif sell_pct_adj >= sell_thresh:
        consensus = "SELL"
    else:
        consensus = "HOLD"

    # Consensus strength: |buy_score - sell_score| / total
    consensus_strength = abs(buy_score - sell_score) / total_weighted

    # Weighted score: map from [-1, 1] to [0, 100]
    # -1 = full sell, +1 = full buy
    net_direction = (buy_score - sell_score) / total_weighted
    weighted_score = 50.0 + net_direction * 50.0
    weighted_score = max(0.0, min(100.0, weighted_score))

    # Average confidence
    avg_confidence = sum(v.confidence for v in votes) / len(votes)

    # Signal agreement
    agreement = compute_signal_agreement(votes)

    # Contributing / dissenting
    if consensus in ("STRONG_BUY", "BUY"):
        contributing = [v.strategy for v in buy_votes]
        dissenting = [v.strategy for v in sell_votes]
    elif consensus in ("STRONG_SELL", "SELL"):
        contributing = [v.strategy for v in sell_votes]
        dissenting = [v.strategy for v in buy_votes]
    else:
        contributing = [v.strategy for v in hold_votes]
        dissenting = [v.strategy for v in buy_votes + sell_votes]

    # Extract ticker/name from first signal if available
    ticker = ""
    name = ""
    for v in votes:
        if hasattr(v, "ticker"):
            ticker = getattr(v, "ticker", "")
        if hasattr(v, "name"):
            name = getattr(v, "name", "")
        break

    return VoteResult(
        ticker=ticker,
        name=name,
        buy_votes=len(buy_votes),
        sell_votes=len(sell_votes),
        hold_votes=len(hold_votes),
        total_votes=len(votes),
        consensus=consensus,
        consensus_strength=round(consensus_strength, 4),
        weighted_score=round(weighted_score, 2),
        confidence=round(avg_confidence, 4),
        bullish_pct=round(buy_pct, 4),
        bearish_pct=round(sell_pct, 4),
        signal_agreement=round(agreement, 4),
        contributing_strategies=contributing,
        dissenting_strategies=dissenting,
    )


def vote_batch(
    ticker_signals: Dict[str, List[Union[SignalVote, dict]]],
    config: Optional[EnsembleConfig] = None,
    vix: float = 0,
) -> List[VoteResult]:
    """Batch vote for multiple tickers.

    Args:
        ticker_signals: dict mapping ticker → list of signals.
        config: optional EnsembleConfig.

    Returns:
        List of VoteResult sorted by weighted_score descending.
    """
    results: List[VoteResult] = []
    for ticker, sigs in ticker_signals.items():
        result = vote(sigs, config=config, vix=vix)
        result.ticker = ticker
        results.append(result)

    results.sort(key=lambda r: r.weighted_score, reverse=True)
    return results


def adaptive_vote(
    signals: Sequence[Union[SignalVote, dict]],
    performance_history: List[dict],
    config: Optional[EnsembleConfig] = None,
    vix: float = 0,
) -> VoteResult:
    """Vote with dynamically adjusted strategy weights.

    Combines compute_strategy_weights() with vote().
    Each signal's weight is replaced by the adaptive weight
    if the strategy appears in performance history.
    """
    votes = _ensure_signal_votes(signals)
    weights = compute_strategy_weights(
        performance_history,
        decay_factor=(config or _DEFAULT_CONFIG).decay_factor,
    )

    # Apply adaptive weights
    for v in votes:
        if v.strategy in weights:
            v.weight = weights[v.strategy].adjusted_weight

    return vote(votes, config=config, vix=vix)


def filter_top_consensus(
    results: List[VoteResult],
    top_n: int = 10,
    min_confidence: float = 0.5,
) -> List[VoteResult]:
    """Filter and rank consensus results.

    Returns top_n results with confidence >= min_confidence,
    sorted by weighted_score descending.
    """
    filtered = [r for r in results if r.confidence >= min_confidence]
    filtered.sort(key=lambda r: r.weighted_score, reverse=True)
    return filtered[:top_n]


def vote_with_guard(
    signals: Sequence[Union[SignalVote, dict]],
    config: Optional[EnsembleConfig] = None,
    holding_type: str = "",
    hold_days: int = 0,
    pnl_pct: float = 0.0,
    market_regime: str = "normal",
    signal_source: str = "",
    hit_rate_30d: float = 0.5,
    vix: float = 0,
) -> VoteResult:
    """vote() + 장기보유 보호 + 신뢰도 등급 통합.

    기존 vote()의 모든 기능에 더해:
    1. 장기보유 종목의 매도 신호 억제 (signal_guard.apply_holding_guard)
    2. 신호 신뢰도 등급 A~D 부여 (signal_guard.compute_signal_reliability)

    Args:
        signals: 전략별 투표 리스트.
        config: 앙상블 설정.
        holding_type: 보유 유형 (scalp/swing/position/long_term).
        hold_days: 보유 일수.
        pnl_pct: 현재 수익률 (%).
        market_regime: 시장 레짐.
        signal_source: 신호 소스명.
        hit_rate_30d: 과거 30일 적중률.

    Returns:
        VoteResult with reliability and guard fields populated.
    """
    from kstock.signal.signal_guard import (
        apply_holding_guard,
        compute_signal_reliability,
    )

    # 1. 기본 투표
    result = vote(signals, config=config, vix=vix)

    # 2. 신뢰도 등급 계산
    try:
        rel = compute_signal_reliability(
            consensus=result.consensus,
            confidence=result.confidence,
            agreement=result.signal_agreement,
            contributing_count=len(result.contributing_strategies),
            total_votes=result.total_votes,
            signal_source=signal_source,
            hit_rate_30d=hit_rate_30d,
            holding_type=holding_type,
            market_regime=market_regime,
        )
        result.reliability_grade = rel.grade
        result.reliability_score = rel.score
        result.reliability_emoji = rel.emoji
        result.reliability_warning = rel.warning
    except Exception as e:
        logger.warning("Signal reliability calculation failed: %s", e)

    # 3. 장기보유 보호 (holding_type이 있을 때만)
    if holding_type and result.consensus in ("SELL", "STRONG_SELL"):
        try:
            guard = apply_holding_guard(
                consensus=result.consensus,
                holding_type=holding_type,
                hold_days=hold_days,
                pnl_pct=pnl_pct,
                confidence=result.confidence,
                agreement=result.signal_agreement,
                market_regime=market_regime,
            )
            if guard.suppressed:
                result.original_consensus = result.consensus
                result.consensus = guard.adjusted_consensus
                result.holding_suppressed = True
                logger.info(
                    "Holding guard suppressed: %s → %s (%s)",
                    guard.original_consensus,
                    guard.adjusted_consensus,
                    guard.reason,
                )
        except Exception as e:
            logger.warning("Holding guard failed: %s", e)

    return result


def format_ensemble_result(result: VoteResult) -> str:
    """Format VoteResult for Telegram display (plain text + emoji).

    Follows project convention: no parse_mode, emoji separators,
    short lines.
    """
    # Consensus emoji mapping
    emoji_map = {
        "STRONG_BUY": "🟢🟢",
        "BUY": "🟢",
        "HOLD": "🟡",
        "SELL": "🔴",
        "STRONG_SELL": "🔴🔴",
    }
    emoji = emoji_map.get(result.consensus, "⚪")

    # Ticker display
    title = result.ticker
    if result.name:
        title = f"{result.ticker} {result.name}"

    lines = [
        f"{emoji} {title}",
        f"",
        f"📊 컨센서스: {result.consensus}",
        f"💯 점수: {result.weighted_score:.1f}/100",
        f"🎯 신뢰도: {result.confidence:.0%}",
        f"🤝 일치도: {result.signal_agreement:.0%}",
        f"",
        f"📈 매수: {result.buy_votes}표 ({result.bullish_pct:.0%})",
        f"📉 매도: {result.sell_votes}표 ({result.bearish_pct:.0%})",
        f"➖ 관망: {result.hold_votes}표",
        f"📋 총 {result.total_votes}개 전략 참여",
    ]

    if result.contributing_strategies:
        contribs = ", ".join(result.contributing_strategies[:5])
        lines.append(f"")
        lines.append(f"✅ 동의: {contribs}")

    if result.dissenting_strategies:
        dissents = ", ".join(result.dissenting_strategies[:5])
        lines.append(f"❌ 반대: {dissents}")

    # v6.5: 신뢰도 등급
    if result.reliability_grade:
        lines.append(f"")
        lines.append(
            f"{result.reliability_emoji} 신뢰도 "
            f"{result.reliability_grade}등급 ({result.reliability_score:.0f}점)"
        )
        if result.reliability_warning:
            lines.append(f"⚠️ {result.reliability_warning}")

    # v6.5: 장기보유 보호
    if result.holding_suppressed:
        lines.append(f"")
        lines.append(f"🛡 장기보유 보호 적용")
        lines.append(f"원래: {result.original_consensus} → {result.consensus}")

    return "\n".join(lines)
