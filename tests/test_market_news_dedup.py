"""Tests for market news deduplication and YouTube prioritization."""

from kstock.ingest.global_news import (
    NewsItem,
    _youtube_priority_score,
    merge_related_topic_groups,
)
from kstock.store.sqlite import SQLiteStore


def test_recent_global_news_dedups_same_urgent_topic(tmp_path):
    db = SQLiteStore(db_path=tmp_path / "news.db")
    db.save_global_news([
        {
            "title": "미국, 이란 핵시설 공습 검토…중동 전쟁 우려",
            "source": "연합뉴스 국제",
            "url": "https://example.com/a1",
            "category": "geopolitics",
            "lang": "ko",
            "impact_score": 10,
            "is_urgent": 1,
            "published": "",
            "content_summary": "",
            "video_id": "",
        },
        {
            "title": "이란, 호르무즈 봉쇄 경고…미사일 보복 가능성",
            "source": "Reuters Business",
            "url": "https://example.com/a2",
            "category": "geopolitics",
            "lang": "ko",
            "impact_score": 9,
            "is_urgent": 1,
            "published": "",
            "content_summary": "",
            "video_id": "",
        },
    ])

    rows = db.get_recent_global_news(limit=10, hours=24)
    assert len(rows) == 1


def test_similar_alert_sent_detects_same_war_topic(tmp_path):
    db = SQLiteStore(db_path=tmp_path / "alert.db")
    db.save_sent_alert("hash1", "미국, 이란 핵시설 공습 검토…중동 전쟁 우려")

    assert db.is_similar_alert_sent("이란, 호르무즈 봉쇄 경고…미사일 보복 가능성")


def test_youtube_priority_score_prefers_live_market_commentary():
    live_item = NewsItem(
        title="장전 라이브 시황 브리핑 | 반도체 급등 체크",
        source="증시각도기TV",
        video_id="abc123",
        category="youtube_finance",
    )
    generic_item = NewsItem(
        title="오늘의 경제 상식 정리",
        source="일반채널",
        video_id="def456",
        category="youtube_finance",
    )

    assert _youtube_priority_score(live_item) > _youtube_priority_score(generic_item)


def test_merge_related_topic_groups_collapses_same_war_cluster():
    group1 = [
        NewsItem(
            title="미국, 이란 핵시설 공습 검토…중동 전쟁 우려",
            source="연합뉴스 국제",
            impact_score=10,
        )
    ]
    group2 = [
        NewsItem(
            title="이란, 호르무즈 봉쇄 경고…미사일 보복 가능성",
            source="Reuters Business",
            impact_score=9,
        )
    ]

    merged = merge_related_topic_groups([group1, group2])

    assert len(merged) == 1
    assert len(merged[0]) == 2
