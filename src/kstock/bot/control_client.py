"""Synchronous Unix socket client for controlling the running K-Quant bot.

Used by the kbot CLI script to send commands to the bot process.
"""
from __future__ import annotations

import json
import socket
import sys

SOCKET_PATH = "/tmp/kquant_control.sock"
TIMEOUT = 660  # Claude Code 실행 최대 10분 + 여유


def send_command(cmd: str, args: dict | None = None) -> dict:
    """Send a command to the running bot and return the response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.connect(SOCKET_PATH)
    except (FileNotFoundError, ConnectionRefusedError):
        return {"ok": False, "error": "Bot is not running or control server not available"}
    try:
        request = json.dumps({"cmd": cmd, "args": args or {}}, ensure_ascii=False)
        sock.sendall(request.encode("utf-8") + b"\n")
        response = b""
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            response += chunk
            if b"\n" in response:
                break
        return json.loads(response.decode("utf-8").strip())
    except socket.timeout:
        return {"ok": False, "error": "Timeout waiting for response"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        sock.close()


def main() -> None:
    """CLI entry point: python -m kstock.bot.control_client <cmd> [args_json]"""
    if len(sys.argv) < 2:
        print("Usage: control_client.py <command> [args_json]")
        sys.exit(1)
    cmd = sys.argv[1]
    args = {}
    if len(sys.argv) > 2:
        try:
            args = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            args = {"name": sys.argv[2]}
    result = send_command(cmd, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
