"""Remote Claude Code execution via Telegram — 대화 모드 지원."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403

import asyncio
import re
import time
import traceback

logger = logging.getLogger(__name__)

# Claude CLI path
CLAUDE_CLI = "/Users/juhodang/.nvm/versions/node/v20.20.0/bin/claude"

# Project directory
PROJECT_DIR = "/Users/juhodang/k-quant-system"

# Maximum execution time (seconds)
MAX_TIMEOUT = 600  # 10 minutes

# Telegram message limit
TG_MSG_LIMIT = 4000

# Maximum output before head/tail truncation
MAX_OUTPUT_CHARS = 20000

# Dangerous command patterns to block
BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"format\s+",
    r"mkfs\.",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r"shutdown",
    r"reboot",
    r"init\s+0",
]

# Prefix trigger
CLAUDE_PREFIX = "클코"

# Claude Code 대화 모드 키보드
CLAUDE_MODE_MENU = ReplyKeyboardMarkup(
    [["🔙 대화 종료"]],
    resize_keyboard=True,
)


class RemoteClaudeMixin:
    """Mixin for remote Claude Code CLI execution via Telegram.

    대화 모드: 💻 클로드 메뉴 → 연속 대화 (--continue) → 🔙 대화 종료
    단발 모드: 클코 <명령> 접두사로 한 번 실행
    """

    def _is_authorized_chat(self, update: Update) -> bool:
        """Verify the message comes from the authorized CHAT_ID."""
        if not update.effective_chat:
            return False
        return str(update.effective_chat.id) == str(self.chat_id)

    @staticmethod
    def _is_blocked_prompt(prompt: str) -> bool:
        """Check if the prompt contains dangerous patterns."""
        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, prompt, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _split_message(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
        """Split text into chunks that fit Telegram's message limit."""
        if len(text) <= limit:
            return [text]

        chunks = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break

            split_point = text.rfind("\n", 0, limit)
            if split_point == -1 or split_point < limit // 2:
                split_point = limit

            chunks.append(text[:split_point])
            text = text[split_point:].lstrip("\n")

        return chunks

    async def _run_claude_cli(
        self, prompt: str, *, continue_conversation: bool = False
    ) -> tuple[str, int, float]:
        """Execute Claude Code CLI asynchronously.

        Args:
            prompt: The prompt to send.
            continue_conversation: True면 --continue 플래그로 이전 대화 이어가기.

        Returns:
            Tuple of (output_text, return_code, elapsed_seconds).
        """
        start_time = time.monotonic()

        try:
            # CLAUDECODE 환경변수 제거: 중첩 세션 방지
            clean_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            clean_env["PYTHONPATH"] = "src"

            cmd = [CLAUDE_CLI, "-p", prompt, "--output-format", "text"]
            if continue_conversation:
                cmd.append("--continue")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=PROJECT_DIR,
                env=clean_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=MAX_TIMEOUT,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                elapsed = time.monotonic() - start_time
                return (
                    f"타임아웃: {MAX_TIMEOUT}초 초과\n"
                    f"프로세스를 강제 종료했습니다.",
                    -1,
                    elapsed,
                )

            elapsed = time.monotonic() - start_time
            output = stdout.decode("utf-8", errors="replace")
            errors = stderr.decode("utf-8", errors="replace")

            if process.returncode != 0 and errors:
                output = f"{output}\n\n[stderr]\n{errors}" if output else errors

            return output.strip(), process.returncode, elapsed

        except FileNotFoundError:
            elapsed = time.monotonic() - start_time
            return "Claude CLI를 찾을 수 없습니다. 경로를 확인해주세요.", -1, elapsed
        except Exception as e:
            elapsed = time.monotonic() - start_time
            return f"실행 오류: {str(e)[:200]}", -1, elapsed

    async def cmd_claude(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/claude command handler — 대화 모드 진입 또는 단발 실행."""
        if not self._is_authorized_chat(update):
            return

        self._persist_chat_id(update)

        args = context.args or []
        if not args:
            await self._menu_claude_code(update, context)
            return

        prompt = " ".join(args)
        await self._execute_claude_prompt(update, prompt, context=context)

    async def _menu_claude_code(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """💻 클로드 메뉴 버튼 핸들러 — Claude Code 대화 모드 진입."""
        context.user_data["claude_mode"] = True
        context.user_data["claude_turn"] = 0  # 대화 턴 카운터
        await update.message.reply_text(
            "💻 맥미니 원격 개발 모드\n\n"
            "봇 서비스 관리/개발 명령을 내리세요.\n"
            "연속 대화가 이어집니다.\n\n"
            "서비스 관리:\n"
            "  봇 로그 최근 50줄 보여줘\n"
            "  봇 프로세스 상태 확인해줘\n"
            "  테스트 전체 돌려줘\n\n"
            "코드 수정:\n"
            "  scheduler.py에서 매크로 주기 변경해줘\n"
            "  새로운 알림 기능 추가해줘\n"
            "  이 버그 원인 찾아서 고쳐줘\n\n"
            "시스템 확인:\n"
            "  디스크 용량 확인해줘\n"
            "  DB 테이블 목록 보여줘\n"
            "  현재 스케줄 잡 상태 확인\n\n"
            "🔙 대화 종료 버튼으로 나갑니다.",
            reply_markup=CLAUDE_MODE_MENU,
        )

    async def _exit_claude_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Claude Code 대화 모드 종료."""
        turns = context.user_data.get("claude_turn", 0)
        context.user_data.pop("claude_mode", None)
        context.user_data.pop("claude_turn", None)
        context.user_data.pop("awaiting_claude_prompt", None)
        await update.message.reply_text(
            f"💻 Claude Code 대화 종료\n"
            f"총 {turns}회 대화했습니다.",
            reply_markup=MAIN_MENU,
        )

    async def _execute_claude_prompt(
        self, update: Update, prompt: str, *, context=None
    ) -> None:
        """Common logic: validate, run Claude CLI, send result.

        대화 모드에서는 첫 턴 이후 --continue로 이어갑니다.
        """
        if self._is_blocked_prompt(prompt):
            await update.message.reply_text(
                "🚫 차단된 명령입니다.\n위험한 시스템 명령은 실행할 수 없습니다.",
                reply_markup=CLAUDE_MODE_MENU if context and context.user_data.get("claude_mode") else MAIN_MENU,
            )
            return

        # 대화 모드 여부 확인
        in_claude_mode = context and context.user_data.get("claude_mode")
        turn = 0
        if in_claude_mode:
            turn = context.user_data.get("claude_turn", 0)
            context.user_data["claude_turn"] = turn + 1

        placeholder = await update.message.reply_text(
            f"💻 Claude Code 실행 중...\n\n"
            f"📝 {prompt[:100]}{'...' if len(prompt) > 100 else ''}\n"
            f"⏳ 최대 {MAX_TIMEOUT // 60}분 소요될 수 있습니다."
        )

        # 첫 턴은 새 대화, 이후는 --continue
        continue_conv = in_claude_mode and turn > 0
        output, return_code, elapsed = await self._run_claude_cli(
            prompt, continue_conversation=continue_conv,
        )

        status = "✅" if return_code == 0 else "⚠️"
        header = (
            f"💻 {status} ({elapsed:.0f}초)\n"
            f"{'─' * 20}\n\n"
        )

        if not output:
            output = "(출력 없음)"

        full_output = header + output

        # 대화 모드면 키보드 유지
        reply_markup = CLAUDE_MODE_MENU if in_claude_mode else MAIN_MENU

        if len(full_output) > MAX_OUTPUT_CHARS:
            summary = (
                f"{header}"
                f"출력이 너무 깁니다 ({len(output):,}자)\n\n"
                f"{'─' * 20}\n"
                f"[앞부분]\n{output[:3000]}\n\n"
                f"... ({len(output) - 6000:,}자 생략) ...\n\n"
                f"[뒷부분]\n{output[-3000:]}"
            )
            chunks = self._split_message(summary)
        else:
            chunks = self._split_message(full_output)

        # placeholder 삭제하고 새 메시지로 응답 (키보드 유지를 위해)
        try:
            await placeholder.delete()
        except Exception:
            pass

        for i, chunk in enumerate(chunks):
            # 마지막 청크에 키보드 표시
            rm = reply_markup if i == len(chunks) - 1 else None
            await update.message.reply_text(chunk, reply_markup=rm)

        # 대화 모드면 안내 표시
        if in_claude_mode:
            turn_num = context.user_data.get("claude_turn", 1)
            logger.info("Claude Code 대화 모드: turn %d 완료", turn_num)

    # ── 관리자 모드 이미지 처리 ──

    async def _handle_claude_mode_image(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """관리자 모드에서 이미지를 Claude Vision API로 분석."""
        if not self.anthropic_key:
            await update.message.reply_text(
                "⚠️ Anthropic API 키 없음",
                reply_markup=CLAUDE_MODE_MENU,
            )
            return

        caption = update.message.caption or "이 이미지를 분석해줘"
        await update.message.reply_text(
            f"💻 이미지 분석 중...\n📝 {caption[:80]}",
        )

        try:
            import base64
            import httpx

            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            img_bytes = await file.download_as_bytearray()
            img_b64 = base64.b64encode(bytes(img_bytes)).decode()

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-5-20250929",
                        "max_tokens": 2000,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": img_b64,
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        f"주호님의 관리자 모드 이미지 분석 요청입니다.\n\n"
                                        f"질문/요청: {caption}\n\n"
                                        f"이미지가 주식 차트/데이터라면 투자 관점에서 분석해주세요.\n"
                                        f"코드나 에러 스크린샷이면 원인 분석 + 해결책을 제시해주세요.\n"
                                        f"볼드(**) 사용 금지. 이모지로 가독성 확보."
                                    ),
                                },
                            ],
                        }],
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    analysis = data["content"][0]["text"].strip().replace("**", "")
                else:
                    analysis = f"API 오류: {resp.status_code}"

            # 턴 카운트 증가
            turn = context.user_data.get("claude_turn", 0)
            context.user_data["claude_turn"] = turn + 1

            header = f"💻 이미지 분석 완료\n{'─' * 20}\n\n"
            full = header + analysis
            chunks = self._split_message(full)

            for i, chunk in enumerate(chunks):
                rm = CLAUDE_MODE_MENU if i == len(chunks) - 1 else None
                await update.message.reply_text(chunk, reply_markup=rm)

        except Exception as e:
            logger.error("Claude mode image error: %s", e)
            await update.message.reply_text(
                f"⚠️ 이미지 분석 실패: {str(e)[:200]}",
                reply_markup=CLAUDE_MODE_MENU,
            )

    # ── 오류 자동 감지 → Claude Code 수정 요청 ──

    async def _auto_fix_error(
        self, context: ContextTypes.DEFAULT_TYPE, error_source: str, error_msg: str
    ) -> None:
        """봇 오류 발생 시 Claude Code에게 자동 수정 요청."""
        if not self.chat_id:
            return

        prompt = (
            f"봇에서 오류가 발생했습니다. 분석하고 수정해주세요.\n\n"
            f"[오류 위치] {error_source}\n"
            f"[오류 내용]\n{error_msg[:2000]}\n\n"
            f"1. 원인을 분석하세요\n"
            f"2. 수정이 가능하면 코드를 수정하세요\n"
            f"3. 수정 후 PYTHONPATH=src python3 -m pytest tests/ -x -q 로 테스트하세요\n"
            f"4. 수정 내용을 요약해주세요"
        )

        await context.bot.send_message(
            chat_id=self.chat_id,
            text=(
                f"🔧 오류 감지 → Claude Code 자동 수정 시작\n\n"
                f"📍 {error_source}\n"
                f"❌ {error_msg[:200]}\n\n"
                f"⏳ 수정 중..."
            ),
        )

        output, return_code, elapsed = await self._run_claude_cli(prompt)

        status = "\u2705 수정 완료" if return_code == 0 else "\u26a0\ufe0f 수정 실패"
        separator = "\u2500" * 20
        header = (
            f"\U0001f527 Claude Code {status}\n"
            f"\U0001f4cd {error_source}\n"
            f"\u23f1 {elapsed:.1f}\ucd08 \uc18c\uc694\n"
            f"{separator}\n\n"
        )

        if not output:
            output = "(\ucd9c\ub825 \uc5c6\uc74c)"

        result = header + output
        chunks = self._split_message(result)

        # 마지막 청크에 승인/거부 버튼 추가
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1 and return_code == 0:
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "\u2705 \uc2b9\uc778", callback_data="autofix:approve",
                        ),
                        InlineKeyboardButton(
                            "\u274c \ubb34\uc2dc", callback_data="autofix:dismiss",
                        ),
                    ],
                ])
                await context.bot.send_message(
                    chat_id=self.chat_id, text=chunk,
                    reply_markup=keyboard,
                )
            else:
                await context.bot.send_message(
                    chat_id=self.chat_id, text=chunk,
                )

    async def _action_autofix(self, query, context, payload: str) -> None:
        """오류 자동수정 승인/거부 콜백."""
        if payload == "approve":
            await query.edit_message_text(
                query.message.text + "\n\n\u2705 \uc2b9\uc778 \uc644\ub8cc \u2014 \uc218\uc815\uc774 \uc801\uc6a9\ub418\uc5c8\uc2b5\ub2c8\ub2e4."
            )
        else:
            await query.edit_message_text(
                query.message.text + "\n\n\u274c \ubb34\uc2dc\ub428 \u2014 \uc218\uc815\uc744 \uc801\uc6a9\ud558\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4."
            )

    async def _on_error_with_auto_fix(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Global error handler — 오류를 Claude Code에게 자동으로 넘깁니다."""
        error = context.error
        if error is None:
            return

        # 무한루프 방지: 최근 60초 내 같은 오류면 건너뛰기
        error_source = type(error).__name__
        now = time.monotonic()
        last_fix = getattr(self, "_last_auto_fix", {})
        last_time = last_fix.get(error_source, 0)
        if now - last_time < 60:
            logger.warning("Auto-fix skipped (cooldown): %s", error_source)
            return
        if not hasattr(self, "_last_auto_fix"):
            self._last_auto_fix = {}
        self._last_auto_fix[error_source] = now

        # Conflict / Telegram 네트워크 오류는 자동 수정 대상 아님
        error_str = str(error)
        skip_patterns = ["Conflict", "Timed out", "Network", "Connection"]
        if any(p.lower() in error_str.lower() for p in skip_patterns):
            logger.warning("Auto-fix skipped (network/conflict): %s", error_source)
            return

        tb = traceback.format_exception(type(error), error, error.__traceback__)
        error_msg = "".join(tb)

        logger.error("Bot error (auto-fix): %s\n%s", error_source, error_msg)

        try:
            await self._auto_fix_error(context, error_source, error_msg)
        except Exception as fix_err:
            logger.error("Auto-fix failed: %s", fix_err)
            if self.chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=self.chat_id,
                        text=(
                            f"🔧 자동 수정 실패\n\n"
                            f"📍 {error_source}\n"
                            f"❌ {str(error)[:300]}\n\n"
                            f"수동 확인이 필요합니다."
                        ),
                    )
                except Exception:
                    pass
