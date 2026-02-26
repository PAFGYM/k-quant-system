"""Position Sizer 테스트 — Half-Kelly + ATR + 차익실현 자동화."""
import pytest
from kstock.core.position_sizer import (
    PositionSizer,
    PositionSize,
    ProfitAlert,
    TrailingStopState,
    PROFIT_STAGES,
    TRAILING_STOP_CONFIG,
    DEFAULT_LIMITS,
    format_concentration_warnings,
    format_profit_taking_summary,
)


# ── PositionSizer 기본 테스트 ──────────────────────────────


def test_sizer_init():
    sizer = PositionSizer(account_value=200_000_000)
    assert sizer.account_value == 200_000_000
    assert sizer.limits["max_single_weight"] == 0.30


def test_sizer_init_custom_limits():
    sizer = PositionSizer(
        account_value=100_000_000,
        limits={"max_single_weight": 0.35},
    )
    assert sizer.limits["max_single_weight"] == 0.35
    assert sizer.limits["max_sector_weight"] == 0.50  # 기본값 유지


# ── Half-Kelly 계산 테스트 ─────────────────────────────────


def test_half_kelly_positive():
    sizer = PositionSizer()
    kelly = sizer._half_kelly(0.60, 0.10, 0.05)
    # Kelly = (0.6*2 - 0.4)/2 = 0.8/2 = 0.4 → Half = 0.2
    assert 0.10 <= kelly <= 0.25  # bounded


def test_half_kelly_zero_win_rate():
    sizer = PositionSizer()
    kelly = sizer._half_kelly(0.0, 0.10, 0.05)
    assert kelly == DEFAULT_LIMITS["min_kelly_fraction"]


def test_half_kelly_high_win_rate():
    sizer = PositionSizer()
    kelly = sizer._half_kelly(0.80, 0.15, 0.05)
    assert kelly == DEFAULT_LIMITS["max_kelly_fraction"]  # capped at 25%


def test_half_kelly_negative():
    """승률이 낮으면 최소값으로."""
    sizer = PositionSizer()
    kelly = sizer._half_kelly(0.30, 0.05, 0.10)
    assert kelly == DEFAULT_LIMITS["min_kelly_fraction"]


# ── ATR 조정 테스트 ────────────────────────────────────────


def test_atr_adjust_stable():
    """ATR이 기준 이하면 조정 없음."""
    sizer = PositionSizer()
    adjusted = sizer._atr_adjust(0.15, 1.0)
    assert adjusted == 0.15  # no change


def test_atr_adjust_high_volatility():
    """ATR이 높으면 비중 축소."""
    sizer = PositionSizer()
    adjusted = sizer._atr_adjust(0.15, 5.0)
    assert adjusted < 0.15


def test_atr_adjust_zero():
    sizer = PositionSizer()
    adjusted = sizer._atr_adjust(0.15, 0)
    assert adjusted == 0.15


# ── 변동성 등급 테스트 ─────────────────────────────────────


def test_volatility_grade_a():
    sizer = PositionSizer()
    assert sizer._volatility_grade(1.5) == "A"


def test_volatility_grade_b():
    sizer = PositionSizer()
    assert sizer._volatility_grade(3.0) == "B"


def test_volatility_grade_c():
    sizer = PositionSizer()
    assert sizer._volatility_grade(5.0) == "C"


# ── 포지션 사이즈 계산 테스트 ──────────────────────────────


def test_calculate_basic():
    sizer = PositionSizer(account_value=200_000_000)
    result = sizer.calculate(
        ticker="005930",
        current_price=75000,
        atr_pct=1.8,
        win_rate=0.60,
        target_pct=0.10,
        stop_pct=-0.05,
        name="삼성전자",
    )
    assert isinstance(result, PositionSize)
    assert result.shares > 0
    assert result.amount > 0
    assert result.weight_pct > 0
    assert result.stop_price > 0
    assert result.target_price > 0
    assert result.volatility_grade == "A"
    assert "삼성전자" in result.name


def test_calculate_zero_price():
    sizer = PositionSizer()
    result = sizer.calculate(
        ticker="000000", current_price=0,
    )
    assert result.shares == 0
    assert "가격" in result.reason


