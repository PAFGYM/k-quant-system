from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from kstock.bot.messages import format_market_status
from kstock.core.tz import KST


def test_format_market_status_includes_korea_passive_axis() -> None:
    macro = SimpleNamespace(
        regime="risk_off",
        fetched_at=datetime(2026, 3, 17, 8, 30, tzinfo=KST),
        is_cached=False,
        kospi=2685.0,
        kospi_change_pct=1.14,
        kosdaq=772.0,
        kosdaq_change_pct=-1.27,
        spx_change_pct=0.72,
        nasdaq_change_pct=1.35,
        es_futures=5850.0,
        es_futures_change_pct=0.45,
        nq_futures=20350.0,
        nq_futures_change_pct=0.88,
        ewy_change_pct=1.1,
        koru_price=18.5,
        koru_change_pct=3.6,
        vix=22.3,
        vix_change_pct=-4.2,
        us10y=4.12,
        us10y_change_pct=0.1,
        dxy=103.5,
        dxy_change_pct=-0.2,
        usdkrw=1388.0,
        usdkrw_change_pct=0.1,
        btc_price=95000.0,
        btc_change_pct=1.2,
        gold_price=2350.0,
        gold_change_pct=0.8,
        fear_greed_score=58.0,
        fear_greed_label="중립",
        institution_total=0.0,
        foreign_total=0.0,
        korean_vol=18.0,
        vol_regime="normal",
    )
    text = format_market_status(macro, regime_mode=None, sector_text="", fx_message="", alert_mode="normal")
    assert "코스피: 2,685" in text
    assert "코스닥: 772" in text
    assert "EWY(MSCI Korea): +1.1%" in text
    assert "KORU: +3.6%" in text
    assert "한국 대형주 우위, 코스닥 디커플링 가능성" in text
