"""classify_hold_type — horizon 매핑 테스트."""
from __future__ import annotations

import pytest
from kstock.core.investor_profile import (
    HORIZON_TO_HOLD_TYPE,
    classify_hold_type,
)


class TestHorizonToHoldType:
    """HORIZON_TO_HOLD_TYPE 매핑 상수."""

    def test_mapping_keys(self):
        assert set(HORIZON_TO_HOLD_TYPE) == {"danta", "dangi", "junggi", "janggi"}

    def test_mapping_values(self):
        assert HORIZON_TO_HOLD_TYPE["danta"] == "scalp"
        assert HORIZON_TO_HOLD_TYPE["dangi"] == "swing"
        assert HORIZON_TO_HOLD_TYPE["junggi"] == "position"
        assert HORIZON_TO_HOLD_TYPE["janggi"] == "long_term"


class TestClassifyHoldTypeWithHorizon:
    """classify_hold_type에서 horizon 우선순위 테스트."""

    def test_explicit_holding_type_overrides_horizon(self):
        """holding_type이 명시되면 horizon보다 우선."""
        h = {"buy_date": "2020-01-01", "holding_type": "scalp", "horizon": "janggi"}
        assert classify_hold_type(h) == "scalp"

    def test_horizon_overrides_auto_classification(self):
        """horizon이 있으면 buy_date 자동분류보다 우선."""
        # buy_date가 5일 전이면 자동으로 swing이지만, horizon=janggi → long_term
        h = {"buy_date": "2026-02-20", "horizon": "janggi"}
        assert classify_hold_type(h) == "long_term"

    def test_horizon_danta(self):
        h = {"buy_date": "2020-01-01", "horizon": "danta"}
        assert classify_hold_type(h) == "scalp"

    def test_horizon_dangi(self):
        h = {"buy_date": "2020-01-01", "horizon": "dangi"}
        assert classify_hold_type(h) == "swing"

    def test_horizon_junggi(self):
        h = {"buy_date": "2020-01-01", "horizon": "junggi"}
        assert classify_hold_type(h) == "position"

    def test_horizon_janggi(self):
        h = {"buy_date": "2020-01-01", "horizon": "janggi"}
        assert classify_hold_type(h) == "long_term"

    def test_unknown_horizon_falls_through(self):
        """알 수 없는 horizon 값은 무시 → buy_date 기반 분류."""
        h = {"buy_date": "2020-01-01", "horizon": "unknown_value"}
        assert classify_hold_type(h) == "long_term"  # 오래된 buy_date

    def test_empty_horizon_falls_through(self):
        """빈 horizon은 무시."""
        h = {"buy_date": "2020-01-01", "horizon": ""}
        assert classify_hold_type(h) == "long_term"

    def test_no_horizon_no_holding_type(self):
        """horizon, holding_type 모두 없으면 buy_date 기반."""
        h = {"buy_date": "2026-02-25"}
        result = classify_hold_type(h)
        assert result in ("scalp", "swing", "position", "long_term")

    def test_leverage_overrides_horizon(self):
        """holding_type 없고 horizon 없으면 leverage → scalp."""
        h = {"buy_date": "2020-01-01", "leverage_flag": True}
        assert classify_hold_type(h) == "scalp"

    def test_horizon_overrides_leverage(self):
        """horizon이 있으면 leverage보다 우선."""
        h = {"buy_date": "2020-01-01", "horizon": "janggi", "leverage_flag": True}
        assert classify_hold_type(h) == "long_term"