def test_calculate_high_existing_weight():
    """이미 30% 보유 → 추가 매수 불가."""
    sizer = PositionSizer(account_value=200_000_000)
    result = sizer.calculate(
        ticker="005930", current_price=75000,
        existing_weight=0.30,
    )
    assert result.shares == 0
    assert "한도" in result.reason


def test_calculate_sector_limit():
    """섹터 50% 초과 → 추가 매수 불가."""
    sizer = PositionSizer(account_value=200_000_000)
    result = sizer.calculate(
        ticker="005930", current_price=75000,
        sector_weight=0.50,
    )
    assert result.shares == 0
    assert "한도" in result.reason


def test_calculate_high_atr_reduces_shares():
    """ATR이 높은 종목은 더 적은 수량 추천."""
    sizer = PositionSizer(account_value=200_000_000)
    stable = sizer.calculate(
        ticker="005930", current_price=75000, atr_pct=1.0,
        win_rate=0.60, target_pct=0.10, stop_pct=-0.05,
    )
    volatile = sizer.calculate(
        ticker="086520", current_price=50000, atr_pct=6.0,
        win_rate=0.60, target_pct=0.10, stop_pct=-0.05,
    )
    # 변동성 높은 종목의 투자 비중이 더 작아야 함
    assert volatile.weight_pct <= stable.weight_pct


# ── 차익실현 체크 테스트 ───────────────────────────────────


def test_profit_taking_no_alert():
    """수익률이 낮으면 알림 없음."""
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=75000, current_price=78000,
        quantity=100,
    )
    assert alert is None


def test_profit_taking_stage_1():
    """+50% 수익 → 1차 익절 알림."""
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="086520", name="에코프로",
        buy_price=50000, current_price=80000,  # +60%
        quantity=100,
    )
    assert alert is not None
    assert alert.alert_type == "stage_1"
    assert alert.sell_pct == pytest.approx(0.33, abs=0.01)
    assert alert.sell_shares == 33


def test_profit_taking_stage_2():
    """+100% 수익 → 원금 회수 알림."""
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="086520", name="에코프로",
        buy_price=50000, current_price=105000,  # +110%
        quantity=100,
    )
    assert alert is not None
    # stage_1이 아직 안 됐으므로 stage_1이 먼저
    assert alert.alert_type == "stage_1"

    # stage_1 트리거 후 다시 체크 → stage_2
    alert2 = sizer.check_profit_taking(
        ticker="086520", name="에코프로",
        buy_price=50000, current_price=105000,
        quantity=100,
    )
    assert alert2 is not None
    assert alert2.alert_type == "stage_2"


def test_profit_taking_stop_loss():
    """손절 도달 → 손절 알림."""
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=75000, current_price=70000,  # -6.7%
        quantity=100,
        holding_type="swing",  # -5% 손절
    )
    assert alert is not None
    assert alert.alert_type == "stop_loss"
    assert alert.urgency == "critical"
    assert alert.sell_shares == 100


def test_profit_taking_stop_loss_scalp():
    """스캘프 -3% 손절."""
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=75000, current_price=72000,  # -4%
        quantity=50,
        holding_type="scalp",
    )
    assert alert is not None
    assert alert.alert_type == "stop_loss"


def test_profit_taking_long_no_stop():
    """장기 -5%는 아직 손절이 아님 (한도 -15%)."""
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=75000, current_price=71250,  # -5%
        quantity=100,
        holding_type="long_term",
    )
    # 장기는 -15%까지 허용
    assert alert is None


# ── 트레일링 스탑 테스트 ───────────────────────────────────


def test_trailing_stop_not_active_initially():
    sizer = PositionSizer()
    state = sizer._update_trailing_stop(
        "005930", 78000, 75000, "swing",  # +4%, activate_at=8%
    )
    assert not state.is_active


def test_trailing_stop_activates():
    sizer = PositionSizer()
    # 8% 이상 수익에서 활성화
    state = sizer._update_trailing_stop(
        "005930", 82000, 75000, "swing",  # +9.3%
    )
    assert state.is_active
    assert state.stop_price > 0
    assert state.stop_price == pytest.approx(82000 * 0.95, abs=10)


