"""Tests for the investment-horizon-based diagnosis module."""

from __future__ import annotations

import pytest

from kstock.bot.horizon_diagnosis import (
    HORIZON_CONFIG,
    HORIZON_PROMPTS,
    HorizonDiagnosisResult,
    MARGIN_KEYWORDS,
    _margin_warning,
    build_horizon_prompt,
    detect_margin_purchase,
    fallback_diagnosis,
    format_horizon_report,
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
def holding_d() -> dict:
    """Holding with -10% loss (category D)."""
    return {
        "name": "네이버",
        "ticker": "035420",
        "avg_price": 200000,
        "current_price": 180000,
        "profit_pct": -10.0,
    }


@pytest.fixture
def holding_margin() -> dict:
    """Holding purchased on margin (신용)."""
    return {
        "name": "에코프로",
        "ticker": "086520",
        "avg_price": 90700,
        "current_price": 170900,
        "profit_pct": 88.01,
        "purchase_type": "신용",
    }


@pytest.fixture
def holding_yuyung() -> dict:
    """Holding purchased with 유융 margin."""
    return {
        "name": "현대차",
        "ticker": "005380",
        "avg_price": 200000,
        "current_price": 210000,
        "profit_pct": 5.0,
        "purchase_type": "유융",
    }


@pytest.fixture
def holding_cash() -> dict:
    """Holding purchased with cash (현금)."""
    return {
        "name": "LG에너지솔루션",
        "ticker": "373220",
        "avg_price": 500000,
        "current_price": 520000,
        "profit_pct": 4.0,
        "purchase_type": "현금",
    }


# ---------------------------------------------------------------------------
# TestHorizonConfig
# ---------------------------------------------------------------------------

class TestHorizonConfig:
    """Verify the 4 horizon configurations."""

    def test_has_four_horizons(self):
        assert len(HORIZON_CONFIG) == 4
        assert set(HORIZON_CONFIG.keys()) == {"danta", "dangi", "junggi", "janggi"}

    def test_danta_config(self):
        cfg = HORIZON_CONFIG["danta"]
        assert cfg["label"] == "단타 (1~5일)"
        assert cfg["stop"] == -2
        assert cfg["target"] == 5
        assert cfg["trailing"] == -3

    def test_dangi_config(self):
        cfg = HORIZON_CONFIG["dangi"]
        assert cfg["label"] == "단기 (1~4주)"
        assert cfg["stop"] == -5
        assert cfg["target"] == 10
        assert cfg["trailing"] == -5

    def test_junggi_config(self):
        cfg = HORIZON_CONFIG["junggi"]
        assert cfg["label"] == "중기 (1~6개월)"
        assert cfg["stop"] == -8
        assert cfg["target"] == 20
        assert cfg["trailing"] == -8

    def test_janggi_config(self):
        cfg = HORIZON_CONFIG["janggi"]
        assert cfg["label"] == "장기 (6개월+)"
        assert cfg["stop"] == -15
        assert cfg["target"] == 50
        assert cfg["trailing"] == -15

    def test_each_horizon_has_prompt(self):
        for hz in HORIZON_CONFIG:
            assert hz in HORIZON_PROMPTS, f"Missing prompt for {hz}"


# ---------------------------------------------------------------------------
# TestDetectMarginPurchase
# ---------------------------------------------------------------------------

class TestDetectMarginPurchase:
    """Detect margin purchase from purchase_type and name."""

    def test_detect_sinnyong(self, holding_margin):
        is_margin, mtype = detect_margin_purchase(holding_margin)
        assert is_margin is True
        assert mtype == "신용"

    def test_detect_yuyung(self, holding_yuyung):
        is_margin, mtype = detect_margin_purchase(holding_yuyung)
        assert is_margin is True
        assert mtype == "유융"

    def test_detect_yuong(self):
        h = {"name": "테스트", "purchase_type": "유옹"}
        is_margin, mtype = detect_margin_purchase(h)
        assert is_margin is True
        assert mtype == "유옹"

    def test_detect_dambo(self):
        h = {"name": "테스트", "purchase_type": "담보"}
        is_margin, mtype = detect_margin_purchase(h)
        assert is_margin is True
        assert mtype == "담보"

    def test_cash_not_margin(self, holding_cash):
        is_margin, mtype = detect_margin_purchase(holding_cash)
        assert is_margin is False
        assert mtype is None

    def test_empty_purchase_type(self):
        h = {"name": "테스트", "purchase_type": ""}
        is_margin, mtype = detect_margin_purchase(h)
        assert is_margin is False
        assert mtype is None

    def test_no_purchase_type(self):
        h = {"name": "삼성전자"}
        is_margin, mtype = detect_margin_purchase(h)
        assert is_margin is False
        assert mtype is None

    def test_margin_in_name_fallback(self):
        """If purchase_type is missing but name contains 신용, still detect."""
        h = {"name": "삼성전자 신용", "purchase_type": ""}
        is_margin, mtype = detect_margin_purchase(h)
        assert is_margin is True
        assert mtype == "신용"

    def test_margin_keywords_complete(self):
        assert MARGIN_KEYWORDS == {"신용", "유융", "유옹", "담보"}


# ---------------------------------------------------------------------------
# TestFallbackDiagnosis - Danta
# ---------------------------------------------------------------------------

class TestFallbackDiagnosisDanta:
    """Rule-based fallback with danta (1~5일) parameters."""

    def test_category_a(self, holding_a):
        r = fallback_diagnosis(holding_a, "danta")
        assert r.horizon == "danta"
        assert r.diagnosis == "A"
        assert r.action == "hold"
        assert r.trailing_stop_pct == 3.0  # danta trailing = -3
        assert "단타 (1~5일)" in r.message
        assert "주호님" in r.message
        assert "**" not in r.message

    def test_category_b(self, holding_b):
        r = fallback_diagnosis(holding_b, "danta")
        assert r.diagnosis == "B"
        assert r.action == "hold"
        assert "단타 (1~5일)" in r.message

    def test_category_c(self):
        """Profit -1% is within danta stop (-2%), so should be C."""
        h = {
            "name": "카카오", "ticker": "035720",
            "avg_price": 60000, "current_price": 59400, "profit_pct": -1.0,
        }
        r = fallback_diagnosis(h, "danta")
        assert r.diagnosis == "C"
        assert r.action == "add"
        assert "단타 (1~5일)" in r.message

    def test_category_d_beyond_danta_stop(self, holding_c):
        """Profit -3% exceeds danta stop (-2%), so should be D."""
        r = fallback_diagnosis(holding_c, "danta")
        assert r.diagnosis == "D"
        assert "단타 (1~5일)" in r.message

    def test_category_d(self, holding_d):
        r = fallback_diagnosis(holding_d, "danta")
        assert r.diagnosis == "D"
        assert "단타 (1~5일)" in r.message


# ---------------------------------------------------------------------------
# TestFallbackDiagnosis - Dangi
# ---------------------------------------------------------------------------

class TestFallbackDiagnosisDangi:
    """Rule-based fallback with dangi (1~4주) parameters."""

    def test_category_a(self, holding_a):
        r = fallback_diagnosis(holding_a, "dangi")
        assert r.horizon == "dangi"
        assert r.diagnosis == "A"
        assert r.trailing_stop_pct == 5.0  # dangi trailing = -5
        assert "단기 (1~4주)" in r.message

    def test_category_b(self, holding_b):
        r = fallback_diagnosis(holding_b, "dangi")
        assert r.diagnosis == "B"
        assert "단기 (1~4주)" in r.message

    def test_category_c_within_stop(self, holding_c):
        """Profit -3% is within dangi stop (-5%), so should be C."""
        r = fallback_diagnosis(holding_c, "dangi")
        assert r.diagnosis == "C"

    def test_category_d(self, holding_d):
        r = fallback_diagnosis(holding_d, "dangi")
        assert r.diagnosis == "D"
        assert "단기 (1~4주)" in r.message


# ---------------------------------------------------------------------------
# TestFallbackDiagnosis - Junggi
# ---------------------------------------------------------------------------

class TestFallbackDiagnosisJunggi:
    """Rule-based fallback with junggi (1~6개월) parameters."""

    def test_category_a(self, holding_a):
        r = fallback_diagnosis(holding_a, "junggi")
        assert r.horizon == "junggi"
        assert r.trailing_stop_pct == 8.0  # junggi trailing = -8
        assert "중기 (1~6개월)" in r.message

    def test_category_c_within_junggi_stop(self):
        """Profit -6% is within junggi stop (-8%), so should be C."""
        h = {
            "name": "카카오", "ticker": "035720",
            "avg_price": 60000, "current_price": 56400, "profit_pct": -6.0,
        }
        r = fallback_diagnosis(h, "junggi")
        assert r.diagnosis == "C"

    def test_category_d_beyond_junggi_stop(self):
        """Profit -10% is beyond junggi stop (-8%), so should be D."""
        h = {
            "name": "네이버", "ticker": "035420",
            "avg_price": 200000, "current_price": 180000, "profit_pct": -10.0,
        }
        r = fallback_diagnosis(h, "junggi")
        assert r.diagnosis == "D"


# ---------------------------------------------------------------------------
# TestFallbackDiagnosis - Janggi
# ---------------------------------------------------------------------------

class TestFallbackDiagnosisJanggi:
    """Rule-based fallback with janggi (6개월+) parameters."""

    def test_category_a(self, holding_a):
        r = fallback_diagnosis(holding_a, "janggi")
        assert r.horizon == "janggi"
        assert r.trailing_stop_pct == 15.0  # janggi trailing = -15
        assert "장기 (6개월+)" in r.message

    def test_high_profit(self):
        """Very high profit stock in janggi: should still be A with large target."""
        h = {
            "name": "에코프로", "ticker": "086520",
            "avg_price": 90700, "current_price": 170900, "profit_pct": 88.01,
        }
        r = fallback_diagnosis(h, "janggi")
        assert r.diagnosis == "A"
        assert r.target_price > 170900  # target should be above current

    def test_category_c_wide_tolerance(self):
        """Profit -10% is within janggi stop (-15%), so should be C."""
        h = {
            "name": "네이버", "ticker": "035420",
            "avg_price": 200000, "current_price": 180000, "profit_pct": -10.0,
        }
        r = fallback_diagnosis(h, "janggi")
        assert r.diagnosis == "C"

    def test_category_d(self):
        """Profit -20% exceeds janggi stop (-15%)."""
        h = {
            "name": "LG에너지솔루션", "ticker": "373220",
            "avg_price": 500000, "current_price": 400000, "profit_pct": -20.0,
        }
        r = fallback_diagnosis(h, "janggi")
        assert r.diagnosis == "D"


# ---------------------------------------------------------------------------
# TestFallbackDiagnosis - RSI override
# ---------------------------------------------------------------------------

class TestFallbackDiagnosisRSI:
    """D-category with low RSI should suggest holding."""

    def test_d_low_rsi_hold(self, holding_d):
        tech = {"rsi": 25.0}
        r = fallback_diagnosis(holding_d, "dangi", tech_data=tech)
        assert r.diagnosis == "D"
        assert r.action == "hold"
        assert "과매도" in r.message

    def test_d_high_rsi_stop_loss(self, holding_d):
        tech = {"rsi": 55.0}
        r = fallback_diagnosis(holding_d, "dangi", tech_data=tech)
        assert r.diagnosis == "D"
        assert r.action == "stop_loss"


# ---------------------------------------------------------------------------
# TestBuildHorizonPrompt
# ---------------------------------------------------------------------------

class TestBuildHorizonPrompt:
    """Verify prompts include correct horizon-specific content."""

    def test_danta_prompt_has_danta_context(self, holding_a):
        prompt = build_horizon_prompt(holding_a, "danta")
        assert "단타 (1~5일)" in prompt
        assert "RSI" in prompt  # danta context mentions RSI
        assert "삼성전자" in prompt
        assert "005930" in prompt

    def test_dangi_prompt_has_dangi_context(self, holding_b):
        prompt = build_horizon_prompt(holding_b, "dangi")
        assert "단기 (1~4주)" in prompt
        assert "수급" in prompt  # dangi mentions 수급

    def test_junggi_prompt_has_junggi_context(self, holding_b):
        prompt = build_horizon_prompt(holding_b, "junggi")
        assert "중기 (1~6개월)" in prompt
        assert "컨센서스" in prompt

    def test_janggi_prompt_has_janggi_context(self, holding_a):
        prompt = build_horizon_prompt(holding_a, "janggi")
        assert "장기 (6개월+)" in prompt
        assert "재무" in prompt
        assert "산업 전망" in prompt

    def test_prompt_includes_stop_target(self, holding_a):
        prompt = build_horizon_prompt(holding_a, "danta")
        assert "-2%" in prompt  # danta stop
        assert "+5%" in prompt  # danta target

    def test_prompt_includes_extra_data(self, holding_a):
        extra = {"rsi": 65.0, "bb_pctb": 0.8}
        prompt = build_horizon_prompt(holding_a, "danta", extra_data=extra)
        assert "RSI: 65.0" in prompt
        assert "볼린저밴드 %B: 0.80" in prompt

    def test_prompt_includes_consensus(self, holding_b):
        extra = {"avg_target_price": 150000, "upside_pct": 45.6}
        prompt = build_horizon_prompt(holding_b, "junggi", extra_data=extra)
        assert "150,000" in prompt
        assert "45.6" in prompt

    def test_prompt_includes_financials(self, holding_a):
        extra = {"revenue_cagr": 32.0, "op_margin": 15.5, "roe": 22.0}
        prompt = build_horizon_prompt(holding_a, "janggi", extra_data=extra)
        assert "매출 CAGR" in prompt
        assert "32.0" in prompt
        assert "영업이익률" in prompt


# ---------------------------------------------------------------------------
# TestFormatReport
# ---------------------------------------------------------------------------

class TestFormatReport:
    """Verify formatted report for Telegram."""

    def test_no_bold(self, holding_a):
        r = fallback_diagnosis(holding_a, "janggi")
        report = format_horizon_report([r])
        assert "**" not in report

    def test_contains_username(self, holding_a):
        r = fallback_diagnosis(holding_a, "danta")
        report = format_horizon_report([r])
        assert "주호님" in report

    def test_contains_horizon_label(self, holding_a):
        r = fallback_diagnosis(holding_a, "danta")
        report = format_horizon_report([r])
        assert "단타 (1~5일)" in report

    def test_contains_stock_name(self, holding_a):
        r = fallback_diagnosis(holding_a, "danta")
        report = format_horizon_report([r])
        assert "삼성전자" in report

    def test_empty_results(self):
        report = format_horizon_report([])
        assert "주호님" in report
        assert "진단할 보유 종목이 없습니다" in report

    def test_multiple_horizons(self, holding_a, holding_b, holding_c):
        results = [
            fallback_diagnosis(holding_a, "janggi"),
            fallback_diagnosis(holding_b, "dangi"),
            fallback_diagnosis(holding_c, "danta"),
        ]
        report = format_horizon_report(results)
        assert "장기 (6개월+)" in report
        assert "단기 (1~4주)" in report
        assert "단타 (1~5일)" in report

    def test_margin_warning_in_report(self, holding_margin):
        r = fallback_diagnosis(holding_margin, "dangi")
        report = format_horizon_report([r])
        assert "만기" in report or "신용" in r.margin_warning


# ---------------------------------------------------------------------------
# TestMarginWarning
# ---------------------------------------------------------------------------

class TestMarginWarning:
    """Margin purchase warning logic."""

    def test_margin_detected_in_diagnosis(self, holding_margin):
        r = fallback_diagnosis(holding_margin, "dangi")
        assert r.is_margin is True
        assert r.margin_type == "신용"
        assert r.margin_warning != ""
        assert "만기" in r.margin_warning

    def test_janggi_margin_strong_warning(self, holding_margin):
        """장기 + 신용 = 강한 경고."""
        r = fallback_diagnosis(holding_margin, "janggi")
        assert r.is_margin is True
        assert "양립 불가" in r.margin_warning
        assert "현금 전환" in r.margin_warning

    def test_danta_margin_warning(self, holding_margin):
        """단타 + 신용 = 일반 경고."""
        r = fallback_diagnosis(holding_margin, "danta")
        assert r.is_margin is True
        assert "만기" in r.margin_warning
        assert "양립 불가" not in r.margin_warning

    def test_cash_no_margin_warning(self, holding_cash):
        r = fallback_diagnosis(holding_cash, "janggi")
        assert r.is_margin is False
        assert r.margin_warning == ""

    def test_margin_warning_helper_janggi(self):
        msg = _margin_warning("janggi", "신용")
        assert "양립 불가" in msg

    def test_margin_warning_helper_other(self):
        msg = _margin_warning("dangi", "유융")
        assert "유융" in msg
        assert "만기" in msg


# ---------------------------------------------------------------------------
# TestHorizonDiagnosisResult
# ---------------------------------------------------------------------------

class TestHorizonDiagnosisResult:
    """Dataclass field defaults."""

    def test_default_values(self):
        r = HorizonDiagnosisResult()
        assert r.ticker == ""
        assert r.name == ""
        assert r.horizon == "default"
        assert r.diagnosis == "B"
        assert r.action == "hold"
        assert r.is_margin is False
        assert r.margin_type is None
        assert r.margin_warning == ""


# ---------------------------------------------------------------------------
# TestProfitPctComputation
# ---------------------------------------------------------------------------

class TestProfitPctComputation:
    """Profit pct is computed from avg_price/current_price when missing."""

    def test_auto_compute_profit_pct(self):
        h = {
            "name": "테스트", "ticker": "000000",
            "avg_price": 10000, "current_price": 11000, "profit_pct": 0,
        }
        r = fallback_diagnosis(h, "danta")
        assert r.profit_pct == pytest.approx(10.0, abs=0.1)
        assert r.diagnosis == "A"


# ---------------------------------------------------------------------------
# TestDBIntegration (lightweight schema test)
# ---------------------------------------------------------------------------

class TestDBIntegration:
    """Verify investment_horizons table and screenshot_holdings migration."""

    @pytest.fixture
    def store(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        return SQLiteStore(db_path=tmp_path / "test.db")

    def test_add_and_get_investment_horizon(self, store):
        hid = store.add_investment_horizon(
            ticker="005930", name="삼성전자", horizon="janggi",
            screenshot_id=1, stop_pct=-15, target_pct=50, trailing_pct=-15,
            is_margin=0, margin_type=None,
            diagnosis="A", diagnosis_action="hold", diagnosis_msg="잘 잡으셨습니다",
        )
        assert hid > 0
        row = store.get_investment_horizon("005930")
        assert row is not None
        assert row["horizon"] == "janggi"
        assert row["diagnosis"] == "A"

    def test_get_horizons_for_screenshot(self, store):
        store.add_investment_horizon(
            ticker="005930", name="삼성전자", horizon="janggi", screenshot_id=42,
        )
        store.add_investment_horizon(
            ticker="000660", name="SK하이닉스", horizon="dangi", screenshot_id=42,
        )
        rows = store.get_horizons_for_screenshot(42)
        assert len(rows) == 2

    def test_screenshot_holding_margin_columns(self, store):
        sid = store.add_screenshot(total_eval=1000000)
        hid = store.add_screenshot_holding(
            screenshot_id=sid, ticker="005930", name="삼성전자",
            is_margin=1, margin_type="신용",
        )
        assert hid > 0
        rows = store.get_screenshot_holdings(sid)
        assert len(rows) == 1
        assert rows[0]["is_margin"] == 1
        assert rows[0]["margin_type"] == "신용"

    def test_portfolio_horizon_save_and_retrieve(self, store):
        store.upsert_portfolio_horizon("086520", "에코프로", "janggi")
        row = store.get_portfolio_horizon("086520")
        assert row is not None
        assert row["horizon"] == "janggi"
        assert row["name"] == "에코프로"

    def test_portfolio_horizon_update(self, store):
        store.upsert_portfolio_horizon("086520", "에코프로", "dangi")
        store.upsert_portfolio_horizon("086520", "에코프로", "janggi")
        row = store.get_portfolio_horizon("086520")
        assert row["horizon"] == "janggi"
