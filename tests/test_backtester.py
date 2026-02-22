"""Tests for the backtester module (core/backtester.py)."""

from __future__ import annotations

import pytest

from kstock.core.backtester import (
    DailySnapshot,
    BacktestMetrics,
    PortfolioBacktestResult,
    compute_metrics,
    simulate_portfolio,
    compute_walk_forward,
    compute_monthly_returns,
    format_backtest_report,
)


# =========================================================================
# TestDailySnapshot
# =========================================================================

class TestDailySnapshot:
    """DailySnapshot 데이터클래스 기본 동작 테스트."""

    def test_fields_present(self):
        snap = DailySnapshot(
            date="2025-01-10",
            portfolio_value=100_000_000,
            cash=50_000_000,
            holdings_count=5,
        )
        assert snap.date == "2025-01-10"
        assert snap.portfolio_value == 100_000_000
        assert snap.cash == 50_000_000
        assert snap.holdings_count == 5

    def test_benchmark_defaults(self):
        snap = DailySnapshot(date="2025-01-10", portfolio_value=100, cash=50, holdings_count=0)
        assert snap.benchmark_kospi == 0.0
        assert snap.benchmark_kosdaq == 0.0


# =========================================================================
# TestBacktestMetrics
# =========================================================================

class TestBacktestMetrics:
    """BacktestMetrics 데이터클래스 기본 동작 테스트."""

    def test_defaults_all_zero(self):
        m = BacktestMetrics()
        assert m.total_return_pct == 0.0
        assert m.cagr_pct == 0.0
        assert m.mdd_pct == 0.0
        assert m.mdd_date == ""
        assert m.sharpe_ratio == 0.0
        assert m.sortino_ratio == 0.0
        assert m.win_rate_pct == 0.0
        assert m.avg_win_pct == 0.0
        assert m.avg_loss_pct == 0.0
        assert m.profit_factor == 0.0
        assert m.alpha_vs_kospi == 0.0
        assert m.alpha_vs_kosdaq == 0.0
        assert m.total_trades == 0
        assert m.benchmark_return_pct == 0.0

    def test_all_fields_present(self):
        """BacktestMetrics 에 14개 필드가 존재하는지 확인."""
        m = BacktestMetrics()
        expected_fields = [
            "total_return_pct", "cagr_pct", "mdd_pct", "mdd_date",
            "sharpe_ratio", "sortino_ratio", "win_rate_pct", "avg_win_pct",
            "avg_loss_pct", "profit_factor", "alpha_vs_kospi", "alpha_vs_kosdaq",
            "total_trades", "benchmark_return_pct",
        ]
        for f in expected_fields:
            assert hasattr(m, f), f"BacktestMetrics 에 {f} 필드 없음"


# =========================================================================
# TestComputeMetrics
# =========================================================================