def test_trailing_stop_updates_high():
    sizer = PositionSizer()
    # 처음 활성화
    sizer._update_trailing_stop("005930", 82000, 75000, "swing")
    # 가격 상승 → 고점 갱신
    state = sizer._update_trailing_stop("005930", 85000, 75000, "swing")
    assert state.high_price == 85000
    assert state.stop_price == pytest.approx(85000 * 0.95, abs=10)


def test_trailing_stop_triggered():
    """트레일링 스탑 발동 체크."""
    sizer = PositionSizer()
    # 활성화
    sizer._update_trailing_stop("005930", 82000, 75000, "swing")
    # 고점 갱신
    sizer._update_trailing_stop("005930", 90000, 75000, "swing")
    # 하락 → 스탑 가격 아래
    stop_price = 90000 * 0.95  # 85500
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=75000, current_price=stop_price - 100,
        quantity=100, holding_type="swing",
    )
    assert alert is not None
    assert alert.alert_type == "trailing_stop"
    assert alert.urgency == "high"


def test_trailing_stop_reset():
    sizer = PositionSizer()
    sizer._update_trailing_stop("005930", 82000, 75000, "swing")
    assert "005930" in sizer._trailing_states
    sizer.reset_trailing_stop("005930")
    assert "005930" not in sizer._trailing_states


def test_get_trailing_state():
    sizer = PositionSizer()
    assert sizer.get_trailing_state("005930") is None
    sizer._update_trailing_stop("005930", 80000, 75000, "swing")
    state = sizer.get_trailing_state("005930")
    assert state is not None
    assert state.ticker == "005930"


# ── 집중도 분석 테스트 ─────────────────────────────────────


def test_concentration_no_holdings():
    sizer = PositionSizer()
    warnings = sizer.analyze_concentration([])
    assert warnings == []


def test_concentration_normal():
    sizer = PositionSizer()
    holdings = [
        {"ticker": "005930", "name": "삼성전자", "eval_amount": 20_000_000},
        {"ticker": "086520", "name": "에코프로", "eval_amount": 20_000_000},
        {"ticker": "005380", "name": "현대차", "eval_amount": 20_000_000},
        {"ticker": "035420", "name": "NAVER", "eval_amount": 20_000_000},
    ]
    warnings = sizer.analyze_concentration(holdings)
    assert len(warnings) == 0  # 각 25%로 한도(30%) 이하


def test_concentration_single_stock_violation():
    """한 종목 50% → 경고."""
    sizer = PositionSizer()
    holdings = [
        {"ticker": "086520", "name": "에코프로", "eval_amount": 100_000_000},
        {"ticker": "005930", "name": "삼성전자", "eval_amount": 50_000_000},
        {"ticker": "005380", "name": "현대차", "eval_amount": 50_000_000},
    ]
    warnings = sizer.analyze_concentration(holdings)
    assert len(warnings) >= 1
    assert any("에코프로" in w for w in warnings)


def test_concentration_sector_violation():
    """2차전지 섹터 70% → 경고 (한도 50%)."""
    sizer = PositionSizer()
    holdings = [
        {"ticker": "086520", "name": "에코프로", "eval_amount": 50_000_000},
        {"ticker": "247540", "name": "에코프로비엠", "eval_amount": 30_000_000},
        {"ticker": "006400", "name": "삼성SDI", "eval_amount": 20_000_000},
        {"ticker": "005930", "name": "삼성전자", "eval_amount": 10_000_000},  # 반도체 9%
    ]
    warnings = sizer.analyze_concentration(holdings)
    assert any("2차전지" in w or "섹터" in w for w in warnings)


# ── 포맷 테스트 ────────────────────────────────────────────


def test_format_position_advice_buy():
    sizer = PositionSizer()
    result = PositionSize(
        ticker="005930", name="삼성전자",
        shares=200, amount=15_000_000,
        weight_pct=7.5, kelly_fraction=0.10,
        atr_adjusted=0.08, volatility_grade="A",
        expected_return=0.035,
        stop_price=71250, target_price=82500,
        reason="Kelly 양호. 기대수익률 +3.5%.",
    )
    msg = sizer.format_position_advice(result)
    assert "삼성전자" in msg
    assert "200주" in msg
    assert "목표가" in msg


