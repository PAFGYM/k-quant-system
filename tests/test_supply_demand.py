"""Tests for kstock.signal.supply_demand module."""

from __future__ import annotations

from kstock.signal.supply_demand import (
    SupplyDemandData,
    SupplyDemandSignal,
    detect_patterns,
    compute_supply_score,
    format_supply_alert,
)


class TestSupplyDemandData:
    def test_dataclass(self):
        d = SupplyDemandData(
            ticker="005930", name="삼성전자", date="2026-02-20",
            foreign_net=100, institution_net=50, retail_net=-150,
            program_net=30, short_balance=1000, short_ratio=2.5,
        )
        assert d.ticker == "005930"
        assert d.foreign_net == 100


class TestDetectPatterns:
    def _make_history(self, foreign_vals, inst_vals=None, retail_vals=None):
        """Build ascending-sorted history (oldest first)."""
        hist = []
        for i, fv in enumerate(foreign_vals):
            inst = (inst_vals or [0] * len(foreign_vals))[i]
            ret = (retail_vals or [0] * len(foreign_vals))[i]
            hist.append({
                "date": f"2026-02-{16+i:02d}",
                "foreign_net": fv,
                "institution_net": inst,
                "retail_net": ret,
                "program_net": 0,
                "short_balance": 1000,
                "short_ratio": 2.0,
            })
        return hist

    def test_foreign_3day_buy(self):
        hist = self._make_history([-10, -5, 50, 30, 20])
        sig = detect_patterns(hist, ticker="005930", name="삼성전자")
        assert sig.score_adj > 0
        assert any("외인" in p or "매수" in p for p in sig.patterns)

    def test_foreign_5day_sell(self):
        hist = self._make_history([-50, -30, -20, -10, -5])
        sig = detect_patterns(hist, ticker="005930", name="삼성전자")
        assert sig.score_adj < 0

    def test_double_buy(self):
        hist = self._make_history(
            [50, 30, 20], inst_vals=[40, 30, 20]
        )
        sig = detect_patterns(hist, ticker="005930", name="삼성전자")
        assert sig.score_adj > 0

    def test_retail_warning(self):
        hist = self._make_history(
            [-50, -30, -20], retail_vals=[200, 150, 100]
        )
        sig = detect_patterns(hist, ticker="005930", name="삼성전자")
        assert sig.score_adj < 0

    def test_no_pattern(self):
        hist = self._make_history([10, -10, 5, -5, 3])
        sig = detect_patterns(hist, ticker="005930", name="삼성전자")
        assert isinstance(sig, SupplyDemandSignal)

    def test_empty_history(self):
        sig = detect_patterns([], ticker="005930", name="삼성전자")
        assert sig.score_adj == 0

    def test_short_balance_increase(self):
        hist = []
        for i in range(5):
            hist.append({
                "date": f"2026-02-{20-i:02d}",
                "foreign_net": 0, "institution_net": 0, "retail_net": 0,
                "program_net": 0,
                "short_balance": 2000 - i * 300 if i > 0 else 2000,
                "short_ratio": 3.0,
            })
        sig = detect_patterns(hist, ticker="005930", name="삼성전자")
        assert isinstance(sig, SupplyDemandSignal)


class TestComputeSupplyScore:
    def test_positive_signal(self):
        sig = SupplyDemandSignal(
            ticker="005930", name="삼성전자",
            patterns=["외인 3일 순매수"], score_adj=10, message="",
        )
        assert compute_supply_score(sig) == 10

    def test_cap_at_20(self):
        sig = SupplyDemandSignal(
            ticker="005930", name="삼성전자",
            patterns=[], score_adj=30, message="",
        )
        assert compute_supply_score(sig) <= 20

    def test_cap_at_negative_20(self):
        sig = SupplyDemandSignal(
            ticker="005930", name="삼성전자",
            patterns=[], score_adj=-30, message="",
        )
        assert compute_supply_score(sig) >= -20


class TestFormatSupplyAlert:
    def test_basic_format(self):
        sig = SupplyDemandSignal(
            ticker="005930", name="삼성전자",
            patterns=["외인 3일 연속 순매수"], score_adj=10,
            message="외인 매집 감지",
        )
        result = format_supply_alert(sig)
        assert "**" not in result
        assert "삼성전자" in result or "005930" in result

    def test_empty_signal(self):
        sig = SupplyDemandSignal(
            ticker="005930", name="삼성전자",
            patterns=[], score_adj=0, message="",
        )
        result = format_supply_alert(sig)
        assert isinstance(result, str)