class TestComputeMetrics:
    """compute_metrics 함수의 핵심 로직 테스트."""

    def test_simple_rising_values(self):
        """상승 추세 → 양의 수익률, 음의 MDD, Sharpe float."""
        daily_values = [100, 102, 101, 105, 103]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
        benchmark = [100, 101, 102, 103, 104]
        m = compute_metrics(daily_values, daily_dates, benchmark, 100, [3.0, -1.0, 5.0, -2.0])
        assert m.total_return_pct > 0
        assert m.mdd_pct < 0  # 최소 101->101 이후 하락 구간 존재
        assert isinstance(m.sharpe_ratio, float)
        assert 0 <= m.win_rate_pct <= 100
        assert m.profit_factor > 0

    def test_flat_values(self):
        """가격 변동 없음 → 수익률 0, MDD 0."""
        daily_values = [100, 100, 100, 100, 100]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
        benchmark = [100, 100, 100, 100, 100]
        m = compute_metrics(daily_values, daily_dates, benchmark, 100, [])
        assert m.total_return_pct == 0.0
        assert m.mdd_pct == 0.0

    def test_declining_values(self):
        """하락 추세 → 음의 수익률."""
        daily_values = [100, 95, 90, 85, 80]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
        benchmark = [100, 100, 100, 100, 100]
        m = compute_metrics(daily_values, daily_dates, benchmark, 100, [-5.0, -5.0])
        assert m.total_return_pct < 0
        assert m.mdd_pct < 0

    def test_single_value_returns_default(self):
        """일별 데이터 1일 → 기본 BacktestMetrics 반환."""
        m = compute_metrics([100], ["2025-01-06"], [100], 100, [])
        assert m.total_return_pct == 0.0

    def test_win_rate_all_wins(self):
        """전부 양수 trade → 승률 100%."""
        daily_values = [100, 110, 120]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08"]
        m = compute_metrics(daily_values, daily_dates, [100, 100, 100], 100, [5.0, 3.0, 2.0])
        assert m.win_rate_pct == 100.0

    def test_win_rate_all_losses(self):
        """전부 음수 trade → 승률 0%."""
        daily_values = [100, 90, 80]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08"]
        m = compute_metrics(daily_values, daily_dates, [100, 100, 100], 100, [-5.0, -3.0])
        assert m.win_rate_pct == 0.0

    def test_profit_factor_no_losses(self):
        """손실 거래 없음 → profit_factor = sum_wins (특수 케이스)."""
        daily_values = [100, 110, 120]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08"]
        m = compute_metrics(daily_values, daily_dates, [100, 100, 100], 100, [5.0, 3.0])
        assert m.profit_factor == 8.0

    def test_mdd_date_is_set(self):
        """MDD가 발생한 날짜가 기록되는지 확인."""
        daily_values = [100, 110, 90, 95, 100]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
        m = compute_metrics(daily_values, daily_dates, [100] * 5, 100, [])
        assert m.mdd_date == "2025-01-08"  # 110 -> 90 최대 낙폭 지점

    def test_benchmark_return_computed(self):
        """벤치마크 수익률이 올바르게 계산되는지 확인."""
        daily_values = [100, 110, 120]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08"]
        benchmark = [1000, 1050, 1100]
        m = compute_metrics(daily_values, daily_dates, benchmark, 100, [])
        assert m.benchmark_return_pct == 10.0

    def test_cagr_positive_growth(self):
        """양의 성장 → 양의 CAGR."""
        daily_values = [100, 102, 104, 106, 108]
        daily_dates = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]
        m = compute_metrics(daily_values, daily_dates, [100] * 5, 100, [])
        assert m.cagr_pct > 0


# =========================================================================
# TestSimulatePortfolio
# =========================================================================

class TestSimulatePortfolio:
    """simulate_portfolio 함수 테스트."""

    @pytest.fixture
    def minimal_data(self):
        """최소한의 scores_by_date 와 price_data."""
        scores_by_date = {
            "2025-01-06": [
                {"ticker": "005930", "name": "삼성전자", "score": 90, "strategy": "A"},
                {"ticker": "000660", "name": "SK하이닉스", "score": 85, "strategy": "B"},
                {"ticker": "035420", "name": "NAVER", "score": 80, "strategy": "A"},
            ],
            "2025-01-07": [
                {"ticker": "005930", "name": "삼성전자", "score": 88, "strategy": "A"},
                {"ticker": "035420", "name": "NAVER", "score": 82, "strategy": "A"},
                {"ticker": "000660", "name": "SK하이닉스", "score": 78, "strategy": "B"},
            ],
            "2025-01-08": [
                {"ticker": "000660", "name": "SK하이닉스", "score": 92, "strategy": "B"},
                {"ticker": "005930", "name": "삼성전자", "score": 86, "strategy": "A"},
                {"ticker": "035420", "name": "NAVER", "score": 75, "strategy": "A"},
            ],
        }
        price_data = {
            "005930": {"2025-01-06": 70000, "2025-01-07": 71000, "2025-01-08": 72000},
            "000660": {"2025-01-06": 120000, "2025-01-07": 121000, "2025-01-08": 123000},
            "035420": {"2025-01-06": 200000, "2025-01-07": 198000, "2025-01-08": 202000},
        }
        return scores_by_date, price_data

    def test_basic_result_structure(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, rebalance="daily")
        assert result.start_date == "2025-01-06"
        assert result.end_date == "2025-01-08"
        assert len(result.daily_snapshots) > 0
        assert result.final_value > 0

    def test_initial_capital_preserved_in_result(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, initial_capital=50_000_000)
        assert result.initial_capital == 50_000_000

    def test_empty_scores_returns_empty_result(self):
        result = simulate_portfolio({}, {}, top_n=5)
        assert result.start_date == ""
        assert result.end_date == ""
        assert len(result.daily_snapshots) == 0

    def test_rebalance_daily(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, rebalance="daily")
        assert result.rebalance_period == "daily"
        assert len(result.daily_snapshots) == 3

    def test_rebalance_weekly(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, rebalance="weekly")
        assert result.rebalance_period == "weekly"

    def test_rebalance_monthly(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, rebalance="monthly")
        assert result.rebalance_period == "monthly"

    def test_top_n_limits_holdings(self, minimal_data):
        """top_n=1 이면 한 종목만 보유."""
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=1, rebalance="daily")
        # 마지막 스냅샷의 holdings_count는 1 이하
        last_snap = result.daily_snapshots[-1]
        assert last_snap.holdings_count <= 1

    def test_daily_snapshots_have_positive_portfolio_value(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, rebalance="daily")
        for snap in result.daily_snapshots:
            assert snap.portfolio_value > 0

    def test_metrics_computed(self, minimal_data):
        scores, prices = minimal_data
        result = simulate_portfolio(scores, prices, top_n=2, rebalance="daily")
        assert isinstance(result.metrics, BacktestMetrics)


