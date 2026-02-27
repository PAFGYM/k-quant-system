"""Tests for the main/more menu structure and new handlers."""

from __future__ import annotations

import pytest

from kstock.bot.bot_imports import MAIN_MENU, MORE_MENU


# ---------------------------------------------------------------------------
# MAIN_MENU structure (v3.6.2: 4-row compact layout)
# ---------------------------------------------------------------------------

class TestMainMenuStructure:
    """Verify 2-column main menu layout."""

    def test_menu_is_reply_keyboard(self):
        assert MAIN_MENU is not None
        assert hasattr(MAIN_MENU, "keyboard")

    def test_menu_has_rows(self):
        rows = MAIN_MENU.keyboard
        assert len(rows) == 5  # v5.3: 5행 (클로드 추가)

    def test_each_row_has_max_two_columns(self):
        rows = MAIN_MENU.keyboard
        for row in rows:
            assert 1 <= len(row) <= 2, f"Row has {len(row)} buttons: {row}"

    def test_main_menu_buttons(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        for expected in ["분석", "시황", "잔고", "즐겨찾기", "클로드", "에이전트", "리포트", "AI질문", "더보기"]:
            assert any(expected in b for b in flat), f"Missing: {expected}"


# ---------------------------------------------------------------------------
# MORE_MENU structure
# ---------------------------------------------------------------------------

class TestMoreMenuStructure:
    """Verify MORE_MENU has the key feature buttons."""

    def test_more_menu_is_reply_keyboard(self):
        assert MORE_MENU is not None
        assert hasattr(MORE_MENU, "keyboard")

    def test_more_menu_buttons(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MORE_MENU.keyboard for btn in row]
        for expected in [
            "계좌분석", "전략별 보기", "급등주", "스윙 기회",
            "멀티분석", "매집탐지", "주간 보고서", "공매도",
            "미래기술", "30억 목표", "재무 진단", "KIS설정",
            "알림 설정", "최적화", "클로드", "관리자", "메인으로",
        ]:
            assert any(expected in b for b in flat), f"Missing in MORE_MENU: {expected}"


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------

class TestHandlerDispatch:
    """Verify all menu buttons have handler connections."""

    def test_bot_has_notification_settings_handler(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        assert hasattr(bot, "_menu_notification_settings")

    def test_bot_has_weekly_report_handler(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        assert hasattr(bot, "_menu_weekly_report")

    def test_bot_has_report_submenu_handler(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        assert hasattr(bot, "_action_report_submenu")

    def test_bot_has_notification_toggle_handler(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        assert hasattr(bot, "_action_notification_toggle")


# ---------------------------------------------------------------------------
# Notification settings DB
# ---------------------------------------------------------------------------

class TestNotificationSettingsDB:
    """Verify notification_settings table operations."""

    @pytest.fixture
    def store(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        return SQLiteStore(db_path=tmp_path / "test.db")

    def test_default_settings_all_on(self, store):
        settings = store.get_notification_settings()
        assert len(settings) >= 6
        for key, val in settings.items():
            assert val is True, f"{key} should default to ON"

    def test_toggle_setting_off(self, store):
        new_state = store.toggle_notification_setting("report_alert")
        assert new_state is False

    def test_toggle_setting_on_again(self, store):
        store.toggle_notification_setting("report_alert")  # OFF
        new_state = store.toggle_notification_setting("report_alert")  # ON
        assert new_state is True

    def test_all_expected_settings_present(self, store):
        settings = store.get_notification_settings()
        expected = {
            "report_alert", "supply_alert", "earnings_alert",
            "policy_alert", "morning_briefing", "weekly_report",
        }
        assert expected <= set(settings.keys())


# ---------------------------------------------------------------------------
# Portfolio horizon DB
# ---------------------------------------------------------------------------

class TestPortfolioHorizonDB:
    """Verify portfolio_horizon table operations."""

    @pytest.fixture
    def store(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        return SQLiteStore(db_path=tmp_path / "test.db")

    def test_upsert_and_get(self, store):
        store.upsert_portfolio_horizon("005930", "삼성전자", "janggi")
        row = store.get_portfolio_horizon("005930")
        assert row is not None
        assert row["horizon"] == "janggi"
        assert row["name"] == "삼성전자"

    def test_upsert_overwrites(self, store):
        store.upsert_portfolio_horizon("005930", "삼성전자", "dangi")
        store.upsert_portfolio_horizon("005930", "삼성전자", "janggi")
        row = store.get_portfolio_horizon("005930")
        assert row["horizon"] == "janggi"

    def test_get_all_horizons(self, store):
        store.upsert_portfolio_horizon("005930", "삼성전자", "janggi")
        store.upsert_portfolio_horizon("000660", "SK하이닉스", "dangi")
        rows = store.get_all_portfolio_horizons()
        assert len(rows) == 2

    def test_nonexistent_ticker(self, store):
        row = store.get_portfolio_horizon("999999")
        assert row is None


# ---------------------------------------------------------------------------
# Holdings with horizon JOIN
# ---------------------------------------------------------------------------

class TestHoldingsHorizonJoin:
    """get_active_holdings()가 portfolio_horizon을 LEFT JOIN하는지 확인."""

    @pytest.fixture
    def store(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        return SQLiteStore(db_path=tmp_path / "test.db")

    def test_holdings_include_horizon(self, store):
        holding_id = store.add_holding("005930", "삼성전자", 75000)
        store.upsert_portfolio_horizon("005930", "삼성전자", "janggi")
        holdings = store.get_active_holdings()
        assert len(holdings) >= 1
        h = [x for x in holdings if x["ticker"] == "005930"][0]
        assert h["horizon"] == "janggi"

    def test_holdings_without_horizon(self, store):
        store.add_holding("000660", "SK하이닉스", 150000)
        holdings = store.get_active_holdings()
        h = [x for x in holdings if x["ticker"] == "000660"][0]
        assert h.get("horizon") is None
