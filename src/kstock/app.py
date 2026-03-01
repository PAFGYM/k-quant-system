"""Main runner: starts scheduler and Telegram bot."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv(override=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PID_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "bot.pid")


def _kill_existing_bot() -> None:
    """기존 봇 프로세스를 확실히 종료 (중복 실행 방지 → 409 방지)."""
    # 1) PID 파일 기반 정리
    try:
        pid_path = os.path.abspath(PID_FILE)
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                try:
                    os.kill(old_pid, signal.SIGKILL)
                    logger.info("기존 봇 (PID %d) 종료", old_pid)
                except ProcessLookupError:
                    pass
    except Exception:
        pass

    # 2) pgrep 기반 정리 (PID 파일 없이 떠있는 좀비 프로세스 대응)
    try:
        result = subprocess.run(
            ["pgrep", "-f", "kstock.app"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            pid = int(line)
            if pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGKILL)
                    logger.info("좀비 프로세스 (PID %d) 종료", pid)
                except ProcessLookupError:
                    pass
    except Exception:
        pass

    # PID 파일 갱신
    try:
        pid_path = os.path.abspath(PID_FILE)
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def main() -> None:
    """Start the K-Quant system (Telegram bot + scheduled jobs)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env file!")
        sys.exit(1)

    # 중복 실행 방지: 기존 프로세스 모두 kill
    _kill_existing_bot()

    import time
    time.sleep(5)  # 이전 프로세스의 Telegram long-poll 세션 만료 대기

    from kstock.bot.bot import KQuantBot

    bot = KQuantBot()
    app = bot.build_app()
    bot.schedule_jobs(app)

    logger.info("K-Quant System v6.1.2 started. Press Ctrl+C to stop.")
    # 409 처리: run_polling 내부에서 deleteWebhook + 자동 재시도
    # bootstrap_retries=5: 시작 시 409 발생하면 최대 5회 재시도
    app.run_polling(
        poll_interval=1.0,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
        bootstrap_retries=5,
    )


if __name__ == "__main__":
    main()