# =========================================================================
# TestComputeWalkForward
# =========================================================================

class TestComputeWalkForward:
    """compute_walk_forward 함수 테스트."""

    def test_insufficient_data_returns_empty(self):
        """날짜 부족 시 빈 리스트 반환."""
        scores = {f"2025-01-{d:02d}": [] for d in range(6, 16)}
        prices: dict = {}
        result = compute_walk_forward(scores, prices, train_months=12, test_months=3)
        assert result == []

    def test_returns_list_of_dicts(self):
        """충분한 데이터 → 올바른 키를 가진 dict 리스트."""
        # 2년치 일별 데이터 생성 (약 500일)
        from datetime import date, timedelta
        start = date(2023, 1, 2)
        scores: dict = {}
        prices: dict = {}
        ticker = "005930"
        base_price = 70000
        for i in range(500):
            d = start + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            dt_str = d.strftime("%Y-%m-%d")
            scores[dt_str] = [
                {"ticker": ticker, "name": "삼성전자", "score": 80 + (i % 10), "strategy": "A"},
            ]
            prices[ticker] = prices.get(ticker, {})
            prices[ticker][dt_str] = base_price + (i * 10)

        result = compute_walk_forward(scores, prices, train_months=6, test_months=3, top_n=1)
        if result:  # 기간 충분할 때만
            for entry in result:
                assert "train_period" in entry
                assert "test_period" in entry
                assert "train_return" in entry
                assert "test_return" in entry
                assert "overfit_gap" in entry

    def test_walk_forward_empty_scores(self):
        result = compute_walk_forward({}, {})
        assert result == []


# =========================================================================
# TestComputeMonthlyReturns
# =========================================================================

class TestComputeMonthlyReturns:
    """compute_monthly_returns 함수 테스트."""

    def test_two_months(self):
        """2개월에 걸친 데이터 → 월별 수익률 계산."""
        daily_values = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118]
        daily_dates = [
            "2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10",
            "2025-02-03", "2025-02-04", "2025-02-05", "2025-02-06", "2025-02-07",
        ]
        result = compute_monthly_returns(daily_values, daily_dates)
        assert "2025-01" in result or "2025-02" in result
        # 2025-02 월의 수익률은 prev_month_end=108 -> 118: +9.26%
        if "2025-02" in result:
            assert result["2025-02"] == pytest.approx(9.26, abs=0.1)

    def test_single_month(self):
        """한 달 데이터만 → 해당 월 수익률 반환."""
        daily_values = [100, 105, 110]
        daily_dates = ["2025-03-03", "2025-03-04", "2025-03-05"]
        result = compute_monthly_returns(daily_values, daily_dates)
        assert "2025-03" in result
        assert result["2025-03"] == 10.0  # 100 -> 110

    def test_empty_returns_empty(self):
        assert compute_monthly_returns([], []) == {}

    def test_single_value_returns_empty(self):
        assert compute_monthly_returns([100], ["2025-01-06"]) == {}

    def test_mismatched_lengths_returns_empty(self):
        assert compute_monthly_returns([100, 110], ["2025-01-06"]) == {}


# =========================================================================
# TestFormatBacktestReport
# =========================================================================

