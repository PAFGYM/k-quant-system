"""Tests for RemoteClaudeMixin — Claude Code remote execution."""
import unittest
from unittest.mock import MagicMock


class TestRemoteClaudeConstants(unittest.TestCase):
    """Test constants and configuration."""

    def test_claude_prefix_in_bot_imports(self):
        from kstock.bot.bot_imports import CLAUDE_PREFIX
        assert CLAUDE_PREFIX == "클코"

    def test_claude_cli_path(self):
        from kstock.bot.mixins.remote_claude import CLAUDE_CLI
        assert "claude" in CLAUDE_CLI

    def test_project_dir(self):
        from kstock.bot.mixins.remote_claude import PROJECT_DIR
        assert PROJECT_DIR == "/Users/juhodang/k-quant-system"

    def test_max_timeout(self):
        from kstock.bot.mixins.remote_claude import MAX_TIMEOUT
        assert MAX_TIMEOUT == 600


class TestRemoteClaudeMixinMethods(unittest.TestCase):
    """Test mixin method existence on KQuantBot."""

    def test_bot_has_cmd_claude(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_claude")
        assert callable(getattr(KQuantBot, "cmd_claude"))

    def test_bot_has_execute_claude_prompt(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_execute_claude_prompt")

    def test_bot_has_run_claude_cli(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_run_claude_cli")

    def test_bot_has_is_authorized_chat(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_is_authorized_chat")

    def test_bot_has_is_blocked_prompt(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_is_blocked_prompt")

    def test_bot_has_split_message(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_split_message")


class TestSplitMessage(unittest.TestCase):
    """Test message splitting logic."""

    def test_short_message_no_split(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        result = RemoteClaudeMixin._split_message("short text")
        assert result == ["short text"]

    def test_exact_limit_no_split(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        text = "a" * 4000
        result = RemoteClaudeMixin._split_message(text, limit=4000)
        assert len(result) == 1

    def test_long_message_splits(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        long_text = "line\n" * 2000
        result = RemoteClaudeMixin._split_message(long_text, limit=4000)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 4000

    def test_split_preserves_content(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        lines = [f"line {i}\n" for i in range(500)]
        text = "".join(lines)
        result = RemoteClaudeMixin._split_message(text, limit=4000)
        reassembled = "\n".join(result)
        # Every line number should be present
        for i in range(500):
            assert f"line {i}" in reassembled

    def test_no_newline_hard_split(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        text = "a" * 5000  # no newlines
        result = RemoteClaudeMixin._split_message(text, limit=4000)
        assert len(result) == 2
        assert len(result[0]) == 4000


class TestBlockedPatterns(unittest.TestCase):
    """Test dangerous command detection."""

    def test_rm_rf_blocked(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        assert RemoteClaudeMixin._is_blocked_prompt("rm -rf / 해줘")

    def test_normal_prompt_allowed(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        assert not RemoteClaudeMixin._is_blocked_prompt("테스트 실행해줘")

    def test_format_blocked(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        assert RemoteClaudeMixin._is_blocked_prompt("format C:")

    def test_shutdown_blocked(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        assert RemoteClaudeMixin._is_blocked_prompt("shutdown now")

    def test_reboot_blocked(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        assert RemoteClaudeMixin._is_blocked_prompt("reboot 해줘")

    def test_safe_commands_pass(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        assert not RemoteClaudeMixin._is_blocked_prompt("bot.log 최근 에러 분석해줘")
        assert not RemoteClaudeMixin._is_blocked_prompt("프로젝트 구조 설명해줘")
        assert not RemoteClaudeMixin._is_blocked_prompt("테스트 중 실패한 것 찾아줘")
        assert not RemoteClaudeMixin._is_blocked_prompt("삼성전자 분석해줘")


class TestAuthorizationCheck(unittest.TestCase):
    """Test CHAT_ID verification."""

    def _make_mixin(self, chat_id="6247622742"):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin
        mixin = RemoteClaudeMixin()
        mixin.chat_id = chat_id
        return mixin

    def test_authorized_chat(self):
        mixin = self._make_mixin()
        update = MagicMock()
        update.effective_chat.id = 6247622742
        assert mixin._is_authorized_chat(update)

    def test_unauthorized_chat(self):
        mixin = self._make_mixin()
        update = MagicMock()
        update.effective_chat.id = 9999999999
        assert not mixin._is_authorized_chat(update)

    def test_no_effective_chat(self):
        mixin = self._make_mixin()
        update = MagicMock()
        update.effective_chat = None
        assert not mixin._is_authorized_chat(update)

    def test_string_vs_int_comparison(self):
        mixin = self._make_mixin("6247622742")
        update = MagicMock()
        update.effective_chat.id = 6247622742  # int
        assert mixin._is_authorized_chat(update)


if __name__ == "__main__":
    unittest.main()
