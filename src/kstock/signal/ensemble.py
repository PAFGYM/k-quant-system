"""Strategy ensemble voting system — multi-strategy weighted consensus.

Aggregates signals from multiple trading strategies (Livermore, O'Neil,
Lynch, Buffett, etc.) into a single buy/sell/hold consensus using
weighted voting, entropy-based agreement metrics, and adaptive
performance weighting.

v6.3 — full implementation replacing v5.0 stub.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Union


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

    # Consensus determination
    if buy_pct >= cfg.strong_threshold:
        consensus = "STRONG_BUY"
    elif buy_pct >= cfg.buy_threshold:
        consensus = "BUY"
    elif sell_pct >= cfg.strong_threshold:
        consensus = "STRONG_SELL"
    elif sell_pct >= cfg.sell_threshold:
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
        result = vote(sigs, config=config)
        result.ticker = ticker
        results.append(result)

    results.sort(key=lambda r: r.weighted_score, reverse=True)
    return results


def adaptive_vote(
    signals: Sequence[Union[SignalVote, dict]],
    performance_history: List[dict],
    config: Optional[EnsembleConfig] = None,
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

    return vote(votes, config=config)


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

    return "\n".join(lines)
