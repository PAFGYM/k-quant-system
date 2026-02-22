"""Tests for kstock.signal.event_driven (Section 58 - event-driven signals)."""

from __future__ import annotations

import pytest

from kstock.signal.event_driven import (
    BUYBACK_MIN_PCT,
    EARNINGS_SURPRISE_THRESHOLD,
    EventSignal,
    SCORE_ADJ_MAP,
    STAKE_THRESHOLD_PCT,
    TARGET_UPGRADE_MIN_BROKERS,
    detect_buyback,
    detect_earnings_surprise,
    detect_policy_benefit,
    detect_stake_change,
    detect_target_upgrade_chain,
    format_event_alert,
)


# ---------------------------------------------------------------------------
# EventSignal dataclass
# ---------------------------------------------------------------------------


class TestEventSignalDataclass:
    def test_creation(self) -> None:
        sig = EventSignal(
            event_type="earnings_surprise",
            ticker="005930",
            name="삼성전자",
            score_adj=15,
            description="서프라이즈",
            action="매수 추천",
        )
        assert sig.event_type == "earnings_surprise"
        assert sig.ticker == "005930"
        assert sig.score_adj == 15
        assert sig.details == {}  # default empty dict

    def test_default_message_empty(self) -> None:
        sig = EventSignal(
            event_type="buyback", ticker="T", name="N",
            score_adj=10, description="D", action="A",
        )
        assert sig.message == ""


# ---------------------------------------------------------------------------
# detect_earnings_surprise
# ---------------------------------------------------------------------------


class TestDetectEarningsSurprise:
    def test_positive_surprise(self) -> None:
        """OP income +20% vs consensus -> surprise signal."""
        result = detect_earnings_surprise(
            ticker="005930", name="삼성전자",
            op_income=120_000_000_000, consensus=100_000_000_000,
            current_price=70000,
        )
        assert result is not None
        assert result.event_type == "earnings_surprise"
        assert result.score_adj == SCORE_ADJ_MAP["earnings_surprise"]
        assert result.details["surprise_pct"] == 20.0

    def test_just_at_threshold(self) -> None:
        """Exactly at 15% threshold -> signal."""
        result = detect_earnings_surprise(
            ticker="005930", name="삼성전자",
            op_income=115, consensus=100,
            current_price=70000,
        )
        assert result is not None

    def test_below_threshold_returns_none(self) -> None:
        """OP income +10% < 15% threshold -> None."""
        result = detect_earnings_surprise(
            ticker="005930", name="삼성전자",
            op_income=110_000_000_000, consensus=100_000_000_000,
            current_price=70000,
        )
        assert result is None

    def test_negative_surprise_returns_none(self) -> None:
        """OP income below consensus -> None."""
        result = detect_earnings_surprise(
            ticker="005930", name="삼성전자",
            op_income=80_000_000_000, consensus=100_000_000_000,
            current_price=70000,
        )
        assert result is None

    def test_zero_consensus_returns_none(self) -> None:
        result = detect_earnings_surprise(
            ticker="005930", name="삼성전자",
            op_income=100, consensus=0,
            current_price=70000,
        )
        assert result is None

    def test_target_and_stop_in_details(self) -> None:
        result = detect_earnings_surprise(
            ticker="005930", name="삼성전자",
            op_income=130, consensus=100,
            current_price=70000,
        )
        assert result is not None
        assert "target_price" in result.details
        assert "stop_price" in result.details
        assert result.details["target_price"] > 70000
        assert result.details["stop_price"] < 70000


# ---------------------------------------------------------------------------
# detect_target_upgrade_chain
# ---------------------------------------------------------------------------


class TestDetectTargetUpgradeChain:
    def test_three_plus_brokers_signal(self) -> None:
        """3+ brokers raising target -> signal."""
        reports = [
            {"broker": "미래에셋", "prev_target": 80000, "new_target": 90000, "date": "2026-02-20"},
            {"broker": "NH투자", "prev_target": 78000, "new_target": 88000, "date": "2026-02-21"},
            {"broker": "삼성증권", "prev_target": 82000, "new_target": 92000, "date": "2026-02-22"},
        ]
        result = detect_target_upgrade_chain("005930", "삼성전자", reports)
        assert result is not None
        assert result.event_type == "target_upgrade_chain"
        assert result.details["upgrade_count"] == 3

    def test_two_brokers_returns_none(self) -> None:
        """Only 2 brokers -> None."""
        reports = [
            {"broker": "미래에셋", "prev_target": 80000, "new_target": 90000, "date": "2026-02-20"},
            {"broker": "NH투자", "prev_target": 78000, "new_target": 88000, "date": "2026-02-21"},
        ]
        result = detect_target_upgrade_chain("005930", "삼성전자", reports)
        assert result is None

    def test_empty_reports_returns_none(self) -> None:
        result = detect_target_upgrade_chain("005930", "삼성전자", [])
        assert result is None

    def test_no_actual_upgrades_returns_none(self) -> None:
        """Reports where new_target <= prev_target are not upgrades."""
        reports = [
            {"broker": "A", "prev_target": 90000, "new_target": 80000},
            {"broker": "B", "prev_target": 90000, "new_target": 85000},
            {"broker": "C", "prev_target": 90000, "new_target": 90000},
        ]
        result = detect_target_upgrade_chain("005930", "삼성전자", reports)
        assert result is None


# ---------------------------------------------------------------------------
# detect_buyback
# ---------------------------------------------------------------------------


