"""고급 실행 알고리즘 모듈 테스트 — VWAP, TWAP, 슬리피지, 분할주문, 품질평가."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.broker.execution_algo import (
    ExecutionQuality,
    SlippageModel,
    SplitOrder,
    TWAPSchedule,
    VWAPSchedule,
    compute_twap_schedule,
    compute_vwap_schedule,
    estimate_dynamic_slippage,
    evaluate_execution,
    format_execution_plan,
    format_execution_quality,
    plan_split_order,
)


# ── 공통 fixture ──────────────────────────────────────────

@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """390분 (09:00~15:30) 분봉 OHLCV 샘플 데이터."""
    idx = pd.date_range("2026-03-02 09:00", periods=390, freq="1min", tz="Asia/Seoul")
    rng = np.random.default_rng(42)
    prices = 50000 + np.cumsum(rng.normal(0, 50, 390))
    # 거래량 프로파일: 개장/마감 높고 점심 낮음
    volume_base = np.concatenate([
        rng.integers(5000, 15000, 30),   # 09:00-09:30 높음
        rng.integers(3000, 8000, 120),   # 09:30-11:30 중간
        rng.integers(1000, 4000, 150),   # 11:30-14:00 낮음
        rng.integers(4000, 12000, 90),   # 14:00-15:30 높음
    ])
    return pd.DataFrame({
        "open": prices - rng.uniform(0, 100, 390),
        "high": prices + rng.uniform(0, 200, 390),
        "low": prices - rng.uniform(0, 200, 390),
        "close": prices,
        "volume": volume_base,
    }, index=idx)


@pytest.fixture
def empty_ohlcv() -> pd.DataFrame:
    """빈 OHLCV 데이터프레임."""
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


# ── TestVWAP ──────────────────────────────────────────────

class TestVWAP:
    """VWAP 스케줄 생성 테스트."""

    def test_slice_pct_sum_approx_one(self, sample_ohlcv: pd.DataFrame):
        """시간대별 비중 합이 대략 1.0이어야 한다."""
        schedule = compute_vwap_schedule(1000, sample_ohlcv)
        assert isinstance(schedule, VWAPSchedule)
        pct_sum = sum(schedule.target_pct_by_slice)
        assert abs(pct_sum - 1.0) < 0.01

    def test_participation_rate_reflected(self, sample_ohlcv: pd.DataFrame):
        """participation_rate가 time_slices에 저장되어야 한다."""
        schedule = compute_vwap_schedule(1000, sample_ohlcv, participation_rate=0.05)
        for s in schedule.time_slices:
            assert s["participation_rate"] == 0.05

    def test_total_qty_distributed(self, sample_ohlcv: pd.DataFrame):
        """모든 슬라이스 수량 합 = total_qty."""
        schedule = compute_vwap_schedule(1000, sample_ohlcv)
        total = sum(s["target_qty"] for s in schedule.time_slices)
        assert total == 1000

    def test_estimated_vwap_positive(self, sample_ohlcv: pd.DataFrame):
        """estimated_vwap가 양수여야 한다."""
        schedule = compute_vwap_schedule(500, sample_ohlcv)
        assert schedule.estimated_vwap > 0

    def test_empty_ohlcv_uses_defaults(self, empty_ohlcv: pd.DataFrame):
        """빈 OHLCV → 기본 프로파일 사용, 에러 없이 동작."""
        schedule = compute_vwap_schedule(1000, empty_ohlcv)
        assert len(schedule.time_slices) == 4
        total = sum(s["target_qty"] for s in schedule.time_slices)
        assert total == 1000

    def test_zero_qty(self, sample_ohlcv: pd.DataFrame):
        """수량 0 → 빈 스케줄."""
        schedule = compute_vwap_schedule(0, sample_ohlcv)
        assert schedule.time_slices == []
        assert schedule.estimated_vwap == 0.0

    def test_four_slices(self, sample_ohlcv: pd.DataFrame):
        """한국 시장 기본 4개 타임 슬라이스."""
        schedule = compute_vwap_schedule(2000, sample_ohlcv)
        assert len(schedule.time_slices) == 4

    def test_expected_cost_scales_with_participation(self, sample_ohlcv: pd.DataFrame):
        """참여율이 높을수록 예상 비용 증가."""
        low = compute_vwap_schedule(1000, sample_ohlcv, participation_rate=0.05)
        high = compute_vwap_schedule(1000, sample_ohlcv, participation_rate=0.30)
        assert high.expected_cost_bps > low.expected_cost_bps


# ── TestTWAP ──────────────────────────────────────────────

class TestTWAP:
    """TWAP 스케줄 생성 테스트."""

    def test_equal_split(self):
        """균등 분할 수량 확인."""
        schedule = compute_twap_schedule(120, duration_minutes=60, interval_minutes=10)
        assert isinstance(schedule, TWAPSchedule)
        assert schedule.n_slices == 6
        assert schedule.qty_per_slice == 20

    def test_qty_sum_equals_total(self):
        """qty_per_slice * n_slices + remainder = total_qty."""
        schedule = compute_twap_schedule(100, duration_minutes=60, interval_minutes=10)
        distributed = schedule.qty_per_slice * schedule.n_slices
        remainder = schedule.total_qty - distributed
        assert distributed + remainder == 100

    def test_interval_preserved(self):
        """interval_minutes가 보존되어야 한다."""
        schedule = compute_twap_schedule(500, duration_minutes=120, interval_minutes=15)
        assert schedule.interval_minutes == 15

    def test_zero_qty(self):
        """수량 0 → n_slices=0."""
        schedule = compute_twap_schedule(0)
        assert schedule.n_slices == 0

    def test_single_slice(self):
        """duration <= interval → 1개 슬라이스."""
        schedule = compute_twap_schedule(100, duration_minutes=10, interval_minutes=10)
        assert schedule.n_slices == 1
        assert schedule.qty_per_slice == 100

    def test_remainder_handling(self):
        """나머지 수량이 올바르게 계산."""
        schedule = compute_twap_schedule(103, duration_minutes=50, interval_minutes=10)
        assert schedule.n_slices == 5
        assert schedule.qty_per_slice == 20
        # 103 - 20*5 = 3 잔여
        assert schedule.total_qty == 103


# ── TestSlippage ──────────────────────────────────────────

class TestSlippage:
    """동적 슬리피지 추정 테스트."""

    def test_large_order_high_slippage(self):
        """큰 주문 → 높은 슬리피지."""
        large = estimate_dynamic_slippage(
            order_qty=100000, price=50000, avg_volume=200000,
            volatility_pct=3.0, time_of_day="mid", market_regime="normal",
        )
        small = estimate_dynamic_slippage(
            order_qty=100, price=50000, avg_volume=200000,
            volatility_pct=3.0, time_of_day="mid", market_regime="normal",
        )
        assert isinstance(large, SlippageModel)
        assert large.total_bps > small.total_bps

    def test_small_order_low_slippage(self):
        """작은 주문 (거래량 1% 미만) → volume_adj 최소."""
        result = estimate_dynamic_slippage(
            order_qty=10, price=50000, avg_volume=1000000,
            volatility_pct=1.0, time_of_day="mid", market_regime="normal",
        )
        # 작은 주문: vol_ratio = 10/1000000 = 0.00001 < 0.01
        assert result.volume_adj_bps == 0.0

    def test_crisis_adds_cost(self):
        """crisis 레짐 → 추가 비용."""
        normal = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, market_regime="normal",
        )
        crisis = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, market_regime="crisis",
        )
        assert crisis.total_bps > normal.total_bps
        assert crisis.total_bps - normal.total_bps >= 10.0  # crisis +10bps

    def test_volatile_regime_adds_cost(self):
        """volatile 레짐 → normal 대비 +5bps."""
        normal = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, market_regime="normal",
        )
        volatile = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, market_regime="volatile",
        )
        assert volatile.total_bps - normal.total_bps >= 5.0

    def test_open_time_adds_cost(self):
        """개장 시간대 → +5bps."""
        mid = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, time_of_day="mid",
        )
        opening = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, time_of_day="open",
        )
        assert opening.total_bps - mid.total_bps >= 5.0

    def test_lunch_time_adds_cost(self):
        """점심 시간대 → +3bps."""
        mid = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, time_of_day="mid",
        )
        lunch = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, time_of_day="lunch",
        )
        assert lunch.total_bps - mid.total_bps >= 3.0

    def test_regime_field(self):
        """regime 필드가 입력값과 일치."""
        result = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=100000,
            volatility_pct=2.0, market_regime="crisis",
        )
        assert result.regime == "crisis"

    def test_zero_volume_conservative(self):
        """거래량 0 → 보수적 추정."""
        result = estimate_dynamic_slippage(
            order_qty=1000, price=50000, avg_volume=0,
            volatility_pct=2.0,
        )
        assert result.volume_adj_bps == 10.0


# ── TestSplitOrder ────────────────────────────────────────

class TestSplitOrder:
    """분할 주문 계획 테스트."""

    def test_vwap_child_qty_sum(self, sample_ohlcv: pd.DataFrame):
        """VWAP: child 수량 합 = total."""
        split = plan_split_order(1000, 50000, 500000, algo="vwap", ohlcv=sample_ohlcv)
        assert isinstance(split, SplitOrder)
        total = sum(c["qty"] for c in split.child_orders)
        assert total == 1000

    def test_twap_child_qty_sum(self):
        """TWAP: child 수량 합 = total."""
        split = plan_split_order(500, 50000, 500000, algo="twap")
        total = sum(c["qty"] for c in split.child_orders)
        assert total == 500

    def test_iceberg_child_qty_sum(self):
        """Iceberg: child 수량 합 = total."""
        split = plan_split_order(1000, 50000, 500000, algo="iceberg")
        total = sum(c["qty"] for c in split.child_orders)
        assert total == 1000

    def test_pov_child_qty_sum(self):
        """PoV: child 수량 합 = total."""
        split = plan_split_order(1000, 50000, 500000, algo="pov")
        total = sum(c["qty"] for c in split.child_orders)
        assert total == 1000

    def test_algo_difference(self, sample_ohlcv: pd.DataFrame):
        """알고리즘별로 child 구조가 다르다."""
        vwap = plan_split_order(1000, 50000, 500000, algo="vwap", ohlcv=sample_ohlcv)
        twap = plan_split_order(1000, 50000, 500000, algo="twap")
        iceberg = plan_split_order(1000, 50000, 500000, algo="iceberg")
        # 슬라이스 수가 다를 수 있다
        assert vwap.algo == "vwap"
        assert twap.algo == "twap"
        assert iceberg.algo == "iceberg"

    def test_urgency_low_vs_high(self, sample_ohlcv: pd.DataFrame):
        """urgency 높을수록 참여율/분할 방식이 달라진다."""
        low = plan_split_order(
            1000, 50000, 500000, algo="twap", urgency="low",
        )
        high = plan_split_order(
            1000, 50000, 500000, algo="twap", urgency="high",
        )
        # low: duration=240min → 24 slices, high: duration=60min → 6 slices
        assert len(low.child_orders) > len(high.child_orders)

    def test_parent_order_id_unique(self):
        """parent_order_id가 호출마다 고유."""
        a = plan_split_order(100, 50000, 500000, algo="twap")
        b = plan_split_order(100, 50000, 500000, algo="twap")
        assert a.parent_order_id != b.parent_order_id

    def test_status_is_planned(self):
        """초기 status = 'planned'."""
        split = plan_split_order(100, 50000, 500000, algo="twap")
        assert split.status == "planned"
        assert split.filled_qty == 0

    def test_unknown_algo_fallback(self):
        """알 수 없는 알고 → TWAP fallback."""
        split = plan_split_order(100, 50000, 500000, algo="unknown_algo")
        assert len(split.child_orders) > 0
        total = sum(c["qty"] for c in split.child_orders)
        assert total == 100

    def test_child_has_required_fields(self, sample_ohlcv: pd.DataFrame):
        """각 child_order에 필수 필드가 있어야 한다."""
        split = plan_split_order(1000, 50000, 500000, algo="vwap", ohlcv=sample_ohlcv)
        for child in split.child_orders:
            assert "slice_id" in child
            assert "qty" in child
            assert "target_time" in child
            assert "price_limit" in child


# ── TestExecutionQuality ──────────────────────────────────

class TestExecutionQuality:
    """실행 품질 평가 테스트."""

    def test_benchmark_match_grade_a(self, sample_ohlcv: pd.DataFrame):
        """벤치마크와 일치하는 체결 → A 등급."""
        fills = [{"price": 50000.0, "qty": 100, "time": "09:30"}]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        assert isinstance(quality, ExecutionQuality)
        assert quality.grade == "A"
        assert abs(quality.slippage_bps) < 1.0

    def test_large_slippage_grade_d(self, sample_ohlcv: pd.DataFrame):
        """큰 슬리피지 → D 등급."""
        # 벤치마크 50000, 체결가 50200 → 40bps
        fills = [{"price": 50200.0, "qty": 100, "time": "10:00"}]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        assert quality.grade == "D"
        assert quality.slippage_bps >= 30.0

    def test_moderate_slippage_grade_b(self, sample_ohlcv: pd.DataFrame):
        """중간 슬리피지 → B 등급 (5~15bps)."""
        # 50000 * 10bps = 50 → 체결가 50050
        fills = [{"price": 50050.0, "qty": 100, "time": "10:00"}]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        assert quality.grade == "B"

    def test_empty_fills_grade_d(self, sample_ohlcv: pd.DataFrame):
        """빈 체결 → D 등급."""
        quality = evaluate_execution([], 50000.0, sample_ohlcv)
        assert quality.grade == "D"

    def test_weighted_avg_fill(self, sample_ohlcv: pd.DataFrame):
        """가중 평균 체결가 정확성."""
        fills = [
            {"price": 50000.0, "qty": 300, "time": "09:30"},
            {"price": 50100.0, "qty": 100, "time": "10:00"},
        ]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        expected_avg = (50000 * 300 + 50100 * 100) / 400
        assert abs(quality.avg_fill_price - expected_avg) < 0.01

    def test_negative_slippage_still_graded(self, sample_ohlcv: pd.DataFrame):
        """벤치마크보다 좋은 체결 (음의 슬리피지) → A 등급 가능."""
        fills = [{"price": 49990.0, "qty": 100, "time": "09:30"}]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        assert quality.slippage_bps < 0
        assert quality.grade == "A"  # abs(-2bps) < 5


# ── TestFormat ────────────────────────────────────────────

class TestFormat:
    """포맷 함수 테스트."""

    def test_format_execution_plan_returns_str(self):
        """format_execution_plan → str 반환."""
        split = plan_split_order(500, 50000, 500000, algo="twap")
        result = format_execution_plan(split)
        assert isinstance(result, str)
        assert "TWAP" in result
        assert "500" in result

    def test_format_execution_quality_returns_str(self, sample_ohlcv: pd.DataFrame):
        """format_execution_quality → str 반환."""
        fills = [{"price": 50000.0, "qty": 100, "time": "09:30"}]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        result = format_execution_quality(quality)
        assert isinstance(result, str)
        assert "등급" in result
        assert quality.grade in result

    def test_format_plan_contains_child_info(self):
        """포맷 결과에 child order 정보가 포함."""
        split = plan_split_order(1000, 50000, 500000, algo="iceberg")
        result = format_execution_plan(split)
        assert "ICEBERG" in result
        assert "#" in result  # slice_id

    def test_format_quality_contains_bps(self, sample_ohlcv: pd.DataFrame):
        """포맷 결과에 bps 정보가 포함."""
        fills = [{"price": 50100.0, "qty": 100, "time": "10:00"}]
        quality = evaluate_execution(fills, 50000.0, sample_ohlcv)
        result = format_execution_quality(quality)
        assert "bps" in result