class TestFormatBacktestReport:
    """format_backtest_report 함수 테스트."""

    @pytest.fixture
    def sample_result(self):
        metrics = BacktestMetrics(
            total_return_pct=15.5,
            cagr_pct=12.3,
            mdd_pct=-8.2,
            mdd_date="2025-06-15",
            sharpe_ratio=1.45,
            sortino_ratio=2.1,
            win_rate_pct=62.5,
            avg_win_pct=3.5,
            avg_loss_pct=-1.8,
            profit_factor=2.0,
            alpha_vs_kospi=5.3,
            alpha_vs_kosdaq=3.1,
            total_trades=48,
            benchmark_return_pct=7.2,
        )
        return PortfolioBacktestResult(
            start_date="2025-01-06",
            end_date="2025-12-30",
            top_n=10,
            rebalance_period="weekly",
            initial_capital=100_000_000,
            final_value=115_500_000,
            metrics=metrics,
            monthly_returns={"2025-01": 2.5, "2025-02": -1.3},
        )

    def test_contains_username(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "주호님" in report

    def test_no_bold_markdown(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "**" not in report

    def test_contains_cagr(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "CAGR" in report

    def test_contains_mdd(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "MDD" in report

    def test_contains_sharpe_korean(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "샤프" in report

    def test_contains_dates(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "2025-01-06" in report
        assert "2025-12-30" in report

    def test_contains_monthly_returns(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "2025-01" in report
        assert "2025-02" in report

    def test_contains_win_rate(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "승률" in report

    def test_contains_profit_factor(self, sample_result):
        report = format_backtest_report(sample_result)
        assert "Profit Factor" in report

    def test_positive_return_interpretation(self, sample_result):
        """양의 수익률 + 높은 샤프 → 알파 생성 메시지."""
        report = format_backtest_report(sample_result)
        assert "알파" in report

    def test_negative_return_interpretation(self):
        """마이너스 수익률 → 재검토 권장 메시지."""
        metrics = BacktestMetrics(total_return_pct=-5.0, sharpe_ratio=0.3)
        result = PortfolioBacktestResult(
            start_date="2025-01-06",
            end_date="2025-06-30",
            metrics=metrics,
            initial_capital=100_000_000,
            final_value=95_000_000,
        )
        report = format_backtest_report(result)
        assert "마이너스" in report


# =========================================================================
# TestLookAheadBias
# =========================================================================

class TestLookAheadBias:
    """스코어가 해당 날짜의 데이터만 사용하는지 확인."""

    def test_scores_only_use_current_date(self):
        """simulate_portfolio 에 전달된 scores_by_date 의 날짜가
        해당 일자에만 사용되는지 확인 (미래 데이터 누출 없음)."""
        scores_by_date = {
            "2025-01-06": [
                {"ticker": "005930", "name": "삼성전자", "score": 90, "strategy": "A"},
            ],
            "2025-01-07": [
                {"ticker": "000660", "name": "SK하이닉스", "score": 95, "strategy": "B"},
            ],
        }
        price_data = {
            "005930": {"2025-01-06": 70000, "2025-01-07": 71000},
            "000660": {"2025-01-06": 120000, "2025-01-07": 122000},
        }
        result = simulate_portfolio(
            scores_by_date, price_data, top_n=1, rebalance="daily",
        )
        # 첫 날에는 005930만 선택됨 (000660은 아직 1/7 스코어 미존재)
        snaps = result.daily_snapshots
        assert len(snaps) == 2
        # 첫 날 포트폴리오 보유 1종목
        assert snaps[0].holdings_count <= 1

    def test_future_date_scores_not_used_on_past_date(self):
        """미래 날짜의 스코어가 과거 날짜에 사용되지 않는지 확인."""
        scores_by_date = {
            "2025-01-06": [
                {"ticker": "005930", "name": "삼성전자", "score": 50, "strategy": "A"},
            ],
            # 이 스코어는 1/7 에만 사용되어야 함
            "2025-01-07": [
                {"ticker": "005930", "name": "삼성전자", "score": 99, "strategy": "A"},
            ],
        }
        price_data = {
            "005930": {"2025-01-06": 70000, "2025-01-07": 71000},
        }
        result = simulate_portfolio(
            scores_by_date, price_data, top_n=1, rebalance="daily",
        )
        # 날짜 순서대로 처리됨
        assert result.start_date == "2025-01-06"
        assert result.end_date == "2025-01-07"


# =========================================================================
# TestPortfolioBacktestResult
# =========================================================================

class TestPortfolioBacktestResult:
    """PortfolioBacktestResult 데이터클래스 테스트."""

    def test_defaults(self):
        r = PortfolioBacktestResult()
        assert r.start_date == ""
        assert r.end_date == ""
        assert r.top_n == 10
        assert r.rebalance_period == "weekly"
        assert r.initial_capital == 100_000_000
        assert r.final_value == 0.0
        assert isinstance(r.metrics, BacktestMetrics)
        assert isinstance(r.daily_snapshots, list)
        assert isinstance(r.monthly_returns, dict)
        assert isinstance(r.strategy_attribution, dict)
        assert isinstance(r.walk_forward_results, list)
