"""Tests for herd detector patterns."""

from kstock.signal.herd_detector import detect_herd_pattern, scan_herd_all


def test_detect_genuine_force_pattern():
    signal = detect_herd_pattern(
        ticker="005930",
        name="삼성전자",
        daily_volumes=[100] * 15 + [160, 170, 180, 190, 220],
        daily_closes=[70000] * 15 + [70500, 71000, 71500, 72000, 73000],
        daily_inst=[0] * 17 + [2_0000_0000, 2_2000_0000, 2_4000_0000],
        daily_foreign=[0] * 17 + [1_5000_0000, 1_7000_0000, 1_9000_0000],
    )

    assert signal is not None
    assert signal.pattern == "진성 세력"
    assert signal.danger_level == "안전"


def test_detect_retail_herd_pattern():
    signal = detect_herd_pattern(
        ticker="123456",
        name="테마주",
        daily_volumes=[100] * 19 + [420],
        daily_closes=[1000 + i * 5 for i in range(20)],
        daily_inst=[0] * 20,
        daily_foreign=[0] * 20,
    )

    assert signal is not None
    assert signal.pattern == "개미떼 유입"
    assert signal.danger_level == "위험"


def test_scan_herd_all_sorts_by_absolute_score_adjustment():
    results = scan_herd_all([
        {
            "ticker": "A",
            "name": "급락주",
            "daily_volumes": [100] * 15 + [150, 160, 170, 180, 250],
            "daily_closes": [100] * 14 + [102, 104, 108, 120, 113, 110],
            "daily_inst": [0] * 20,
            "daily_foreign": [0] * 20,
        },
        {
            "ticker": "B",
            "name": "수급주",
            "daily_volumes": [100] * 15 + [160, 170, 180, 190, 220],
            "daily_closes": [100] * 15 + [101, 102, 103, 104, 105],
            "daily_inst": [0] * 17 + [2_0000_0000, 2_2000_0000, 2_4000_0000],
            "daily_foreign": [0] * 17 + [1_5000_0000, 1_7000_0000, 1_9000_0000],
        },
    ])

    assert len(results) >= 1
    assert abs(results[0].score_adj) >= abs(results[-1].score_adj)
