"""Tests for the foreign flow direction predictor."""

from __future__ import annotations

import pytest

from kstock.signal.foreign_predictor import (
    ForeignPrediction,
    format_foreign_prediction,
    predict_foreign_flow,
)


# ---------------------------------------------------------------------------
# ForeignPrediction dataclass
# ---------------------------------------------------------------------------


class TestForeignPredictionDataclass:
    """Tests for the ForeignPrediction dataclass."""

    def test_dataclass_instantiation(self) -> None:
        pred = ForeignPrediction(
            fx_signal="inflow",
            us_market_signal="inflow",
            msci_signal="inflow",
            futures_signal="inflow",
            dxy_signal="inflow",
            inflow_count=5,
            outflow_count=0,
            prediction="강한 유입",
            score_adj=10,
        )
        assert pred.inflow_count == 5
        assert pred.prediction == "강한 유입"

    def test_dataclass_fields(self) -> None:
        pred = ForeignPrediction(
            fx_signal="outflow",
            us_market_signal="outflow",
            msci_signal="outflow",
            futures_signal="outflow",
            dxy_signal="outflow",
            inflow_count=0,
            outflow_count=5,
            prediction="강한 유출",
            score_adj=-10,
        )
        assert pred.outflow_count == 5
        assert pred.score_adj == -10


# ---------------------------------------------------------------------------
# predict_foreign_flow – all inflow
# ---------------------------------------------------------------------------


class TestPredictForeignFlowAllInflow:
    """Tests for predict_foreign_flow with all signals pointing to inflow."""

    def test_all_inflow_strong_prediction(self) -> None:
        """All 5 signals inflow -> 강한 유입, +10."""
        pred = predict_foreign_flow(
            usdkrw=1280,
            usdkrw_20d_ma=1300,    # FX < MA -> inflow
            spx_change_pct=1.0,     # positive -> inflow
            msci_em_flow="inflow",  # inflow
            foreign_futures_net_krw=500,  # positive -> inflow
            dxy=103.0,
            dxy_change_pct=-0.5,    # negative -> inflow
        )
        assert pred.inflow_count == 5
        assert pred.outflow_count == 0
        assert pred.prediction == "강한 유입"
        assert pred.score_adj == 10

    def test_all_signals_are_inflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1280, usdkrw_20d_ma=1300,
            spx_change_pct=1.0, msci_em_flow="inflow",
            foreign_futures_net_krw=500, dxy=103.0, dxy_change_pct=-0.5,
        )
        assert pred.fx_signal == "inflow"
        assert pred.us_market_signal == "inflow"
        assert pred.msci_signal == "inflow"
        assert pred.futures_signal == "inflow"
        assert pred.dxy_signal == "inflow"


# ---------------------------------------------------------------------------
# predict_foreign_flow – all outflow
# ---------------------------------------------------------------------------


class TestPredictForeignFlowAllOutflow:
    """Tests for predict_foreign_flow with all signals pointing to outflow."""

    def test_all_outflow_strong_prediction(self) -> None:
        """All 5 signals outflow -> 강한 유출, -10."""
        pred = predict_foreign_flow(
            usdkrw=1320,
            usdkrw_20d_ma=1300,     # FX > MA -> outflow
            spx_change_pct=-1.0,     # negative -> outflow
            msci_em_flow="outflow",  # outflow
            foreign_futures_net_krw=-500,  # negative -> outflow
            dxy=105.0,
            dxy_change_pct=0.5,      # positive -> outflow
        )
        assert pred.inflow_count == 0
        assert pred.outflow_count == 5
        assert pred.prediction == "강한 유출"
        assert pred.score_adj == -10


# ---------------------------------------------------------------------------
# predict_foreign_flow – partial inflow
# ---------------------------------------------------------------------------


