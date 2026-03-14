from __future__ import annotations

from datetime import date

from kstock.signal.risk_windows import assess_krx_risk_window


def test_assess_krx_risk_window_detects_april_earnings_window():
    assessment = assess_krx_risk_window(date(2026, 4, 24))

    assert assessment.active is True
    assert assessment.key == "earnings_apr_may"
    assert assessment.severity == 3
    assert assessment.cash_floor_add >= 5.0


def test_assess_krx_risk_window_detects_november_msci_window():
    assessment = assess_krx_risk_window(date(2026, 11, 14))

    assert assessment.active is True
    assert assessment.key == "msci_nov"
    assert assessment.scalp_multiplier < 1.0
    assert assessment.swing_multiplier < 1.0


def test_assess_krx_risk_window_is_inactive_outside_windows():
    assessment = assess_krx_risk_window(date(2026, 6, 10))

    assert assessment.active is False
    assert assessment.severity == 0
