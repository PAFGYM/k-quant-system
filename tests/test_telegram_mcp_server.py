from __future__ import annotations

import json
from pathlib import Path

from kstock.mcp.telegram_server import TelegramMCPServer
from kstock.store.sqlite import SQLiteStore


def _build_server(tmp_path: Path, sent_messages: list[str]) -> TelegramMCPServer:
    db_path = tmp_path / "test.db"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()

    (reports_dir / "daily_report.pdf").write_bytes(b"%PDF-1.4 fake")
    (reports_dir / "impact.png").write_bytes(b"png")

    db = SQLiteStore(db_path=db_path)
    db.insert_alert("005930", "surge", "삼성전자 급등")
    db.add_chat_message("user", "오늘 뭐 사지?")
    db.add_chat_message("assistant", "방어주 위주로 보세요")
    shot_id = db.add_screenshot(
        total_eval=125000000,
        total_profit=15000000,
        total_profit_pct=13.6,
        cash=25000000,
        portfolio_score=82,
        holdings_json="[]",
        image_hash="hash-1",
    )
    db.add_screenshot_holding(
        shot_id,
        "039200",
        "오스코텍",
        quantity=120,
        avg_price=42000,
        current_price=46300,
        profit_pct=10.24,
        eval_amount=5556000,
        diagnosis="보유",
        diagnosis_action="hold",
        diagnosis_msg="추세 유지",
    )
    db.add_holding("039200", "오스코텍", buy_price=42000, holding_type="swing")
    db.add_report(
        source="naver_research",
        title="오스코텍 목표가 상향",
        broker="신한투자증권",
        date="2026-03-13",
        ticker="039200",
        target_price=60000,
        summary="바이오 모멘텀 강화",
    )

    def _fake_send(text: str) -> dict:
        sent_messages.append(text)
        return {"ok": True, "result": {"text": text}}

    return TelegramMCPServer(
        db=db,
        reports_dir=reports_dir,
        token="dummy",
        chat_id="dummy",
        message_sender=_fake_send,
    )


def test_recent_context_tool_reads_alerts_and_chat(tmp_path: Path):
    sent_messages: list[str] = []
    server = _build_server(tmp_path, sent_messages)

    result = server.call_tool("telegram_recent_context", {"limit": 5})

    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["alerts"][0]["ticker"] == "005930"
    assert data["chat_messages"][0]["role"] == "user"
    assert data["chat_messages"][1]["role"] == "assistant"


def test_send_message_tool_uses_configured_sender(tmp_path: Path):
    sent_messages: list[str] = []
    server = _build_server(tmp_path, sent_messages)

    result = server.call_tool("telegram_send_message", {"text": "테스트 전송"})

    assert result["structuredContent"]["ok"] is True
    assert sent_messages == ["테스트 전송"]


def test_portfolio_snapshot_tool_reads_screenshot_holdings_and_reports(tmp_path: Path):
    sent_messages: list[str] = []
    server = _build_server(tmp_path, sent_messages)

    result = server.call_tool("portfolio_snapshot", {"report_limit": 5, "history_limit": 3})
    data = result["structuredContent"]

    assert data["latest_screenshot"]["portfolio_score"] == 82
    assert data["latest_screenshot_holdings"][0]["name"] == "오스코텍"
    assert data["active_holdings"][0]["ticker"] == "039200"
    assert data["related_reports"][0]["ticker"] == "039200"
    assert data["report_files"][0]["path"].endswith(".pdf") or data["report_files"][0]["path"].endswith(".png")


def test_read_resource_returns_json_payload(tmp_path: Path):
    sent_messages: list[str] = []
    server = _build_server(tmp_path, sent_messages)

    response = server.read_resource("telegram://portfolio/recent-reports")

    assert response["contents"][0]["mimeType"] == "application/json"
    text = response["contents"][0]["text"]
    payload = json.loads(text)
    assert payload["recent_reports"][0]["title"] == "오스코텍 목표가 상향"
