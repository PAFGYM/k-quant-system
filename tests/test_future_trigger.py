"""Tests for signal/future_trigger.py - Trigger monitoring and entry evaluation."""

import pytest
from kstock.signal.future_trigger import (
    TRIGGER_TYPES,
    TriggerEvent,
    match_keywords,
    classify_trigger_type,
    detect_sector_for_text,
    match_beneficiaries,
    analyze_trigger,
    evaluate_entry,
    format_trigger_alert,
    format_entry_signal,
)
from kstock.signal.future_tech import EntrySignal


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

class TestMatchKeywords:
    """Test keyword matching."""

    def test_basic_match(self):
        result = match_keywords("자율주행 L3 허용", ["자율주행", "L3", "L4"])
        assert "자율주행" in result
        assert "L3" in result
        assert len(result) == 2

    def test_no_match(self):
        result = match_keywords("삼성전자 실적 발표", ["양자컴퓨터", "큐비트"])
        assert result == []

    def test_case_insensitive(self):
        result = match_keywords("IONQ announced quantum", ["IONQ", "quantum"])
        assert len(result) == 2


class TestClassifyTriggerType:
    """Test trigger type classification."""

    def test_policy_trigger(self):
        ttype, impact = classify_trigger_type("자율주행 L3 전면 허용 규제완화")
        assert ttype == "policy"
        assert impact == "HIGH"

    def test_corporate_trigger(self):
        ttype, impact = classify_trigger_type("현대차 L4 자율주행 수주 계약 체결")
        assert ttype == "corporate"

    def test_global_trigger(self):
        ttype, impact = classify_trigger_type("테슬라 FSD v13 완전자율주행 승인")
        assert ttype == "global"
        assert impact == "HIGH"

    def test_earnings_trigger(self):
        ttype, impact = classify_trigger_type("텔레칩스 실적 서프라이즈 매출 급증")
        assert ttype == "earnings"

    def test_unknown_trigger(self):
        ttype, impact = classify_trigger_type("오늘 날씨가 좋습니다")
        assert ttype == "unknown"
        assert impact == "LOW"


class TestDetectSector:
    """Test sector detection from text."""

    def test_autonomous_driving_detected(self):
        sectors = detect_sector_for_text("자율주행 L3 상용화 시작")
        assert "autonomous_driving" in sectors

    def test_space_detected(self):
        sectors = detect_sector_for_text("누리호 5차 발사 성공")
        assert "space_aerospace" in sectors

    def test_quantum_detected(self):
        sectors = detect_sector_for_text("양자컴퓨터 양자암호통신 상용화")
        assert "quantum_computing" in sectors

    def test_no_sector(self):
        sectors = detect_sector_for_text("오늘 점심 뭐먹지")
        assert sectors == []

    def test_multiple_sectors(self):
        # "우주" → space, "양자" → quantum
        sectors = detect_sector_for_text("우주 양자통신 위성 발사")
        assert len(sectors) >= 2


# ---------------------------------------------------------------------------
# Beneficiary matching
# ---------------------------------------------------------------------------

class TestMatchBeneficiaries:
    """Test beneficiary stock matching."""

    def test_match_by_stock_name(self):
        result = match_beneficiaries("현대오토에버 SDV 수주 확대", "autonomous_driving")
        names = [b["name"] for b in result]
        assert "현대오토에버" in names

    def test_match_by_reason_keywords(self):
        result = match_beneficiaries("누리호 엔진 테스트 성공", "space_aerospace")
        # 한화에어로스페이스 has reason containing "누리호 엔진"
        names = [b["name"] for b in result]
        assert "한화에어로스페이스" in names

    def test_empty_for_unknown_sector(self):
        result = match_beneficiaries("자율주행", "unknown_sector")
        assert result == []

    def test_sorted_by_relevance(self):
        result = match_beneficiaries("현대차 자율주행 SDV 센서", "autonomous_driving")
        if len(result) >= 2:
            assert result[0]["relevance_score"] >= result[1]["relevance_score"]


# ---------------------------------------------------------------------------
# Trigger analysis
# ---------------------------------------------------------------------------

