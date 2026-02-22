"""Tests for bot/hallucination_guard.py - AI response hallucination guard."""

import pytest
from kstock.bot.hallucination_guard import (
    extract_target_prices,
    extract_events,
    verify_target_price,
    guard_response,
    format_hallucination_log,
)


# ---------------------------------------------------------------------------
# TestExtractTargetPrices
# ---------------------------------------------------------------------------

class TestExtractTargetPrices:
    """extract_target_prices: 목표가 추출."""

    def test_extract_with_ticker(self):
        """'삼성전자의 목표가 85,000원' -> (삼성전자, 85000)."""
        text = "삼성전자의 목표가 85,000원을 제시합니다."
        results = extract_target_prices(text)
        assert len(results) == 1
        name, price = results[0]
        assert name == "삼성전자"
        assert price == 85_000

    def test_extract_standalone(self):
        """'목표가 85,000원' (이름 없이) -> ('', 85000)."""
        text = "목표가 85,000원 수준입니다."
        results = extract_target_prices(text)
        assert len(results) == 1
        assert results[0][1] == 85_000

    def test_no_target_price(self):
        """목표가 패턴이 없으면 빈 리스트."""
        text = "현재 주가가 좋습니다."
        results = extract_target_prices(text)
        assert results == []

    def test_multiple_targets(self):
        """여러 목표가 추출."""
        text = "삼성전자의 목표가 85,000원, SK하이닉스의 목표가 200,000원."
        results = extract_target_prices(text)
        assert len(results) == 2
        prices = sorted([r[1] for r in results])
        assert prices == [85_000, 200_000]


# ---------------------------------------------------------------------------
# TestExtractEvents
# ---------------------------------------------------------------------------

class TestExtractEvents:
    """extract_events: 날짜-이벤트 추출."""

    def test_extract_event_with_date(self):
        """'2026.03.15 실적발표' -> 이벤트 추출."""
        text = "2026.03.15 실적발표 예정입니다."
        results = extract_events(text)
        assert len(results) == 1
        assert results[0]["date"] == "2026.03.15"
        assert "실적발표" in results[0]["event"]

    def test_no_date_empty(self):
        """날짜 패턴이 없으면 빈 리스트."""
        text = "다음 분기에 실적발표가 있습니다."
        results = extract_events(text)
        assert results == []

    def test_extract_with_dash_separator(self):
        """2026-03-15 형태도 추출."""
        text = "2026-03-15 배당락일입니다."
        results = extract_events(text)
        assert len(results) == 1
        assert results[0]["date"] == "2026.03.15"


# ---------------------------------------------------------------------------
# TestVerifyTargetPrice
# ---------------------------------------------------------------------------

class TestVerifyTargetPrice:
    """verify_target_price: 컨센서스 대비 검증."""

    def test_within_tolerance(self):
        """알려진 가격 80000, 주장 82000 -> 2.5% 차이, 10% 내 -> True."""
        db = {"삼성전자": 80_000}
        assert verify_target_price("삼성전자", 82_000, db) is True

    def test_exceeds_tolerance(self):
        """알려진 가격 80000, 주장 100000 -> 25% 차이 -> False."""
        db = {"삼성전자": 80_000}
        assert verify_target_price("삼성전자", 100_000, db) is False

    def test_unknown_ticker(self):
        """DB에 없는 종목 -> False (미확인)."""
        db = {"삼성전자": 80_000}
        assert verify_target_price("LG에너지", 200_000, db) is False

    def test_empty_ticker(self):
        """빈 ticker -> False."""
        db = {"삼성전자": 80_000}
        assert verify_target_price("", 80_000, db) is False

    def test_exact_match(self):
        """정확히 일치하면 True."""
        db = {"삼성전자": 80_000}
        assert verify_target_price("삼성전자", 80_000, db) is True


# ---------------------------------------------------------------------------
# TestGuardResponse
# ---------------------------------------------------------------------------

class TestGuardResponse:
    """guard_response: 응답 검증 및 [미확인] 태깅."""

    def test_unverified_claim_tagged(self):
        """검증 불가 목표가 -> [미확인] 태그 추가."""
        response = "삼성전자의 목표가 100,000원을 제시합니다."
        known_prices = {"삼성전자": 80_000}
        modified, unverified = guard_response(response, known_prices=known_prices)
        assert "[미확인]" in modified
        assert len(unverified) > 0

    def test_verified_claim_unchanged(self):
        """검증된 목표가 -> [미확인] 없음."""
        response = "삼성전자의 목표가 82,000원을 제시합니다."
        known_prices = {"삼성전자": 80_000}
        modified, unverified = guard_response(response, known_prices=known_prices)
        assert "[미확인]" not in modified
        assert len(unverified) == 0

    def test_clean_response_unchanged(self):
        """목표가/이벤트 없는 깨끗한 응답 -> 그대로 반환."""
        response = "현재 시장 상황이 좋습니다."
        modified, unverified = guard_response(response)
        assert modified == response
        assert unverified == []

    def test_footer_added_when_claims_present(self):
        """미확인 항목이 있으면 하단 안내 문구 추가."""
        response = "삼성전자의 목표가 100,000원입니다."
        modified, unverified = guard_response(response, known_prices={})
        assert "확인해 주세요" in modified or "검증되지 않은" in modified


# ---------------------------------------------------------------------------
# TestFormatHallucinationLog
# ---------------------------------------------------------------------------

class TestFormatHallucinationLog:
    """format_hallucination_log: 환각 검증 로그 포맷."""

    def test_no_bold_markers(self):
        log = format_hallucination_log("삼성전자 전망", "좋습니다", ["목표가 100,000원"])
        assert "**" not in log

    def test_contains_query_info(self):
        log = format_hallucination_log("삼성전자 전망", "좋습니다", [])
        assert "삼성전자" in log

    def test_contains_count(self):
        claims = ["목표가 100,000원", "실적발표 예정"]
        log = format_hallucination_log("질의", "응답", claims)
        assert "2" in log