def test_format_position_advice_blocked():
    sizer = PositionSizer()
    result = PositionSize(
        ticker="005930", name="삼성전자",
        shares=0, reason="비중 한도 초과.",
    )
    msg = sizer.format_position_advice(result)
    assert "매수 불가" in msg


def test_format_profit_alert():
    sizer = PositionSizer()
    alert = ProfitAlert(
        ticker="086520", name="에코프로",
        alert_type="stage_1", pnl_pct=55.0,
        buy_price=50000, current_price=77500,
        action="1차 익절",
        sell_shares=33, sell_pct=0.33,
        urgency="medium",
        message="🟡 에코프로 1차 익절\n   수익률 +55.0%",
    )
    msg = sizer.format_profit_alert(alert)
    assert "차익실현" in msg
    assert "에코프로" in msg


def test_format_concentration_warnings_empty():
    msg = format_concentration_warnings([])
    assert msg == ""


def test_format_concentration_warnings_with_data():
    warnings = [
        "⚠️ 에코프로 비중 50.0% (한도 30%, 20%p 초과)",
    ]
    msg = format_concentration_warnings(warnings)
    assert "집중도" in msg
    assert "에코프로" in msg


def test_format_profit_taking_summary_empty():
    msg = format_profit_taking_summary([])
    assert msg == ""


def test_format_profit_taking_summary_with_alerts():
    alerts = [
        ProfitAlert(
            ticker="086520", name="에코프로",
            alert_type="stage_1", pnl_pct=55.0,
            buy_price=50000, current_price=77500,
            action="1차 익절",
            sell_shares=33, sell_pct=0.33,
            urgency="medium",
            message="수익 +55%",
        ),
    ]
    msg = format_profit_taking_summary(alerts)
    assert "차익실현" in msg
    assert "에코프로" in msg


# ── 상수 검증 ──────────────────────────────────────────────


def test_profit_stages_config():
    assert len(PROFIT_STAGES) == 2
    assert PROFIT_STAGES[0]["threshold"] == 0.50
    assert PROFIT_STAGES[1]["threshold"] == 1.00


def test_trailing_stop_config():
    assert "swing" in TRAILING_STOP_CONFIG
    assert "long" in TRAILING_STOP_CONFIG
    assert TRAILING_STOP_CONFIG["scalp"]["trail_pct"] == 0.03
    assert TRAILING_STOP_CONFIG["long"]["trail_pct"] == 0.15


def test_default_limits():
    assert DEFAULT_LIMITS["max_single_weight"] == 0.30
    assert DEFAULT_LIMITS["max_sector_weight"] == 0.50
    assert DEFAULT_LIMITS["min_kelly_fraction"] == 0.03
    assert DEFAULT_LIMITS["max_kelly_fraction"] == 0.25


# ── 엣지 케이스 ────────────────────────────────────────────


def test_calculate_very_small_account():
    sizer = PositionSizer(account_value=100_000)  # 10만원
    result = sizer.calculate(
        ticker="005930", current_price=75000,
        win_rate=0.60, target_pct=0.10, stop_pct=-0.05,
    )
    # 75000원 × 1주 = 75000원, 10만원 계좌에서는 1주 가능할 수도
    assert result.shares >= 0


def test_check_profit_taking_zero_quantity():
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=75000, current_price=120000,
        quantity=0,  # 0주
    )
    assert alert is None


def test_check_profit_taking_zero_buy_price():
    sizer = PositionSizer()
    alert = sizer.check_profit_taking(
        ticker="005930", name="삼성전자",
        buy_price=0, current_price=75000,
        quantity=100,
    )
    assert alert is None


def test_multiple_holdings_trailing():
    """여러 종목 동시 트레일링 스탑 추적."""
    sizer = PositionSizer()
    sizer._update_trailing_stop("005930", 82000, 75000, "swing")
    sizer._update_trailing_stop("086520", 80000, 50000, "mid")

    states = sizer.get_all_trailing_states()
    assert len(states) == 2
    assert "005930" in states
    assert "086520" in states
