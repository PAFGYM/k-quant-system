"""Tests for the performance tracker module (core/performance_tracker.py)."""

from __future__ import annotations

import pytest

from kstock.core.performance_tracker import (
    RecommendationTrack,
    StrategyPerformance,
    PerformanceSummary,
    track_recommendation,
    update_track_returns,
    compute_strategy_performance,
    compute_performance_summary,
    create_portfolio_snapshot,
    compute_benchmark_alpha,
    format_performance_report,
    format_live_scorecard,
)


# =========================================================================
# TestTrackRecommendation
# =========================================================================

class TestTrackRecommendation:
    """track_recommendation 함수 테스트."""

    def test_creates_track_with_correct_fields(self):
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=85.5, entry_price=70000, date_str="2025-06-10",
        )
        assert t.ticker == "005930"
        assert t.name == "삼성전자"
        assert t.strategy == "A"
        assert t.score == 85.5
        assert t.entry_price == 70000
        assert t.recommended_date == "2025-06-10"
        assert t.returns == {}
        assert t.hit is False

    def test_default_date_is_today(self):
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=80.0, entry_price=70000,
        )
        assert t.recommended_date != ""
        assert len(t.recommended_date) == 10  # YYYY-MM-DD

    def test_score_rounded(self):
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=85.123456, entry_price=70000, date_str="2025-01-10",
        )
        assert t.score == 85.12


# =========================================================================
# TestUpdateTrackReturns
# =========================================================================

class TestUpdateTrackReturns:
    """update_track_returns 함수 테스트."""

    def test_updates_returns_dict(self):
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=80.0, entry_price=50000, date_str="2025-06-10",
        )
        t = update_track_returns(t, {1: 51000, 3: 52500})
        assert 1 in t.returns
        assert 3 in t.returns
        assert t.returns[1] == pytest.approx(2.0, abs=0.1)  # (51000-50000)/50000*100
        assert t.returns[3] == pytest.approx(5.0, abs=0.1)

    def test_hit_true_if_any_positive(self):
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=80.0, entry_price=50000, date_str="2025-06-10",
        )
        t = update_track_returns(t, {1: 49000, 3: 51000})
        assert t.hit is True  # D+3 양수

    def test_hit_false_if_all_negative(self):
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=80.0, entry_price=50000, date_str="2025-06-10",
        )
        t = update_track_returns(t, {1: 49000, 3: 48000})
        assert t.hit is False

    def test_ignores_non_track_days(self):
        """TRACK_DAYS에 없는 day offset은 무시."""
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=80.0, entry_price=50000, date_str="2025-06-10",
        )
        t = update_track_returns(t, {2: 51000, 7: 52000})  # 2, 7은 추적 대상 아님
        assert 2 not in t.returns
        assert 7 not in t.returns

    def test_zero_entry_price_no_update(self):
        """진입가 0 → 수익률 계산 불가."""
        t = track_recommendation(
            ticker="005930", name="삼성전자", strategy="A",
            score=80.0, entry_price=0, date_str="2025-06-10",
        )
        t = update_track_returns(t, {1: 51000})
        assert t.returns == {}


# =========================================================================
# TestComputeStrategyPerformance
# =========================================================================

class TestComputeStrategyPerformance:
    """compute_strategy_performance 함수 테스트."""

    def _make_track(self, strategy, returns_dict, hit):
        t = RecommendationTrack(
            ticker="005930", name="삼성전자", strategy=strategy,
            score=80.0, recommended_date="2025-06-10", entry_price=50000,
            returns=returns_dict, hit=hit,
        )
        return t

    def test_single_strategy_three_tracks(self):
        tracks = [
            self._make_track("A", {1: 2.0, 3: 5.0}, hit=True),
            self._make_track("A", {1: -1.0, 3: 3.0}, hit=True),
            self._make_track("A", {1: -2.0, 3: -1.0}, hit=False),
        ]
        results = compute_strategy_performance(tracks)
        assert len(results) == 1
        sp = results[0]
        assert sp.strategy == "A"
        assert sp.total_recs == 3
        assert sp.hits == 2
        assert sp.hit_rate_pct == pytest.approx(66.7, abs=0.1)

    def test_mixed_strategies(self):
        tracks = [
            self._make_track("A", {1: 2.0}, hit=True),
            self._make_track("B", {1: -3.0}, hit=False),
            self._make_track("A", {1: 1.0}, hit=True),
        ]
        results = compute_strategy_performance(tracks)
        strategies = {sp.strategy for sp in results}
        assert strategies == {"A", "B"}

    def test_empty_tracks(self):
        results = compute_strategy_performance([])
        assert results == []

    def test_tracks_without_returns_not_evaluated(self):
        """수익률 미수신 트랙은 적중률 계산에서 제외."""
        tracks = [
            self._make_track("A", {}, hit=False),
            self._make_track("A", {1: 5.0}, hit=True),
        ]
        results = compute_strategy_performance(tracks)
        assert results[0].total_recs == 2
        assert results[0].hits == 1
        assert results[0].hit_rate_pct == 100.0  # 평가된 1건 중 1건 적중


