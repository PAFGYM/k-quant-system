"""Regression tests for user-facing alert copy."""

from kstock.bot.messages import format_sell_alert_profit, format_sell_alert_stop


def test_profit_alert_uses_softer_partial_exit_copy():
    text = format_sell_alert_profit(
        "씨에스윈드",
        {"buy_price": 50000},
        56000,
    )
    assert "파세요" not in text
    assert "부분 정리 검토" in text
    assert "절반(50%) 정리를 우선 검토" in text


def test_stop_alert_uses_review_language():
    text = format_sell_alert_stop(
        "씨에스윈드",
        {"buy_price": 50000},
        45000,
    )
    assert "손절하세요" not in text
    assert "정리 검토" in text
    assert "종가 회복 여부 확인 후 정리 비중을 판단" in text
