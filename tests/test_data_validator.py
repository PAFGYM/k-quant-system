"""Tests for core/data_validator.py - Data quality validation."""

import pytest
from kstock.core.data_validator import (
    validate_price,
    validate_volume,
    detect_anomalies,
    cross_validate_prices,
    generate_quality_report,
    format_data_alert,
)


# ---------------------------------------------------------------------------
# TestValidatePrice
# ---------------------------------------------------------------------------

class TestValidatePrice:
    """validate_price: 전일종가 대비 변동률 검증."""

    def test_normal_change_valid(self):
        """5% 변동은 정상 범위."""
        result = validate_price(105_000, 100_000, ticker="005930")
        assert result["valid"] is True
        assert abs(result["change_pct"] - 0.05) < 1e-4
        assert result["anomaly_type"] == ""

    def test_extreme_change_invalid(self):
        """35% 변동은 가격제한폭(30%) 초과 -> 이상."""
        result = validate_price(135_000, 100_000, ticker="005930")
        assert result["valid"] is False
        assert result["anomaly_type"] == "price_limit_exceeded"
        assert result["change_pct"] > 0.30

    def test_negative_extreme_change_invalid(self):
        """-35% 변동도 이상."""
        result = validate_price(65_000, 100_000, ticker="005930")
        assert result["valid"] is False
        assert result["anomaly_type"] == "price_limit_exceeded"

    def test_zero_prev_close_handled(self):
        """전일종가 0 -> valid=False, 'invalid_prev_close'."""
        result = validate_price(100_000, 0, ticker="005930")
        assert result["valid"] is False
        assert result["anomaly_type"] == "invalid_prev_close"

    def test_negative_prev_close_handled(self):
        """전일종가 음수 -> valid=False."""
        result = validate_price(100_000, -50_000, ticker="005930")
        assert result["valid"] is False
        assert result["anomaly_type"] == "invalid_prev_close"

    def test_exactly_at_limit(self):
        """정확히 30% 변동 -> 초과하지 않으므로 valid."""
        result = validate_price(130_000, 100_000, ticker="005930")
        assert result["valid"] is True
        assert abs(result["change_pct"] - 0.30) < 1e-4


# ---------------------------------------------------------------------------
# TestValidateVolume
# ---------------------------------------------------------------------------

class TestValidateVolume:
    """validate_volume: 평균 거래량 대비 배율 검증."""

    def test_normal_volume_valid(self):
        """평균 대비 2배 -> 정상."""
        result = validate_volume(200_000, 100_000, ticker="005930")
        assert result["valid"] is True
        assert abs(result["ratio"] - 2.0) < 0.01
        assert result["anomaly_type"] == ""

    def test_spike_volume_anomaly(self):
        """평균 대비 15배 -> 스파이크(기준 10배 초과)."""
        result = validate_volume(1_500_000, 100_000, ticker="005930")
        assert result["valid"] is False
        assert result["ratio"] == 15.0
        assert result["anomaly_type"] == "volume_spike"

    def test_zero_avg_handled(self):
        """평균 거래량 0 -> valid=False."""
        result = validate_volume(100_000, 0, ticker="005930")
        assert result["valid"] is False
        assert result["anomaly_type"] == "invalid_avg_volume"

    def test_negative_avg_handled(self):
        """평균 거래량 음수 -> valid=False."""
        result = validate_volume(100_000, -50_000, ticker="005930")
        assert result["valid"] is False
        assert result["anomaly_type"] == "invalid_avg_volume"


# ---------------------------------------------------------------------------
# TestDetectAnomalies
# ---------------------------------------------------------------------------

class TestDetectAnomalies:
    """detect_anomalies: 가격/거래량 이상 일괄 탐지."""

    def test_mix_of_normal_and_anomalous(self):
        """정상 항목과 이상 항목이 섞인 입력."""
        prices = [
            {
                "ticker": "NORMAL",
                "price": 105_000,
                "prev_close": 100_000,
                "volume": 200_000,
                "avg_volume": 100_000,
            },
            {
                "ticker": "PRICE_ANOMALY",
                "price": 150_000,
                "prev_close": 100_000,
                "volume": 200_000,
                "avg_volume": 100_000,
            },
            {
                "ticker": "VOLUME_ANOMALY",
                "price": 102_000,
                "prev_close": 100_000,
                "volume": 1_500_000,
                "avg_volume": 100_000,
            },
        ]
        anomalies = detect_anomalies(prices)
        tickers = [a["ticker"] for a in anomalies]
        assert "NORMAL" not in tickers
        assert "PRICE_ANOMALY" in tickers
        assert "VOLUME_ANOMALY" in tickers
        assert len(anomalies) == 2

    def test_empty_input(self):
        anomalies = detect_anomalies([])
        assert anomalies == []


# ---------------------------------------------------------------------------
# TestCrossValidatePrices
# ---------------------------------------------------------------------------

class TestCrossValidatePrices:
    """cross_validate_prices: 두 소스 간 가격 교차 검증."""

    def test_matching_prices_no_mismatches(self):
        """가격이 동일하면 불일치 없음."""
        s1 = {"005930": 76_500, "000660": 130_000}
        s2 = {"005930": 76_500, "000660": 130_000}
        mismatches = cross_validate_prices(s1, s2)
        assert mismatches == []

    def test_small_diff_within_tolerance(self):
        """0.5% 차이 -> 기본 tolerance 1% 내 -> 불일치 아님."""
        s1 = {"005930": 76_500}
        s2 = {"005930": 76_117}
        mismatches = cross_validate_prices(s1, s2)
        assert mismatches == []

    def test_mismatch_found(self):
        """2% 차이 -> tolerance(1%) 초과 -> 불일치."""
        s1 = {"005930": 76_500}
        s2 = {"005930": 75_000}
        mismatches = cross_validate_prices(s1, s2)
        assert len(mismatches) == 1
        assert mismatches[0]["ticker"] == "005930"
        assert mismatches[0]["diff_pct"] > 0.01


# ---------------------------------------------------------------------------
# TestGenerateQualityReport
# ---------------------------------------------------------------------------

class TestGenerateQualityReport:
    """generate_quality_report: 데이터 품질 리포트 포맷."""

    def test_no_bold_markers(self):
        report = generate_quality_report([], [])
        assert "**" not in report

    def test_contains_data_keyword(self):
        report = generate_quality_report([], [])
        assert "데이터" in report

    def test_contains_anomaly_keyword(self):
        anomalies = [{"ticker": "X", "type": "price", "change_pct": 0.5}]
        report = generate_quality_report(anomalies, [])
        assert "이상" in report

    def test_clean_report_quality_ok(self):
        """이상이 없으면 '양호' 메시지."""
        report = generate_quality_report([], [], duplicates_removed=0, nulls_filled=0)
        assert "양호" in report


# ---------------------------------------------------------------------------
# TestFormatDataAlert
# ---------------------------------------------------------------------------

class TestFormatDataAlert:
    """format_data_alert: 이상 항목 알림 메시지."""

    def test_no_bold_markers(self):
        anomalies = [{"ticker": "005930", "type": "price", "change_pct": 0.5}]
        alert = format_data_alert(anomalies)
        assert "**" not in alert

    def test_contains_alert_info(self):
        anomalies = [{"ticker": "005930", "type": "price", "change_pct": 0.5}]
        alert = format_data_alert(anomalies)
        assert "이상" in alert
        assert "005930" in alert

    def test_empty_anomalies_returns_empty(self):
        assert format_data_alert([]) == ""
