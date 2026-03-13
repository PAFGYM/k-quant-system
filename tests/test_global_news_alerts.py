from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kstock.ingest.global_news import (
    NewsItem,
    _format_urgent_alert_basic,
    _is_actionable_market_news,
    analyze_urgent_news,
    translate_titles_to_korean,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", json_data: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return self._response


class _FakeAnthropicMessages:
    def __init__(self, text: str):
        self._text = text

    async def create(self, *args, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class _FakeAnthropicClient:
    def __init__(self, text: str):
        self.messages = _FakeAnthropicMessages(text)


@pytest.mark.asyncio
async def test_translate_titles_to_korean_rejects_truncated_translation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    item = NewsItem(
        title="The two oil pipelines helping Saudi Arabia and UAE bypass the Strait of Hormuz",
        source="CNBC Energy",
        url="https://www.cnbc.com/2026/03/12/strait-of-hormuz-oil-pipelines-iran-war-saudi-arabia-uae.html",
        lang="en",
    )
    response = _FakeResponse(
        200,
        json_data={"content": [{"text": "1. 사우"}]},
    )

    with patch("httpx.AsyncClient", return_value=_FakeAsyncClient(response)):
        results = await translate_titles_to_korean([item])

    assert results[0].title.startswith("The two oil pipelines")
    assert results[0].original_title.startswith("The two oil pipelines")


def test_urgent_alert_prefers_original_title_when_current_title_is_broken():
    item = NewsItem(
        title="사우",
        original_title="The two oil pipelines helping Saudi Arabia and UAE bypass the Strait of Hormuz",
        source="CNBC Energy",
        url="https://www.cnbc.com/2026/03/12/strait-of-hormuz-oil-pipelines-iran-war-saudi-arabia-uae.html",
        impact_score=9,
        is_urgent=True,
    )

    text = _format_urgent_alert_basic([[item]])

    assert "The two oil pipelines helping Saudi Arabia" in text
    assert "\n🔴🔴🔴🔴 사우" not in text


@pytest.mark.asyncio
async def test_analyze_urgent_news_falls_back_when_ai_requests_more_info(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    item = NewsItem(
        title="사우",
        original_title="The two oil pipelines helping Saudi Arabia and UAE bypass the Strait of Hormuz",
        source="CNBC Energy",
        url="https://www.cnbc.com/2026/03/12/strait-of-hormuz-oil-pipelines-iran-war-saudi-arabia-uae.html",
        impact_score=9,
        is_urgent=True,
    )
    fake_module = SimpleNamespace(
        AsyncAnthropic=lambda api_key="": _FakeAnthropicClient(
            "안녕하세요. 제공하신 정보가 불완전합니다. 뉴스 전문을 복사-붙여넣기 해주시겠어요?"
        ),
    )

    with patch.dict(sys.modules, {"anthropic": fake_module}):
        text = await analyze_urgent_news([[item]], db=None)

    assert "시장 영향:" in text
    assert "행동:" in text
    assert "복사-붙여넣기" not in text
    assert "불완전합니다" not in text


def test_market_relevance_filter_drops_noise_articles():
    noisy = NewsItem(
        title="배드민턴 동호인 모여라 삼성생명 배드민턴 페스티벌 참가자 모집",
        source="매일경제",
        category="market",
    )
    useful = NewsItem(
        title="유가 쇼크에 코스피 하락 출발 환율 1490원 돌파",
        source="연합뉴스 경제",
        category="economy",
        impact_score=8,
        is_urgent=True,
    )
    corporate = NewsItem(
        title="삼성전자 플랙트 광주 생산라인 투자협약 연기",
        source="연합뉴스 경제",
        category="economy",
    )

    assert _is_actionable_market_news(noisy) is False
    assert _is_actionable_market_news(useful) is True
    assert _is_actionable_market_news(corporate) is True
