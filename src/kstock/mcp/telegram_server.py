"""Minimal MCP server for Telegram-centric K-Quant workflows.

This server exposes three high-value capabilities for external MCP clients:
1. Read recent Telegram-facing alerts and chat context
2. Send a message through the configured Telegram bot
3. Read the latest portfolio screenshot snapshot and recent reports

The implementation intentionally avoids an external MCP SDK dependency and
speaks a compact JSON-RPC over stdio using LSP-style Content-Length framing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kstock.store.sqlite import SQLiteStore

SERVER_NAME = "kquant-telegram-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-06-18"

ToolResult = dict[str, Any]
MessageSender = Callable[[str], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelegramMCPServer:
    """Tiny MCP-compatible server for Telegram and portfolio context."""

    def __init__(
        self,
        db: SQLiteStore | None = None,
        reports_dir: str | Path = "reports",
        token: str | None = None,
        chat_id: str | None = None,
        message_sender: MessageSender | None = None,
    ) -> None:
        self.db = db or SQLiteStore()
        self.reports_dir = Path(reports_dir)
        self.token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self._message_sender = message_sender or self._post_telegram_message

    def list_tools(self) -> list[dict[str, Any]]:
        """Return exposed MCP tool metadata."""
        return [
            {
                "name": "telegram_recent_context",
                "description": "최근 알림과 대화를 읽어 텔레그램 맥락을 요약합니다.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                        "include_alerts": {"type": "boolean", "default": True},
                        "include_chat": {"type": "boolean", "default": True},
                    },
                },
            },
            {
                "name": "telegram_send_message",
                "description": "설정된 텔레그램 봇으로 메시지를 전송합니다.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "전송할 텍스트"},
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "portfolio_snapshot",
                "description": "최신 보유 스크린샷, 현재 보유, 관련 리포트와 파일을 읽습니다.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "report_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                        "history_limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                    },
                },
            },
        ]

    def list_resources(self) -> list[dict[str, Any]]:
        """Return exposed MCP resources."""
        return [
            {
                "uri": "telegram://recent-context",
                "name": "최근 알림/대화",
                "mimeType": "application/json",
                "description": "최근 텔레그램 알림과 채팅 맥락",
            },
            {
                "uri": "telegram://portfolio/latest-snapshot",
                "name": "최신 보유 스냅샷",
                "mimeType": "application/json",
                "description": "최신 스크린샷 보유내역과 현재 active holdings",
            },
            {
                "uri": "telegram://portfolio/recent-reports",
                "name": "최근 리포트/파일",
                "mimeType": "application/json",
                "description": "보유 관련 리포트와 reports 폴더 최근 산출물",
            },
        ]

    def get_recent_context(
        self,
        limit: int = 10,
        include_alerts: bool = True,
        include_chat: bool = True,
    ) -> dict[str, Any]:
        """Read recent Telegram-facing context from the local store."""
        safe_limit = max(1, min(int(limit or 10), 50))
        payload: dict[str, Any] = {
            "generated_at": _utc_now(),
            "limit": safe_limit,
            "alerts": [],
            "chat_messages": [],
        }
        if include_alerts:
            payload["alerts"] = self.db.get_recent_alerts(safe_limit)
        if include_chat:
            payload["chat_messages"] = self.db.get_recent_chat_messages(safe_limit)
        return payload

    def send_message(self, text: str) -> dict[str, Any]:
        """Send a Telegram message through the configured bot."""
        clean = (text or "").strip()
        if not clean:
            raise ValueError("text is required")
        result = self._message_sender(clean)
        return {
            "ok": bool(result.get("ok", True)),
            "text_length": len(clean),
            "sent_at": _utc_now(),
            "telegram_result": result,
        }

    def get_portfolio_snapshot(
        self,
        report_limit: int = 5,
        history_limit: int = 3,
    ) -> dict[str, Any]:
        """Return latest screenshot snapshot, holdings, and related reports."""
        safe_report_limit = max(1, min(int(report_limit or 5), 20))
        safe_history_limit = max(1, min(int(history_limit or 3), 10))

        latest_screenshot = self.db.get_last_screenshot()
        screenshot_holdings: list[dict[str, Any]] = []
        if latest_screenshot:
            screenshot_holdings = self.db.get_screenshot_holdings(latest_screenshot["id"])

        active_holdings = self.db.get_active_holdings()
        tickers = list({
            str(item.get("ticker", "")).strip()
            for item in [*active_holdings, *screenshot_holdings]
            if item.get("ticker")
        })

        related_reports = self.db.get_reports_for_tickers(tickers, limit=safe_report_limit) if tickers else []
        recent_reports = self.db.get_recent_reports(limit=safe_report_limit)

        return {
            "generated_at": _utc_now(),
            "active_holdings": active_holdings,
            "latest_screenshot": latest_screenshot,
            "latest_screenshot_holdings": screenshot_holdings,
            "screenshot_history": self.db.get_screenshot_history(limit=safe_history_limit),
            "related_reports": related_reports,
            "recent_reports": recent_reports,
            "report_files": self._recent_report_files(limit=safe_report_limit),
        }

    def read_resource(self, uri: str) -> dict[str, Any]:
        """Read an MCP resource URI."""
        if uri == "telegram://recent-context":
            data = self.get_recent_context(limit=10)
        elif uri == "telegram://portfolio/latest-snapshot":
            snapshot = self.get_portfolio_snapshot(report_limit=5, history_limit=3)
            data = {
                "generated_at": snapshot["generated_at"],
                "active_holdings": snapshot["active_holdings"],
                "latest_screenshot": snapshot["latest_screenshot"],
                "latest_screenshot_holdings": snapshot["latest_screenshot_holdings"],
                "screenshot_history": snapshot["screenshot_history"],
            }
        elif uri == "telegram://portfolio/recent-reports":
            snapshot = self.get_portfolio_snapshot(report_limit=10, history_limit=1)
            data = {
                "generated_at": snapshot["generated_at"],
                "related_reports": snapshot["related_reports"],
                "recent_reports": snapshot["recent_reports"],
                "report_files": snapshot["report_files"],
            }
        else:
            raise ValueError(f"Unknown resource URI: {uri}")

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(data, ensure_ascii=False, indent=2),
                }
            ]
        }

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        """Execute an MCP tool call and wrap the result in MCP content."""
        arguments = arguments or {}
        if name == "telegram_recent_context":
            result = self.get_recent_context(
                limit=arguments.get("limit", 10),
                include_alerts=arguments.get("include_alerts", True),
                include_chat=arguments.get("include_chat", True),
            )
        elif name == "telegram_send_message":
            result = self.send_message(arguments.get("text", ""))
        elif name == "portfolio_snapshot":
            result = self.get_portfolio_snapshot(
                report_limit=arguments.get("report_limit", 5),
                history_limit=arguments.get("history_limit", 3),
            )
        else:
            raise ValueError(f"Unknown tool: {name}")

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": result,
            "isError": False,
        }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC request."""
        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        if method == "notifications/initialized":
            return None
        if method == "shutdown":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "exit":
            return None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.list_tools()}
            elif method == "resources/list":
                result = {"resources": self.list_resources()}
            elif method == "resources/read":
                result = self.read_resource(params.get("uri", ""))
            elif method == "tools/call":
                result = self.call_tool(params.get("name", ""), params.get("arguments", {}))
            else:
                raise NotImplementedError(f"Method not found: {method}")

            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            code = -32601 if isinstance(exc, NotImplementedError) else -32000
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": code,
                    "message": str(exc),
                },
            }

    def serve_stdio(self, instream: BinaryIO | None = None, outstream: BinaryIO | None = None) -> None:
        """Serve JSON-RPC messages over stdio using Content-Length framing."""
        instream = instream or sys.stdin.buffer
        outstream = outstream or sys.stdout.buffer

        while True:
            request = _read_message(instream)
            if request is None:
                break
            response = self.handle_request(request)
            if response is not None:
                _write_message(outstream, response)
            if request.get("method") == "exit":
                break

    def _recent_report_files(self, limit: int = 5) -> list[dict[str, Any]]:
        if not self.reports_dir.exists():
            return []

        files = [
            path for path in self.reports_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}
        ]
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)

        result: list[dict[str, Any]] = []
        for path in files[:limit]:
            stat = path.stat()
            result.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return result

    def _post_telegram_message(self, text: str) -> dict[str, Any]:
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not configured")

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = urlencode({"chat_id": self.chat_id, "text": text[:4096]}).encode("utf-8")
        request = Request(url, data=payload, method="POST")
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram sendMessage failed"))
        return data


def _read_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read a single framed JSON-RPC message."""
    headers: dict[str, str] = {}
    line = stream.readline()
    while line in {b"\r\n", b"\n"}:
        line = stream.readline()
    if not line:
        return None

    while line and line not in {b"\r\n", b"\n"}:
        decoded = line.decode("utf-8").strip()
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.lower()] = value.strip()
        line = stream.readline()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None

    body = stream.read(content_length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    """Write a single framed JSON-RPC message."""
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    stream.write(body)
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="K-Quant Telegram MCP server")
    parser.add_argument("--db", default="data/kquant.db", help="SQLite DB path")
    parser.add_argument("--reports-dir", default="reports", help="Report artifacts directory")
    args = parser.parse_args(argv)

    server = TelegramMCPServer(
        db=SQLiteStore(db_path=Path(args.db)),
        reports_dir=args.reports_dir,
    )
    server.serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