# =========================================================================
# TestComputePerformanceSummary
# =========================================================================

class TestComputePerformanceSummary:
    """compute_performance_summary 함수 테스트."""

    def _make_track(self, strategy, returns_dict, hit):
        return RecommendationTrack(
            ticker="005930", name="삼성전자", strategy=strategy,
            score=80.0, recommended_date="2025-06-10", entry_price=50000,
            returns=returns_dict, hit=hit,
        )

    def test_overall_stats(self):
        tracks = [
            self._make_track("A", {1: 3.0, 3: 5.0}, hit=True),
            self._make_track("B", {1: -1.0, 3: -2.0}, hit=False),
            self._make_track("A", {1: 2.0, 3: 4.0}, hit=True),
        ]
        summary = compute_performance_summary(tracks, start_date="2025-06-01")
        assert summary.total_recs == 3
        assert summary.overall_hit_rate_pct == pytest.approx(66.7, abs=0.1)
        assert len(summary.strategy_breakdown) == 2

    def test_empty_tracks_defaults(self):
        summary = compute_performance_summary([], start_date="2025-06-01")
        assert summary.total_recs == 0
        assert summary.overall_hit_rate_pct == 0.0
        assert summary.overall_avg_return_pct == 0.0

    def test_alpha_computation(self):
        tracks = [
            self._make_track("A", {1: 10.0}, hit=True),
        ]
        summary = compute_performance_summary(
            tracks, start_date="2025-06-01",
            kospi_return=5.0, kosdaq_return=3.0,
        )
        assert summary.alpha_vs_kospi == pytest.approx(5.0, abs=0.1)
        assert summary.alpha_vs_kosdaq == pytest.approx(7.0, abs=0.1)


# =========================================================================
# TestCreatePortfolioSnapshot
# =========================================================================

class TestCreatePortfolioSnapshot:
    """create_portfolio_snapshot 함수 테스트."""

    def test_returns_dict_with_expected_keys(self):
        holdings = [
            {
                "ticker": "005930", "name": "삼성전자",
                "quantity": 100, "avg_price": 70000,
                "current_price": 72000, "profit_pct": 2.86,
            },
        ]
        snap = create_portfolio_snapshot(
            holdings=holdings,
            total_value=10_000_000,
            cash=2_000_000,
            kospi_close=2600.0,
            kosdaq_close=800.0,
        )
        expected_keys = [
            "snapshot_date", "snapshot_ts", "total_value",
            "invested_value", "cash", "total_profit", "total_profit_pct",
            "stock_weight_pct", "cash_weight_pct", "stock_count",
            "kospi_close", "kosdaq_close", "holdings",
        ]
        for key in expected_keys:
            assert key in snap, f"스냅샷에 {key} 키 없음"

    def test_stock_count(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 10, "avg_price": 70000, "current_price": 70000},
            {"ticker": "000660", "name": "SK하이닉스", "quantity": 5, "avg_price": 120000, "current_price": 120000},
        ]
        snap = create_portfolio_snapshot(holdings, total_value=10_000_000, cash=1_000_000)
        assert snap["stock_count"] == 2

    def test_empty_holdings(self):
        snap = create_portfolio_snapshot([], total_value=10_000_000, cash=10_000_000)
        assert snap["stock_count"] == 0
        assert snap["cash_weight_pct"] == 100.0


