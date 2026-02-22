"""Tests for multi-strategy system v2.5."""

from __future__ import annotations

import pytest

from kstock.features.technical import TechnicalIndicators
from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.scoring import FlowData, ScoreBreakdown
from kstock.signal.strategies import (
    STRATEGY_META,
    evaluate_all_strategies,
    evaluate_strategy_a,
    evaluate_strategy_b,
    evaluate_strategy_c,
    evaluate_strategy_d,
    evaluate_strategy_e,
    evaluate_strategy_f,
    evaluate_strategy_g,
    compute_confidence_score,
    get_regime_mode,
)


def _make_tech(
    rsi=50, bb_pctb=0.5, macd_cross=0, atr=1000, atr_pct=2.0,
    ema_50=0, ema_200=0, golden_cross=False, dead_cross=False,
    weekly_trend="neutral", mtf_aligned=False,
    high_52w=0, high_20d=0, volume_ratio=1.0,
    bb_squeeze=False, return_3m_pct=0,
):
    return TechnicalIndicators(
        rsi=rsi, bb_pctb=bb_pctb, bb_bandwidth=0.1,
        macd_signal_cross=macd_cross, macd_histogram=100,
        atr=atr, atr_pct=atr_pct,
        ema_50=ema_50, ema_200=ema_200,
        golden_cross=golden_cross, dead_cross=dead_cross,
        weekly_trend=weekly_trend, mtf_aligned=mtf_aligned,
        high_52w=high_52w, high_20d=high_20d,
        volume_ratio=volume_ratio, bb_squeeze=bb_squeeze,
        return_3m_pct=return_3m_pct,
    )


def _make_macro(regime="neutral", vix=18, spx_change=0.5, vix_change=0):
    return MacroSnapshot(
        vix=vix, vix_change_pct=vix_change, spx_change_pct=spx_change,
        usdkrw=1300, usdkrw_change_pct=0, us10y=4.0, dxy=104,
        regime=regime,
    )


def _make_score(composite=75, signal="BUY"):
    return ScoreBreakdown(
        macro=0.6, flow=0.7, fundamental=0.6, technical=0.7,
        risk=0.6, composite=composite, signal=signal,
    )


def _make_flow(foreign=-5, inst=3):
    return FlowData(
        foreign_net_buy_days=foreign,
        institution_net_buy_days=inst,
        avg_trade_value_krw=5e9,
    )


class TestStrategyA:
    def test_oversold_bounce(self):
        tech = _make_tech(rsi=25, bb_pctb=0.1, macd_cross=1)
        flow = _make_flow(foreign=-5, inst=3)
        score = _make_score(75, "BUY")
        macro = _make_macro()
        result = evaluate_strategy_a("005930", "삼성전자", score, tech, flow, macro)
        assert result is not None
        assert result.strategy == "A"
        assert result.action == "BUY"

    def test_skips_etf(self):
        tech = _make_tech(rsi=25)
        flow = _make_flow()
        score = _make_score()
        macro = _make_macro()
        result = evaluate_strategy_a("122630", "KODEX 레버리지", score, tech, flow, macro)
        assert result is None

    def test_no_signal_when_neutral(self):
        tech = _make_tech(rsi=50, bb_pctb=0.5)
        flow = _make_flow(foreign=0, inst=0)
        score = _make_score(60, "WATCH")
        macro = _make_macro()
        result = evaluate_strategy_a("005930", "삼성전자", score, tech, flow, macro)
        assert result is None


