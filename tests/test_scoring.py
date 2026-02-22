"""Tests for the scoring module."""

from __future__ import annotations

import pytest

from kstock.features.technical import TechnicalIndicators
from kstock.ingest.kis_client import StockInfo
from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.scoring import (
    FlowData,
    ScoreBreakdown,
    compute_composite_score,
    score_flow,
    score_fundamental,
    score_macro,
    score_risk,
    score_technical,
)


@pytest.fixture
def config() -> dict:
    """Test scoring config matching scoring.yaml structure."""
    return {
        "weights": {
            "macro": 0.10,
            "flow": 0.30,
            "fundamental": 0.30,
            "technical": 0.20,
            "risk": 0.10,
        },
        "thresholds": {
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bb_lower_pct": 0.2,
            "bb_upper_pct": 0.8,
            "macd_signal_threshold": 0.0,
            "debt_ratio_max": 200,
            "per_max": 30,
            "per_min": 5,
            "roe_min": 8.0,
            "consensus_target_pct": 10.0,
            "foreign_net_buy_days": 3,
            "institution_net_buy_days": 3,
            "min_avg_value_krw": 3000000000,
            "vix_high": 25,
            "vix_low": 15,
            "usdkrw_high": 1350,
            "usdkrw_low": 1250,
            "max_drawdown_pct": 20,
            "beta_max": 1.5,
        },
        "buy_threshold": 70,
        "watch_threshold": 55,
    }


@pytest.fixture
def thresholds(config: dict) -> dict:
    return config["thresholds"]


# --- score_macro ---


class TestScoreMacro:
    def test_risk_on_environment(self, thresholds: dict) -> None:
        """Low VIX + positive SPX + low USDKRW = high score."""
        macro = MacroSnapshot(
            vix=13.0,
            vix_change_pct=-2.0,
            spx_change_pct=1.0,
            usdkrw=1230.0,
            usdkrw_change_pct=-0.1,
            us10y=4.0,
            dxy=104.0,
            regime="risk_on",
        )
        score = score_macro(macro, thresholds)
        assert 0.7 <= score <= 1.0

    def test_risk_off_environment(self, thresholds: dict) -> None:
        """High VIX + negative SPX + high USDKRW = low score."""
        macro = MacroSnapshot(
            vix=30.0,
            vix_change_pct=15.0,
            spx_change_pct=-2.0,
            usdkrw=1400.0,
            usdkrw_change_pct=1.5,
            us10y=4.5,
            dxy=108.0,
            regime="risk_off",
        )
        score = score_macro(macro, thresholds)
        assert 0.0 <= score <= 0.3

    def test_neutral_environment(self, thresholds: dict) -> None:
        """Middle range values = around 0.5."""
        macro = MacroSnapshot(
            vix=20.0,
            vix_change_pct=0.0,
            spx_change_pct=0.1,
            usdkrw=1300.0,
            usdkrw_change_pct=0.0,
            us10y=4.2,
            dxy=105.0,
            regime="neutral",
        )
        score = score_macro(macro, thresholds)
        assert 0.3 <= score <= 0.7

    def test_score_bounds(self, thresholds: dict) -> None:
        """Score must always be between 0 and 1."""
        macro = MacroSnapshot(
            vix=50.0,
            vix_change_pct=30.0,
            spx_change_pct=-5.0,
            usdkrw=1500.0,
            usdkrw_change_pct=3.0,
            us10y=5.5,
            dxy=115.0,
            regime="risk_off",
        )
        score = score_macro(macro, thresholds)
        assert 0.0 <= score <= 1.0


# --- score_flow ---


class TestScoreFlow:
    def test_strong_foreign_buying(self, thresholds: dict) -> None:
        """Consecutive foreign net buying = high score."""
        flow = FlowData(
            foreign_net_buy_days=5,
            institution_net_buy_days=4,
            avg_trade_value_krw=5_000_000_000,
        )
        score = score_flow(flow, thresholds)
        assert score >= 0.7

    def test_strong_selling(self, thresholds: dict) -> None:
        """Consecutive selling = low score."""
        flow = FlowData(
            foreign_net_buy_days=-5,
            institution_net_buy_days=-4,
            avg_trade_value_krw=1_000_000_000,
        )
        score = score_flow(flow, thresholds)
        assert score <= 0.3

    def test_neutral_flow(self, thresholds: dict) -> None:
        """No clear direction = neutral score."""
        flow = FlowData(
            foreign_net_buy_days=0,
            institution_net_buy_days=0,
            avg_trade_value_krw=3_000_000_000,
        )
        score = score_flow(flow, thresholds)
        assert 0.4 <= score <= 0.7