class TestPredictForeignFlowPartial:
    """Tests for predict_foreign_flow with mixed signals."""

    def test_three_inflow_yields_inflow_dominant(self) -> None:
        """3 inflow / 2 outflow -> 유입 우세, +5."""
        pred = predict_foreign_flow(
            usdkrw=1280,
            usdkrw_20d_ma=1300,     # inflow
            spx_change_pct=0.5,      # inflow
            msci_em_flow="inflow",   # inflow
            foreign_futures_net_krw=-100,  # outflow
            dxy=105.0,
            dxy_change_pct=0.3,      # outflow
        )
        assert pred.inflow_count == 3
        assert pred.outflow_count == 2
        assert pred.prediction == "유입 우세"
        assert pred.score_adj == 5

    def test_three_outflow_yields_outflow_dominant(self) -> None:
        """3 outflow / 2 inflow -> 유출 우세, -5."""
        pred = predict_foreign_flow(
            usdkrw=1320,
            usdkrw_20d_ma=1300,     # outflow
            spx_change_pct=-0.5,     # outflow
            msci_em_flow="outflow",  # outflow
            foreign_futures_net_krw=100,  # inflow
            dxy=103.0,
            dxy_change_pct=-0.3,     # inflow
        )
        assert pred.inflow_count == 2
        assert pred.outflow_count == 3
        assert pred.prediction == "유출 우세"
        assert pred.score_adj == -5

    def test_four_inflow_strong(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1280, usdkrw_20d_ma=1300,  # inflow
            spx_change_pct=0.5,                 # inflow
            msci_em_flow="inflow",              # inflow
            foreign_futures_net_krw=100,         # inflow
            dxy=105.0, dxy_change_pct=0.5,      # outflow
        )
        assert pred.inflow_count == 4
        assert pred.prediction == "강한 유입"
        assert pred.score_adj == 10


# ---------------------------------------------------------------------------
# Individual signal logic
# ---------------------------------------------------------------------------


class TestIndividualSignals:
    """Tests for individual signal components."""

    def test_fx_inflow_when_below_ma(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1280, usdkrw_20d_ma=1300,
            spx_change_pct=-1.0, msci_em_flow="outflow",
            foreign_futures_net_krw=-100, dxy=105, dxy_change_pct=0.5,
        )
        assert pred.fx_signal == "inflow"

    def test_fx_outflow_when_above_ma(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=-1.0, msci_em_flow="outflow",
            foreign_futures_net_krw=-100, dxy=105, dxy_change_pct=0.5,
        )
        assert pred.fx_signal == "outflow"

    def test_spx_positive_inflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=1.5, msci_em_flow="outflow",
            foreign_futures_net_krw=-100, dxy=105, dxy_change_pct=0.5,
        )
        assert pred.us_market_signal == "inflow"

    def test_spx_negative_outflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=-0.5, msci_em_flow="outflow",
            foreign_futures_net_krw=-100, dxy=105, dxy_change_pct=0.5,
        )
        assert pred.us_market_signal == "outflow"

    def test_dxy_falling_inflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=-1.0, msci_em_flow="outflow",
            foreign_futures_net_krw=-100, dxy=103, dxy_change_pct=-0.8,
        )
        assert pred.dxy_signal == "inflow"

    def test_dxy_rising_outflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=-1.0, msci_em_flow="outflow",
            foreign_futures_net_krw=-100, dxy=105, dxy_change_pct=0.3,
        )
        assert pred.dxy_signal == "outflow"

    def test_futures_positive_inflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=-1.0, msci_em_flow="outflow",
            foreign_futures_net_krw=300, dxy=105, dxy_change_pct=0.5,
        )
        assert pred.futures_signal == "inflow"

    def test_futures_negative_outflow(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1320, usdkrw_20d_ma=1300,
            spx_change_pct=-1.0, msci_em_flow="outflow",
            foreign_futures_net_krw=-300, dxy=105, dxy_change_pct=0.5,
        )
        assert pred.futures_signal == "outflow"


# ---------------------------------------------------------------------------
# format_foreign_prediction
# ---------------------------------------------------------------------------


class TestFormatForeignPrediction:
    """Tests for format_foreign_prediction."""

    def test_returns_string(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1280, usdkrw_20d_ma=1300,
            spx_change_pct=1.0, msci_em_flow="inflow",
            foreign_futures_net_krw=500, dxy=103, dxy_change_pct=-0.5,
        )
        result = format_foreign_prediction(pred)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_all_signal_labels(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1280, usdkrw_20d_ma=1300,
            spx_change_pct=1.0, msci_em_flow="inflow",
            foreign_futures_net_krw=500, dxy=103, dxy_change_pct=-0.5,
        )
        result = format_foreign_prediction(pred)
        assert "환율" in result
        assert "미국 시장" in result
        assert "MSCI" in result
        assert "선물" in result
        assert "DXY" in result

    def test_contains_prediction_label(self) -> None:
        pred = predict_foreign_flow(
            usdkrw=1280, usdkrw_20d_ma=1300,
            spx_change_pct=1.0, msci_em_flow="inflow",
            foreign_futures_net_krw=500, dxy=103, dxy_change_pct=-0.5,
        )
        result = format_foreign_prediction(pred)
        assert pred.prediction in result
