"""Tests for YouTube learning upgrade path."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kstock.ingest.global_news import (
    _build_whisper_download_attempts,
    _download_audio_for_whisper,
    _feed_is_disabled,
    _is_high_signal_youtube_item,
    _mark_feed_failure,
    _mark_youtube_video_failure,
    _youtube_video_is_in_backoff,
    batch_youtube_live_watch,
    fetch_transcript_whisper,
    batch_deep_youtube_analysis,
    summarize_transcript_structured,
)
from kstock.store.sqlite import SQLiteStore


class TestYoutubeIntelligenceUpgrade:
    """저품질 YouTube 요약 업그레이드 판단 테스트."""

    def test_low_quality_entry_should_upgrade(self, tmp_path):
        db = SQLiteStore(db_path=tmp_path / "yt.db")
        db.save_youtube_intelligence({
            "video_id": "abc123def45",
            "source": "테스트채널",
            "title": "짧은 요약",
            "mentioned_tickers": [],
            "mentioned_sectors": [],
            "market_outlook": "",
            "key_numbers": [],
            "investment_implications": "",
            "full_summary": "짧음",
            "raw_summary": "짧음",
            "confidence": 0.2,
        })

        assert db.should_upgrade_youtube_intelligence("abc123def45") is True

    def test_high_quality_entry_should_not_upgrade(self, tmp_path):
        db = SQLiteStore(db_path=tmp_path / "yt.db")
        db.save_youtube_intelligence({
            "video_id": "xyz987uvw65",
            "source": "테스트채널",
            "title": "충분한 요약",
            "mentioned_tickers": [{"ticker": "005930", "name": "삼성전자"}],
            "mentioned_sectors": ["반도체"],
            "market_outlook": "중립 이상",
            "key_numbers": ["영업이익 20% 증가"],
            "investment_implications": "실적과 수급이 함께 좋아지는 구간으로 해석된다.",
            "full_summary": "A" * 220,
            "raw_summary": "B" * 220,
            "confidence": 0.82,
        })

        assert db.should_upgrade_youtube_intelligence("xyz987uvw65") is False


class TestBatchDeepYoutubeAnalysis:
    """심화 분석이 기존 저품질 저장본을 업그레이드하는지 테스트."""

    @pytest.mark.asyncio
    async def test_reprocesses_existing_low_quality_video(self):
        item = SimpleNamespace(
            video_id="abc123def45",
            title="장전 시황 매크로 점검 영상",
            source="🎬 테스트채널",
            category="youtube_finance",
        )
        db = MagicMock()
        db.check_youtube_processed.return_value = True
        db.should_upgrade_youtube_intelligence.return_value = True

        structured = {
            "video_id": item.video_id,
            "full_summary": "충분히 긴 심화 요약",
            "raw_summary": "원문 요약",
            "mentioned_tickers": [{"ticker": "005930", "name": "삼성전자"}],
            "mentioned_sectors": ["반도체"],
            "transcript_method": "whisper",
        }

        with patch(
            "kstock.ingest.global_news.fetch_global_news",
            AsyncMock(return_value=[item]),
        ), patch(
            "kstock.ingest.global_news.deep_analyze_youtube",
            AsyncMock(return_value=structured),
        ) as mock_deep, patch(
            "kstock.ingest.global_news.asyncio.sleep",
            AsyncMock(),
        ):
            results = await batch_deep_youtube_analysis(
                db=db, max_videos=3, hours_lookback=12,
            )

        assert len(results) == 1
        db.should_upgrade_youtube_intelligence.assert_called_once_with(item.video_id)
        mock_deep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_existing_high_quality_video(self):
        item = SimpleNamespace(
            video_id="xyz987uvw65",
            title="장중 시황 이미 잘 학습된 영상",
            source="🎬 테스트채널",
            category="youtube_finance",
        )
        db = MagicMock()
        db.check_youtube_processed.return_value = True
        db.should_upgrade_youtube_intelligence.return_value = False

        with patch(
            "kstock.ingest.global_news.fetch_global_news",
            AsyncMock(return_value=[item]),
        ), patch(
            "kstock.ingest.global_news.deep_analyze_youtube",
            AsyncMock(),
        ) as mock_deep, patch(
            "kstock.ingest.global_news.asyncio.sleep",
            AsyncMock(),
        ):
            results = await batch_deep_youtube_analysis(
                db=db, max_videos=3, hours_lookback=12,
            )

        assert results == []
        db.should_upgrade_youtube_intelligence.assert_called_once_with(item.video_id)
        mock_deep.assert_not_called()


class TestBatchYoutubeLiveWatch:
    @pytest.mark.asyncio
    async def test_live_watch_prioritizes_market_live_titles(self):
        live_item = SimpleNamespace(
            video_id="live123def45",
            title="장전 라이브 시황 | 오늘장 반도체 수급 점검",
            source="🎬 테스트증권",
            category="youtube_broker",
            published="2026-03-14T08:10:00+0900",
        )
        normal_item = SimpleNamespace(
            video_id="norm123def45",
            title="주말 브이로그",
            source="🎬 기타채널",
            category="youtube_finance",
            published="2026-03-14T08:05:00+0900",
        )
        db = MagicMock()
        db.check_youtube_processed.return_value = False
        db.should_upgrade_youtube_intelligence.return_value = True

        structured = {
            "video_id": live_item.video_id,
            "full_summary": "장전 핵심 요약",
            "raw_summary": "장전 핵심 요약",
            "mentioned_tickers": [{"ticker": "000660", "name": "SK하이닉스", "sentiment": "긍정"}],
            "mentioned_sectors": ["반도체"],
            "market_outlook": "반도체 수급 확인 후 긍정",
            "investment_implications": "시초 추격보다 눌림 분할",
        }

        with patch(
            "kstock.ingest.global_news.fetch_global_news",
            AsyncMock(return_value=[normal_item, live_item]),
        ), patch(
            "kstock.ingest.global_news.deep_analyze_youtube",
            AsyncMock(return_value=structured),
        ) as mock_deep, patch(
            "kstock.ingest.global_news.asyncio.sleep",
            AsyncMock(),
        ):
            results = await batch_youtube_live_watch(
                db=db, max_videos=2, hours_lookback=3,
            )

        assert len(results) == 1
        assert results[0]["video_id"] == live_item.video_id
        assert results[0]["live_watch"] is True
        assert results[0]["priority_score"] > 0
        mock_deep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_live_watch_skips_processed_high_quality_video(self):
        item = SimpleNamespace(
            video_id="done123def45",
            title="장중 라이브 브리핑",
            source="🎬 테스트증권",
            category="youtube_broker",
            published="2026-03-14T10:00:00+0900",
        )
        db = MagicMock()
        db.check_youtube_processed.return_value = True
        db.should_upgrade_youtube_intelligence.return_value = False

        with patch(
            "kstock.ingest.global_news.fetch_global_news",
            AsyncMock(return_value=[item]),
        ), patch(
            "kstock.ingest.global_news.deep_analyze_youtube",
            AsyncMock(),
        ) as mock_deep, patch(
            "kstock.ingest.global_news.asyncio.sleep",
            AsyncMock(),
        ):
            results = await batch_youtube_live_watch(
                db=db, max_videos=2, hours_lookback=3,
            )

        assert results == []
        db.should_upgrade_youtube_intelligence.assert_called_once_with(item.video_id)
        mock_deep.assert_not_called()


def test_high_signal_youtube_item_filters_noise_titles():
    noisy_item = SimpleNamespace(
        video_id="noise123def4",
        title="[사주팔자 보러와] 오늘 뭐 살지 모르겠다면 추천주 상담",
        source="🎬 매일경제TV",
        category="youtube_finance",
    )
    strong_item = SimpleNamespace(
        video_id="live123def4",
        title="장전 시황 라이브 | 외인 수급과 반도체 체크",
        source="🎬 테스트증권",
        category="youtube_broker",
    )

    assert _is_high_signal_youtube_item(noisy_item) is False
    assert _is_high_signal_youtube_item(strong_item) is True


def test_youtube_video_failure_enters_backoff():
    _mark_youtube_video_failure("abc123def45", reason="metadata_only")
    _mark_youtube_video_failure("abc123def45", reason="metadata_only")
    assert _youtube_video_is_in_backoff("abc123def45") is True


def test_youtube_feed_404_backoff_is_longer():
    feed = {
        "name": "토마토증권통",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=test",
        "category": "youtube_finance",
    }
    _mark_feed_failure(feed, status_code=404)
    from kstock.ingest.global_news import _DEAD_FEED_BACKOFF, _feed_backoff_key

    state = _DEAD_FEED_BACKOFF[_feed_backoff_key(feed)]
    assert "skip_until" in state


def test_known_dead_feed_is_disabled():
    feed = {
        "name": "한경 글로벌",
        "url": "https://www.hankyung.com/feed/globalmarket",
        "category": "market",
        "disabled": True,
    }
    assert _feed_is_disabled(feed) is True


def test_repeated_transcript_failure_backoff_gets_longer():
    _mark_youtube_video_failure("video1234567", reason="download_failed")
    _mark_youtube_video_failure("video1234567", reason="download_failed")
    assert _youtube_video_is_in_backoff("video1234567") is True


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


class TestStructuredSummaryFallback:
    @pytest.mark.asyncio
    async def test_structured_summary_falls_back_to_openai_on_low_credit(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-test")

        anthropic_resp = _FakeResponse(
            400,
            text='{"error":{"message":"Your credit balance is too low to access the Anthropic API."}}',
        )
        expected = {
            "full_summary": "시장 핵심 요약",
            "mentioned_tickers": [{"name": "삼성전자", "ticker": "005930"}],
            "mentioned_sectors": ["반도체"],
            "market_outlook": "mixed",
            "key_numbers": [],
            "investment_implications": "눌림 확인 후 대응",
            "raw_summary": "시장 핵심 요약",
            "confidence": 0.9,
        }

        with patch(
            "httpx.AsyncClient",
            return_value=_FakeAsyncClient(anthropic_resp),
        ), patch(
            "kstock.ingest.global_news._summarize_structured_with_openai",
            AsyncMock(return_value=expected),
        ) as mock_openai:
            result = await summarize_transcript_structured(
                transcript="A" * 500,
                title="시장 점검 라이브",
                source="테스트 채널",
            )

        assert result == expected
        mock_openai.assert_awaited_once()


class TestWhisperDownloadFallbacks:
    def test_build_whisper_download_attempts_includes_audio_stream_fallback(self):
        attempts = _build_whisper_download_attempts(
            "https://www.youtube.com/watch?v=test1234567",
            "/tmp/test1234567.%(ext)s",
            has_ffmpeg=False,
        )

        labels = [label for label, _ in attempts]
        assert labels == ["audio_stream", "compact_video"]

    def test_download_audio_for_whisper_retries_with_fallback(self):
        fake_result = MagicMock()
        fake_result.stderr = ""
        fake_result.stdout = ""

        with patch(
            "subprocess.run",
            return_value=fake_result,
        ) as mock_run, patch(
            "kstock.ingest.global_news._resolve_whisper_media_path",
            side_effect=["", "/tmp/abc123.m4a"],
        ):
            path = _download_audio_for_whisper(
                video_id="abc123",
                tmp_dir="/tmp",
                env={},
                has_ffmpeg=False,
            )

        assert path == "/tmp/abc123.m4a"
        assert mock_run.call_count == 2

    def test_fetch_transcript_whisper_skips_when_video_is_in_backoff(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
        _mark_youtube_video_failure("blocked12345", reason="download_failed")

        with patch(
            "kstock.ingest.global_news._download_audio_for_whisper",
            side_effect=AssertionError("should not be called"),
        ):
            transcript = fetch_transcript_whisper("blocked12345")

        assert transcript == ""
