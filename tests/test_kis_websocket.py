from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from kstock.ingest.kis_websocket import KISWebSocket


class _FakeSocket:
    def __init__(self) -> None:
        self.send = AsyncMock()
        self.close = AsyncMock()
        self.recv = AsyncMock()


@pytest.mark.asyncio
async def test_subscribe_tracks_desired_even_when_disconnected():
    ws = KISWebSocket()
    ok = await ws.subscribe("005930")
    assert ok is False
    assert "005930" in ws._desired_subscriptions


@pytest.mark.asyncio
async def test_receive_loop_stale_timeout_triggers_connection_loss():
    ws = KISWebSocket()
    ws._connected = True
    ws._ws = _FakeSocket()
    ws._last_message_ts = 0

    async def _raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    async def _handle(reason: str):
        ws._connected = False
        ws._ws = None
        assert reason == "receive heartbeat timeout"

    ws._handle_connection_loss = AsyncMock(side_effect=_handle)

    with patch("asyncio.wait_for", side_effect=_raise_timeout), \
         patch("time.time", return_value=200):
        await ws._receive_loop()

    ws._handle_connection_loss.assert_awaited_once()


@pytest.mark.asyncio
async def test_restore_subscriptions_replays_desired_tickers():
    ws = KISWebSocket()
    ws._connected = True
    ws._ws = _FakeSocket()
    ws._desired_subscriptions = {"005930", "000660"}

    await ws._restore_subscriptions()

    assert ws.get_subscriptions() == {"005930", "000660"}
    assert ws._ws.send.await_count == 4


@pytest.mark.asyncio
async def test_handle_connection_loss_schedules_single_reconnect_task():
    ws = KISWebSocket()
    ws._connected = True
    fake_socket = _FakeSocket()
    ws._ws = fake_socket

    task = AsyncMock()
    task.done.return_value = False

    def _fake_create_task(coro):
        coro.close()
        return task

    with patch("asyncio.create_task", side_effect=_fake_create_task) as mock_create:
        await ws._handle_connection_loss("broken pipe")
        await ws._handle_connection_loss("broken pipe")

    assert mock_create.call_count == 1
    assert ws._reconnect_task is task
    fake_socket.close.assert_awaited_once()


def test_status_includes_last_disconnect_reason():
    ws = KISWebSocket()
    ws._desired_subscriptions = {"005930"}
    ws._last_disconnect_reason = "ping timeout"
    assert "ping timeout" in ws.get_status()
