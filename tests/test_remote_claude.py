"""Tests for RemoteClaudeMixin — Claude Code remote execution."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def post(self, *args, **kwargs):
        if not self._responses:
            raise AssertionError("No fake responses left")
        return self._responses.pop(0)


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
        assert "k-quant-system" in PROJECT_DIR

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


class TestRemoteClaudeFallbacks(unittest.IsolatedAsyncioTestCase):
    def _make_mixin(self):
        from kstock.bot.mixins.remote_claude import RemoteClaudeMixin

        class DummyRemoteClaude(RemoteClaudeMixin):
            pass

        mixin = DummyRemoteClaude()
        mixin.anthropic_key = "anthropic-test"
        mixin.db = MagicMock()
        mixin.db.get_active_holdings.return_value = []
        mixin.db.get_macro_snapshot.return_value = {}
        mixin._detect_stock_query = MagicMock(return_value=None)
        mixin._build_text_chat_system_prompt = AsyncMock(return_value="system prompt")
        mixin._build_image_system_prompt = AsyncMock(return_value="image system")
        return mixin

    def _make_update(self, reply_count=2):
        placeholder = MagicMock()
        placeholder.delete = AsyncMock()
        placeholder.edit_text = AsyncMock()
        reply_side_effect = [placeholder] + [MagicMock() for _ in range(reply_count - 1)]

        update = MagicMock()
        update.message = MagicMock()
        update.message.reply_text = AsyncMock(side_effect=reply_side_effect)
        return update

    def _make_context(self, tier="bujang"):
        context = MagicMock()
        context.user_data = {"claude_tier": tier}
        return context

    async def test_direct_chat_falls_back_to_openai_when_anthropic_returns_400(self):
        from kstock.bot.mixins import remote_claude

        mixin = self._make_mixin()
        update = self._make_update()
        context = self._make_context()
        fake_client = _FakeClient([
            _FakeResponse(
                400,
                text='{"error":{"message":"Your credit balance is too low to access the Anthropic API"}}',
            ),
            _FakeResponse(
                200,
                json_data={
                    "choices": [
                        {"message": {"content": "씨에스윈드는 방산/풍력 수주 확인이 우선입니다."}}
                    ],
                    "usage": {"prompt_tokens": 123, "completion_tokens": 45},
                },
            ),
        ])

        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-test"}, clear=False):
            with patch.object(remote_claude, "_get_api_client", return_value=fake_client):
                with patch.object(remote_claude, "get_reply_markup", return_value=None):
                    await mixin._claude_direct_chat(
                        update,
                        context,
                        "코루 지수가 엄청안좋은데 씨에스윈드 주가는 어떻게 될것 같아?",
                        tier=mixin._CLAUDE_TIERS["bujang"],
                    )

        final_text = update.message.reply_text.await_args_list[-1].args[0]
        assert "API 오류" not in final_text
        assert "보조 엔진으로 우회" in final_text
        assert "씨에스윈드" in final_text

    async def test_image_analysis_falls_back_to_openai_when_anthropic_returns_400(self):
        from kstock.bot.mixins import remote_claude

        mixin = self._make_mixin()
        update = self._make_update()
        context = self._make_context()
        fake_client = _FakeClient([
            _FakeResponse(
                400,
                text='{"error":{"message":"Your credit balance is too low to access the Anthropic API"}}',
            ),
            _FakeResponse(
                200,
                json_data={
                    "choices": [
                        {"message": {"content": "이미지상 핵심은 보유 비중 조절과 손절 기준 재확인입니다."}}
                    ],
                    "usage": {"prompt_tokens": 98, "completion_tokens": 37},
                },
            ),
        ])

        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-test"}, clear=False):
            with patch.object(remote_claude, "_get_api_client", return_value=fake_client):
                await mixin._analyze_image_with_text(
                    update,
                    context,
                    "이 스크린샷 해석해줘",
                    img_b64="ZmFrZS1pbWFnZQ==",
                )

        final_text = update.message.reply_text.await_args_list[-1].args[0]
        assert "API 오류" not in final_text
        assert "보조 엔진으로 우회" in final_text
        assert "보유 비중 조절" in final_text


class TestRemoteClaudeHelpers(unittest.TestCase):
    def test_extract_provider_error_message_from_json(self):
        from kstock.bot.mixins.remote_claude import _extract_provider_error_message

        msg = _extract_provider_error_message(
            '{"error":{"message":"Your credit balance is too low to access the Anthropic API"}}'
        )
        assert "credit balance is too low" in msg

    def test_should_try_openai_chat_fallback_on_credit_error(self):
        from kstock.bot.mixins.remote_claude import _should_try_openai_chat_fallback

        assert _should_try_openai_chat_fallback(
            status_code=400,
            body='{"error":{"message":"Your credit balance is too low to access the Anthropic API"}}',
            openai_key="openai-test",
        )


if __name__ == "__main__":
    unittest.main()
