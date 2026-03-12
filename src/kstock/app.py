"""Main runner: starts scheduler and Telegram bot."""

from __future__ import annotations

import atexit
import json
import logging
import os
import socket
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - production is macOS/Linux
    fcntl = None

from dotenv import load_dotenv

load_dotenv(override=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
PID_FILE = ROOT_DIR / "bot.pid"
RUNTIME_DIR = ROOT_DIR / "data" / "runtime"
LOCK_FILE = RUNTIME_DIR / "bot.lock"
STATE_FILE = RUNTIME_DIR / "instance.json"
HOSTNAME = socket.gethostname().split(".")[0]

_lock_fd: int | None = None


def _write_runtime_state(status: str, **extra: object) -> None:
    """런타임 상태를 기록해 충돌/재시작 원인 파악을 돕는다."""
    payload = {
        "status": status,
        "pid": os.getpid(),
        "hostname": HOSTNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Runtime state write failed", exc_info=True)


def _acquire_instance_lock() -> None:
    """로컬 단일 인스턴스 락을 잡아 같은 장비 중복 실행을 막는다."""
    global _lock_fd
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError("이미 실행 중인 로컬 봇 프로세스가 있습니다.") from exc

    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()} {HOSTNAME}\n".encode("utf-8"))
    _lock_fd = fd
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _write_runtime_state("locked")


def _release_instance_lock() -> None:
    """락과 PID 파일을 정리한다."""
    global _lock_fd
    if _lock_fd is None:
        return
    with suppress(Exception):
        if fcntl is not None:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
    with suppress(Exception):
        os.close(_lock_fd)
    _lock_fd = None
    with suppress(Exception):
        PID_FILE.unlink()


def _reset_event_loop() -> None:
    """run_polling 재시도 전에 이벤트 루프를 안전하게 초기화한다."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


def main() -> None:
    """Start the K-Quant system (Telegram bot + scheduled jobs)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env file!")
        sys.exit(1)

    # SQLite 영속성 레이어 초기화 (재시작 시 상태 복원)
    from kstock.core.persistence import init_tables
    init_tables()

    try:
        _acquire_instance_lock()
    except RuntimeError as exc:
        logger.warning("%s", exc)
        _write_runtime_state("blocked", reason=str(exc))
        sys.exit(0)

    atexit.register(_release_instance_lock)
    _write_runtime_state("booting")

    from kstock.bot.bot import KQuantBot
    import kstock as kstock_pkg
    from kstock import __version__

    bot = KQuantBot()
    conflict_count = 0
    restart_delay_sec = 10
    module_path = str(Path(getattr(kstock_pkg, "__file__", "")).resolve())

    if not module_path.startswith(str(ROOT_DIR / "src")):
        logger.warning("Unexpected kstock module path: %s", module_path)

    # v9.3: 기존 holdings 임계값 마이그레이션 (holding_type별 재계산)
    try:
        migrated = bot.db.migrate_holding_thresholds()
        if migrated:
            logger.info("Holdings 임계값 마이그레이션: %d건 업데이트", migrated)
    except Exception:
        logger.debug("Holdings 임계값 마이그레이션 실패", exc_info=True)

    import time

    while True:
        try:
            _reset_event_loop()
            app = bot.build_app()
            bot.schedule_jobs(app)

            _write_runtime_state(
                "running",
                version=__version__,
                conflict_count=conflict_count,
                module_path=module_path,
            )
            logger.info(
                "K-Quant System v%s started on %s from %s. Press Ctrl+C to stop.",
                __version__,
                HOSTNAME,
                module_path,
            )
            app.run_polling(
                poll_interval=3.0,
                timeout=0,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],
                bootstrap_retries=0,
                close_loop=False,
            )
            break
        except KeyboardInterrupt:
            logger.info("Shutdown requested by user.")
            _write_runtime_state("stopping", version=__version__)
            break
        except Exception as exc:
            error_text = str(exc)
            if "conflict" in error_text.lower():
                conflict_count += 1
                delay = min(60, 5 * conflict_count)
                logger.warning(
                    "Telegram polling conflict on %s (count=%d). "
                    "다른 장비/세션이 활성 상태일 수 있습니다. %ds 후 재시도합니다.",
                    HOSTNAME,
                    conflict_count,
                    delay,
                )
                _write_runtime_state(
                    "telegram_conflict",
                    version=__version__,
                    conflict_count=conflict_count,
                    retry_in_sec=delay,
                    last_error=error_text[:200],
                )
                time.sleep(delay)
                continue

            logger.error("Bot crashed: %s", exc, exc_info=True)
            _write_runtime_state(
                "crashed",
                version=__version__,
                restart_in_sec=restart_delay_sec,
                last_error=error_text[:300],
            )
            time.sleep(restart_delay_sec)
            continue

    _write_runtime_state("stopped", version=__version__)
    _release_instance_lock()


if __name__ == "__main__":
    main()
