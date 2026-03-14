from kstock.bot.mixins.scheduler import _normalize_adaptive_intervals


def test_normalize_adaptive_intervals_enforces_sane_floor():
    intervals = _normalize_adaptive_intervals(
        {
            "calm": {"intraday_monitor": 60, "market_pulse": 90},
            "normal": {"intraday_monitor": 30, "market_pulse": 30},
            "fear": {"intraday_monitor": 20, "market_pulse": 20},
            "panic": {"intraday_monitor": 10, "market_pulse": 10},
        }
    )

    assert intervals["calm"]["intraday_monitor"] == 180
    assert intervals["normal"]["intraday_monitor"] == 90
    assert intervals["fear"]["intraday_monitor"] == 60
    assert intervals["panic"]["intraday_monitor"] == 30


def test_normalize_adaptive_intervals_preserves_more_conservative_values():
    intervals = _normalize_adaptive_intervals(
        {
            "normal": {"intraday_monitor": 150, "market_pulse": 180},
            "panic": {"intraday_monitor": 45, "market_pulse": 45},
        }
    )

    assert intervals["normal"]["intraday_monitor"] == 150
    assert intervals["normal"]["market_pulse"] == 180
    assert intervals["panic"]["intraday_monitor"] == 45
