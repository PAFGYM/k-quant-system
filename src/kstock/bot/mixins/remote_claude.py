"""Remote Claude Code execution via Telegram — 대화 모드 지원."""
from __future__ import annotations

from kstock.bot.bot_imports import *  # noqa: F403

import asyncio
import httpx
import json
import re
import time
import traceback

logger = logging.getLogger(__name__)

# v10.3.1: 공유 httpx 클라이언트 (FD leak 방지)
_shared_api_client: httpx.AsyncClient | None = None

_ANTHROPIC_FALLBACK_MARKERS = (
    "credit balance is too low",
    "insufficient_quota",
    "rate limit",
    "overloaded",
    "temporarily unavailable",
    "service unavailable",
)

_OPENAI_CHAT_FALLBACK_MODELS = {
    "daeri": "gpt-4o-mini",
    "bujang": "gpt-4o",
    "daepyo": "gpt-4o",
}

_DEFAULT_CLAUDE_CLI_BUDGET_USD = 0.80
_DEFAULT_CLAUDE_CLI_OPUS_BUDGET_USD = 3.00
_DEFAULT_CLAUDE_CLI_MAX_TURNS = 8
_DEFAULT_CLAUDE_CLI_OPUS_MAX_TURNS = 5
_DEFAULT_CEO_OPUS_ARM_SECONDS = 300
_WEB_SEARCH_HINTS = re.compile(
    r"(검색|서치|웹|뉴스\s*찾|최신|실시간|look up|web search|browse|기사\s*찾)",
    re.IGNORECASE,
)
_NO_WEB_SEARCH_PROMPT = (
    "웹 검색 도구는 기본적으로 사용하지 마라. "
    "로컬 파일, 현재 작업 디렉터리, 이미 제공된 문맥과 추론만으로 해결하라. "
    "사용자가 최신 정보 확인이나 웹 검색을 명시적으로 요청한 경우에만 웹 검색을 고려하라."
)
_CLAUDE_CLI_BLOCKED_ENV_KEYS = {
    "CLAUDECODE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
}


def _get_api_client() -> httpx.AsyncClient:
    global _shared_api_client
    if _shared_api_client is None or _shared_api_client.is_closed:
        _shared_api_client = httpx.AsyncClient(timeout=45)
    return _shared_api_client


def _build_claude_cli_env() -> dict[str, str]:
    """Claude CLI는 구독 로그인 경로를 우선 사용하도록 인증 env를 제거한다."""
    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in _CLAUDE_CLI_BLOCKED_ENV_KEYS
    }
    clean_env["PYTHONPATH"] = "src"
    return clean_env


def _get_openai_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def _load_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
        return value if value > 0 else default
    except Exception:
        return default


def _load_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
        return value if value > 0 else default
    except Exception:
        return default


def _should_allow_claude_web_search(prompt: str) -> bool:
    return bool(_WEB_SEARCH_HINTS.search(str(prompt or "")))


def _get_claude_cli_budget_usd(model: str) -> float:
    if model == "opus":
        return _load_float_env(
            "CLAUDE_CODE_OPUS_MAX_BUDGET_USD",
            _DEFAULT_CLAUDE_CLI_OPUS_BUDGET_USD,
        )
    return _load_float_env(
        "CLAUDE_CODE_MAX_BUDGET_USD",
        _DEFAULT_CLAUDE_CLI_BUDGET_USD,
    )


def _get_claude_cli_max_turns(model: str) -> int:
    if model == "opus":
        return _load_int_env(
            "CLAUDE_CODE_OPUS_MAX_TURNS",
            _DEFAULT_CLAUDE_CLI_OPUS_MAX_TURNS,
        )
    return _load_int_env(
        "CLAUDE_CODE_MAX_TURNS",
        _DEFAULT_CLAUDE_CLI_MAX_TURNS,
    )


def _extract_provider_error_message(body: str) -> str:
    """Upstream API 오류 바디에서 사람이 읽을 메시지를 뽑는다."""
    if not body:
        return ""

    try:
        payload = json.loads(body)
    except Exception:
        payload = {}

    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("details") or "").strip()
        elif error:
            message = str(error).strip()
        if not message:
            message = str(payload.get("message") or "").strip()

    if not message:
        message = str(body).strip()

    message = re.sub(r"\s+", " ", message).strip()
    return message[:240]


