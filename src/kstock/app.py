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
    #    자신(os.getpid())과 부모(os.getppid()) 모두 제외
    #    → launchd bash 래퍼를 kill하면 KeepAlive 재생성 → 무한 중복 루프 발생
    my_pid = os.getpid()
    my_ppid = os.getppid()
    try:
        result = subprocess.run(
            ["pgrep", "-f", "kstock.app"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            pid = int(line)
            if pid != my_pid and pid != my_ppid:
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

    # SQLite 영속성 레이어 초기화 (재시작 시 상태 복원)
    from kstock.core.persistence import init_tables
    init_tables()

    # 중복 실행 방지: 기존 프로세스 모두 kill
    _kill_existing_bot()

    import time
    time.sleep(15)  # 이전 프로세스의 Telegram long-poll 세션 만료 대기 (timeout=10 + 여유 5s)

    from kstock.bot.bot import KQuantBot

    bot = KQuantBot()

    # v9.3: 기존 holdings 임계값 마이그레이션 (holding_type별 재계산)
    try:
        migrated = bot.db.migrate_holding_thresholds()
        if migrated:
            logger.info("Holdings 임계값 마이그레이션: %d건 업데이트", migrated)
    except Exception:
        logger.debug("Holdings 임계값 마이그레이션 실패", exc_info=True)

    app = bot.build_app()
    bot.schedule_jobs(app)

    from kstock import __version__
    logger.info("K-Quant System v%s started. Press Ctrl+C to stop.", __version__)
    # 409 해결: short polling (timeout=0) + poll_interval=3s
    # long-poll(timeout>0)은 httpx read_timeout(5s)보다 길어서 409 발생
    # short polling은 즉시 응답 → 409 원천 차단
    app.run_polling(
        poll_interval=3.0,
        timeout=0,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
        bootstrap_retries=0,
    )


if __name__ == "__main__":
    main()
