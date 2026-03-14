from __future__ import annotations

from unittest.mock import MagicMock

from kstock.bot.mixins.core_handlers import CoreHandlersMixin


def _make_mixin() -> CoreHandlersMixin:
    mixin = CoreHandlersMixin.__new__(CoreHandlersMixin)
    mixin.all_tickers = [
        {"code": "340450", "name": "GC지놈", "market": "KOSDAQ"},
        {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
    ]
    mixin.db = MagicMock()
    mixin.db.get_active_holdings.return_value = []
    return mixin


def test_detect_stock_query_matches_pronounced_alias():
    mixin = _make_mixin()
    detected = mixin._detect_stock_query("지씨지놈")
    assert detected is not None
    assert detected["code"] == "340450"


def test_find_stock_candidates_returns_similar_matches():
    mixin = _make_mixin()
    candidates = mixin._find_stock_candidates("지씨지놈")
    assert candidates
    assert candidates[0]["code"] == "340450"


def test_build_stock_action_keyboard_exposes_full_flow():
    mixin = _make_mixin()
    markup = mixin._build_stock_action_keyboard("340450", existing=False)
    callbacks = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
    ]
    assert "stock_act:analyze:340450" in callbacks
    assert "stock_act:debate:340450" in callbacks
    assert "stock_act:plan:340450" in callbacks
    assert "stock_act:add:340450" in callbacks