def _should_try_openai_chat_fallback(
    *,
    status_code: int,
    body: str,
    openai_key: str,
) -> bool:
    """Anthropic 대화 실패 시 OpenAI 우회 가능 여부를 판단한다."""
    if not openai_key:
        return False

    body_lower = (body or "").lower()
    if any(marker in body_lower for marker in _ANTHROPIC_FALLBACK_MARKERS):
        return True
    return status_code in {400, 408, 409, 429, 500, 502, 503, 504}


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
        ["📋 오늘 행동", "🔎 종목 검색"],
        ["💰 내 보유", "📈 시장 브리핑"],
        ["🧠 AI 토론", "⭐ 즐겨찾기"],
        ["💻 클로드", "🤖 에이전트"],
        ["⚙️ 더보기"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


class RemoteClaudeMixin:
    """Mixin for remote Claude Code CLI execution via Telegram.

    대화 모드: 💻 클로드 메뉴 → 연속 대화 (--continue) → 🔙 대화 종료
    단발 모드: 클코 <명령> 접두사로 한 번 실행
    클대표 리모콘: 텔레그램에서 Claude Code CLI 완전 제어
    """

    # ── 클대표 리모콘 퀵 액션 (v11.0) ────────────────────────────
    _CEO_ACTIONS: dict[str, dict] = {
        "git_status": {
            "label": "📋 Git Status",
            "bash": "cd /Users/botddol/k-quant-system && git status && echo '---' && git log --oneline -5",
        },
        "git_diff": {
            "label": "📝 Git Diff",
            "bash": "cd /Users/botddol/k-quant-system && git diff --stat && echo '---' && git diff | head -100",
        },
        "commit": {
            "label": "💾 커밋",
            "cli": "변경사항을 확인하고 적절한 커밋 메시지로 커밋해줘. git status로 확인 후 진행.",
            "turns": 5,
        },
        "push": {
            "label": "🔀 푸시",
            "bash": "cd /Users/botddol/k-quant-system && git push origin main 2>&1",
        },
        "test": {
            "label": "🧪 테스트",
            "bash": "cd /Users/botddol/k-quant-system && PYTHONPATH=src python3 -m pytest tests/ -x -q --tb=short 2>&1 | tail -30",
        },
        "logs": {
            "label": "📊 에러로그",
            "bash": "cd /Users/botddol/k-quant-system && tail -n 120 data/logs/kquant_error.log data/bot_error.log 2>/dev/null | grep -iE '(error|exception|traceback|critical|warning)' | tail -20",
        },
        "restart": {"label": "🔄 봇재시작", "special": "restart"},
        "system": {"label": "📦 시스템상태", "special": "stats"},
        "dashboard": {"label": "🏠 대시보드", "special": "dashboard"},
        "continue": {"label": "🔄 이어서", "special": "continue"},
        "arm_opus": {"label": "🔥 Opus 1회 승인", "special": "arm_opus"},
    }

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
        self, prompt: str, *, continue_conversation: bool = False,
        model: str = "sonnet",
    ) -> tuple[str, int, float]:
        """Execute Claude Code CLI asynchronously.

        Args:
            prompt: The prompt to send.
            continue_conversation: True면 --continue 플래그로 이전 대화 이어가기.
            model: CLI 모델 (sonnet/opus). 클대표는 기본 Sonnet, 승인 시 1회 Opus.

        Returns:
            Tuple of (output_text, return_code, elapsed_seconds).
        """
        start_time = time.monotonic()

        try:
            # Claude CLI는 구독 로그인(oauth/keychain) 경로를 우선 사용한다.
            clean_env = _build_claude_cli_env()

            cmd = [
                CLAUDE_CLI, "-p", prompt,
                "--output-format", "text",
                "--dangerously-skip-permissions",
                "--model", model,
                "--max-turns", str(_get_claude_cli_max_turns(model)),
                "--max-budget-usd", f"{_get_claude_cli_budget_usd(model):.2f}",
            ]
            if not _should_allow_claude_web_search(prompt):
                cmd.extend(["--append-system-prompt", _NO_WEB_SEARCH_PROMPT])
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

    # ── 클로드 3단계 모드 (v10.3.1) ─────────────────────────────
    _CLAUDE_TIERS = {
        "daeri": {
            "name": "클대리",
            "emoji": "💬",
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 800,
            "desc": "빠른 답변, 일반 대화, 간단한 질문",
        },
        "bujang": {
            "name": "클부장",
            "emoji": "📊",
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 1400,
            "desc": "주식 분석, 투자 전략, 시황 해석",
        },
        "daepyo": {
            "name": "클대표",
            "emoji": "🧠",
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 1800,
            "desc": "오류 수정, 기능 개발, 기본 Sonnet 안전모드",
            "use_cli": True,
        },
    }

    async def _menu_claude_code(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """💻 클로드 → 3단계 모드 선택 (v10.3.1)."""
        context.user_data["claude_mode"] = True
        context.user_data["claude_turn"] = 0

        buttons = [
            [InlineKeyboardButton(
                "💬 클대리 — 빠른 답변",
                callback_data="claude_tier:daeri",
            )],
            [InlineKeyboardButton(
                "📊 클부장 — 주식 분석",
                callback_data="claude_tier:bujang",
            )],
            [InlineKeyboardButton(
                "🧠 클대표 — 관리자 안전모드",
                callback_data="claude_tier:daepyo",
            )],
        ]
        # 이전 모드가 있으면 표시
        prev = context.user_data.get("claude_tier", "")
        prev_name = self._CLAUDE_TIERS.get(prev, {}).get("name", "")
        status = f"\n현재: {prev_name}" if prev_name else ""

        await update.message.reply_text(
            f"💻 Claude 에이전트{status}\n"
            f"{'━' * 22}\n\n"
            f"💬 클대리 (Haiku)\n"
            f"  빠른 답변, 일상 대화\n\n"
            f"📊 클부장 (Sonnet)\n"
            f"  주식 분석, 투자 전략\n\n"
            f"🧠 클대표 (기본 Sonnet)\n"
            f"  오류 수정, 기능 개발, 관리자 안전모드\n"
            f"  Opus는 1회 승인 후에만 실행\n\n"
            f"모드를 선택하세요 👇",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _action_claude_tier(self, query, context, payload: str = "") -> None:
        """claude_tier:{tier} 콜백 — 모드 전환."""
        tier = self._CLAUDE_TIERS.get(payload)
        if not tier:
            return

        context.user_data["claude_mode"] = True
        context.user_data["claude_tier"] = payload
        context.user_data["claude_chat_history"] = []
        context.user_data["claude_turn"] = 0
        context.user_data.pop("ceo_opus_armed_until", None)

        # v11.0: 클대표 → 리모콘 대시보드
        if payload == "daepyo":
            await self._ceo_dashboard(query, context)
            return

        await safe_edit_or_reply(query,
            f"{tier['emoji']} {tier['name']} 모드 활성화\n"
            f"{'━' * 22}\n\n"
            f"{tier['desc']}\n\n"
            f"자유롭게 대화하세요.\n"
            f"모드 전환: 💻 클로드 다시 누르기\n"
            f"종료: 다른 메뉴 버튼"
            )

    # ── 클대표 리모콘 메서드 (v11.0) ────────────────────────────

    @staticmethod
    def _get_ceo_opus_arm_seconds() -> int:
        return _load_int_env(
            "CLAUDE_CODE_OPUS_ARM_SECONDS",
            _DEFAULT_CEO_OPUS_ARM_SECONDS,
        )

    def _is_ceo_opus_armed(self, context) -> bool:
        deadline = float(context.user_data.get("ceo_opus_armed_until", 0) or 0)
        if deadline <= time.time():
            context.user_data.pop("ceo_opus_armed_until", None)
            return False
        return True

    def _arm_ceo_opus(self, context) -> int:
        seconds = self._get_ceo_opus_arm_seconds()
        context.user_data["ceo_opus_armed_until"] = time.time() + seconds
        return seconds

    def _consume_ceo_cli_model(self, context) -> tuple[str, bool]:
        if self._is_ceo_opus_armed(context):
            context.user_data.pop("ceo_opus_armed_until", None)
            return "opus", True
        return "sonnet", False

    async def _ceo_dashboard(self, target, context=None) -> None:
        """🧠 클대표 리모콘 대시보드 — 퀵 액션 버튼 표시.

        target: CallbackQuery 또는 Update (둘 다 호환).
        """
        opus_ready = bool(context and self._is_ceo_opus_armed(context))
        opus_label = "🔥 Opus 승인됨" if opus_ready else "🔥 Opus 1회 승인"
        buttons = [
            [
                InlineKeyboardButton("📋 Git Status", callback_data="ceo:git_status"),
                InlineKeyboardButton("📝 Git Diff", callback_data="ceo:git_diff"),
            ],
            [
                InlineKeyboardButton("💾 커밋", callback_data="ceo:commit"),
                InlineKeyboardButton("🔀 푸시", callback_data="ceo:push"),
            ],
            [
                InlineKeyboardButton("🧪 테스트", callback_data="ceo:test"),
                InlineKeyboardButton("📊 에러로그", callback_data="ceo:logs"),
            ],
            [
                InlineKeyboardButton(opus_label, callback_data="ceo:arm_opus"),
                InlineKeyboardButton("📦 시스템상태", callback_data="ceo:system"),
            ],
            [
                InlineKeyboardButton("🔄 봇재시작", callback_data="ceo:restart"),
                InlineKeyboardButton("🏠 대시보드", callback_data="ceo:dashboard"),
            ],
        ]
        mode_line = "현재 실행모드: Opus 1회 승인됨" if opus_ready else "현재 실행모드: Sonnet 안전모드"
        text = (
            "🧠 클대표 리모콘\n"
            f"{'━' * 22}\n\n"
            "Claude Code CLI 원격 제어\n\n"
            f"⚡ {mode_line}\n"
            "기본은 Sonnet 안전모드입니다.\n"
            "Opus는 1회 승인 후 다음 실행에만 사용됩니다.\n\n"
            "💸 각 실행은 비용 상한과 턴 제한이 걸려 있습니다.\n"
            "🌐 웹검색은 사용자가 명시적으로 요청할 때만 허용됩니다."
        )
        markup = InlineKeyboardMarkup(buttons)
        await safe_edit_or_reply(target, text, reply_markup=markup)

    async def _action_ceo(self, query, context, payload: str = "") -> None:
        """ceo:{action} 콜백 핸들러 — 퀵 액션 라우터."""
        action = self._CEO_ACTIONS.get(payload)
        if not action:
            return

        special = action.get("special")

        # 특수 액션
        if special == "dashboard":
            await self._ceo_dashboard(query, context)
            return
        if special == "continue":
            context.user_data["ceo_continue"] = True
            await safe_edit_or_reply(query,
                "🔄 이어서 모드\n\n"
                "다음 메시지가 이전 Claude Code 대화를 이어갑니다.\n"
                "명령을 입력하세요."
            )
            return
        if special == "arm_opus":
            seconds = self._arm_ceo_opus(context)
            await safe_edit_or_reply(query,
                "🔥 Opus 1회 승인 완료\n\n"
                f"다음 Claude Code 실행 1회에만 Opus를 사용합니다.\n"
                f"승인 유지 시간: {seconds // 60}분\n"
                "그 이후에는 자동으로 Sonnet 안전모드로 돌아갑니다.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 대시보드", callback_data="ceo:dashboard")],
                ]),
            )
            return
        if special == "restart":
            await safe_edit_or_reply(query,
                "🔄 봇 재시작 중...\n잠시 후 자동으로 연결됩니다."
            )
            # 기존 ControlMixin의 재시작 로직 재사용
            import sys
            os.execv(sys.executable, [sys.executable, "-m", "kstock.app"])
            return
        if special == "stats":
            # 기존 시스템 상태 정보 조합
            try:
                import psutil
                mem = psutil.Process().memory_info().rss / 1024 / 1024
                mem_text = f"메모리: {mem:.0f}MB"
            except Exception:
                mem_text = "메모리: (확인불가)"
            try:
                score_row = self.db.get_latest_system_score()
                score_text = f"시스템 점수: {score_row.get('total_score', '?')}/100" if score_row else "시스템 점수: (없음)"
            except Exception:
                score_text = "시스템 점수: (없음)"
            try:
                cost = self.db.get_api_costs_summary()
                cost_today = cost.get("today_cost", 0) if cost else 0
                cost_text = f"오늘 API: ${cost_today:.3f}"
            except Exception:
                cost_text = "오늘 API: (확인불가)"
            alert_mode = getattr(self, "_alert_mode", "normal")
            pid = os.getpid()

            buttons = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 대시보드", callback_data="ceo:dashboard")],
            ])
            await safe_edit_or_reply(query,
                f"📦 시스템 상태\n"
                f"{'━' * 22}\n\n"
                f"🔧 PID: {pid}\n"
                f"💾 {mem_text}\n"
                f"📊 {score_text}\n"
                f"💰 {cost_text}\n"
                f"🚨 알림모드: {alert_mode}\n"
                f"🕐 가동시간: {time.monotonic() / 3600:.1f}h\n"
                f"🧠 클대표 기본모드: Sonnet",
                reply_markup=buttons,
            )
            return

        # Bash 직접 실행
        bash_cmd = action.get("bash")
        if bash_cmd:
            await self._ceo_run_bash(query, bash_cmd, action["label"])
            return

        # Claude Code CLI 실행
        cli_prompt = action.get("cli")
        if cli_prompt:
            turns = action.get("turns", 5)
            await self._ceo_run_claude_via_callback(query, context, cli_prompt, turns)
            return

    async def _ceo_run_bash(self, query, cmd: str, label: str) -> None:
        """Bash 명령 직접 실행 (빠르고 무료)."""
        await safe_edit_or_reply(query, f"⏳ {label} 실행 중...")

        try:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=PROJECT_DIR,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30,
            )
            output = stdout.decode("utf-8", errors="replace").strip()
            errors = stderr.decode("utf-8", errors="replace").strip()
            if errors and process.returncode != 0:
                output = f"{output}\n\n[stderr]\n{errors}" if output else errors
        except asyncio.TimeoutError:
            output = "⏰ 30초 타임아웃"
        except Exception as e:
            output = f"❌ 실행 오류: {e}"

        if not output:
            output = "(출력 없음)"

        # 출력 제한 (Telegram 메시지 한도)
        if len(output) > 3500:
            output = output[:1500] + "\n\n... (중략) ...\n\n" + output[-1500:]

        status = "✅" if "오류" not in output and "타임아웃" not in output else "⚠️"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 대시보드", callback_data="ceo:dashboard")],
        ])

        await query.message.reply_text(
            f"{status} {label}\n"
            f"{'─' * 20}\n\n"
            f"{output}",
            reply_markup=buttons,
        )

    async def _ceo_run_claude_via_callback(
        self, query, context, prompt: str, turns: int = 5,
    ) -> None:
        """콜백에서 Claude Code CLI 실행 (커밋 등 복잡한 작업)."""
        cli_model, used_opus = self._consume_ceo_cli_model(context)
        mode_label = "Opus 프리미엄" if used_opus else "Sonnet 안전모드"
        await safe_edit_or_reply(
            query,
            f"🧠 클대표 실행 중...\n⚙️ {mode_label}\n📝 {prompt[:80]}",
        )

        placeholder = await query.message.reply_text(
            f"⏳ Claude Code({cli_model}) 실행 중..."
        )

        progress_task = asyncio.create_task(
            self._ceo_progress_updater(placeholder, time.monotonic(), "클대표")
        )

        # 대화 이어가기 여부
        turn = context.user_data.get("claude_turn", 0)
        continue_conv = turn > 0
        context.user_data["claude_turn"] = turn + 1

        output, return_code, elapsed = await self._run_claude_cli(
            prompt, continue_conversation=continue_conv, model=cli_model,
        )

        progress_task.cancel()

        if not output:
            output = "(출력 없음)"

        status = "✅" if return_code == 0 else "⚠️"
        engine = "Opus" if used_opus else "Sonnet"
        header = f"💻 {status} {engine} ({elapsed:.0f}초)\n{'─' * 20}\n\n"
        full = header + output

        if len(full) > MAX_OUTPUT_CHARS:
            full = (
                f"{header}출력이 깁니다 ({len(output):,}자)\n\n"
                f"[앞부분]\n{output[:3000]}\n\n"
                f"... ({len(output) - 6000:,}자 생략) ...\n\n"
                f"[뒷부분]\n{output[-3000:]}"
            )

        try:
            await placeholder.delete()
        except Exception:
            pass

        ceo_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 이어서", callback_data="ceo:continue"),
                InlineKeyboardButton("💾 커밋", callback_data="ceo:commit"),
            ],
            [
                InlineKeyboardButton("🔀 푸시", callback_data="ceo:push"),
                InlineKeyboardButton("🔄 봇재시작", callback_data="ceo:restart"),
            ],
            [
                InlineKeyboardButton("🔥 Opus 1회 승인", callback_data="ceo:arm_opus"),
                InlineKeyboardButton("🧪 테스트", callback_data="ceo:test"),
            ],
            [InlineKeyboardButton("🏠 대시보드", callback_data="ceo:dashboard")],
        ])

        chunks = self._split_message(full)
        for i, chunk in enumerate(chunks):
            rm = ceo_buttons if i == len(chunks) - 1 else None
            await query.message.reply_text(chunk, reply_markup=rm)

    async def _ceo_progress_updater(
        self, message, start_time: float, tier_name: str,
    ) -> None:
        """30초마다 placeholder 메시지의 경과 시간을 업데이트."""
        try:
            while True:
                await asyncio.sleep(30)
                elapsed = int(time.monotonic() - start_time)
                try:
                    await message.edit_text(
                        f"🧠 {tier_name} 실행 중... ⏱️ {elapsed}초"
                    )
                except Exception:
                    pass  # 메시지 삭제됨 등
        except asyncio.CancelledError:
            pass

    async def _exit_claude_mode(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """클로드 대화 모드 종료."""
        turns = context.user_data.get("claude_turn", 0)
        tier_key = context.user_data.get("claude_tier", "")
        tier_name = self._CLAUDE_TIERS.get(tier_key, {}).get("name", "Claude")
        context.user_data.pop("claude_mode", None)
        context.user_data.pop("claude_turn", None)
        context.user_data.pop("claude_tier", None)
        context.user_data.pop("awaiting_claude_prompt", None)
        context.user_data.pop("claude_chat_history", None)
        context.user_data.pop("ceo_opus_armed_until", None)
        await update.message.reply_text(
            f"🤖 {tier_name} 대화를 종료합니다.\n"
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
    # 주식/투자 관련 패턴 → Sonnet 라우팅 + 코드 작업 제외
    _STOCK_OVERRIDE = re.compile(
        r"(매수|매도|종목|주가|차트|시황|코스피|코스닥|배당|PER|PBR|"
        r"포트폴리오.*분석|리스크|수익률|잔고|"
        r"분석해|투자|증시|금리|환율|원달러|테마|섹터|업종|실적|"
        r"전망|매크로|지수|선물|옵션|공매도|외국인|기관|"
        r"SK하이닉스|삼성전자|에코프로|현대차|LG|카카오|네이버|"
        r"\d{6})",  # 6자리 종목코드
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
        """클로드 자유 대화 — 선택된 모드별 라우팅 (v10.3.1).

        클대리(Haiku) → API 빠른 응답
        클부장(Sonnet) → API 주식 분석
        클대표(Sonnet 기본) → CLI 코드 수정/기능 개발, 필요 시 Opus 1회 승인
        """
        # 모드 미선택 시 선택 화면 표시
        tier_key = context.user_data.get("claude_tier", "")
        if not tier_key:
            await self._menu_claude_code(update, context)
            return

        tier = self._CLAUDE_TIERS.get(tier_key, self._CLAUDE_TIERS["daeri"])

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
                    "⏰ 이전 이미지가 만료되었습니다.\n"
                    "텍스트만으로 진행합니다.",
                )

        # v11.0: 클대표 리모콘 — 모든 텍스트를 CLI로 전송
        if tier_key == "daepyo":
            # ceo_continue 플래그: "이어서" 버튼으로 설정됨
            if context.user_data.pop("ceo_continue", False):
                context.user_data["claude_turn"] = max(
                    context.user_data.get("claude_turn", 0), 1
                )
            await self._execute_claude_prompt(update, text, context=context)
            return

        # 공통: API 직접 호출 + 대화 이력
        await self._claude_direct_chat(update, context, text, tier)

    async def _claude_direct_chat(
        self, update: Update, context, text: str, tier: dict | None = None,
    ) -> None:
        """Claude API 직접 호출 — 선택된 모드의 모델 사용.

        v10.3.1: 클대리/클부장/클대표 모드별 모델 자동 적용.
        대화 이력 10턴 유지 → 연속 질문 가능.
        """
        if tier is None:
            tier_key = context.user_data.get("claude_tier", "daeri")
            tier = self._CLAUDE_TIERS.get(tier_key, self._CLAUDE_TIERS["daeri"])

        openai_key = _get_openai_key()
        if not self.anthropic_key and not openai_key:
            await update.message.reply_text(
                "⚠️ AI 대화 API 키가 설정되지 않았습니다.",
                reply_markup=get_reply_markup(context),
            )
            return

        placeholder = await update.message.reply_text(
            f"{tier['emoji']} {tier['name']} 응답 중..."
        )

        try:
            import httpx

            tier_key = context.user_data.get("claude_tier", "daeri")
            system_text = await self._build_text_chat_system_prompt(tier_key)

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
            history_limit = 8 if tier_key == "daeri" else 14
            if len(history) > history_limit * 2:
                history = history[-history_limit * 2:]
            messages = list(history)
            messages.append({"role": "user", "content": enriched})
            temperature = 0.15 if tier_key == "daeri" else 0.25
            answer = ""
            used_openai_fallback = False

            if self.anthropic_key:
                client = _get_api_client()
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self.anthropic_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": tier["model"],
                        "max_tokens": tier["max_tokens"],
                        "temperature": temperature,
                        "system": system_text,
                        "messages": messages,
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    answer = data["content"][0]["text"].strip().replace("**", "")
                    try:
                        from kstock.core.token_tracker import track_usage_global

                        usage = data.get("usage", {})
                        track_usage_global(
                            provider="anthropic",
                            model=tier["model"],
                            function_name="claude_direct_chat",
                            input_tokens=usage.get("input_tokens", 0) or 0,
                            output_tokens=usage.get("output_tokens", 0) or 0,
                            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                            cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                        )
                    except Exception:
                        pass
                else:
                    error_text = _extract_provider_error_message(resp.text)
                    if _should_try_openai_chat_fallback(
                        status_code=resp.status_code,
                        body=resp.text,
                        openai_key=openai_key,
                    ):
                        logger.warning(
                            "Claude direct chat returned %d, using OpenAI fallback: %s",
                            resp.status_code,
                            error_text or "no body",
                        )
                        answer = await self._call_openai_chat_fallback(
                            system_text,
                            messages,
                            tier_key=tier_key,
                            max_tokens=tier["max_tokens"],
                            temperature=temperature,
                        )
                        used_openai_fallback = True
                    else:
                        answer = (
                            "⚠️ 클로드 대화 서버가 요청을 처리하지 못했습니다.\n"
                            f"원인: {error_text or f'HTTP {resp.status_code}'}\n"
                            "잠시 후 다시 시도해주세요."
                        )
            else:
                answer = await self._call_openai_chat_fallback(
                    system_text,
                    messages,
                    tier_key=tier_key,
                    max_tokens=tier["max_tokens"],
                    temperature=temperature,
                )
                used_openai_fallback = True

            if used_openai_fallback:
                answer = (
                    "참고: 클로드 응답 경로에 문제가 있어 보조 엔진으로 우회했습니다.\n\n"
                    + answer
                )

            # 대화 이력 저장
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": answer})
            if len(history) > history_limit * 2:
                history = history[-history_limit * 2:]
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

    async def _call_openai_chat_fallback(
        self,
        system_text: str,
        messages: list[dict[str, str]],
        *,
        tier_key: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """클로드 대화 실패 시 OpenAI 채팅으로 우회한다."""
        openai_key = _get_openai_key()
        if not openai_key:
            raise RuntimeError("OPENAI_API_KEY 미설정")

        model = _OPENAI_CHAT_FALLBACK_MODELS.get(tier_key, "gpt-4o-mini")
        payload_messages = []
        if system_text:
            payload_messages.append({"role": "system", "content": system_text})
        payload_messages.extend(messages)

        client = _get_api_client()
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": payload_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        if resp.status_code != 200:
            error_text = _extract_provider_error_message(resp.text)
            raise RuntimeError(
                f"OpenAI fallback error {resp.status_code}: {error_text or 'unknown'}"
            )

        data = resp.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(answer, list):
            answer = "".join(
                part.get("text", "")
                for part in answer
                if isinstance(part, dict)
            )
        answer = str(answer).strip().replace("**", "")

        try:
            from kstock.core.token_tracker import track_usage_global

            usage = data.get("usage", {})
            track_usage_global(
                provider="gpt",
                model=model,
                function_name="claude_text_chat_openai_fallback",
                input_tokens=usage.get("prompt_tokens", 0) or 0,
                output_tokens=usage.get("completion_tokens", 0) or 0,
            )
        except Exception:
            pass

        return answer or "응답이 비어 있습니다."

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

        tier_key = context.user_data.get("claude_tier", "") if context else ""
        tier_name = self._CLAUDE_TIERS.get(tier_key, {}).get("name", "Claude Code")
        is_ceo = tier_key == "daepyo"

        placeholder = await update.message.reply_text(
            f"🧠 {tier_name} 실행 중...\n\n"
            f"📝 {prompt[:100]}{'...' if len(prompt) > 100 else ''}\n"
            f"⏳ 최대 {MAX_TIMEOUT // 60}분 소요될 수 있습니다."
        )

        # v11.0: 진행 상황 업데이트 (30초마다)
        progress_task = None
        if is_ceo:
            progress_task = asyncio.create_task(
                self._ceo_progress_updater(placeholder, time.monotonic(), tier_name)
            )

        # 첫 턴은 새 대화, 이후는 --continue
        continue_conv = in_claude_mode and turn > 0
        used_opus = False
        cli_model = "sonnet"
        if is_ceo:
            cli_model, used_opus = self._consume_ceo_cli_model(context)
        output, return_code, elapsed = await self._run_claude_cli(
            prompt, continue_conversation=continue_conv, model=cli_model,
        )

        # 진행 업데이트 태스크 취소
        if progress_task:
            progress_task.cancel()

        status = "✅" if return_code == 0 else "⚠️"
        engine = " Opus" if used_opus else (" Sonnet" if is_ceo else "")
        header = (
            f"💻 {status}{engine} ({elapsed:.0f}초)\n"
            f"{'─' * 20}\n\n"
        )

        if not output:
            output = "(출력 없음)"

        full_output = header + output

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

        # placeholder 삭제하고 새 메시지로 응답
        try:
            await placeholder.delete()
        except Exception:
            logger.debug("_execute_claude_prompt placeholder delete failed", exc_info=True)

        # v11.0: 클대표 모드 → 후속 액션 버튼
        if is_ceo:
            ceo_buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔄 이어서", callback_data="ceo:continue"),
                    InlineKeyboardButton("💾 커밋", callback_data="ceo:commit"),
                ],
                [
                    InlineKeyboardButton("🔀 푸시", callback_data="ceo:push"),
                    InlineKeyboardButton("🔄 봇재시작", callback_data="ceo:restart"),
                ],
                [
                    InlineKeyboardButton("🔥 Opus 1회 승인", callback_data="ceo:arm_opus"),
                    InlineKeyboardButton("🧪 테스트", callback_data="ceo:test"),
                ],
                [InlineKeyboardButton("🏠 대시보드", callback_data="ceo:dashboard")],
            ])
            for i, chunk in enumerate(chunks):
                rm = ceo_buttons if i == len(chunks) - 1 else None
                await update.message.reply_text(chunk, reply_markup=rm)
        else:
            reply_markup = get_reply_markup(context)
            for i, chunk in enumerate(chunks):
                rm = reply_markup if i == len(chunks) - 1 else None
                await update.message.reply_text(chunk, reply_markup=rm)

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
        if not self.anthropic_key and not _get_openai_key():
            await update.message.reply_text(
                "⚠️ 이미지 분석용 AI API 키가 없습니다.",
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

    async def _build_text_chat_system_prompt(self, tier_key: str = "daeri") -> str:
        """텍스트 전용 클로드 대화 프롬프트."""
        tier = self._CLAUDE_TIERS.get(tier_key, self._CLAUDE_TIERS["daeri"])
        parts = [
            f"너는 주호님의 {tier['name']}이다.",
            "한국어 존댓말로 답하고, 말돌리지 말고 바로 핵심부터 답하라.",
            "가격은 제공된 실시간 데이터가 있을 때만 구체적으로 말하라.",
            "모호한 질문도 먼저 의도를 추정해 실행 가능한 답부터 제시하라.",
        ]

        if tier_key == "daeri":
            parts.append(
                "답변 형식: 1) 한줄 결론 2) 핵심 이유 2~3개. "
                "가능하면 6줄 이내로 짧게 답하라."
            )
        elif tier_key == "bujang":
            parts.append(
                "투자 질문이면 '관심/매수/보유/매도' 중 어디에 가까운지 먼저 말하고, "
                "그 뒤 이유와 지금 확인할 포인트를 정리하라."
            )

        try:
            holdings = self.db.get_active_holdings()
            if holdings:
                holding_lines = ["[보유종목]"]
                for h in holdings[:6]:
                    name = h.get("name", "")
                    ticker = h.get("ticker", "")
                    buy_price = float(h.get("buy_price", 0) or 0)
                    holding_lines.append(f"- {name}({ticker}) 매수가 {buy_price:,.0f}원")
                parts.append("\n".join(holding_lines))
        except Exception:
            logger.debug("text chat holdings prompt build failed", exc_info=True)

        try:
            macro = self.db.get_macro_snapshot() or {}
            market_lines = []
            if macro.get("kospi"):
                market_lines.append(
                    f"코스피 {macro['kospi']:,.2f} ({macro.get('kospi_change_pct', 0):+.2f}%)"
                )
            if macro.get("kosdaq"):
                market_lines.append(
                    f"코스닥 {macro['kosdaq']:,.2f} ({macro.get('kosdaq_change_pct', 0):+.2f}%)"
                )
            if macro.get("vix"):
                market_lines.append(f"VIX {macro.get('vix', 0):.1f}")
            if market_lines:
                parts.append("[시장]\n" + " | ".join(market_lines))
        except Exception:
            logger.debug("text chat market prompt build failed", exc_info=True)

        return "\n\n".join(parts)

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

            if img_b64 is None:
                photo = update.message.photo[-1]
                file = await context.bot.get_file(photo.file_id)
                img_bytes = await file.download_as_bytearray()
                img_b64 = base64.b64encode(bytes(img_bytes)).decode()

            # v9.5.1: 시스템 프롬프트 구축 (포트폴리오/브리핑 컨텍스트 포함)
            system_text = await self._build_image_system_prompt()
            openai_key = _get_openai_key()
            analysis = ""
            used_openai_fallback = False

            if self.anthropic_key:
                client = _get_api_client()
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
                    try:
                        from kstock.core.token_tracker import track_usage_global

                        usage = data.get("usage", {})
                        track_usage_global(
                            provider="anthropic",
                            model="claude-sonnet-4-5-20250929",
                            function_name="claude_image_analysis",
                            input_tokens=usage.get("input_tokens", 0) or 0,
                            output_tokens=usage.get("output_tokens", 0) or 0,
                            cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                            cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                        )
                    except Exception:
                        pass
                else:
                    error_text = _extract_provider_error_message(resp.text)
                    if _should_try_openai_chat_fallback(
                        status_code=resp.status_code,
                        body=resp.text,
                        openai_key=openai_key,
                    ):
                        logger.warning(
                            "Claude vision returned %d, using OpenAI fallback: %s",
                            resp.status_code,
                            error_text or "no body",
                        )
                        analysis = await self._call_openai_vision_fallback(
                            system_text=system_text,
                            prompt=prompt,
                            img_b64=img_b64,
                        )
                        used_openai_fallback = True
                    else:
                        analysis = (
                            "⚠️ 클로드 이미지 분석 서버가 요청을 처리하지 못했습니다.\n"
                            f"원인: {error_text or f'HTTP {resp.status_code}'}\n"
                            "잠시 후 다시 시도해주세요."
                        )
            else:
                analysis = await self._call_openai_vision_fallback(
                    system_text=system_text,
                    prompt=prompt,
                    img_b64=img_b64,
                )
                used_openai_fallback = True

            if used_openai_fallback:
                analysis = (
                    "참고: 클로드 이미지 경로에 문제가 있어 보조 엔진으로 우회했습니다.\n\n"
                    + analysis
                )

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

    async def _call_openai_vision_fallback(
        self,
        *,
        system_text: str,
        prompt: str,
        img_b64: str,
    ) -> str:
        """클로드 비전 실패 시 OpenAI Vision으로 우회한다."""
        openai_key = _get_openai_key()
        if not openai_key:
            raise RuntimeError("OPENAI_API_KEY 미설정")

        model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")
        vision_prompt = (
            f"{system_text}\n\n"
            f"주호님의 이미지 분석 요청입니다.\n\n"
            f"질문/요청: {prompt}\n\n"
            "이미지가 주식 차트/데이터/봇 스크린샷이라면 "
            "보유종목과 최근 브리핑을 참고하여 투자 관점에서 분석해주세요.\n"
            "K-Quant 봇이 보낸 메시지 스크린샷이면 "
            "해당 내용을 이미 알고 있는 시스템 컨텍스트와 연결하여 답변해주세요.\n"
            "코드나 에러 스크린샷이면 원인 분석 + 해결책을 제시해주세요.\n"
            "볼드(**) 사용 금지. 이모지로 가독성 확보."
        )

        client = _get_api_client()
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1800,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": vision_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}",
                                },
                            },
                        ],
                    },
                ],
            },
        )
        if resp.status_code != 200:
            error_text = _extract_provider_error_message(resp.text)
            raise RuntimeError(
                f"OpenAI vision fallback error {resp.status_code}: {error_text or 'unknown'}"
            )

        data = resp.json()
        analysis = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(analysis, list):
            analysis = "".join(
                part.get("text", "")
                for part in analysis
                if isinstance(part, dict)
            )
        analysis = str(analysis).strip().replace("**", "")

        try:
            from kstock.core.token_tracker import track_usage_global

            usage = data.get("usage", {})
            track_usage_global(
                provider="gpt",
                model=model,
                function_name="claude_vision_openai_fallback",
                input_tokens=usage.get("prompt_tokens", 0) or 0,
                output_tokens=usage.get("completion_tokens", 0) or 0,
            )
        except Exception:
            pass

        return analysis or "이미지 분석 결과가 비어 있습니다."

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

        # 날짜/만기일 정보 (AI 날짜 오류 방지)
        try:
            from datetime import datetime as _dt, date as _date
            import calendar
            now = _dt.now()
            days_kr = ["월", "화", "수", "목", "금", "토", "일"]
            today_str = f"{now.month}월 {now.day}일({days_kr[now.weekday()]})"

            # KOSPI200 선물옵션 만기일 = 두번째 목요일
            def _second_thursday(year, month):
                cal = calendar.monthcalendar(year, month)
                count = 0
                for week in cal:
                    if week[3] != 0:  # Thursday
                        count += 1
                        if count == 2:
                            return _date(year, month, week[3])
                return None

            # 미국 옵션 만기일 = 세번째 금요일
            def _third_friday(year, month):
                cal = calendar.monthcalendar(year, month)
                count = 0
                for week in cal:
                    if week[4] != 0:  # Friday
                        count += 1
                        if count == 3:
                            return _date(year, month, week[4])
                return None

            kr_exp = _second_thursday(now.year, now.month)
            us_exp = _third_friday(now.year, now.month)
            # 다음달
            nm = now.month + 1 if now.month < 12 else 1
            ny = now.year if now.month < 12 else now.year + 1
            kr_exp_next = _second_thursday(ny, nm)
            us_exp_next = _third_friday(ny, nm)

            date_lines = [f"[오늘 날짜] {now.year}년 {today_str}"]
            if kr_exp:
                d_diff = (kr_exp - now.date()).days
                status = "오늘!" if d_diff == 0 else f"{d_diff}일 후" if d_diff > 0 else "지남"
                date_lines.append(
                    f"[KR 선물옵션 만기] {kr_exp.month}월 {kr_exp.day}일"
                    f"({days_kr[kr_exp.weekday()]}) — {status}"
                )
            if kr_exp_next and kr_exp and (kr_exp - now.date()).days < 0:
                date_lines.append(
                    f"[KR 다음 만기] {kr_exp_next.month}월 {kr_exp_next.day}일"
                    f"({days_kr[kr_exp_next.weekday()]})"
                )
            if us_exp:
                d_diff = (us_exp - now.date()).days
                status = "오늘!" if d_diff == 0 else f"{d_diff}일 후" if d_diff > 0 else "지남"
                date_lines.append(
                    f"[US 옵션 만기] {us_exp.month}월 {us_exp.day}일"
                    f"({days_kr[us_exp.weekday()]}) — {status}"
                )
            parts.append("\n".join(date_lines))
        except Exception:
            pass

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
        skip_patterns = [
            "Timed out",
            "Network",
            "Connection",
            "ReadTimeout",
            "WriteTimeout",
            "message is not modified",
            "query is too old",
            "callback query is too old",
            "invalid callback data",
            "message to edit not found",
        ]
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
