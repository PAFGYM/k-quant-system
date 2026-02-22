"""Tests for kstock.signal.tariff_tracker module."""

from __future__ import annotations

from kstock.signal.tariff_tracker import (
    CURRENT_TARIFFS,
    TICKER_TARIFF_IMPACT,
    TariffChange,
    evaluate_tariff_impact,
    compute_tariff_score_adj,
    format_tariff_change_alert,
    format_tariff_status,
)


class TestCurrentTariffs:
    def test_global_15(self):
        assert "global_15" in CURRENT_TARIFFS
        assert CURRENT_TARIFFS["global_15"]["rate"] == 15

    def test_auto_25(self):
        assert "auto_25" in CURRENT_TARIFFS
        assert CURRENT_TARIFFS["auto_25"]["rate"] == 25

    def test_steel_50(self):
        assert "steel_50" in CURRENT_TARIFFS
        assert CURRENT_TARIFFS["steel_50"]["rate"] == 50

    def test_semiconductor_25(self):
        assert "semiconductor_25" in CURRENT_TARIFFS

    def test_reciprocal_invalid(self):
        assert CURRENT_TARIFFS["reciprocal"]["status"] == "무효"


class TestTickerTariffImpact:
    def test_ecopro(self):
        assert "086520" in TICKER_TARIFF_IMPACT
        assert TICKER_TARIFF_IMPACT["086520"]["impact"] == "제한적"

    def test_hyundai(self):
        assert "005380" in TICKER_TARIFF_IMPACT
        assert TICKER_TARIFF_IMPACT["005380"]["impact"] == "부정적"

    def test_samsung(self):
        assert "005930" in TICKER_TARIFF_IMPACT
        assert TICKER_TARIFF_IMPACT["005930"]["impact"] == "주의"


class TestEvaluateTariffImpact:
    def test_known_ticker(self):
        result = evaluate_tariff_impact("005380", "현대차")
        assert isinstance(result, dict)
        assert "impact" in result

    def test_unknown_ticker(self):
        result = evaluate_tariff_impact("999999", "알수없음")
        assert isinstance(result, dict)

    def test_ecopro_limited(self):
        result = evaluate_tariff_impact("086520")
        assert result.get("impact") == "제한적"


class TestComputeTariffScoreAdj:
    def test_negative_impact(self):
        adj = compute_tariff_score_adj("005380")  # 현대차 = 부정적
        assert adj < 0

    def test_limited_impact(self):
        adj = compute_tariff_score_adj("086520")  # 에코프로 = 제한적
        assert adj == 0

    def test_caution_impact(self):
        adj = compute_tariff_score_adj("005930")  # 삼성전자 = 주의
        assert adj < 0

    def test_unknown_ticker(self):
        adj = compute_tariff_score_adj("999999")
        assert adj == 0


class TestFormatTariffChangeAlert:
    def test_basic_format(self):
        change = TariffChange(
            category="auto", prev_rate=25.0, new_rate=30.0,
            effective_date="2026-03-15",
            description="자동차 관세 인상",
            affected_tickers=["005380"],
            message="",
        )
        result = format_tariff_change_alert(change)
        assert "**" not in result
        assert "30" in result or "관세" in result

    def test_contains_juhonim(self):
        change = TariffChange(
            category="steel", prev_rate=50.0, new_rate=60.0,
            effective_date="2026-04-01",
            description="철강 관세 인상",
            affected_tickers=["005490"],
            message="",
        )
        result = format_tariff_change_alert(change)
        assert "주호님" in result


class TestFormatTariffStatus:
    def test_no_bold(self):
        result = format_tariff_status()
        assert "**" not in result

    def test_contains_tariff_info(self):
        result = format_tariff_status()
        assert "관세" in result
        assert "15%" in result or "25%" in result or "50%" in result

    def test_non_empty(self):
        result = format_tariff_status()
        assert len(result) > 50
