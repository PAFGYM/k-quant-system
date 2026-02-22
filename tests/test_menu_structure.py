"""Tests for the main menu structure and new handlers."""

from __future__ import annotations

import pytest

from kstock.bot.bot import MAIN_MENU


# ---------------------------------------------------------------------------
# MAIN_MENU structure
# ---------------------------------------------------------------------------

class TestMainMenuStructure:
    """Verify 2-column main menu layout."""

    def test_menu_is_reply_keyboard(self):
        assert MAIN_MENU is not None
        # ReplyKeyboardMarkup wraps a list of rows
        assert hasattr(MAIN_MENU, "keyboard")

    def test_menu_has_rows(self):
        rows = MAIN_MENU.keyboard
        assert len(rows) >= 7

    def test_each_row_has_two_columns(self):
        rows = MAIN_MENU.keyboard
        for row in rows:
            assert len(row) == 2, f"Row has {len(row)} buttons: {row}"

    def test_usage_guide_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("사용법 가이드" in b for b in flat)

    def test_notification_settings_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("알림 설정" in b for b in flat)

    def test_account_analysis_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("계좌분석" in b for b in flat)

    def test_ai_chat_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("AI에게 질문" in b for b in flat)

    def test_reports_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("증권사 리포트" in b for b in flat)

    def test_financial_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("재무 진단" in b for b in flat)

    def test_swing_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("스윙 기회" in b for b in flat)

    def test_strategy_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("전략별 보기" in b for b in flat)

    def test_market_status_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("시장현황" in b for b in flat)

    def test_reco_performance_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("추천 성과" in b for b in flat)

    def test_weekly_report_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("주간 보고서" in b for b in flat)

    def test_kis_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("KIS설정" in b for b in flat)

    def test_goal_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("30억 목표" in b for b in flat)

    def test_optimize_in_menu(self):
        flat = [btn.text if hasattr(btn, "text") else str(btn) for row in MAIN_MENU.keyboard for btn in row]
        assert any("최적화" in b for b in flat)

    def test_left_column_utilities(self):
        """Left column should contain utility/settings buttons."""
        left_buttons = [row[0].text if hasattr(row[0], "text") else str(row[0]) for row in MAIN_MENU.keyboard]
        left_text = " ".join(left_buttons)
        assert "사용법" in left_text
        assert "알림" in left_text
        assert "최적화" in left_text

    def test_right_column_investing(self):
        """Right column should contain investing feature buttons."""
        right_buttons = [row[1].text if hasattr(row[1], "text") else str(row[1]) for row in MAIN_MENU.keyboard]
        right_text = " ".join(right_buttons)
        assert "계좌분석" in right_text
        assert "AI" in right_text
        assert "리포트" in right_text


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------

class TestHandlerDispatch:
    """Verify all menu buttons have handler connections."""

    def test_bot_has_usage_guide_handler(self):
        from kstock.bot.bot import KQuantBot
        bot = KQuantBot.__new__(KQuantBot)
        assert hasattr(bot, "_menu_usage_guide")

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
# Usage guide text
# ---------------------------------------------------------------------------

class TestUsageGuideText:
    """Verify usage guide content."""

    def test_guide_mentions_username(self):
        """The usage guide should mention 주호님."""
        # We test the text by calling the method logic indirectly
        # Since we can't easily call the async method, we verify the class has it
        from kstock.bot.bot import KQuantBot
        import inspect
        source = inspect.getsource(KQuantBot._menu_usage_guide)
        assert "주호님" in source

    def test_guide_mentions_all_features(self):
        from kstock.bot.bot import KQuantBot
        import inspect
        source = inspect.getsource(KQuantBot._menu_usage_guide)
        for feature in [
            "계좌분석", "AI에게 질문", "증권사 리포트", "재무 진단",
            "스윙 기회", "전략별 보기", "주간 보고서",
            "알림 설정", "최적화", "KIS설정", "30억 목표",
            "추천 성과", "시장현황",
        ]:
            assert feature in source, f"Missing feature in guide: {feature}"

    def test_guide_no_bold(self):
        from kstock.bot.bot import KQuantBot
        import inspect
        source = inspect.getsource(KQuantBot._menu_usage_guide)
        # Check the string literals inside the method for **
        # Extract the msg string
        assert '**' not in source.split('msg = ')[1].split('await')[0] if 'msg = ' in source else True


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
