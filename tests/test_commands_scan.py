from __future__ import annotations

from kstock.bot.bot_imports import ScanResult
from kstock.bot.mixins.commands import CommandsMixin
from kstock.features.technical import TechnicalIndicators
from kstock.ingest.kis_client import StockInfo
from kstock.signal.scoring import FlowData, ScoreBreakdown


def _make_scan_result(
    ticker: str,
    name: str,
    *,
    price: float,
    rsi: float,
    bb_pctb: float,
    macd_signal_cross: int,
    volume_ratio: float,
    ma20: float,
    return_3m_pct: float,
) -> ScanResult:
    return ScanResult(
        ticker=ticker,
        name=name,
        score=ScoreBreakdown(
            macro=0.6,
            flow=0.6,
            fundamental=0.6,
            technical=0.6,
            risk=0.6,
            composite=70.0,
            signal="WATCH",
        ),
        tech=TechnicalIndicators(
            rsi=rsi,
            bb_pctb=bb_pctb,
            bb_bandwidth=0.12,
            macd_histogram=0.5,
            macd_signal_cross=macd_signal_cross,
            atr=120.0,
            atr_pct=2.1,
            volume_ratio=volume_ratio,
            ma20=ma20,
            return_3m_pct=return_3m_pct,
        ),
        info=StockInfo(
            ticker=ticker,
            name=name,
            market="KOSDAQ",
            market_cap=100_000_000_000,
            per=18.0,
            roe=12.0,
            debt_ratio=55.0,
            consensus_target=0.0,
            current_price=price,
        ),
        flow=FlowData(foreign_net_buy_days=1, institution_net_buy_days=1, avg_trade_value_krw=1_000_000_000),
    )


def test_swing_signal_from_scan_result_returns_candidate_for_strong_setup():
    mixin = CommandsMixin.__new__(CommandsMixin)
    result = _make_scan_result(
        "123456",
        "테스트",
        price=10200,
        rsi=34.0,
        bb_pctb=0.18,
        macd_signal_cross=1,
        volume_ratio=1.7,
        ma20=9800,
        return_3m_pct=12.0,
    )
    swing = mixin._swing_signal_from_scan_result(result)
    assert swing is not None
    assert swing["ticker"] == "123456"
    assert swing["score"] >= 25
    assert swing["source"] == "cache"


def test_swing_signal_from_scan_result_skips_weak_setup():
    mixin = CommandsMixin.__new__(CommandsMixin)
    result = _make_scan_result(
        "654321",
        "약함",
        price=10200,
        rsi=58.0,
        bb_pctb=0.65,
        macd_signal_cross=0,
        volume_ratio=0.9,
        ma20=11000,
        return_3m_pct=-8.0,
    )
    swing = mixin._swing_signal_from_scan_result(result)
    assert swing is None
