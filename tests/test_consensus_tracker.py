"""Tests for kstock.signal.consensus_tracker (Section 49 - consensus tracking)."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta

from kstock.signal.consensus_tracker import (
    ConsensusData,
    compute_consensus,
    compute_consensus_score,
    format_consensus,
    format_consensus_from_dict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(
    target_price: float = 90000,
    opinion: str = "매수",
    prev_opinion: str = "매수",
    date: str = "",
    **extra,
) -> dict:
    """Build a single report dict with sensible defaults."""
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    d = {
        "ticker": "005930",
        "name": "삼성전자",
        "target_price": target_price,
        "opinion": opinion,
        "prev_opinion": prev_opinion,
        "date": date,
    }
    d.update(extra)
    return d


def _old_date(days_ago: int = 60) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# ConsensusData dataclass
# ---------------------------------------------------------------------------


class TestConsensusDataDataclass:
    def test_creation(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=90000, current_price=70000,
            upside_pct=28.57,
        )
        assert cd.ticker == "005930"
        assert cd.upside_pct == 28.57

    def test_defaults(self) -> None:
        cd = ConsensusData(
            ticker="T", name="N",
            avg_target_price=0, current_price=0,
            upside_pct=0,
        )
        assert cd.opinions == {}
        assert cd.target_trend == "유지"
        assert cd.score_bonus == 0


# ---------------------------------------------------------------------------
# compute_consensus
# ---------------------------------------------------------------------------


class TestComputeConsensus:
    def test_with_reports(self) -> None:
        reports = [
            _make_report(target_price=90000),
            _make_report(target_price=95000),
            _make_report(target_price=85000),
        ]
        result = compute_consensus(reports, current_price=70000)
        assert result.ticker == "005930"
        assert result.name == "삼성전자"
        assert result.avg_target_price == 90000  # (90k+95k+85k)/3 = 90k
        assert result.upside_pct > 20.0
        assert result.total_brokers == 3

    def test_empty_reports(self) -> None:
        result = compute_consensus([], current_price=70000)
        assert result.ticker == ""
        assert result.avg_target_price == 0.0
        assert result.score_bonus == 0

    def test_upside_30_plus_bonus(self) -> None:
        """Upside > 30% -> bonus includes +15."""
        reports = [_make_report(target_price=100000)]
        result = compute_consensus(reports, current_price=70000)
        # upside = (100000-70000)/70000*100 = 42.86%
        assert result.upside_pct > 30.0
        assert result.score_bonus >= 15

    def test_upside_15_plus_bonus(self) -> None:
        """Upside between 15-30% -> bonus includes +10."""
        reports = [_make_report(target_price=85000)]
        result = compute_consensus(reports, current_price=70000)
        # upside = (85000-70000)/70000*100 = 21.4%
        assert 15.0 < result.upside_pct < 30.0
        assert result.score_bonus >= 10

    def test_buy_ratio_80_pct_bonus(self) -> None:
        """80%+ buy opinions -> +5 bonus."""
        reports = [
            _make_report(target_price=90000, opinion="매수"),
            _make_report(target_price=85000, opinion="매수"),
            _make_report(target_price=88000, opinion="매수"),
            _make_report(target_price=87000, opinion="매수"),
            _make_report(target_price=92000, opinion="중립"),
        ]
        result = compute_consensus(reports, current_price=70000)
        buy_count = result.opinions.get("매수", 0)
        buy_ratio = buy_count / result.total_brokers
        assert buy_ratio >= 0.8

    def test_target_trend_upward(self) -> None:
        """Recent targets much higher than older -> 상향."""
        reports = [
            _make_report(target_price=70000, date=_old_date(60)),
            _make_report(target_price=72000, date=_old_date(45)),
            _make_report(target_price=90000),
            _make_report(target_price=92000),
        ]
        result = compute_consensus(reports, current_price=70000)
        # Recent avg ~91k vs older avg ~71k = +28% -> 상향
        assert result.target_trend == "상향"

    def test_bonus_capped_at_20(self) -> None:
        """Score bonus should never exceed 20."""
        # Very favorable: huge upside, 상향 trend, 80%+ buy
        reports = [
            _make_report(target_price=70000, date=_old_date(60)),
            _make_report(target_price=150000, opinion="매수"),
            _make_report(target_price=155000, opinion="매수"),
            _make_report(target_price=160000, opinion="매수"),
            _make_report(target_price=145000, opinion="매수"),
            _make_report(target_price=148000, opinion="매수"),
        ]
        result = compute_consensus(reports, current_price=70000)
        assert result.score_bonus <= 20

    def test_bonus_capped_at_negative_20(self) -> None:
        """Score bonus should never be below -20."""
        # Downtrend + opinion downgrades
        reports = [
            _make_report(target_price=100000, date=_old_date(60)),
            _make_report(target_price=95000, date=_old_date(50)),
            _make_report(target_price=70000, opinion="중립", prev_opinion="매수"),
            _make_report(target_price=68000, opinion="매도", prev_opinion="매수"),
            _make_report(target_price=65000, opinion="매도", prev_opinion="중립"),
        ]
        result = compute_consensus(reports, current_price=70000)
        assert result.score_bonus >= -20


# ---------------------------------------------------------------------------
# compute_consensus_score
# ---------------------------------------------------------------------------


class TestComputeConsensusScore:
    def test_returns_capped_bonus(self) -> None:
        cd = ConsensusData(
            ticker="T", name="N",
            avg_target_price=100000, current_price=70000,
            upside_pct=42.0, score_bonus=15,
        )
        assert compute_consensus_score(cd) == 15

    def test_caps_at_20(self) -> None:
        cd = ConsensusData(
            ticker="T", name="N",
            avg_target_price=100000, current_price=70000,
            upside_pct=42.0, score_bonus=25,
        )
        assert compute_consensus_score(cd) == 20

    def test_caps_at_negative_20(self) -> None:
        cd = ConsensusData(
            ticker="T", name="N",
            avg_target_price=50000, current_price=70000,
            upside_pct=-28.0, score_bonus=-30,
        )
        assert compute_consensus_score(cd) == -20


# ---------------------------------------------------------------------------
# format_consensus
# ---------------------------------------------------------------------------


class TestFormatConsensus:
    def test_contains_ticker_name(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=90000, current_price=70000,
            upside_pct=28.57, total_brokers=5,
            opinions={"매수": 4, "중립": 1, "매도": 0},
        )
        msg = format_consensus(cd)
        assert "삼성전자" in msg
        assert "005930" in msg

    def test_no_bold(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=90000, current_price=70000,
            upside_pct=28.57, total_brokers=5,
            opinions={"매수": 4, "중립": 1, "매도": 0},
            target_trend="상향", target_trend_pct=5.0,
            score_bonus=15,
        )
        msg = format_consensus(cd)
        assert "**" not in msg

    def test_contains_upside(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=90000, current_price=70000,
            upside_pct=28.57,
        )
        msg = format_consensus(cd)
        assert "상승여력" in msg

    def test_contains_coverage(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=90000, current_price=70000,
            upside_pct=28.57, total_brokers=5,
            opinions={"매수": 4, "중립": 1, "매도": 0},
        )
        msg = format_consensus(cd)
        assert "5개사" in msg

    def test_score_bonus_shown(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=90000, current_price=70000,
            upside_pct=28.57, score_bonus=10,
        )
        msg = format_consensus(cd)
        assert "+10" in msg

    def test_negative_score_shown(self) -> None:
        cd = ConsensusData(
            ticker="005930", name="삼성전자",
            avg_target_price=50000, current_price=70000,
            upside_pct=-28.0, score_bonus=-10,
        )
        msg = format_consensus(cd)
        assert "-10" in msg


# ---------------------------------------------------------------------------
# format_consensus_from_dict
# ---------------------------------------------------------------------------


class TestFormatConsensusFromDict:
    def test_same_behavior_as_format_consensus(self) -> None:
        data = {
            "ticker": "005930",
            "name": "삼성전자",
            "avg_target_price": 90000,
            "current_price": 70000,
            "upside_pct": 28.57,
            "buy_count": 4,
            "hold_count": 1,
            "sell_count": 0,
            "target_trend": "상향",
            "target_trend_pct": 5.0,
            "score_bonus": 10,
        }
        msg = format_consensus_from_dict(data)
        assert "삼성전자" in msg
        assert "005930" in msg
        assert "**" not in msg

    def test_from_dict_no_bold(self) -> None:
        data = {
            "ticker": "035720",
            "name": "카카오",
            "avg_target_price": 65000,
            "current_price": 50000,
            "upside_pct": 30.0,
            "buy_count": 3,
            "hold_count": 2,
            "sell_count": 0,
            "target_trend": "유지",
            "target_trend_pct": 0.0,
            "score_bonus": 5,
        }
        msg = format_consensus_from_dict(data)
        assert "**" not in msg
        assert "카카오" in msg

    def test_from_dict_with_zero_bonus(self) -> None:
        data = {
            "ticker": "000660",
            "name": "SK하이닉스",
            "avg_target_price": 150000,
            "current_price": 150000,
            "upside_pct": 0.0,
            "buy_count": 2,
            "hold_count": 2,
            "sell_count": 1,
            "target_trend": "유지",
            "target_trend_pct": 0.0,
            "score_bonus": 0,
        }
        msg = format_consensus_from_dict(data)
        assert "SK하이닉스" in msg
        # Score bonus 0 should not show bonus/adjustment lines
        assert "보너스" not in msg
        assert "조정" not in msg
