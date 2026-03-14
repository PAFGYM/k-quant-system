from __future__ import annotations

import pandas as pd

from kstock.signal.timing_windows import analyze_timing_windows


def test_analyze_timing_windows_detects_end_phase():
    close = pd.Series(
        [100.0] * 28 + [98.0, 97.0, 96.0, 96.5, 98.0, 100.0, 102.0],
        dtype=float,
    )

    assessment = analyze_timing_windows(close)

    assert assessment is not None
    assert assessment.overall_phase == "end"
    assert assessment.preferred_window == 15
    assert "씨앗 또는 1차 분할" in assessment.coach_line


def test_analyze_timing_windows_detects_early_phase():
    close = pd.Series(
        [100.0] * 28 + [99.0, 98.0, 97.0, 96.5, 96.6, 96.7, 96.8],
        dtype=float,
    )

    assessment = analyze_timing_windows(close)

    assert assessment is not None
    assert assessment.overall_phase == "early"
    assert "서두르지 않는 편" in assessment.coach_line


def test_analyze_timing_windows_detects_late_phase():
    close = pd.Series(
        [100.0] * 28 + [95.0, 96.0, 99.0, 103.0, 108.0, 112.0, 116.0],
        dtype=float,
    )

    assessment = analyze_timing_windows(close)

    assert assessment is not None
    assert assessment.overall_phase == "late"
    assert "추격 구간" in assessment.coach_line
