"""Remote Claude Code execution via Telegram — 대화 모드 지원."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403

import asyncio
import re
import time
import traceback

logger = logging.getLogger(__name__)

# Claude CLI path (auto-detect)
_CLAUDE_CLI_CANDIDATES = [
    "/Users/botddol/.local/bin/claude",
    "/Users/botddol/.nvm/versions/node/v20.20.0/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
]

CLAUDE_CLI = next(
    (p for p in _CLAUDE_CLI_CANDIDATES if os.path.exists(p)),
    _CLAUDE_CLI_CANDIDATES[0],
)

# Project directory
PROJECT_DIR = "/Users/botddol/k-quant-system"

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

# Claude 대화 모드 키보드 — MAIN_MENU와 동일 구조 + is_persistent
CLAUDE_MODE_MENU = ReplyKeyboardMarkup(
    [
        ["📊 분석", "📈 시황"],
        ["💰 잔고", "⭐ 즐겨찾기"],
        ["💻 클로드", "🤖 에이전트"],
        ["💬 AI비서", "📋 리포트"],
        ["⚙️ 더보기"],
    ],
    resize_keyboard=True,
    is_persistent=True,
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

            cmd = [
                CLAUDE_CLI, "-p", prompt,
                "--output-format", "text",
                "--dangerously-skip-permissions",
                "--model", "sonnet",
                "--max-turns", "10",
            ]
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
            logger.error("Claude CLI exec error: %s", e, exc_info=True)
            return "실행 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.", -1, elapsed

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
        """💻 클로드 버튼 — 순수 Claude Code 에이전트.

        v8.3: 모든 메시지를 Claude CLI로 라우팅.
        투자 분석 + 맥미니 제어 + 코드 수정 + 범용 AI 모두 가능.
        """
        context.user_data["claude_mode"] = True
        context.user_data["claude_turn"] = 0

        await update.message.reply_text(
            f"💻 Claude Code 에이전트\n"
            f"{'━' * 22}\n\n"
            f"순수 Claude가 맥미니에서 직접 실행됩니다.\n"
            f"투자 분석, 코드 수정, 시스템 제어 모두 가능.\n\n"
            f"예시:\n"
            f"  '삼성전자 분석해줘'\n"
            f"  '봇 로그 확인해줘'\n"
            f"  '파이썬 패키지 설치해줘'\n"
            f"  '디스크 용량 확인'\n"
            f"  '코드 수정해줘'\n\n"
            f"🔙 종료하려면 다른 메뉴 버튼을 누르세요.",
            reply_markup=get_reply_markup(context),
        )

    async def _exit_claude_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """클로드 대화 모드 종료."""
        turns = context.user_data.get("claude_turn", 0)
        context.user_data.pop("claude_mode", None)
        context.user_data.pop("claude_turn", None)
        context.user_data.pop("awaiting_claude_prompt", None)
        context.user_data.pop("claude_chat_history", None)
        await update.message.reply_text(
            f"🤖 Claude 대화를 종료합니다.\n"
            f"총 {turns}회 대화하셨습니다.",
            reply_markup=get_reply_markup(context),
        )

    # v6.2.1: 작업 지시 패턴 — 코드 수정/구현/디버그 등 Claude Code CLI로 라우팅
    _WORK_PATTERNS = re.compile(
        r"(구현|수정|고쳐|만들어|추가|삭제|리팩|업데이트|패치|빌드|배포|재시작|테스트|"
        r"디버그|버그|에러|오류.*수정|코드.*변경|파일.*수정|기능.*넣|설치|"
        r"fix|implement|add|remove|update|deploy|restart|debug|refactor|"
        r"바꿔|변경해|개선해|최적화|마이그|merge|commit|push|pull)",
        re.IGNORECASE,
    )
    # 제외 패턴: 주식 관련 질문은 코드 작업이 아님
    _STOCK_OVERRIDE = re.compile(
        r"(매수|매도|종목|주가|차트|시황|코스피|코스닥|배당|PER|PBR|"
        r"포트폴리오.*분석|리스크|수익률|잔고)",
        re.IGNORECASE,
    )

    def _is_work_instruction(self, text: str) -> bool:
        """텍스트가 코드/시스템 작업 지시인지 판별."""
        # 주식 관련이면 작업 지시 아님
        if self._STOCK_OVERRIDE.search(text):
            return False
        return bool(self._WORK_PATTERNS.search(text))

    # v9.6.1: 투자 질문 강제 라우팅 제거 — 클로드 모드에서 자유롭게 대화
    # 기존 _INVESTMENT_QUESTION_RE 삭제: 클로드가 투자 질문도 직접 처리
    # (필요 시 클로드 CLI 프롬프트에 투자 컨텍스트 주입)

    async def _handle_claude_free_chat(
        self, update: Update, context, text: str
    ) -> None:
        """클로드 자유 대화 — 작업/주식/일반 3단 분기.

        v10.3: 응답 속도 개선
        - 작업 지시 → CLI subprocess (코드/시스템 제어)
        - 주식 질문 → 데이터 수집 + Sonnet full pipeline
        - 일반 대화 → Claude API 직접 호출 (2~3초)
        """
        # 대기 중인 이미지가 있으면 이미지+텍스트 합쳐서 분석
        pending_img = context.user_data.pop("pending_image", None)
        pending_ts = context.user_data.pop("pending_image_ts", 0)
        if pending_img:
            if (time.time() - pending_ts) < self._IMG_WAIT_SECONDS:
                await self._analyze_image_with_text(
                    update, context, text, img_b64=pending_img,
                )
                return
            else:
                await update.message.reply_text(
                    "⏰ 이전 이미지가 만료되었습니다. 필요하면 다시 첨부해주세요.\n"
                    "텍스트만으로 진행합니다.",
                )

        # 1) 작업 지시 → CLI subprocess (코드 수정, 시스템 제어)
        if self._is_work_instruction(text):
            await self._execute_claude_prompt(update, text, context=context)
            return

        # 2) 모든 대화(주식+일반) → API 직접 호출 + 대화 이력 유지
        await self._claude_direct_chat(update, context, text)

    async def _claude_direct_chat(
        self, update: Update, context, text: str
    ) -> None:
        """Claude API 직접 호출 — 모든 대화를 하나의 이력으로 처리.

        v10.3: 주식 질문 + 일반 대화 모두 여기서 처리.
        종목 감지 시 실시간 가격 데이터 자동 추가.
        대화 이력 10턴 유지 → 연속 질문 가능.
        """
        if not self.anthropic_key:
            await update.message.reply_text(
                "⚠️ ANTHROPIC_API_KEY 미설정",
                reply_markup=get_reply_markup(context),
            )
            return

        placeholder = await update.message.reply_text("💬 응답 중...")

        try:
            import httpx

            system_text = await self._build_image_system_prompt()

            # 종목 감지 시 실시간 데이터 추가
            enriched = text
            stock = self._detect_stock_query(text)
            if stock:
                try:
                    code = stock.get("code", "")
                    name = stock.get("name", code)
                    price = await self._get_price(code)
                    data_parts = [f"{name}({code})"]
                    if price and price > 0:
                        data_parts.append(f"현재가: {price:,.0f}원")
                    enriched = f"{text}\n\n[실시간 데이터] {' / '.join(data_parts)}"
                except Exception:
                    pass

            # 대화 이력 (최근 10턴 유지)
            history = context.user_data.get("claude_chat_history", [])
            messages = list(history)
            messages.append({"role": "user", "content": enriched})

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
                        "system": system_text,
                        "messages": messages,
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    answer = data["content"][0]["text"].strip().replace("**", "")
                else:
                    answer = f"API 오류: {resp.status_code}"

            # 대화 이력 저장
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": answer})
            if len(history) > 20:
                history = history[-20:]
            context.user_data["claude_chat_history"] = history

            # 턴 카운트 증가
            turn = context.user_data.get("claude_turn", 0)
            context.user_data["claude_turn"] = turn + 1

            try:
                await placeholder.delete()
            except Exception:
                pass

            reply_markup = get_reply_markup(context)
            chunks = self._split_message(answer)
            for i, chunk in enumerate(chunks):
                rm = reply_markup if i == len(chunks) - 1 else None
                await update.message.reply_text(chunk, reply_markup=rm)

        except Exception as e:
            logger.error("Claude direct chat error: %s", e)
            try:
                await placeholder.edit_text(
                    "⚠️ 응답 중 오류가 발생했어요. 다시 시도해주세요."
                )
            except Exception:
                await update.message.reply_text(
                    "⚠️ 응답 중 오류가 발생했어요. 다시 시도해주세요.",
                    reply_markup=get_reply_markup(context),
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
                reply_markup=get_reply_markup(context),
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
        reply_markup = get_reply_markup(context)

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
            logger.debug("_execute_claude_prompt placeholder delete failed", exc_info=True)

        for i, chunk in enumerate(chunks):
            # 마지막 청크에 키보드 표시
            rm = reply_markup if i == len(chunks) - 1 else None
            await update.message.reply_text(chunk, reply_markup=rm)

        # 대화 모드면 안내 표시
        if in_claude_mode:
            turn_num = context.user_data.get("claude_turn", 1)
            logger.info("Claude Code 대화 모드: turn %d 완료", turn_num)

    # ── 관리자 모드 이미지 처리 ──

    # v9.3: 이미지 대기 시간 120초로 확대 (30초 → 2분)
    _IMG_WAIT_SECONDS = 120

    async def _handle_claude_mode_image(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """클로드 모드 이미지 처리 — 캡션 있으면 즉시, 없으면 후속 텍스트 대기.

        v6.2.1: 이미지 먼저 보내고 텍스트를 나중에 보내는 워크플로 지원.
        v9.3: 대기 시간 30초 → 2분으로 확대, 만료 시 알림 추가.
        """
        if not self.anthropic_key:
            await update.message.reply_text(
                "⚠️ Anthropic API 키 없음",
                reply_markup=CLAUDE_MODE_MENU,
            )
            return

        caption = update.message.caption or ""

        if caption.strip():
            # 캡션이 있으면 즉시 분석
            await self._analyze_image_with_text(update, context, caption)
        else:
            # 캡션 없이 이미지만 보낸 경우: 이미지 임시 저장, 텍스트 대기
            import base64
            try:
                photo = update.message.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                img_bytes = await file.download_as_bytearray()
                img_b64 = base64.b64encode(bytes(img_bytes)).decode()
                context.user_data["pending_image"] = img_b64
                context.user_data["pending_image_ts"] = time.time()
                await update.message.reply_text(
                    "📸 이미지 받았습니다!\n"
                    "💬 2분 안에 질문/지시를 텍스트로 보내주세요.\n"
                    "(바로 분석하려면 '분석' 입력)",
                    reply_markup=CLAUDE_MODE_MENU,
                )
            except Exception as e:
                logger.error("pending image save error: %s", e)
                await update.message.reply_text(
                    "⚠️ 이미지 저장 실패. 다시 보내주세요.",
                    reply_markup=CLAUDE_MODE_MENU,
                )

    async def _analyze_image_with_text(
        self, update: Update, context, prompt: str,
        img_b64: str | None = None,
    ) -> None:
        """이미지 + 텍스트를 합쳐서 Claude Vision API로 분석.

        v9.5.1: 시스템 프롬프트에 포트폴리오/시장/브리핑 컨텍스트 포함.
        img_b64가 None이면 update.message.photo에서 추출.
        """
        placeholder = await update.message.reply_text(
            f"💻 이미지 분석 중...\n📝 {prompt[:80]}",
        )

        try:
            import base64
            import httpx

            if img_b64 is None:
                photo = update.message.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                img_bytes = await file.download_as_bytearray()
                img_b64 = base64.b64encode(bytes(img_bytes)).decode()

            # v9.5.1: 시스템 프롬프트 구축 (포트폴리오/브리핑 컨텍스트 포함)
            system_text = await self._build_image_system_prompt()

            async with httpx.AsyncClient(timeout=45) as client:
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
                        "system": system_text,
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
                                        f"주호님의 이미지 분석 요청입니다.\n\n"
                                        f"질문/요청: {prompt}\n\n"
                                        f"이미지가 주식 차트/데이터/봇 스크린샷이라면 "
                                        f"보유종목과 최근 브리핑을 참고하여 투자 관점에서 분석해주세요.\n"
                                        f"K-Quant 봇이 보낸 메시지 스크린샷이면 "
                                        f"해당 내용을 이미 알고 있는 시스템 컨텍스트와 연결하여 답변해주세요.\n"
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

            try:
                await placeholder.delete()
            except Exception:
                pass

            for i, chunk in enumerate(chunks):
                rm = CLAUDE_MODE_MENU if i == len(chunks) - 1 else None
                await update.message.reply_text(chunk, reply_markup=rm)

        except Exception as e:
            logger.error("Claude mode image error: %s", e)
            await update.message.reply_text(
                "⚠️ 이미지 분석에 실패했어요. 다른 이미지로 시도해주세요.",
                reply_markup=CLAUDE_MODE_MENU,
            )

    async def _build_image_system_prompt(self) -> str:
        """v9.5.1: 이미지 분석용 시스템 프롬프트 (보유종목 + 브리핑 컨텍스트).

        일반 채팅과 동일한 수준의 컨텍스트를 제공하여
        봇 스크린샷/차트 분석 시 자기 데이터를 참조할 수 있게 함.
        """
        parts = [
            "너는 주호님의 전속 AI 수행비서 '퀀트봇'이다.\n"
            "주호님의 이미지/스크린샷을 분석하는 역할이다.\n"
            "아래 보유종목과 최근 브리핑을 참고하여 답변하라.\n\n"
            "[가격 규칙]\n"
            "구체적 가격(지지선, 저항선, 목표가)은 아래 데이터에 있을 때만 인용.\n"
            "추측 가격 절대 금지. 데이터가 없으면 '차트 확인 필요'로 대체.\n\n"
            "[형식 규칙]\n"
            "볼드(**) 사용 금지. 이모지로 가독성 확보.\n"
            "한국어 존댓말. 핵심부터."
        ]

        # 보유종목
        try:
            holdings = self.db.get_active_holdings()
            if holdings:
                h_lines = ["[보유종목]"]
                for h in holdings[:10]:
                    name = h.get("name", "")
                    ticker = h.get("ticker", "")
                    buy_price = h.get("buy_price", 0)
                    current_price = h.get("current_price", 0)
                    pnl_pct = h.get("pnl_pct", 0)
                    h_lines.append(
                        f"- {name}({ticker}): "
                        f"매수가 {buy_price:,.0f}원, "
                        f"현재가 {current_price:,.0f}원, "
                        f"수익률 {pnl_pct:+.1f}%"
                    )
                parts.append("\n".join(h_lines))
        except Exception:
            pass

        # 최근 브리핑
        try:
            briefings = self.db.get_recent_briefings(hours=18, limit=2)
            if briefings:
                for b in briefings:
                    b_type = b.get("briefing_type", "")
                    b_time = b.get("created_at", "")[:16]
                    content = b.get("content", "")[:1200]
                    label = {"premarket": "🇺🇸 프리마켓", "morning": "☀️ 모닝"}.get(
                        b_type, b_type
                    )
                    parts.append(f"[{label} 브리핑 {b_time}]\n{content}")
        except Exception:
            pass

        # 매니저 의견
        try:
            stances = self.db.get_recent_manager_stances(hours=24)
            if stances:
                manager_names = {
                    "scalp": "리버모어", "swing": "오닐",
                    "position": "린치", "long_term": "버핏",
                }
                s_lines = ["[매니저 의견]"]
                for key in ("scalp", "swing", "position", "long_term"):
                    s = stances.get(key, "")
                    if s:
                        s_lines.append(f"- {manager_names.get(key, key)}: {s[:60]}")
                if len(s_lines) > 1:
                    parts.append("\n".join(s_lines))
        except Exception:
            pass

        return "\n\n".join(parts)

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
            await safe_edit_or_reply(query,
                query.message.text + "\n\n\u2705 \uc2b9\uc778 \uc644\ub8cc \u2014 \uc218\uc815\uc774 \uc801\uc6a9\ub418\uc5c8\uc2b5\ub2c8\ub2e4."
            )
        else:
            await safe_edit_or_reply(query,
                query.message.text + "\n\n\u274c \ubb34\uc2dc\ub428 \u2014 \uc218\uc815\uc744 \uc801\uc6a9\ud558\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4."
            )

    async def _on_error_with_auto_fix(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Global error handler — 오류를 Claude Code에게 자동으로 넘깁니다."""
        error = context.error
        if error is None:
            return

        error_source = type(error).__name__
        error_str = str(error)

        # Conflict: polling 경합 — 프레임워크 자체 retry에 맡김
        if "conflict" in error_str.lower():
            if not hasattr(self, "_conflict_count"):
                self._conflict_count = 0
            self._conflict_count += 1
            if self._conflict_count % 10 == 0:
                logger.warning("409 Conflict ongoing (count=%d). Check for duplicate bot processes.", self._conflict_count)
            return

        # Telegram 네트워크 오류는 자동 수정 대상 아님
        skip_patterns = ["Timed out", "Network", "Connection", "ReadTimeout", "WriteTimeout"]
        combined = f"{error_source} {error_str}".lower()
        if any(p.lower() in combined for p in skip_patterns):
            return

        # 무한루프 방지: 최근 60초 내 같은 오류면 건너뛰기
        now = time.monotonic()
        last_fix = getattr(self, "_last_auto_fix", {})
        last_time = last_fix.get(error_source, 0)
        if now - last_time < 60:
            logger.warning("Auto-fix skipped (cooldown): %s", error_source)
            return
        if not hasattr(self, "_last_auto_fix"):
            self._last_auto_fix = {}
        self._last_auto_fix[error_source] = now

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
                    logger.debug("_auto_fix_error notification send failed", exc_info=True)
