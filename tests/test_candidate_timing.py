from kstock.bot.mixins.scheduler import _rotation_rank_adjustment, _timing_rank_adjustment


def test_timing_rank_adjustment_penalizes_late_entries():
    score, label, action = _timing_rank_adjustment("late")
    assert score < 0
    assert label == "추격 구간"
    assert "눌림" in action


def test_timing_rank_adjustment_rewards_end_entries():
    score, label, action = _timing_rank_adjustment("end")
    assert score > 0
    assert label == "변곡 끝자락"
    assert "분할" in action


def test_rotation_rank_adjustment_penalizes_nuclear_during_rotation():
    snapshot = {
        "tags": ["코스피-코스닥 디커플링", "대형 반도체 쏠림", "원전/전력 차익실현"],
    }
    score, note = _rotation_rank_adjustment(
        snapshot,
        candidate_sector="원전/전력",
        listing_market="KOSDAQ",
        mgr_key="swing",
    )
    assert score < 0
    assert note


def test_rotation_rank_adjustment_rewards_semis():
    snapshot = {
        "tags": ["대형 반도체 쏠림"],
    }
    score, note = _rotation_rank_adjustment(
        snapshot,
        candidate_sector="반도체",
        listing_market="KOSPI",
        mgr_key="position",
    )
    assert score > 0
    assert "반도체" in note