# =========================================================================
# TestComputeBenchmarkAlpha
# =========================================================================

class TestComputeBenchmarkAlpha:
    """compute_benchmark_alpha 함수 테스트."""

    def test_portfolio_outperforms(self):
        """포트폴리오 +10%, 벤치마크 +5% → 알파 +5%."""
        alpha = compute_benchmark_alpha([10.0], [5.0])
        assert alpha == pytest.approx(5.0, abs=0.01)

    def test_portfolio_underperforms(self):
        alpha = compute_benchmark_alpha([3.0], [8.0])
        assert alpha == pytest.approx(-5.0, abs=0.01)

    def test_empty_portfolio(self):
        alpha = compute_benchmark_alpha([], [5.0])
        assert alpha == 0.0

    def test_empty_benchmark(self):
        """벤치마크 없음 → 벤치마크 0으로 처리."""
        alpha = compute_benchmark_alpha([10.0], [])
        assert alpha == pytest.approx(10.0, abs=0.01)

    def test_multiple_periods(self):
        """여러 기간 평균."""
        port = [2.0, 4.0, 6.0]   # 평균 4.0
        bench = [1.0, 2.0, 3.0]  # 평균 2.0
        alpha = compute_benchmark_alpha(port, bench)
        assert alpha == pytest.approx(2.0, abs=0.01)


# =========================================================================
# TestFormatPerformanceReport
# =========================================================================

class TestFormatPerformanceReport:
    """format_performance_report 함수 테스트."""

    @pytest.fixture
    def sample_summary(self):
        return PerformanceSummary(
            start_date="2025-06-01",
            days_active=30,
            total_recs=15,
            overall_hit_rate_pct=66.7,
            overall_avg_return_pct=3.5,
            alpha_vs_kospi=2.5,
            alpha_vs_kosdaq=1.8,
            strategy_breakdown=[
                StrategyPerformance(
                    strategy="A", total_recs=10, hits=7,
                    hit_rate_pct=70.0, avg_return_pct=4.2,
                    best_return_pct=12.0, worst_return_pct=-3.5,
                ),
                StrategyPerformance(
                    strategy="B", total_recs=5, hits=2,
                    hit_rate_pct=40.0, avg_return_pct=1.1,
                    best_return_pct=5.0, worst_return_pct=-4.0,
                ),
            ],
        )

    def test_no_bold(self, sample_summary):
        text = format_performance_report(sample_summary)
        assert "**" not in text

    def test_contains_username(self, sample_summary):
        text = format_performance_report(sample_summary)
        assert "주호님" in text

    def test_contains_hit_rate_korean(self, sample_summary):
        text = format_performance_report(sample_summary)
        assert "적중률" in text

    def test_contains_alpha_korean(self, sample_summary):
        text = format_performance_report(sample_summary)
        assert "알파" in text

    def test_contains_strategy_breakdown(self, sample_summary):
        text = format_performance_report(sample_summary)
        assert "전략별" in text


# =========================================================================
# TestFormatLiveScorecard
# =========================================================================

class TestFormatLiveScorecard:
    """format_live_scorecard 함수 테스트."""

    def test_contains_d_plus_1(self):
        tracks = [
            RecommendationTrack(
                ticker="005930", name="삼성전자", strategy="A",
                score=85.0, recommended_date="2025-06-10",
                entry_price=70000, returns={1: 2.0, 3: 4.5}, hit=True,
            ),
        ]
        text = format_live_scorecard(tracks)
        assert "D+1" in text

    def test_no_bold(self):
        tracks = [
            RecommendationTrack(
                ticker="005930", name="삼성전자", strategy="A",
                score=85.0, recommended_date="2025-06-10",
                entry_price=70000, returns={1: 2.0}, hit=True,
            ),
        ]
        text = format_live_scorecard(tracks)
        assert "**" not in text

    def test_empty_tracks(self):
        text = format_live_scorecard([])
        assert "추적 중인 추천이 없습니다" in text

    def test_contains_username(self):
        tracks = [
            RecommendationTrack(
                ticker="005930", name="삼성전자", strategy="A",
                score=85.0, recommended_date="2025-06-10",
                entry_price=70000, returns={}, hit=False,
            ),
        ]
        text = format_live_scorecard(tracks)
        assert "주호님" in text
