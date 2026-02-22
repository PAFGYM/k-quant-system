"""Tests for the per-stock diagnosis module."""

from __future__ import annotations

import pytest

from kstock.bot.diagnosis import (
    DiagnosisResult,
    _action_label,
    _build_stock_prompt,
    _diagnosis_emoji,
    _fallback_diagnosis,
    _won,
    format_diagnosis_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def holding_a() -> dict:
    """Holding with +8% profit (category A)."""
    return {
        "name": "삼성전자",
        "ticker": "005930",
        "avg_price": 70000,
        "current_price": 75600,
        "profit_pct": 8.0,
    }


@pytest.fixture
def holding_b() -> dict:
    """Holding with +3% profit (category B)."""
    return {
        "name": "SK하이닉스",
        "ticker": "000660",
        "avg_price": 100000,
        "current_price": 103000,
        "profit_pct": 3.0,
    }


@pytest.fixture
def holding_c() -> dict:
    """Holding with -3% loss (category C)."""
    return {
        "name": "카카오",
        "ticker": "035720",
        "avg_price": 60000,
        "current_price": 58200,
        "profit_pct": -3.0,
    }


@pytest.fixture
def holding_d_low_rsi() -> dict:
    """Holding with -8% loss (category D) for oversold scenario."""
    return {
        "name": "네이버",
        "ticker": "035420",
        "avg_price": 200000,
        "current_price": 184000,
        "profit_pct": -8.0,
    }


@pytest.fixture
def holding_d_no_rsi() -> dict:
    """Holding with -10% loss (category D) without low RSI."""
    return {
        "name": "LG에너지솔루션",
        "ticker": "373220",
        "avg_price": 500000,
        "current_price": 450000,
        "profit_pct": -10.0,
    }


@pytest.fixture
def tech_data_low_rsi() -> dict:
    """Technical data with oversold RSI."""
    return {
        "rsi": 25.0,
        "macd_histogram": -0.5,
        "macd_signal_cross": -1,
        "ema_50": 190000,
        "ema_200": 200000,
        "bb_pctb": 0.1,
        "sector": "IT",
    }


@pytest.fixture
def tech_data_normal() -> dict:
    """Technical data with normal RSI."""
    return {
        "rsi": 55.0,
        "macd_histogram": 0.3,
        "macd_signal_cross": 1,
        "ema_50": 105000,
        "ema_200": 100000,
        "bb_pctb": 0.6,
        "sector": "반도체",
    }


@pytest.fixture
def flow_data() -> dict:
    """Flow data with foreign buying and institutional selling."""
    return {
        "foreign_net_buy_days": 5,
        "institution_net_buy_days": -3,
    }


# ---------------------------------------------------------------------------
# _fallback_diagnosis tests
# ---------------------------------------------------------------------------

class TestFallbackDiagnosisCategoryA:
    """Category A: profit >= +5%."""

    def test_category_a_basic(self, holding_a: dict) -> None:
        """Holding with +8% should be diagnosed as category A with hold action."""
        result = _fallback_diagnosis(holding_a)
        assert result.diagnosis == "A"
        assert result.action == "hold"
        assert result.ticker == "005930"
        assert result.name == "삼성전자"
        assert result.profit_pct == 8.0

    def test_category_a_trailing_stop_below_10(self) -> None:
        """Profit between 5-10% should get 7% trailing stop."""
        holding = {
            "name": "테스트", "ticker": "000001",
            "avg_price": 10000, "current_price": 10700, "profit_pct": 7.0,
        }
        result = _fallback_diagnosis(holding)
        assert result.trailing_stop_pct == 7.0

    def test_category_a_trailing_stop_above_10(self) -> None:
        """Profit >= 10% should get 10% trailing stop."""
        holding = {
            "name": "테스트", "ticker": "000001",
            "avg_price": 10000, "current_price": 11200, "profit_pct": 12.0,
        }
        result = _fallback_diagnosis(holding)
        assert result.trailing_stop_pct == 10.0

    def test_category_a_target_and_stop(self, holding_a: dict) -> None:
        """Category A should set target +10% from current and stop based on trailing."""
        result = _fallback_diagnosis(holding_a)
        expected_target = round(75600 * 1.10, 0)
        expected_stop = round(75600 * (1 - 7.0 / 100), 0)
        expected_add = round(75600 * 0.97, 0)
        assert result.target_price == expected_target
        assert result.stop_loss == expected_stop
        assert result.add_buy_price == expected_add

    def test_category_a_message_contains_praise(self, holding_a: dict) -> None:
        """Category A message should contain praise phrase."""
        result = _fallback_diagnosis(holding_a)
        assert "잘 잡으셨습니다" in result.message


class TestFallbackDiagnosisCategoryB:
    """Category B: profit 0~5%."""

    def test_category_b_basic(self, holding_b: dict) -> None:
        """Holding with +3% should be diagnosed as category B with hold action."""
        result = _fallback_diagnosis(holding_b)
        assert result.diagnosis == "B"
        assert result.action == "hold"
        assert result.ticker == "000660"
        assert result.profit_pct == 3.0

    def test_category_b_no_add_buy(self, holding_b: dict) -> None:
        """Category B should not set add_buy_price or trailing_stop."""
        result = _fallback_diagnosis(holding_b)
        assert result.add_buy_price == 0.0
        assert result.trailing_stop_pct == 0.0

    def test_category_b_target_and_stop(self, holding_b: dict) -> None:
        """Category B target +5% from current, stop -5% from avg."""
        result = _fallback_diagnosis(holding_b)
        assert result.target_price == round(103000 * 1.05, 0)
        assert result.stop_loss == round(100000 * 0.95, 0)


class TestFallbackDiagnosisCategoryC:
    """Category C: profit -5~0%."""

    def test_category_c_basic(self, holding_c: dict) -> None:
        """Holding with -3% should be diagnosed as category C with add action."""
        result = _fallback_diagnosis(holding_c)
        assert result.diagnosis == "C"
        assert result.action == "add"
        assert result.ticker == "035720"
        assert result.profit_pct == -3.0

    def test_category_c_add_buy_price(self, holding_c: dict) -> None:
        """Category C should suggest an add_buy_price at -3% of current."""
        result = _fallback_diagnosis(holding_c)
        expected_add = round(58200 * 0.97, 0)
        assert result.add_buy_price == expected_add

    def test_category_c_stop_and_target(self, holding_c: dict) -> None:
        """Category C target +3% from avg, stop -7% from avg."""
        result = _fallback_diagnosis(holding_c)
        assert result.target_price == round(60000 * 1.03, 0)
        assert result.stop_loss == round(60000 * 0.93, 0)


class TestFallbackDiagnosisCategoryD:
    """Category D: profit below -5%."""

    def test_category_d_low_rsi_hold(
        self, holding_d_low_rsi: dict, tech_data_low_rsi: dict,
    ) -> None:
        """Category D with RSI < 30 should recommend hold (potential rebound)."""
        result = _fallback_diagnosis(holding_d_low_rsi, tech_data_low_rsi)
        assert result.diagnosis == "D"
        assert result.action == "hold"
        assert "버티세요" in result.message

    def test_category_d_no_low_rsi_stop_loss(
        self, holding_d_no_rsi: dict, tech_data_normal: dict,
    ) -> None:
        """Category D without low RSI should recommend stop_loss."""
        result = _fallback_diagnosis(holding_d_no_rsi, tech_data_normal)
        assert result.diagnosis == "D"
        assert result.action == "stop_loss"
        assert "손절" in result.message

    def test_category_d_no_tech_data_defaults_stop_loss(
        self, holding_d_no_rsi: dict,
    ) -> None:
        """Category D without tech_data should default to RSI 50 -> stop_loss."""
        result = _fallback_diagnosis(holding_d_no_rsi, None)
        assert result.diagnosis == "D"
        assert result.action == "stop_loss"

    def test_category_d_add_buy_price_zero(
        self, holding_d_low_rsi: dict, tech_data_low_rsi: dict,
    ) -> None:
        """Category D should not set add_buy_price."""
        result = _fallback_diagnosis(holding_d_low_rsi, tech_data_low_rsi)
        assert result.add_buy_price == 0.0
        assert result.trailing_stop_pct == 0.0


class TestFallbackDiagnosisProfitComputation:
    """Test that profit_pct is computed from prices when originally 0."""

    def test_profit_pct_computed_from_prices(self) -> None:
        """When profit_pct is 0, it should be computed from avg and current price."""
        holding = {
            "name": "테스트", "ticker": "999999",
            "avg_price": 50000, "current_price": 55000, "profit_pct": 0,
        }
        result = _fallback_diagnosis(holding)
        expected_pct = round((55000 - 50000) / 50000 * 100, 2)
        assert result.profit_pct == expected_pct
        assert result.diagnosis == "A"  # 10% >= 5%

    def test_profit_pct_uses_buy_price_fallback(self) -> None:
        """When avg_price is missing, buy_price should be used."""
        holding = {
            "name": "테스트", "ticker": "999998",
            "buy_price": 100000, "current_price": 97000, "pnl_pct": -3.0,
        }
        result = _fallback_diagnosis(holding)
        assert result.diagnosis == "C"
        assert result.profit_pct == -3.0


# ---------------------------------------------------------------------------
# _build_stock_prompt tests
# ---------------------------------------------------------------------------

class TestBuildStockPrompt:
    """Tests for _build_stock_prompt."""

    def test_includes_stock_info(self) -> None:
        """Prompt should include stock name, ticker, avg price, current price."""
        holding = {
            "name": "삼성전자", "ticker": "005930",
            "avg_price": 70000, "current_price": 75600, "profit_pct": 8.0,
        }
        prompt = _build_stock_prompt(holding, None, None)
        assert "삼성전자" in prompt
        assert "005930" in prompt
        assert "70,000" in prompt
        assert "75,600" in prompt
        assert "+8.00%" in prompt

    def test_includes_tech_data(self, tech_data_normal: dict) -> None:
        """Prompt should include RSI, MACD, EMA, and sector when tech_data present."""
        holding = {
            "name": "SK하이닉스", "ticker": "000660",
            "avg_price": 100000, "current_price": 103000, "profit_pct": 3.0,
        }
        prompt = _build_stock_prompt(holding, tech_data_normal, None)
        assert "RSI: 55.0" in prompt
        assert "골든크로스" in prompt  # macd_signal_cross == 1
        assert "상승추세" in prompt  # ema_50 > ema_200
        assert "반도체" in prompt  # sector

    def test_includes_flow_data(self, flow_data: dict) -> None:
        """Prompt should include foreign and institutional flow info."""
        holding = {
            "name": "카카오", "ticker": "035720",
            "avg_price": 60000, "current_price": 58200, "profit_pct": -3.0,
        }
        prompt = _build_stock_prompt(holding, None, flow_data)
        assert "외국인" in prompt
        assert "순매수 5일" in prompt
        assert "기관" in prompt
        assert "순매도 3일" in prompt

    def test_ends_with_diagnosis_request(self) -> None:
        """Prompt should end with a request for diagnosis."""
        holding = {
            "name": "테스트", "ticker": "000001",
            "avg_price": 10000, "current_price": 10500, "profit_pct": 5.0,
        }
        prompt = _build_stock_prompt(holding, None, None)
        assert prompt.strip().endswith("위 데이터를 기반으로 진단해주세요.")

    def test_computes_profit_pct_when_zero(self) -> None:
        """Prompt should compute profit_pct from prices when originally 0."""
        holding = {
            "name": "테스트", "ticker": "000001",
            "avg_price": 10000, "current_price": 10500, "profit_pct": 0,
        }
        prompt = _build_stock_prompt(holding, None, None)
        assert "+5.00%" in prompt

    def test_dead_cross_and_downtrend(self) -> None:
        """Prompt should show dead cross and downtrend when indicators warrant it."""
        holding = {
            "name": "테스트", "ticker": "000001",
            "avg_price": 10000, "current_price": 9000, "profit_pct": -10.0,
        }
        tech = {
            "rsi": 28.0,
            "macd_histogram": -0.8,
            "macd_signal_cross": -1,
            "ema_50": 9500,
            "ema_200": 10200,
            "bb_pctb": 0.05,
        }
        prompt = _build_stock_prompt(holding, tech, None)
        assert "데드크로스" in prompt
        assert "하락추세" in prompt


# ---------------------------------------------------------------------------
# _won tests
# ---------------------------------------------------------------------------

class TestWon:
    """Tests for _won price formatting."""

    def test_formats_with_comma_and_won(self) -> None:
        """Positive price should be formatted with comma separator and won suffix."""
        assert _won(58000) == "58,000원"

    def test_large_price(self) -> None:
        """Large price should be formatted properly with commas."""
        assert _won(1250000) == "1,250,000원"

    def test_zero_returns_dash(self) -> None:
        """Zero price returns dash."""
        assert _won(0) == "-"

    def test_negative_returns_dash(self) -> None:
        """Negative price returns dash."""
        assert _won(-100) == "-"


# ---------------------------------------------------------------------------
# _diagnosis_emoji tests
# ---------------------------------------------------------------------------

class TestDiagnosisEmoji:
    """Tests for _diagnosis_emoji."""

    def test_category_a_green(self) -> None:
        """Category A returns green circle."""
        assert _diagnosis_emoji("A") == "\U0001f7e2"

    def test_category_b_yellow(self) -> None:
        """Category B returns yellow circle."""
        assert _diagnosis_emoji("B") == "\U0001f7e1"

    def test_category_c_orange(self) -> None:
        """Category C returns orange circle."""
        assert _diagnosis_emoji("C") == "\U0001f7e0"

    def test_category_d_red(self) -> None:
        """Category D returns red circle."""
        assert _diagnosis_emoji("D") == "\U0001f534"

    def test_unknown_returns_white(self) -> None:
        """Unknown category returns white circle."""
        assert _diagnosis_emoji("Z") == "\u26aa"


# ---------------------------------------------------------------------------
# _action_label tests
# ---------------------------------------------------------------------------

class TestActionLabel:
    """Tests for _action_label."""

    def test_hold(self) -> None:
        assert _action_label("hold") == "보유 유지"

    def test_add(self) -> None:
        assert _action_label("add") == "추가 매수 고려"

    def test_partial_sell(self) -> None:
        assert _action_label("partial_sell") == "일부 익절"

    def test_stop_loss(self) -> None:
        assert _action_label("stop_loss") == "손절 고려"

    def test_unknown_returns_raw(self) -> None:
        """Unknown action returns the original string."""
        assert _action_label("mystery") == "mystery"


# ---------------------------------------------------------------------------
# format_diagnosis_report tests
# ---------------------------------------------------------------------------

class TestFormatDiagnosisReport:
    """Tests for format_diagnosis_report."""

    def test_empty_list(self) -> None:
        """Empty holdings list returns a message about no stocks to diagnose."""
        report = format_diagnosis_report([])
        assert "진단할 보유 종목이 없습니다" in report
        assert "주호님" in report

    def test_mixed_categories(self) -> None:
        """Report with multiple categories groups them correctly."""
        h_a = {"name": "삼성전자", "ticker": "005930", "avg_price": 70000, "current_price": 75600}
        d_a = DiagnosisResult(
            ticker="005930", name="삼성전자", diagnosis="A", action="hold",
            message="잘 잡으셨습니다!", target_price=83160, stop_loss=70308,
            add_buy_price=73332, trailing_stop_pct=7.0, profit_pct=8.0,
        )
        h_d = {"name": "네이버", "ticker": "035420", "avg_price": 200000, "current_price": 184000}
        d_d = DiagnosisResult(
            ticker="035420", name="네이버", diagnosis="D", action="stop_loss",
            message="손절을 고려하세요.", target_price=200000, stop_loss=178480,
            profit_pct=-8.0,
        )

        report = format_diagnosis_report([(h_a, d_a), (h_d, d_d)])

        # Category A section present
        assert "A등급" in report
        assert "삼성전자" in report
        assert "+8.0%" in report

        # Category D section present
        assert "D등급" in report
        assert "네이버" in report
        assert "-8.0%" in report

        # Action labels present
        assert "보유 유지" in report
        assert "손절 고려" in report

        # Target and stop prices present
        assert "83,160원" in report  # target
        assert "70,308원" in report  # stop_loss

        # Trailing stop present for category A
        assert "트레일링" in report

    def test_with_summary(self) -> None:
        """Report with summary dict includes portfolio summary section."""
        h = {"name": "테스트", "ticker": "000001", "avg_price": 10000, "current_price": 10500}
        d = DiagnosisResult(
            ticker="000001", name="테스트", diagnosis="B", action="hold",
            message="보유하세요.", profit_pct=5.0,
        )
        summary = {
            "total_pnl": 3.5,
            "total_count": 5,
            "profit_count": 3,
            "loss_count": 2,
            "best_stock": "삼성전자 +8.0%",
            "worst_stock": "네이버 -8.0%",
        }

        report = format_diagnosis_report([(h, d)], summary=summary)

        assert "포트폴리오 요약" in report
        assert "+3.5%" in report
        assert "5종목" in report
        assert "수익 3" in report
        assert "손실 2" in report
        assert "삼성전자 +8.0%" in report
        assert "네이버 -8.0%" in report

    def test_add_buy_price_in_report(self) -> None:
        """Report should show add_buy_price for category C holdings."""
        h = {"name": "카카오", "ticker": "035720", "avg_price": 60000, "current_price": 58200}
        d = DiagnosisResult(
            ticker="035720", name="카카오", diagnosis="C", action="add",
            message="추가 매수 고려", add_buy_price=56454, profit_pct=-3.0,
            target_price=61800, stop_loss=55800,
        )
        report = format_diagnosis_report([(h, d)])
        assert "추가매수" in report
        assert "56,454원" in report

    def test_footer_present(self) -> None:
        """Empty report should contain info message."""
        report = format_diagnosis_report([])
        assert "진단할 보유 종목이 없습니다" in report

    def test_no_bold_markdown(self) -> None:
        """Report should not contain bold markdown (**) formatting."""
        h = {"name": "테스트", "ticker": "000001", "avg_price": 10000, "current_price": 10500}
        d = DiagnosisResult(
            ticker="000001", name="테스트", diagnosis="A", action="hold",
            message="잘하셨습니다.", profit_pct=8.0, target_price=11550,
            stop_loss=9765, add_buy_price=10185, trailing_stop_pct=7.0,
        )
        report = format_diagnosis_report([(h, d)])
        assert "**" not in report