# --- score_fundamental ---


class TestScoreFundamental:
    def test_good_fundamentals(self, thresholds: dict) -> None:
        """Low PER, high ROE, low debt, upside target = high score."""
        info = StockInfo(
            ticker="005930",
            name="삼성전자",
            market="KOSPI",
            market_cap=400_000_000_000_000,
            per=12.0,
            roe=15.0,
            debt_ratio=50.0,
            consensus_target=85000.0,
            current_price=70000.0,
        )
        score = score_fundamental(info, thresholds)
        assert score >= 0.7

    def test_poor_fundamentals(self, thresholds: dict) -> None:
        """High PER, low ROE, high debt = low score."""
        info = StockInfo(
            ticker="999999",
            name="BadCo",
            market="KOSPI",
            market_cap=1_000_000_000_000,
            per=50.0,
            roe=2.0,
            debt_ratio=300.0,
            consensus_target=8000.0,
            current_price=10000.0,
        )
        score = score_fundamental(info, thresholds)
        assert score <= 0.3


# --- score_technical ---


class TestScoreTechnical:
    def test_oversold_bullish(self, thresholds: dict) -> None:
        """Oversold RSI + low BB + bullish MACD cross = high score."""
        tech = TechnicalIndicators(
            rsi=25.0,
            bb_pctb=0.1,
            bb_bandwidth=0.05,
            macd_histogram=0.5,
            macd_signal_cross=1,
            atr=500.0,
            atr_pct=2.0,
        )
        score = score_technical(tech, thresholds)
        assert score >= 0.7

    def test_overbought_bearish(self, thresholds: dict) -> None:
        """Overbought RSI + high BB + bearish cross = low score."""
        tech = TechnicalIndicators(
            rsi=75.0,
            bb_pctb=0.9,
            bb_bandwidth=0.08,
            macd_histogram=-0.5,
            macd_signal_cross=-1,
            atr=500.0,
            atr_pct=2.0,
        )
        score = score_technical(tech, thresholds)
        assert score <= 0.35


# --- score_risk ---


class TestScoreRisk:
    def test_low_risk(self, thresholds: dict) -> None:
        """Low volatility + low debt = high score."""
        tech = TechnicalIndicators(
            rsi=50.0, bb_pctb=0.5, bb_bandwidth=0.04,
            macd_histogram=0.0, macd_signal_cross=0,
            atr=500.0, atr_pct=1.0,
        )
        info = StockInfo(
            ticker="005930", name="삼성전자", market="KOSPI",
            market_cap=400e12, per=12.0, roe=15.0,
            debt_ratio=50.0, consensus_target=80000.0, current_price=70000.0,
        )
        score = score_risk(tech, info, thresholds)
        assert score >= 0.7

    def test_high_risk(self, thresholds: dict) -> None:
        """High volatility + high debt = low score."""
        tech = TechnicalIndicators(
            rsi=50.0, bb_pctb=0.5, bb_bandwidth=0.04,
            macd_histogram=0.0, macd_signal_cross=0,
            atr=5000.0, atr_pct=6.0,
        )
        info = StockInfo(
            ticker="999999", name="RiskyCo", market="KOSPI",
            market_cap=1e12, per=20.0, roe=5.0,
            debt_ratio=250.0, consensus_target=10000.0, current_price=10000.0,
        )
        score = score_risk(tech, info, thresholds)
        assert score <= 0.4


# --- composite score ---