class TestAnalyzeTrigger:
    """Test full trigger analysis pipeline."""

    def test_creates_trigger_events(self):
        events = analyze_trigger("자율주행 L3 전면 허용 규제완화", source="뉴스1")
        assert len(events) >= 1
        assert events[0].sector == "autonomous_driving"

    def test_no_events_for_irrelevant_news(self):
        events = analyze_trigger("오늘 주식시장 마감")
        assert events == []

    def test_trigger_has_keywords(self):
        events = analyze_trigger("누리호 발사 성공 우주항공청")
        assert len(events) >= 1
        assert len(events[0].matched_keywords) >= 1

    def test_trigger_has_beneficiaries(self):
        events = analyze_trigger("자율주행 SDV 현대차 대규모 수주")
        if events:
            assert len(events[0].beneficiary_tickers) >= 1


# ---------------------------------------------------------------------------
# Entry evaluation
# ---------------------------------------------------------------------------

class TestEvaluateEntry:
    """Test entry signal evaluation."""

    def test_strong_buy_with_4_conditions(self):
        entry = evaluate_entry(
            ticker="054450",
            sector_key="autonomous_driving",
            future_score=70,
            existing_score=150,
            rsi=45,
            volume_ratio=2.0,
            has_recent_trigger=True,
        )
        assert entry.signal == "STRONG_BUY"
        assert entry.conditions_met >= 4
        assert "주호님" in entry.message

    def test_watch_with_3_conditions(self):
        entry = evaluate_entry(
            ticker="054450",
            sector_key="autonomous_driving",
            future_score=70,
            existing_score=150,
            rsi=45,
            volume_ratio=1.0,  # fails
            has_recent_trigger=False,  # fails
        )
        assert entry.signal == "WATCH"
        assert entry.conditions_met == 3

    def test_wait_with_few_conditions(self):
        entry = evaluate_entry(
            ticker="054450",
            sector_key="autonomous_driving",
            future_score=30,
            existing_score=80,
            rsi=75,
            volume_ratio=0.5,
            has_recent_trigger=False,
        )
        assert entry.signal == "WAIT"
        assert entry.conditions_met < 3

    def test_entry_has_condition_details(self):
        entry = evaluate_entry(
            ticker="054450", sector_key="autonomous_driving",
            future_score=70, existing_score=150, rsi=45,
            volume_ratio=2.0, has_recent_trigger=True,
        )
        assert "future_score_min" in entry.conditions_detail
        assert "existing_score_min" in entry.conditions_detail
        assert "not_overbought" in entry.conditions_detail
        assert "volume_confirm" in entry.conditions_detail
        assert "trigger_exists" in entry.conditions_detail

    def test_entry_name_from_watchlist(self):
        entry = evaluate_entry("005380", "autonomous_driving")
        assert entry.name == "현대차"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatTriggerAlert:
    """Test trigger alert formatting."""

    def test_format_contains_sector_name(self):
        event = TriggerEvent(
            sector="autonomous_driving",
            trigger_type="policy",
            impact="HIGH",
            title="자율주행 L3 허용",
        )
        text = format_trigger_alert(event)
        assert "자율주행" in text
        assert "**" not in text
        assert "주호님" in text

    def test_format_contains_source(self):
        event = TriggerEvent(
            sector="space_aerospace",
            trigger_type="corporate",
            impact="MEDIUM",
            title="누리호 발사 성공",
            source="연합뉴스",
        )
        text = format_trigger_alert(event)
        assert "연합뉴스" in text


class TestFormatEntrySignal:
    """Test entry signal formatting."""

    def test_format_strong_buy(self):
        entry = EntrySignal(
            ticker="054450", name="텔레칩스",
            sector="autonomous_driving",
            signal="STRONG_BUY",
            conditions_met=4, conditions_total=5,
            conditions_detail={
                "future_score_min": True,
                "existing_score_min": True,
                "not_overbought": True,
                "volume_confirm": True,
                "trigger_exists": False,
            },
            message="주호님, 텔레칩스 진입 조건 충족.",
        )
        text = format_entry_signal(entry)
        assert "STRONG_BUY" in text
        assert "4/5" in text
        assert "**" not in text

    def test_format_wait(self):
        entry = EntrySignal(
            ticker="277410", name="이와이엘",
            signal="WAIT", conditions_met=1, conditions_total=5,
            conditions_detail={"future_score_min": False, "existing_score_min": False,
                               "not_overbought": True, "volume_confirm": False,
                               "trigger_exists": False},
            message="주호님, 이와이엘 아직 이릅니다.",
        )
        text = format_entry_signal(entry)
        assert "WAIT" in text
        assert "1/5" in text
