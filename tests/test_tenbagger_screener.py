"""Tests for signal/tenbagger_screener.py."""

from __future__ import annotations

from kstock.signal.tenbagger_screener import (
    compute_tenbagger_score,
    format_tenbagger_card,
)


class TestComputeTenbaggerScore:
    """텐배거 스코어 계산기 회귀 테스트."""

    def test_config_context_and_future_value_are_populated(self):
        score = compute_tenbagger_score(
            ticker="105840",
            name="우진",
            market="KRX",
            sector="nuclear_smr",
            revenue_growth_yoy=38,
            tam_cagr=28,
            policy_events=["SMR 지원 정책"],
            first_mover=True,
            market_share_pct=35,
            is_profitable=True,
            operating_profit_growth=55,
            foreign_buy_days_in_20=12,
            institution_buy_days_in_20=9,
            foreign_ratio_change=1.4,
            sector_return_1m=8,
            sector_return_3m=16,
            leader_return_1m=9,
            current_price=10_000,
        )

        assert score.config_grade == "A"
        assert score.config_tier == "core"
        assert "원전" in score.character
        assert score.catalysts
        assert score.kill_conditions
        assert score.monitor_12m
        assert score.future_base_multiple > 0
        assert score.future_bull_multiple > score.future_base_multiple
        assert score.future_base_price > score.current_price
        assert "경로" in score.future_value_note

    def test_to_dict_contains_future_value_summary(self):
        score = compute_tenbagger_score(
            ticker="083650",
            name="비에이치아이",
            market="KRX",
            sector="nuclear_smr",
            revenue_growth_yoy=60,
            tam_cagr=30,
            policy_events=["원전 수출"],
            has_patents=True,
            patent_count=5,
            exclusive_contracts=True,
            is_profitable=True,
            current_price=20_000,
        )

        payload = score.to_dict()
        assert payload["future_base_multiple"] > 0
        assert payload["future_bull_multiple"] > payload["future_base_multiple"]
        assert payload["notes"]


class TestFormatTenbaggerCard:
    """카드 포맷 UX 회귀 테스트."""

    def test_card_includes_future_value_section(self):
        score = compute_tenbagger_score(
            ticker="189300",
            name="인텔리안테크",
            market="KRX",
            sector="space_defense",
            revenue_growth_yoy=42,
            tam_cagr=25,
            first_mover=True,
            is_profitable=True,
            turning_profitable=True,
            foreign_buy_days_in_20=11,
            institution_buy_days_in_20=8,
            current_price=50_000,
        )

        text = format_tenbagger_card(score)
        assert "미래가치 시나리오" in text
        assert "기본(" in text
        assert "낙관(" in text
        assert "메모:" in text