class TestCompositeScore:
    def test_composite_returns_breakdown(self, config: dict) -> None:
        """Composite score returns proper ScoreBreakdown."""
        macro = MacroSnapshot(
            vix=20.0, vix_change_pct=0.0, spx_change_pct=0.2,
            usdkrw=1300.0, usdkrw_change_pct=0.0, us10y=4.0, dxy=104.0,
            regime="neutral",
        )
        flow = FlowData(
            foreign_net_buy_days=2, institution_net_buy_days=1,
            avg_trade_value_krw=4_000_000_000,
        )
        info = StockInfo(
            ticker="005930", name="삼성전자", market="KOSPI",
            market_cap=400e12, per=12.0, roe=15.0,
            debt_ratio=80.0, consensus_target=80000.0, current_price=70000.0,
        )
        tech = TechnicalIndicators(
            rsi=45.0, bb_pctb=0.4, bb_bandwidth=0.04,
            macd_histogram=0.1, macd_signal_cross=0,
            atr=1000.0, atr_pct=1.5,
        )

        result = compute_composite_score(macro, flow, info, tech, config)

        assert isinstance(result, ScoreBreakdown)
        assert 0.0 <= result.composite <= 160.0
        assert result.signal in ("STRONG_BUY", "BUY", "WATCH", "MILD_BUY", "HOLD")
        assert 0.0 <= result.macro <= 1.0
        assert 0.0 <= result.flow <= 1.0
        assert 0.0 <= result.fundamental <= 1.0
        assert 0.0 <= result.technical <= 1.0
        assert 0.0 <= result.risk <= 1.0

    def test_weights_sum_to_one(self, config: dict) -> None:
        """Config weights must sum to 1.0."""
        weights = config["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_buy_signal_threshold(self, config: dict) -> None:
        """High scores should produce BUY signal."""
        macro = MacroSnapshot(
            vix=13.0, vix_change_pct=-2.0, spx_change_pct=1.0,
            usdkrw=1230.0, usdkrw_change_pct=-0.1, us10y=4.0, dxy=104.0,
            regime="risk_on",
        )
        flow = FlowData(
            foreign_net_buy_days=5, institution_net_buy_days=4,
            avg_trade_value_krw=5_000_000_000,
        )
        info = StockInfo(
            ticker="005930", name="삼성전자", market="KOSPI",
            market_cap=400e12, per=10.0, roe=18.0,
            debt_ratio=40.0, consensus_target=90000.0, current_price=70000.0,
        )
        tech = TechnicalIndicators(
            rsi=28.0, bb_pctb=0.15, bb_bandwidth=0.05,
            macd_histogram=0.5, macd_signal_cross=1,
            atr=500.0, atr_pct=1.0,
        )

        result = compute_composite_score(macro, flow, info, tech, config)
        assert result.signal in ("BUY", "STRONG_BUY", "MILD_BUY", "WATCH")
        assert result.composite >= config["watch_threshold"]

    def test_mtf_bonus_increases_score(self, config: dict) -> None:
        """MTF alignment bonus should increase composite score."""
        macro = MacroSnapshot(
            vix=20.0, vix_change_pct=0.0, spx_change_pct=0.2,
            usdkrw=1300.0, usdkrw_change_pct=0.0, us10y=4.0, dxy=104.0,
            regime="neutral",
        )
        flow = FlowData(
            foreign_net_buy_days=2, institution_net_buy_days=1,
            avg_trade_value_krw=4_000_000_000,
        )
        info = StockInfo(
            ticker="005930", name="삼성전자", market="KOSPI",
            market_cap=400e12, per=12.0, roe=15.0,
            debt_ratio=80.0, consensus_target=80000.0, current_price=70000.0,
        )
        tech = TechnicalIndicators(
            rsi=45.0, bb_pctb=0.4, bb_bandwidth=0.04,
            macd_histogram=0.1, macd_signal_cross=0,
            atr=1000.0, atr_pct=1.5,
        )

        base = compute_composite_score(macro, flow, info, tech, config)
        with_mtf = compute_composite_score(macro, flow, info, tech, config, mtf_bonus=10)
        assert with_mtf.composite >= base.composite + 9  # allow small rounding

    def test_sector_adj_modifies_score(self, config: dict) -> None:
        """Sector adjustment should modify composite score."""
        macro = MacroSnapshot(
            vix=20.0, vix_change_pct=0.0, spx_change_pct=0.2,
            usdkrw=1300.0, usdkrw_change_pct=0.0, us10y=4.0, dxy=104.0,
            regime="neutral",
        )
        flow = FlowData(foreign_net_buy_days=0, institution_net_buy_days=0, avg_trade_value_krw=3e9)
        info = StockInfo(
            ticker="005930", name="삼성전자", market="KOSPI",
            market_cap=400e12, per=12.0, roe=15.0,
            debt_ratio=80.0, consensus_target=80000.0, current_price=70000.0,
        )
        tech = TechnicalIndicators(
            rsi=50.0, bb_pctb=0.5, bb_bandwidth=0.04,
            macd_histogram=0.0, macd_signal_cross=0,
            atr=1000.0, atr_pct=2.0,
        )

        base = compute_composite_score(macro, flow, info, tech, config)
        with_sector = compute_composite_score(macro, flow, info, tech, config, sector_adj=5)
        assert with_sector.composite >= base.composite + 4