class TestDetectBuyback:
    def test_significant_buyback(self) -> None:
        """> 1% of market cap -> signal."""
        result = detect_buyback(
            ticker="005930", name="삼성전자",
            buyback_amount=5_000_000_000_000,
            market_cap=400_000_000_000_000,
        )
        assert result is not None
        assert result.event_type == "buyback"

    def test_small_buyback_returns_none(self) -> None:
        """< 1% of market cap -> None."""
        result = detect_buyback(
            ticker="005930", name="삼성전자",
            buyback_amount=1_000_000_000_000,
            market_cap=400_000_000_000_000,
        )
        assert result is None

    def test_zero_market_cap_returns_none(self) -> None:
        result = detect_buyback(
            ticker="005930", name="삼성전자",
            buyback_amount=1_000_000_000_000,
            market_cap=0,
        )
        assert result is None


# ---------------------------------------------------------------------------
# detect_stake_change
# ---------------------------------------------------------------------------


class TestDetectStakeChange:
    def test_5_pct_stake_signal(self) -> None:
        """5%+ stake -> signal."""
        result = detect_stake_change(
            ticker="005930", name="삼성전자",
            investor_name="국민연금",
            stake_pct=7.5,
        )
        assert result is not None
        assert result.event_type == "stake_change"
        assert result.action == "매수 추천"

    def test_below_5_pct_returns_none(self) -> None:
        """< 5% -> None."""
        result = detect_stake_change(
            ticker="005930", name="삼성전자",
            investor_name="Some Fund",
            stake_pct=3.5,
        )
        assert result is None

    def test_activist_fund_monitoring(self) -> None:
        """행동주의 펀드 -> action = 모니터링."""
        result = detect_stake_change(
            ticker="005930", name="삼성전자",
            investor_name="액티비스트",
            stake_pct=6.0,
            investor_type="행동주의 펀드",
        )
        assert result is not None
        assert result.action == "모니터링"

    def test_10_pct_plus_gets_extra_score(self) -> None:
        """10%+ stake -> higher score_adj."""
        result = detect_stake_change(
            ticker="005930", name="삼성전자",
            investor_name="국민연금",
            stake_pct=12.0,
        )
        assert result is not None
        assert result.score_adj == SCORE_ADJ_MAP["stake_change"] + 5


# ---------------------------------------------------------------------------
# detect_policy_benefit
# ---------------------------------------------------------------------------


class TestDetectPolicyBenefit:
    def test_matching_sector_keyword(self) -> None:
        """2차전지 sector + 배터리 keyword -> signal."""
        result = detect_policy_benefit(
            ticker="086520", name="에코프로",
            sector="2차전지",
            policy_keywords=["배터리", "보조금"],
        )
        assert result is not None
        assert result.event_type == "policy_benefit"

    def test_no_keyword_match(self) -> None:
        """2차전지 sector + unrelated keywords -> None."""
        result = detect_policy_benefit(
            ticker="086520", name="에코프로",
            sector="2차전지",
            policy_keywords=["조선", "국방"],
        )
        assert result is None

    def test_unknown_sector_returns_none(self) -> None:
        result = detect_policy_benefit(
            ticker="123456", name="기타기업",
            sector="기타",
            policy_keywords=["배터리"],
        )
        assert result is None

    def test_empty_keywords_returns_none(self) -> None:
        result = detect_policy_benefit(
            ticker="086520", name="에코프로",
            sector="2차전지",
            policy_keywords=[],
        )
        assert result is None

    def test_ai_sector_matching(self) -> None:
        """AI sector + AI keyword -> signal."""
        result = detect_policy_benefit(
            ticker="000000", name="AI회사",
            sector="AI",
            policy_keywords=["AI", "데이터센터"],
        )
        assert result is not None


# ---------------------------------------------------------------------------
# format_event_alert
# ---------------------------------------------------------------------------


class TestFormatEventAlert:
    def test_no_bold(self) -> None:
        sig = EventSignal(
            event_type="earnings_surprise", ticker="005930", name="삼성전자",
            score_adj=15, description="서프라이즈 발생",
            action="매수 추천",
            details={"target_price": 73500, "stop_price": 67900},
        )
        msg = format_event_alert(sig)
        assert "**" not in msg

    def test_contains_juho(self) -> None:
        sig = EventSignal(
            event_type="earnings_surprise", ticker="005930", name="삼성전자",
            score_adj=15, description="서프라이즈 발생",
            action="매수 추천",
        )
        msg = format_event_alert(sig)
        assert "주호님" in msg

    def test_contains_ticker(self) -> None:
        sig = EventSignal(
            event_type="buyback", ticker="005930", name="삼성전자",
            score_adj=10, description="자사주 매입", action="매수 추천",
        )
        msg = format_event_alert(sig)
        assert "005930" in msg
        assert "삼성전자" in msg

    def test_monitoring_action_message(self) -> None:
        sig = EventSignal(
            event_type="policy_benefit", ticker="086520", name="에코프로",
            score_adj=5, description="정책 수혜", action="모니터링",
        )
        msg = format_event_alert(sig)
        assert "추적 중입니다" in msg

    def test_earnings_surprise_shows_target_stop(self) -> None:
        sig = EventSignal(
            event_type="earnings_surprise", ticker="005930", name="삼성전자",
            score_adj=15, description="서프라이즈",
            action="매수 추천",
            details={"target_price": 73500, "stop_price": 67900},
        )
        msg = format_event_alert(sig)
        assert "목표가" in msg
        assert "손절가" in msg
