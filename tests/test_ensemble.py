"""Tests for kstock.signal.ensemble — ensemble voting system."""

from __future__ import annotations

import pytest

from kstock.signal.ensemble import (
    EnsembleConfig,
    SignalVote,
    StrategyWeight,
    VoteResult,
    adaptive_vote,
    compute_signal_agreement,
    compute_strategy_weights,
    filter_top_consensus,
    format_ensemble_result,
    vote,
    vote_batch,
)


# ───────────────────── Helpers ──────────────────────────────────


def _buy_vote(strategy: str = "test", confidence: float = 0.9, weight: float = 1.0) -> SignalVote:
    return SignalVote(strategy=strategy, action="BUY", confidence=confidence, score=80, weight=weight)


def _sell_vote(strategy: str = "test", confidence: float = 0.9, weight: float = 1.0) -> SignalVote:
    return SignalVote(strategy=strategy, action="SELL", confidence=confidence, score=20, weight=weight)


def _hold_vote(strategy: str = "test", confidence: float = 0.5, weight: float = 1.0) -> SignalVote:
    return SignalVote(strategy=strategy, action="HOLD", confidence=confidence, score=50, weight=weight)


# ───────────────────── vote() 기본 ──────────────────────────────


def test_unanimous_buy():
    """전원 BUY -> STRONG_BUY."""
    signals = [_buy_vote(f"s{i}", confidence=0.9) for i in range(5)]
    result = vote(signals)
    assert result.consensus == "STRONG_BUY"
    assert result.buy_votes == 5
    assert result.sell_votes == 0
    assert result.bullish_pct > 0.8


def test_unanimous_sell():
    """전원 SELL -> STRONG_SELL."""
    signals = [_sell_vote(f"s{i}", confidence=0.9) for i in range(5)]
    result = vote(signals)
    assert result.consensus == "STRONG_SELL"
    assert result.sell_votes == 5
    assert result.bearish_pct > 0.8


def test_mixed_signals():
    """혼합 시그널 -> HOLD, 낮은 agreement."""
    signals = [
        _buy_vote("s1"),
        _sell_vote("s2"),
        _hold_vote("s3"),
        _buy_vote("s4"),
        _sell_vote("s5"),
    ]
    result = vote(signals)
    assert result.consensus == "HOLD"
    assert result.signal_agreement < 0.5


def test_weighted_override():
    """고가중치 전략 1개가 저가중치 다수 오버라이드."""
    signals = [
        _sell_vote("weak1", confidence=0.6, weight=0.5),
        _sell_vote("weak2", confidence=0.6, weight=0.5),
        _buy_vote("strong", confidence=0.9, weight=5.0),
    ]
    result = vote(signals)
    assert result.consensus in ("BUY", "STRONG_BUY")


def test_empty_signals():
    """빈 리스트 -> HOLD, confidence=0."""
    result = vote([])
    assert result.consensus == "HOLD"
    assert result.confidence == 0.0


def test_single_vote():
    """단일 투표 반영."""
    result = vote([_buy_vote("solo", confidence=0.8)])
    assert result.total_votes == 1
    assert result.consensus in ("BUY", "STRONG_BUY")
    assert result.buy_votes == 1


# ───────────────────── vote_batch() ─────────────────────────────


def test_batch_multiple_tickers():
    """5종목 배치 처리."""
    ticker_signals = {
        f"00{i}000": [_buy_vote(f"s{j}") for j in range(3)]
        for i in range(5)
    }
    results = vote_batch(ticker_signals)
    assert len(results) == 5
    for r in results:
        assert r.ticker != ""
        assert r.total_votes == 3


def test_batch_sorted_by_score():
    """배치 결과는 weighted_score 내림차순."""
    ticker_signals = {
        "LOW": [_sell_vote("s1"), _sell_vote("s2"), _sell_vote("s3")],
        "HIGH": [_buy_vote("s1", confidence=1.0), _buy_vote("s2", confidence=1.0)],
        "MID": [_hold_vote("s1"), _hold_vote("s2")],
    }
    results = vote_batch(ticker_signals)
    scores = [r.weighted_score for r in results]
    assert scores == sorted(scores, reverse=True)


# ─────────────── compute_strategy_weights() ─────────────────────


def test_good_performer_upweighted():
    """80% 적중률 -> adjusted_weight > 1.0."""
    from datetime import datetime, timedelta

    history = []
    for i in range(10):
        history.append({
            "strategy": "good_strat",
            "date": (datetime.now() - timedelta(days=i)).isoformat(),
            "hit": i < 8,  # 80% hit
            "return_pct": 2.0,
        })
    weights = compute_strategy_weights(history)
    assert "good_strat" in weights
    assert weights["good_strat"].adjusted_weight > 1.0


def test_bad_performer_downweighted():
    """30% 적중률 -> adjusted_weight < 1.0."""
    from datetime import datetime, timedelta

    history = []
    for i in range(10):
        history.append({
            "strategy": "bad_strat",
            "date": (datetime.now() - timedelta(days=i)).isoformat(),
            "hit": i < 3,  # 30% hit
            "return_pct": -1.0,
        })
    weights = compute_strategy_weights(history)
    assert "bad_strat" in weights
    assert weights["bad_strat"].adjusted_weight < 1.0


# ──────────────── compute_signal_agreement() ────────────────────


def test_perfect_agreement():
    """전원 동의 -> 1.0."""
    votes = [_buy_vote(f"s{i}") for i in range(5)]
    assert compute_signal_agreement(votes) == 1.0


def test_no_agreement():
    """균등 분할 -> 낮은 agreement."""
    votes = [
        _buy_vote("s1"),
        _sell_vote("s2"),
        _hold_vote("s3"),
    ]
    agreement = compute_signal_agreement(votes)
    # Perfectly split 3 ways = maximum entropy = agreement ~0.0
    assert agreement < 0.05


# ─────────────────── 하위 호환 ──────────────────────────────────


def test_backward_compatible():
    """list[dict] 입력도 처리."""
    signals = [
        {"strategy": "s1", "action": "BUY", "confidence": 0.9},
        {"strategy": "s2", "action": "SELL", "confidence": 0.8},
        {"strategy": "s3", "signal": "BUY", "confidence": 0.7},
    ]
    result = vote(signals)
    assert isinstance(result, VoteResult)
    assert result.total_votes == 3
    assert result.buy_votes == 2


# ─────────────────── format_ensemble_result() ───────────────────


def test_format_output():
    """str 반환, 주요 정보 포함."""
    result = vote([_buy_vote(f"s{i}", confidence=0.9) for i in range(4)])
    result.ticker = "005930"
    result.name = "삼성전자"
    output = format_ensemble_result(result)
    assert isinstance(output, str)
    assert "005930" in output
    assert "삼성전자" in output
    assert "컨센서스" in output
    assert "점수" in output
