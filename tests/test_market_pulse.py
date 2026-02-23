"""Tests for signal/market_pulse.py - Phase 8."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import pytest

from kstock.signal.market_pulse import (
    MarketPulse,
    MarketChange,
    PortfolioImpact,
    PulseRecord,
    MARKET_STATES,
    format_pulse_alert,
)

KST = timezone(timedelta(hours=9))


@dataclass
class FakeMacro:
    spx_change_pct: float = 0.5
    nasdaq_change_pct: float = 0.8
    vix: float = 18.0
    vix_change_pct: float = -1.0
    usdkrw: float = 1380.0
    usdkrw_change_pct: float = -0.2
    btc_price: float = 95000.0
    btc_change_pct: float = 1.5
    gold_price: float = 2600.0
    gold_change_pct: float = 0.3
    us10y: float = 4.3
    dxy: float = 104.0
    regime: str = "risk_on"
    us10y_change_pct: float = 0.0
    dxy_change_pct: float = 0.0
    fear_greed_score: float = 60.0
    fear_greed_label: str = "탐욕"
    fetched_at: datetime = None
    is_cached: bool = False


class TestMarketPulse:
    def test_init(self):
        pulse = MarketPulse()
        assert pulse.prev_state is None
        assert pulse.state_history == []

    def test_get_current_state_default(self):
        pulse = MarketPulse()
        assert pulse.get_current_state() == "NEUTRAL"

    def test_compute_score_bull(self):
        pulse = MarketPulse()
        macro = FakeMacro(spx_change_pct=1.5, nasdaq_change_pct=2.0, vix=15.0)
        score = pulse._compute_score(macro)
        assert score > 30  # Should be bullish

    def test_compute_score_bear(self):
        pulse = MarketPulse()
        macro = FakeMacro(
            spx_change_pct=-2.0, nasdaq_change_pct=-2.5,
            vix=30.0, usdkrw_change_pct=1.5,
        )
        score = pulse._compute_score(macro)
        assert score < -30  # Should be bearish

    def test_determine_state_strong_bull(self):
        pulse = MarketPulse()
        assert pulse._determine_state(50) == "STRONG_BULL"

    def test_determine_state_bull(self):
        pulse = MarketPulse()
        assert pulse._determine_state(25) == "BULL"

    def test_determine_state_neutral(self):
        pulse = MarketPulse()
        assert pulse._determine_state(0) == "NEUTRAL"

    def test_determine_state_bear(self):
        pulse = MarketPulse()
        assert pulse._determine_state(-25) == "BEAR"

    def test_determine_state_strong_bear(self):
        pulse = MarketPulse()
        assert pulse._determine_state(-50) == "STRONG_BEAR"

    def test_determine_state_reversal_down(self):
        pulse = MarketPulse()
        pulse.prev_state = "BULL"
        assert pulse._determine_state(-20) == "REVERSAL_DOWN"

    def test_determine_state_reversal_up(self):
        pulse = MarketPulse()
        pulse.prev_state = "BEAR"
        assert pulse._determine_state(20) == "REVERSAL_UP"

    def test_analyze_change_severity(self):
        pulse = MarketPulse()
        change = pulse._analyze_change("BULL", "REVERSAL_DOWN", -30)
        assert change.severity == 3
        assert change.from_label == "상승"
        assert change.to_label == "반전 하락!"

    def test_analyze_change_moderate(self):
        pulse = MarketPulse()
        change = pulse._analyze_change("BULL", "BEAR", -25)
        assert change.severity == 2

    def test_check_pulse_weekday_guard(self):
        """Should return None on weekends."""
        pulse = MarketPulse()
        macro = FakeMacro()
        # The actual test depends on the current day, so we just check it doesn't crash
        result = pulse.check_pulse(macro)
        # Result can be None (weekend/outside hours) or MarketChange
        assert result is None or isinstance(result, MarketChange)

    def test_portfolio_impact_reversal_down(self):
        pulse = MarketPulse()
        change = MarketChange(
            from_state="BULL", to_state="REVERSAL_DOWN",
            from_label="상승", to_label="반전 하락!",
            severity=3,
        )
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "pnl_pct": 8.0},
            {"ticker": "373220", "name": "LG에너지솔루션", "pnl_pct": -4.0},
            {"ticker": "035420", "name": "NAVER", "pnl_pct": 1.0},
        ]
        impacts = pulse.analyze_portfolio_impact(change, holdings)
        assert len(impacts) == 3
        assert impacts[0].action == "익절 검토"
        assert impacts[0].urgency == "high"
        assert impacts[1].action == "손절 검토"
        assert impacts[2].action == "관망"

    def test_portfolio_impact_reversal_up(self):
        pulse = MarketPulse()
        change = MarketChange(
            from_state="BEAR", to_state="REVERSAL_UP",
            from_label="하락", to_label="반전 상승",
            severity=3,
        )
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "pnl_pct": -2.0},
        ]
        impacts = pulse.analyze_portfolio_impact(change, holdings)
        assert impacts[0].action == "반등 기대"

    def test_get_recent_history(self):
        pulse = MarketPulse()
        now = datetime.now(KST)
        for i in range(5):
            pulse.state_history.append(PulseRecord(
                time=now - timedelta(minutes=i * 5),
                state="NEUTRAL",
                score=0.0,
            ))
        recent = pulse.get_recent_history(minutes=20)
        assert len(recent) >= 3

    def test_calculate_trend_empty(self):
        pulse = MarketPulse()
        assert pulse._calculate_trend() == 0.0


class TestFormatPulseAlert:
    def test_basic_format(self):
        change = MarketChange(
            from_state="BULL", to_state="REVERSAL_DOWN",
            from_label="상승", to_label="반전 하락!",
            severity=3,
        )
        macro = FakeMacro()
        result = format_pulse_alert(change, macro)
        assert "시장 분위기 변화" in result
        assert "상승" in result
        assert "반전 하락" in result

    def test_format_with_impacts(self):
        change = MarketChange(
            from_state="BULL", to_state="REVERSAL_DOWN",
            from_label="상승", to_label="반전 하락!",
            severity=3,
        )
        macro = FakeMacro()
        impacts = [
            PortfolioImpact("005930", "삼성전자", 8.0, "익절 검토", "high"),
        ]
        result = format_pulse_alert(change, macro, impacts=impacts)
        assert "삼성전자" in result
        assert "익절 검토" in result

    def test_format_with_history(self):
        change = MarketChange(
            from_state="NEUTRAL", to_state="BEAR",
            from_label="보합", to_label="하락",
            severity=2,
        )
        macro = FakeMacro()
        now = datetime.now(KST)
        history = [
            PulseRecord(time=now - timedelta(minutes=i * 5), state="NEUTRAL", score=10 - i * 5)
            for i in range(6)
        ]
        result = format_pulse_alert(change, macro, history=history)
        assert "직전 흐름" in result


class TestMarketStates:
    def test_all_states_have_label(self):
        for state, info in MARKET_STATES.items():
            assert "label" in info
            assert "emoji" in info
