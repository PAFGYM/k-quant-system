"""Tests for YouTube learning upgrade path."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kstock.ingest.global_news import batch_deep_youtube_analysis
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
            title="매크로 점검 영상",
            source="🎬 테스트채널",
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
            title="이미 잘 학습된 영상",
            source="🎬 테스트채널",
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
