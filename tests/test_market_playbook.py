"""Tests for downside tactical playbook."""

from kstock.ingest.macro_client import MacroSnapshot
from kstock.signal.market_playbook import build_downside_playbook, format_downside_playbook


def _macro_snapshot(**overrides):
    base = dict(
        vix=29.0,
        vix_change_pct=18.0,
        spx_change_pct=-1.7,
        usdkrw=1462.0,
        usdkrw_change_pct=0.9,
        us10y=4.2,
        dxy=106.2,
        regime="risk_off",
        nasdaq_change_pct=-2.1,
        kospi=2510.0,
        kospi_change_pct=-2.3,
        kosdaq=710.0,
        kosdaq_change_pct=-3.1,
        es_futures=5100.0,
        es_futures_change_pct=-1.4,
        nq_futures=17800.0,
        nq_futures_change_pct=-1.8,
        koru_change_pct=-6.1,
        ewy_change_pct=-3.2,
    )
    base.update(overrides)
    return MacroSnapshot(**base)


def test_build_downside_playbook_flags_crisis_and_strong_stock():
    macro = _macro_snapshot()
    candidates = [
        {
            "ticker": "111111",
            "name": "방어성장주",
            "day_change": -0.4,
            "return_3m": 22.0,
            "composite": 79.0,
            "foreign_days": 3,
            "inst_days": 2,
            "vol_ratio": 180.0,
            "crowd_signal": "",
            "event_tags": ["GTC"],
            "market_cap": 1_2000_0000_0000,
        },
        {
            "ticker": "222222",
            "name": "리딩방테마주",
            "day_change": -8.2,
            "return_3m": -14.0,
            "composite": 51.0,
            "foreign_days": 0,
            "inst_days": 0,
            "vol_ratio": 320.0,
            "crowd_signal": "리딩방 급행 주의",
            "market_cap": 250_0000_0000,
        },
    ]

    playbook = build_downside_playbook(
        macro,
        candidates,
        leverage_change_pct=-7.5,
        inverse_change_pct=5.2,
    )

    assert playbook.regime == "crisis"
    assert playbook.risk_score >= 65
    assert any("레버리지" in trigger for trigger in playbook.triggers)
    assert playbook.strong_stocks
    assert playbook.strong_stocks[0].ticker == "111111"
    assert playbook.avoid_stocks
    assert playbook.avoid_stocks[0].ticker == "222222"


def test_format_downside_playbook_includes_tactics_and_sections():
    macro = _macro_snapshot(
        vix=24.0,
        vix_change_pct=10.0,
        regime="neutral",
        spx_change_pct=-0.8,
        nasdaq_change_pct=-1.0,
        kospi_change_pct=-1.4,
        kosdaq_change_pct=-1.8,
        es_futures_change_pct=-0.7,
        nq_futures_change_pct=-0.9,
        usdkrw_change_pct=0.2,
        koru_change_pct=-2.6,
        ewy_change_pct=-1.4,
    )
    candidates = [
        {
            "ticker": "333333",
            "name": "탄탄주",
            "day_change": -0.6,
            "return_3m": 11.0,
            "composite": 72.0,
            "foreign_days": 2,
            "inst_days": 1,
            "vol_ratio": 140.0,
            "crowd_signal": "",
            "market_cap": 1_8000_0000_0000,
        },
    ]

    playbook = build_downside_playbook(
        macro,
        candidates,
        leverage_change_pct=-3.5,
        inverse_change_pct=2.4,
    )
    text = format_downside_playbook(playbook)

    assert playbook.regime in {"caution", "defense"}
    assert "오늘 플레이" in text
    assert "버티는 강한 종목" in text
    assert "탄탄주" in text


def test_normal_playbook_formats_to_empty_string():
    macro = _macro_snapshot(
        vix=15.0,
        vix_change_pct=-4.0,
        regime="risk_on",
        kospi_change_pct=0.4,
        kosdaq_change_pct=0.8,
        es_futures_change_pct=0.3,
        nq_futures_change_pct=0.5,
        usdkrw_change_pct=-0.2,
        koru_change_pct=1.2,
        ewy_change_pct=0.7,
    )
    playbook = build_downside_playbook(macro, [], leverage_change_pct=1.5, inverse_change_pct=-1.0)
    assert playbook.regime == "normal"
    assert format_downside_playbook(playbook) == ""


def test_build_downside_playbook_uses_macro_domestic_etf_fields_without_candidates():
    macro = _macro_snapshot(
        kodex_leverage_change_pct=-6.2,
        kodex_inverse2x_change_pct=4.8,
        kospi_change_pct=-1.9,
        kosdaq_change_pct=-2.2,
        es_futures_change_pct=-1.0,
        nq_futures_change_pct=-1.4,
    )

    playbook = build_downside_playbook(macro, [])

    assert playbook.regime in {"defense", "crisis"}
    assert any("KODEX 레버리지" in trigger for trigger in playbook.triggers)
