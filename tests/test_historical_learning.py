from __future__ import annotations

from kstock.bot.historical_learning import _calc_forward_returns


def test_calc_forward_returns_uses_trading_day_offsets() -> None:
    pairs = [
        ("2026-03-02", 100.0),
        ("2026-03-03", 101.0),
        ("2026-03-04", 102.0),
        ("2026-03-05", 103.0),
        ("2026-03-06", 104.0),
        ("2026-03-09", 106.0),
        ("2026-03-10", 105.0),
        ("2026-03-11", 107.0),
        ("2026-03-12", 108.0),
        ("2026-03-13", 110.0),
        ("2026-03-16", 111.0),
        ("2026-03-17", 112.0),
        ("2026-03-18", 113.0),
    ]

    result = _calc_forward_returns(pairs, "2026-03-04")

    assert result["price_d1"] == 103.0
    assert result["price_d3"] == 106.0
    assert result["price_d5"] == 107.0
    assert result["price_d10"] == 113.0
    assert round(result["return_d5"], 2) == round((107.0 - 102.0) / 102.0 * 100.0, 2)
    assert result["correct"] == 1


def test_calc_forward_returns_returns_empty_when_base_missing() -> None:
    pairs = [("2026-03-02", 100.0), ("2026-03-03", 101.0)]
    assert _calc_forward_returns(pairs, "2026-04-01") == {}
