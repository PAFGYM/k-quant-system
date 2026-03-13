#!/usr/bin/env python3
"""Launch the K-Quant Telegram MCP server over stdio."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from kstock.mcp.telegram_server import main


if __name__ == "__main__":
    raise SystemExit(main())
