"""System control mixin: job management, logs, restart via Telegram."""
from __future__ import annotations

import logging
import os
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# 사용자에게 보여줄 주요 작업 목록 (트리거 가능)
_TRIGGERABLE_JOBS = [
    ("morning_briefing", "모닝 브리핑"),
    ("us_premarket_briefing", "미국 프리마켓"),
    ("daily_pdf_report", "장마감 리포트"),
    ("daily_self_report", "자가진단"),
    ("daily_system_score", "시스템 점수"),
    ("sentiment_analysis", "뉴스 감성분석"),
    ("weekly_report", "주간 리포트"),
    ("premarket_buy_planner", "매수 플래너"),
    ("sector_rotation_check", "섹터 로테이션"),
    ("contrarian_scan", "역발상 스캔"),
    ("signal_evaluation", "시그널 평가"),
    ("auto_classify", "자동 분류"),
]


class ControlMixin:
    """Telegram-based system control: jobs, logs, restart."""

    async def _handle_control_callback(self, query, context, payload: str) -> None:
        """Route ctrl:* callbacks."""
        parts = payload.split(":", 1) if payload else ["menu"]
        subcmd = parts[0] if parts else "menu"
        sub_arg = parts[1] if len(parts) > 1 else ""

        handlers = {
            "menu": self._ctrl_menu,
            "jobs": self._ctrl_jobs,
            "trigger": self._ctrl_trigger_menu,
            "run": self._ctrl_run_job,
            "pause_menu": self._ctrl_pause_menu,
            "pause": self._ctrl_pause_job,
            "resume_menu": self._ctrl_resume_menu,
            "resume": self._ctrl_resume_job,
            "stats": self._ctrl_quick_stats,
            "logs": self._ctrl_logs,
            "logs_err": self._ctrl_logs_error,
            "restart": self._ctrl_restart_confirm,
            "restart_yes": self._ctrl_restart_exec,
        }
        handler = handlers.get(subcmd)
        if handler:
            await handler(query, context, sub_arg)

    # ── Menu ──────────────────────────────────────────

    async def _ctrl_menu(self, query, context, _arg: str) -> None:
        buttons = [
            [
                InlineKeyboardButton("📋 작업 목록", callback_data="ctrl:jobs"),
                InlineKeyboardButton("▶️ 작업 실행", callback_data="ctrl:trigger"),
            ],
            [
                InlineKeyboardButton("⏸ 일시중지", callback_data="ctrl:pause_menu"),
                InlineKeyboardButton("▶️ 재개", callback_data="ctrl:resume_menu"),
            ],
            [
                InlineKeyboardButton("📊 점수/비용", callback_data="ctrl:stats"),
                InlineKeyboardButton("📜 로그", callback_data="ctrl:logs"),
            ],
            [
                InlineKeyboardButton("🔴 에러 로그", callback_data="ctrl:logs_err"),
                InlineKeyboardButton("🔄 봇 재시작", callback_data="ctrl:restart"),
            ],
            [
                InlineKeyboardButton("🔙 관리자 메뉴", callback_data="adm:menu"),
            ],
        ]
        await query.edit_message_text(
            "🎮 시스템 컨트롤\n\n실행 중인 봇을 제어합니다.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    # ── Jobs List ─────────────────────────────────────

    async def _ctrl_jobs(self, query, context, _arg: str) -> None:
        jq = getattr(self, "_job_queue", None)
        if not jq:
            await query.edit_message_text("작업 큐를 사용할 수 없습니다.")
            return
        lines = ["📋 스케줄 작업 목록\n"]
        jobs = sorted(jq.jobs(), key=lambda j: j.name or "")
        for job in jobs:
            icon = "▶" if job.enabled else "⏸"
            next_t = job.next_t.strftime("%H:%M") if job.next_t else "-"
            lines.append(f"{icon} {job.name}  ({next_t})")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n..."
        back = [[InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(back))

    # ── Trigger ───────────────────────────────────────

    async def _ctrl_trigger_menu(self, query, context, _arg: str) -> None:
        buttons = []
        row = []
        for job_key, label in _TRIGGERABLE_JOBS:
            row.append(InlineKeyboardButton(label, callback_data=f"ctrl:run:{job_key}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")])
        await query.edit_message_text(
            "▶️ 실행할 작업을 선택하세요",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _ctrl_run_job(self, query, context, job_name: str) -> None:
        if not job_name:
            return
        await query.edit_message_text(f"⏳ '{job_name}' 실행 중...")
        try:
            handler = getattr(self, f"job_{job_name}", None)
            if not handler:
                await query.edit_message_text(f"작업 '{job_name}'을 찾을 수 없습니다.")
                return
            await handler(context)
            back = [[InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")]]
            await query.edit_message_text(
                f"✅ '{job_name}' 실행 완료",
                reply_markup=InlineKeyboardMarkup(back),
            )
        except Exception as e:
            back = [[InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")]]
            await query.edit_message_text(
                f"❌ '{job_name}' 실행 실패\n{str(e)[:500]}",
                reply_markup=InlineKeyboardMarkup(back),
            )

    # ── Pause / Resume ────────────────────────────────

    async def _ctrl_pause_menu(self, query, context, _arg: str) -> None:
        jq = getattr(self, "_job_queue", None)
        if not jq:
            return
        buttons = []
        row = []
        for job in sorted(jq.jobs(), key=lambda j: j.name or ""):
            if job.enabled and job.name:
                row.append(InlineKeyboardButton(
                    f"⏸ {job.name}", callback_data=f"ctrl:pause:{job.name}"
                ))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
        if row:
            buttons.append(row)
        if not buttons:
            buttons.append([InlineKeyboardButton("모든 작업이 이미 중지됨", callback_data="ctrl:menu")])
        buttons.append([InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")])
        await query.edit_message_text(
            "⏸ 일시중지할 작업 선택",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _ctrl_pause_job(self, query, context, job_name: str) -> None:
        jq = getattr(self, "_job_queue", None)
        if not jq:
            return
        for job in jq.jobs():
            if job.name == job_name:
                job.enabled = False
                back = [[InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")]]
                await query.edit_message_text(
                    f"⏸ '{job_name}' 일시중지됨",
                    reply_markup=InlineKeyboardMarkup(back),
                )
                return
        await query.edit_message_text(f"작업 '{job_name}'을 찾을 수 없습니다.")

    async def _ctrl_resume_menu(self, query, context, _arg: str) -> None:
        jq = getattr(self, "_job_queue", None)
        if not jq:
            return
        buttons = []
        row = []
        for job in sorted(jq.jobs(), key=lambda j: j.name or ""):
            if not job.enabled and job.name:
                row.append(InlineKeyboardButton(
                    f"▶ {job.name}", callback_data=f"ctrl:resume:{job.name}"
                ))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
        if row:
            buttons.append(row)
        if not buttons:
            buttons.append([InlineKeyboardButton("중지된 작업 없음", callback_data="ctrl:menu")])
        buttons.append([InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")])
        await query.edit_message_text(
            "▶️ 재개할 작업 선택",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _ctrl_resume_job(self, query, context, job_name: str) -> None:
        jq = getattr(self, "_job_queue", None)
        if not jq:
            return
        for job in jq.jobs():
            if job.name == job_name:
                job.enabled = True
                back = [[InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")]]
                await query.edit_message_text(
                    f"▶️ '{job_name}' 재개됨",
                    reply_markup=InlineKeyboardMarkup(back),
                )
                return
        await query.edit_message_text(f"작업 '{job_name}'을 찾을 수 없습니다.")

    # ── Quick Stats ───────────────────────────────────

    async def _ctrl_quick_stats(self, query, context, _arg: str) -> None:
        lines = ["📊 시스템 점수 + API 비용\n"]
        try:
            with self.db._connect() as conn:
                row = conn.execute(
                    "SELECT score_date, total_score, signal_score, trade_score, "
                    "alert_score, learning_score, cost_score, uptime_score "
                    "FROM system_scores ORDER BY score_date DESC LIMIT 1"
                ).fetchone()
            if row:
                lines.append(f"📅 {row[0]}")
                lines.append(f"🏆 총점: {row[1]}/100")
                lines.append(f"  시그널: {row[2]} | 트레이드: {row[3]}")
                lines.append(f"  알림: {row[4]} | 학습: {row[5]}")
                lines.append(f"  비용: {row[6]} | 업타임: {row[7]}")
            else:
                lines.append("점수 데이터 없음")
        except Exception as e:
            lines.append(f"점수 조회 실패: {e}")

        lines.append("")
        try:
            with self.db._connect() as conn:
                cost_row = conn.execute(
                    "SELECT SUM(total_cost_usd), COUNT(*) FROM api_usage_log"
                ).fetchone()
                today_row = conn.execute(
                    "SELECT SUM(total_cost_usd), COUNT(*) "
                    "FROM api_usage_log WHERE date(timestamp) = date('now')"
                ).fetchone()
            total_cost = round(cost_row[0] or 0, 4) if cost_row else 0
            total_calls = cost_row[1] or 0 if cost_row else 0
            today_cost = round(today_row[0] or 0, 4) if today_row else 0
            today_calls = today_row[1] or 0 if today_row else 0
            lines.append(f"💰 총 비용: ${total_cost} ({total_calls}회)")
            lines.append(f"💰 오늘: ${today_cost} ({today_calls}회)")
        except Exception as e:
            lines.append(f"비용 조회 실패: {e}")

        back = [[InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu")]]
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(back),
        )

    # ── Logs ──────────────────────────────────────────

    async def _ctrl_logs(self, query, context, _arg: str) -> None:
        text = self._read_log_tail(30)
        back = [
            [
                InlineKeyboardButton("🔴 에러만", callback_data="ctrl:logs_err"),
                InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu"),
            ],
        ]
        await query.edit_message_text(
            f"📜 최근 로그 (30줄)\n\n{text}",
            reply_markup=InlineKeyboardMarkup(back),
        )

    async def _ctrl_logs_error(self, query, context, _arg: str) -> None:
        text = self._read_log_tail(50, filter_str="ERROR")
        back = [
            [
                InlineKeyboardButton("📜 전체 로그", callback_data="ctrl:logs"),
                InlineKeyboardButton("🔙 컨트롤", callback_data="ctrl:menu"),
            ],
        ]
        await query.edit_message_text(
            f"🔴 에러 로그\n\n{text}",
            reply_markup=InlineKeyboardMarkup(back),
        )

    def _read_log_tail(self, lines: int = 30, filter_str: str = "") -> str:
        log_path = "/tmp/kstock_bot.log"
        if not os.path.exists(log_path):
            return "로그 파일 없음"
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            if filter_str:
                all_lines = [l for l in all_lines if filter_str.upper() in l.upper()]
            tail = all_lines[-lines:]
            text = "".join(tail)
            if len(text) > 3500:
                text = text[-3500:]
            return text or "(비어있음)"
        except Exception as e:
            return f"로그 읽기 실패: {e}"

    # ── Restart ───────────────────────────────────────

    async def _ctrl_restart_confirm(self, query, context, _arg: str) -> None:
        buttons = [
            [
                InlineKeyboardButton("✅ 재시작 확인", callback_data="ctrl:restart_yes"),
                InlineKeyboardButton("❌ 취소", callback_data="ctrl:menu"),
            ],
        ]
        await query.edit_message_text(
            "🔄 봇을 재시작하시겠습니까?\n\n재시작하면 약 10초간 봇이 응답하지 않습니다.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _ctrl_restart_exec(self, query, context, _arg: str) -> None:
        await query.edit_message_text("🔄 봇 재시작 중...")
        try:
            await context.bot.send_message(
                chat_id=self.chat_id,
                text="🔄 봇 재시작을 시작합니다...",
            )
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable, "-m", "kstock.app"])
