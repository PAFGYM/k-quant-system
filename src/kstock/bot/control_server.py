"""Unix domain socket control server for K-Quant bot.

Allows CLI (kbot) and external tools to send commands to the running bot.
Protocol: line-delimited JSON over Unix socket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SOCKET_PATH = "/tmp/kquant_control.sock"


class ControlServer:
    """Async Unix socket server embedded in the bot event loop."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.server: asyncio.AbstractServer | None = None
        self._handlers = {
            "ping": self._cmd_ping,
            "status": self._cmd_status,
            "list_jobs": self._cmd_list_jobs,
            "trigger_job": self._cmd_trigger_job,
            "pause_job": self._cmd_pause_job,
            "resume_job": self._cmd_resume_job,
            "alert_mode": self._cmd_alert_mode,
            "get_score": self._cmd_get_score,
            "get_cost": self._cmd_get_cost,
            "send_message": self._cmd_send_message,
            "get_logs": self._cmd_get_logs,
            "run_claude": self._cmd_run_claude,
        }

    async def start(self) -> None:
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        self.server = await asyncio.start_unix_server(
            self._handle_client, path=SOCKET_PATH
        )
        os.chmod(SOCKET_PATH, 0o600)
        logger.info("ControlServer listening on %s", SOCKET_PATH)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        logger.info("ControlServer stopped")

    async def _handle_client(self, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=660)
            if not line:
                return
            request = json.loads(line.decode("utf-8"))
            cmd = request.get("cmd", "")
            args = request.get("args", {})
            handler = self._handlers.get(cmd)
            if handler:
                result = await handler(**args)
                response = {"ok": True, "result": result}
            else:
                response = {"ok": False, "error": f"Unknown command: {cmd}"}
            writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            await writer.drain()
        except Exception as e:
            try:
                err = {"ok": False, "error": str(e)}
                writer.write(json.dumps(err).encode("utf-8") + b"\n")
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # ── Commands ──────────────────────────────────────

    async def _cmd_ping(self, **_kw) -> dict:
        uptime = ""
        if hasattr(self.bot, "_start_time"):
            delta = datetime.now(self.bot._start_time.tzinfo) - self.bot._start_time
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            minutes, secs = divmod(rem, 60)
            uptime = f"{hours}h {minutes}m {secs}s"
        return {"version": "v9.1", "uptime": uptime, "pid": os.getpid()}

    async def _cmd_status(self, **_kw) -> dict:
        import resource
        mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1024 * 1024)
        jobs = self._get_jobs_info()
        alert_mode = getattr(self.bot, "_alert_mode", "normal")
        return {
            "pid": os.getpid(),
            "memory_mb": mem_mb,
            "alert_mode": alert_mode,
            "active_jobs": len([j for j in jobs if j["enabled"]]),
            "total_jobs": len(jobs),
        }

    async def _cmd_list_jobs(self, **_kw) -> list:
        return self._get_jobs_info()

    async def _cmd_trigger_job(self, name: str = "", **_kw) -> str:
        if not name:
            return "Error: job name required"
        job_map = self._build_job_map()
        handler = job_map.get(name)
        if not handler:
            available = sorted(job_map.keys())
            return f"Unknown job: {name}. Available: {', '.join(available)}"
        try:
            ctx = _ControlContext(self.bot._application)
            await handler(ctx)
            return f"Job '{name}' triggered successfully"
        except Exception as e:
            return f"Job '{name}' failed: {str(e)[:300]}"

    async def _cmd_pause_job(self, name: str = "", **_kw) -> str:
        if not name:
            return "Error: job name required"
        return self._set_job_enabled(name, False)

    async def _cmd_resume_job(self, name: str = "", **_kw) -> str:
        if not name:
            return "Error: job name required"
        return self._set_job_enabled(name, True)

    async def _cmd_alert_mode(self, mode: str = "", **_kw) -> str:
        current = getattr(self.bot, "_alert_mode", "normal")
        if not mode:
            return f"Current alert mode: {current}"
        if mode not in ("normal", "elevated", "wartime"):
            return f"Invalid mode: {mode}. Use: normal, elevated, wartime"
        if hasattr(self.bot, "set_alert_mode"):
            await self.bot.set_alert_mode(mode, context=None, reason="CLI control", notify=True)
            return f"Alert mode changed: {current} -> {mode}"
        return "Alert mode not available"

    async def _cmd_get_score(self, **_kw) -> dict:
        try:
            with self.bot.db._connect() as conn:
                row = conn.execute(
                    "SELECT score_date, total_score, signal_score, trade_score, "
                    "alert_score, learning_score, cost_score, uptime_score, details_json "
                    "FROM system_scores ORDER BY score_date DESC LIMIT 1"
                ).fetchone()
            if row:
                return {
                    "date": row[0], "total": row[1],
                    "signal": row[2], "trade": row[3],
                    "alert": row[4], "learning": row[5],
                    "cost": row[6], "uptime": row[7],
                    "details": json.loads(row[8]) if row[8] else {},
                }
            return {"error": "No scores found"}
        except Exception as e:
            return {"error": str(e)}

    async def _cmd_get_cost(self, **_kw) -> dict:
        try:
            with self.bot.db._connect() as conn:
                row = conn.execute(
                    "SELECT SUM(total_cost_usd), COUNT(*), "
                    "SUM(input_tokens), SUM(output_tokens) "
                    "FROM api_usage_log"
                ).fetchone()
                today_row = conn.execute(
                    "SELECT SUM(total_cost_usd), COUNT(*) "
                    "FROM api_usage_log WHERE date(timestamp) = date('now')"
                ).fetchone()
            return {
                "total_cost": round(row[0] or 0, 4),
                "total_calls": row[1] or 0,
                "input_tokens": row[2] or 0,
                "output_tokens": row[3] or 0,
                "today_cost": round((today_row[0] or 0), 4) if today_row else 0,
                "today_calls": (today_row[1] or 0) if today_row else 0,
            }
        except Exception as e:
            return {"error": str(e)}

    async def _cmd_send_message(self, text: str = "", **_kw) -> str:
        if not text:
            return "Error: text required"
        try:
            chat_id = self.bot.chat_id
            await self.bot._application.bot.send_message(
                chat_id=chat_id, text=text
            )
            return f"Message sent to {chat_id}"
        except Exception as e:
            return f"Send failed: {e}"

    async def _cmd_get_logs(self, lines: int = 30, filter: str = "", **_kw) -> str:
        log_path = "/tmp/kstock_bot.log"
        if not os.path.exists(log_path):
            return "Log file not found"
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            if filter:
                all_lines = [l for l in all_lines if filter.upper() in l.upper()]
            tail = all_lines[-lines:]
            return "".join(tail)[-3000:]
        except Exception as e:
            return f"Error reading logs: {e}"

    async def _cmd_run_claude(self, prompt: str = "", model: str = "sonnet",
                              max_turns: int = 5, notify: bool = True, **_kw) -> str:
        """Execute Claude Code CLI and optionally send result to Telegram."""
        if not prompt:
            return "Error: prompt required"
        try:
            output, returncode, elapsed = await self.bot._run_claude_cli(prompt)
            result_text = output or "(empty output)"
            if len(result_text) > 5000:
                result_text = result_text[:2000] + "\n\n... (truncated) ...\n\n" + result_text[-2000:]
            if notify and self.bot.chat_id:
                icon = "✅" if returncode == 0 else "⚠️"
                tg_text = (
                    f"💻 {icon} Claude Code (CLI)\n"
                    f"{'━' * 22}\n"
                    f"📝 {prompt[:100]}\n"
                    f"⏱ {elapsed:.1f}s\n\n"
                    f"{result_text[:3500]}"
                )
                try:
                    await self.bot._application.bot.send_message(
                        chat_id=self.bot.chat_id, text=tg_text
                    )
                except Exception as e:
                    logger.warning("Failed to notify telegram: %s", e)
            return result_text
        except Exception as e:
            return f"Claude execution failed: {e}"

    # ── Helpers ───────────────────────────────────────

    def _get_jobs_info(self) -> list:
        jq = getattr(self.bot, "_job_queue", None)
        if not jq:
            return []
        result = []
        for job in jq.jobs():
            next_run = ""
            if job.next_t:
                next_run = job.next_t.strftime("%H:%M:%S")
            result.append({
                "name": job.name or "unnamed",
                "enabled": job.enabled,
                "next_run": next_run,
            })
        result.sort(key=lambda x: x["name"])
        return result

    def _set_job_enabled(self, name: str, enabled: bool) -> str:
        jq = getattr(self.bot, "_job_queue", None)
        if not jq:
            return "Job queue not available"
        for job in jq.jobs():
            if job.name == name:
                job.enabled = enabled
                state = "resumed" if enabled else "paused"
                return f"Job '{name}' {state}"
        return f"Job '{name}' not found"

    def _build_job_map(self) -> dict:
        bot = self.bot
        mapping = {}
        for attr_name in dir(bot):
            if attr_name.startswith("job_"):
                handler = getattr(bot, attr_name, None)
                if callable(handler):
                    job_name = attr_name[4:]
                    mapping[job_name] = handler
        return mapping


class _ControlContext:
    """Minimal context for CLI-triggered jobs (no real Telegram update)."""

    def __init__(self, application) -> None:
        self.application = application
        self.bot = application.bot
        self.job_queue = application.job_queue
        self.job = None
        self.user_data: dict = {}
        self.chat_data: dict = {}
        self.bot_data: dict = {}
