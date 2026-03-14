"""Central log file paths for K-Quant."""

from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
LEGACY_LOG_DIR = LOG_DIR / "legacy"

APP_LOG_FILE = LOG_DIR / "kquant.log"
ERROR_LOG_FILE = LOG_DIR / "kquant_error.log"
STDOUT_LOG_FILE = LOG_DIR / "kquant_stdout.log"

LEGACY_LOG_FILES = (
    ROOT_DIR / "bot.log",
    ROOT_DIR / "logs" / "bot.log",
    DATA_DIR / "bot_error.log",
    DATA_DIR / "bot.log",
)
