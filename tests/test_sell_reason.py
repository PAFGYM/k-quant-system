"""Tests for the sell reason classifier."""

from __future__ import annotations

import pytest

from kstock.signal.sell_reason import SellReason, SellReasonInput, classify_sell_reason


@pytest.fixture
def thresholds() -> dict:
    """Test thresholds matching scoring.yaml sell_reason section."""
    return {
        "macro_risk_off": {
            "spx_drop_pct": -1.2,
            "vix_spike_pct": 10.0,
            "usdkrw_spike_pct": 0.6,
            "broad_sell_pct": 70.0,
        },
        "flow_mechanical": {
            "program_ratio_pct": 25.0,
            "sector_corr": 0.7,
            "basis_pct": -0.3,
        },
        "idiosyncratic": {
            "consensus_drop_pct": -5.0,
        },
        "technical_sell": {
            "near_high_pct": 95.0,
            "volume_multiplier": 2.5,
            "disparity_pct": 110.0,
        },
    }


class TestTypeA:
    """Type A: Macro Risk-off."""

    def test_full_macro_risk_off(self, thresholds: dict) -> None:
        """All macro signals triggered = Type A with high confidence."""
        data = SellReasonInput(
            spx_change_pct=-2.0,
            vix_change_pct=15.0,
            usdkrw_change_pct=1.0,
            broad_sell_pct=80.0,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "A"
        assert result.confidence == 1.0
        assert len(result.sub_signals) == 4

    def test_partial_macro_risk_off(self, thresholds: dict) -> None:
        """Two macro signals = Type A with 0.5 confidence."""
        data = SellReasonInput(
            spx_change_pct=-1.5,
            vix_change_pct=12.0,
            usdkrw_change_pct=0.3,  # below threshold
            broad_sell_pct=50.0,  # below threshold
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "A"
        assert result.confidence == 0.5

    def test_no_macro_risk_off(self, thresholds: dict) -> None:
        """No macro signals = not Type A."""
        data = SellReasonInput(
            spx_change_pct=0.5,
            vix_change_pct=2.0,
            usdkrw_change_pct=0.1,
            broad_sell_pct=30.0,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code != "A"


class TestTypeB:
    """Type B: Flow/Mechanical."""

    def test_program_and_sector(self, thresholds: dict) -> None:
        """High program ratio + sector correlation = Type B."""
        data = SellReasonInput(
            program_ratio_pct=30.0,
            sector_corr=0.8,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "B"
        assert result.confidence >= 0.7

    def test_program_and_basis(self, thresholds: dict) -> None:
        """High program ratio + negative basis = Type B."""
        data = SellReasonInput(
            program_ratio_pct=28.0,
            basis_pct=-0.5,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "B"

    def test_program_only(self, thresholds: dict) -> None:
        """Program ratio alone = Type B with lower confidence."""
        data = SellReasonInput(
            program_ratio_pct=30.0,
            sector_corr=0.3,
            basis_pct=0.1,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "B"
        assert result.confidence == 0.4


class TestTypeC:
    """Type C: Idiosyncratic."""

    def test_stock_drop_with_consensus(self, thresholds: dict) -> None:
        """Stock-only drop + consensus downgrade = Type C."""
        data = SellReasonInput(
            stock_only_drop=True,
            consensus_change_pct=-8.0,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "C"
        assert result.confidence >= 0.7

    def test_stock_drop_with_dart(self, thresholds: dict) -> None:
        """Stock-only drop + DART event = Type C."""
        data = SellReasonInput(
            stock_only_drop=True,
            dart_event=True,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "C"

    def test_all_idiosyncratic(self, thresholds: dict) -> None:
        """All idiosyncratic signals = high confidence Type C."""
        data = SellReasonInput(
            stock_only_drop=True,
            consensus_change_pct=-10.0,
            dart_event=True,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "C"
        assert result.confidence == 1.0


class TestTypeD:
    """Type D: Technical."""

    def test_full_technical_sell(self, thresholds: dict) -> None:
        """All technical signals = Type D with high confidence."""
        data = SellReasonInput(
            near_high_pct=97.0,
            volume_ratio=3.0,
            disparity_pct=115.0,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "D"
        assert result.confidence == 1.0

    def test_partial_technical(self, thresholds: dict) -> None:
        """One technical signal = Type D with low confidence."""
        data = SellReasonInput(
            near_high_pct=96.0,
            volume_ratio=1.5,  # below threshold
            disparity_pct=105.0,  # below threshold
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "D"
        assert result.confidence == 0.33


class TestUnclassified:
    """Unclassified cases."""

    def test_no_signals(self, thresholds: dict) -> None:
        """No signals triggered = Unclassified."""
        data = SellReasonInput()
        result = classify_sell_reason(data, thresholds)
        assert result.code == "U"
        assert result.confidence == 0.0

    def test_below_all_thresholds(self, thresholds: dict) -> None:
        """All values below thresholds = Unclassified."""
        data = SellReasonInput(
            spx_change_pct=0.5,
            vix_change_pct=2.0,
            usdkrw_change_pct=0.1,
            broad_sell_pct=20.0,
            program_ratio_pct=10.0,
            sector_corr=0.3,
            basis_pct=0.1,
            near_high_pct=80.0,
            volume_ratio=1.0,
            disparity_pct=102.0,
        )
        result = classify_sell_reason(data, thresholds)
        assert result.code == "U"


class TestPriority:
    """When multiple types match, highest confidence wins."""

    def test_competing_signals(self, thresholds: dict) -> None:
        """Multiple types triggered: highest confidence wins."""
        data = SellReasonInput(
            # Type A signals (all 4 = confidence 1.0)
            spx_change_pct=-2.0,
            vix_change_pct=15.0,
            usdkrw_change_pct=1.0,
            broad_sell_pct=80.0,
            # Type D signals (all 3 = confidence 1.0)
            near_high_pct=97.0,
            volume_ratio=3.0,
            disparity_pct=115.0,
        )
        result = classify_sell_reason(data, thresholds)
        # Both A and D have confidence 1.0; A comes first in candidates list
        assert result.code in ("A", "D")
        assert result.confidence == 1.0


class TestSellReasonDataclass:
    """Test SellReason dataclass structure."""

    def test_sell_reason_fields(self) -> None:
        """SellReason has all required fields."""
        reason = SellReason(
            code="A",
            label="Macro Risk-off",
            confidence=0.75,
            rationale="Test rationale",
            sub_signals=["signal1", "signal2"],
        )
        assert reason.code == "A"
        assert reason.label == "Macro Risk-off"
        assert reason.confidence == 0.75
        assert reason.rationale == "Test rationale"
        assert len(reason.sub_signals) == 2

    def test_default_sub_signals(self) -> None:
        """SellReason sub_signals defaults to empty list."""
        reason = SellReason(
            code="U",
            label="Unknown",
            confidence=0.0,
            rationale="No signals",
        )
        assert reason.sub_signals == []