class TestStrategyB:
    def test_leverage_on_vix_drop(self):
        tech = _make_tech(rsi=28)
        macro = _make_macro(regime="risk_on", vix=20, vix_change=-8, spx_change=1.5)
        result = evaluate_strategy_b("122630", "KODEX 레버리지", tech, macro)
        assert result is not None
        assert result.strategy == "B"

    def test_inverse_on_risk_off(self):
        tech = _make_tech()
        macro = _make_macro(regime="risk_off", vix=30, spx_change=-2.0)
        result = evaluate_strategy_b("114800", "KODEX 인버스", tech, macro)
        assert result is not None

    def test_skips_non_etf(self):
        tech = _make_tech()
        macro = _make_macro()
        result = evaluate_strategy_b("005930", "삼성전자", tech, macro)
        assert result is None

    def test_bb_squeeze_breakout(self):
        tech = _make_tech(bb_squeeze=True, bb_pctb=0.85)
        macro = _make_macro(regime="risk_on", vix=20, vix_change=-6, spx_change=1.2)
        result = evaluate_strategy_b("122630", "KODEX 레버리지", tech, macro)
        assert result is not None


class TestStrategyC:
    def test_high_dividend_stock(self):
        tech = _make_tech(rsi=45)
        info = {"per": 10, "pbr": 0.8, "dividend_yield": 4.0, "roe": 12, "debt_ratio": 80}
        result = evaluate_strategy_c("105560", "KB금융", info, tech)
        assert result is not None
        assert result.strategy == "C"

    def test_dividend_etf(self):
        tech = _make_tech(rsi=40)
        info = {"per": 0, "pbr": 0, "dividend_yield": 3.5, "roe": 0, "debt_ratio": 0}
        result = evaluate_strategy_c("211560", "TIGER 배당성장", info, tech)
        assert result is not None


class TestStrategyD:
    def test_sector_etf_risk_on(self):
        tech = _make_tech(rsi=50, macd_cross=1)
        macro = _make_macro(regime="risk_on")
        result = evaluate_strategy_d("091160", "KODEX 반도체", tech, macro, sector="반도체")
        assert result is not None
        assert result.strategy == "D"

    def test_non_sector_rejected(self):
        tech = _make_tech()
        macro = _make_macro()
        result = evaluate_strategy_d("005930", "삼성전자", tech, macro)
        assert result is None


class TestStrategyE:
    def test_us_etf(self):
        tech = _make_tech(rsi=35)
        macro = _make_macro(regime="risk_on")
        result = evaluate_strategy_e("360750", "TIGER 미국S&P500", tech, macro)
        assert result is not None
        assert result.strategy == "E"

    def test_gold_risk_off(self):
        tech = _make_tech()
        macro = _make_macro(regime="risk_off", vix=28)
        result = evaluate_strategy_e("132030", "KODEX 골드선물", tech, macro)
        assert result is not None


class TestStrategyF:
    def test_momentum_golden_cross(self):
        tech = _make_tech(
            golden_cross=True, ema_50=60000, ema_200=58000,
            volume_ratio=1.8, return_3m_pct=15,
        )
        result = evaluate_strategy_f(
            "005930", "삼성전자", tech, rs_rank=3, rs_total=30,
        )
        assert result is not None
        assert result.strategy == "F"
        assert result.action == "BUY"

    def test_momentum_no_golden_cross(self):
        tech = _make_tech(ema_50=55000, ema_200=58000)
        result = evaluate_strategy_f("005930", "삼성전자", tech, rs_rank=20, rs_total=30)
        assert result is None

    def test_momentum_skips_etf(self):
        tech = _make_tech(golden_cross=True, ema_50=60000, ema_200=58000, volume_ratio=2.0)
        result = evaluate_strategy_f("122630", "KODEX 레버리지", tech, rs_rank=1, rs_total=10)
        assert result is None

    def test_momentum_weak_rs(self):
        tech = _make_tech(ema_50=60000, ema_200=58000)
        result = evaluate_strategy_f("005930", "삼성전자", tech, rs_rank=25, rs_total=30)
        assert result is None


