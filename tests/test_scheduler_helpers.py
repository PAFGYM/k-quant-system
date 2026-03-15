from datetime import datetime

from kstock.bot.mixins.scheduler import (
    _choose_ml_ohlcv_period,
    _stock_news_is_fresh,
    _stock_news_title_fingerprint,
)
from kstock.core.tz import KST


def test_stock_news_title_fingerprint_collapses_minor_variants():
    first = _stock_news_title_fingerprint(
        "005930",
        '[속보] 삼성전자, "HBM 투자 확대"… 외국인 매수세',
    )
    second = _stock_news_title_fingerprint(
        "005930",
        "삼성전자 HBM 투자 확대 외국인 매수세",
    )

    assert first
    assert first == second


def test_stock_news_is_fresh_rejects_old_titles():
    now = datetime(2026, 3, 15, 12, 0, tzinfo=KST)

    assert _stock_news_is_fresh("2026.03.15 09:30", now=now)
    assert not _stock_news_is_fresh("2026.03.12 08:00", now=now)


def test_choose_ml_ohlcv_period_scales_with_prediction_age():
    now = datetime(2026, 3, 15, 12, 0, tzinfo=KST)

    assert _choose_ml_ohlcv_period("2026-03-10", now=now) == "3mo"
    assert _choose_ml_ohlcv_period("2025-12-01", now=now) == "6mo"
    assert _choose_ml_ohlcv_period("2025-05-01", now=now) == "1y"
    assert _choose_ml_ohlcv_period("2024-02-01", now=now) == "2y"
