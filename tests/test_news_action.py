"""Tests for actionable stock news formatting."""

from kstock.bot.news_action import assess_stock_news_headline, format_stock_news_brief


def test_assess_positive_order_headline():
    signal = assess_stock_news_headline("한화에어로스페이스 대규모 수주 계약 체결")
    assert signal.label in {"매수 재료", "실적 호재"}
    assert signal.score > 0


def test_assess_negative_dilution_headline():
    signal = assess_stock_news_headline("바이오주 유상증자 결정 공시")
    assert signal.label == "리스크"
    assert signal.score < 0


def test_format_stock_news_brief_is_action_oriented():
    text = format_stock_news_brief("테스트", [
        {
            "title": "테스트 대규모 수주 계약 체결",
            "date": "2026-03-12",
            "source": "연합뉴스",
            "url": "https://example.com/order",
        },
        {
            "title": "테스트 IR 설명회 개최",
            "date": "2026-03-12",
            "source": "머니투데이",
            "url": "https://example.com/ir",
        },
    ])

    assert "행동:" in text
    assert "이유:" in text
    assert "매매에 연결될 만한 뉴스" in text