class TestStrategyG:
    def test_breakout_52w_high(self):
        tech = _make_tech(
            ema_50=100000, ema_200=95000,
            high_52w=100000, high_20d=98000,
            volume_ratio=2.5, bb_squeeze=True,
        )
        result = evaluate_strategy_g("005930", "삼성전자", tech)
        assert result is not None
        assert result.strategy == "G"
        assert result.action == "BUY"

    def test_breakout_20d_high_volume(self):
        tech = _make_tech(
            ema_50=50000, ema_200=48000,
            high_52w=55000, high_20d=50000,
            volume_ratio=2.2,
        )
        result = evaluate_strategy_g("005930", "삼성전자", tech)
        assert result is not None

    def test_breakout_skips_etf(self):
        tech = _make_tech(ema_50=15000, high_52w=15000, volume_ratio=3.0)
        result = evaluate_strategy_g("122630", "KODEX 레버리지", tech)
        assert result is None

    def test_no_breakout_no_signal(self):
        tech = _make_tech(ema_50=50000, high_52w=60000, high_20d=55000, volume_ratio=0.8)
        result = evaluate_strategy_g("005930", "삼성전자", tech)
        assert result is None


class TestConfidenceScore:
    def test_full_bonuses(self):
        tech = _make_tech(mtf_aligned=True, weekly_trend="up", ema_50=100, ema_200=50)
        score, stars, label = compute_confidence_score(
            base_score=75, tech=tech, sector_adj=5,
            roe_top_30=True, inst_buy_days=5,
        )
        assert score >= 90
        assert label == "강한 매수"

    def test_penalties(self):
        tech = _make_tech(weekly_trend="down", ema_50=50, ema_200=100)
        score, stars, label = compute_confidence_score(
            base_score=70, tech=tech, sector_adj=-5,
            corr_penalty=True,
        )
        assert score < 70

    def test_leverage_hold_penalty(self):
        tech = _make_tech()
        score, _, _ = compute_confidence_score(
            base_score=70, tech=tech,
            is_leverage_etf=True, leverage_hold_days=5,
        )
        assert score == 65


class TestRegimeMode:
    def test_defense_mode(self):
        macro = _make_macro(regime="risk_off", vix=30)
        mode = get_regime_mode(macro)
        assert mode["mode"] == "defense"
        assert "cash" in mode["allocations"]

    def test_attack_mode(self):
        macro = _make_macro(regime="risk_on", vix=12)
        mode = get_regime_mode(macro)
        assert mode["mode"] == "attack"
        assert mode["allocations"]["F"] > 0

    def test_balanced_mode(self):
        macro = _make_macro(regime="neutral", vix=18)
        mode = get_regime_mode(macro)
        assert mode["mode"] == "balanced"


class TestEvaluateAll:
    def test_returns_list(self):
        tech = _make_tech(rsi=25, bb_pctb=0.1, macd_cross=1)
        flow = _make_flow(foreign=-5, inst=3)
        score = _make_score(75, "BUY")
        macro = _make_macro()
        signals = evaluate_all_strategies(
            "005930", "삼성전자", score, tech, flow, macro
        )
        assert isinstance(signals, list)
        assert len(signals) >= 1
        assert signals[0].strategy == "A"

    def test_includes_fg_when_applicable(self):
        tech = _make_tech(
            rsi=45, golden_cross=True, ema_50=60000, ema_200=58000,
            volume_ratio=2.0, return_3m_pct=20,
            high_52w=60000, high_20d=59000, bb_squeeze=True,
        )
        flow = _make_flow(foreign=0, inst=0)
        score = _make_score(65, "WATCH")
        macro = _make_macro()
        signals = evaluate_all_strategies(
            "005930", "삼성전자", score, tech, flow, macro,
            rs_rank=2, rs_total=30,
        )
        strat_types = [s.strategy for s in signals]
        assert "F" in strat_types
        assert "G" in strat_types


class TestStrategyMeta:
    def test_all_strategies_defined(self):
        for key in ["A", "B", "C", "D", "E", "F", "G"]:
            assert key in STRATEGY_META
            assert "name" in STRATEGY_META[key]
            assert "emoji" in STRATEGY_META[key]
            assert "target" in STRATEGY_META[key]
            assert "stop" in STRATEGY_META[key]
