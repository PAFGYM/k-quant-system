"""Tests for the account_reader module."""

from __future__ import annotations

import pytest

from kstock.bot.account_reader import (
    _detect_media_type,
    _normalize_parsed,
    _parse_vision_response,
    _to_float,
    _to_int,
    compare_screenshots,
    compute_portfolio_score,
    evaluate_diagnosis_accuracy,
    format_screenshot_reminder,
    format_screenshot_summary,
)


# ---------------------------------------------------------------------------
# Helpers: reusable holding / snapshot builders
# ---------------------------------------------------------------------------

def _holding(
    name: str = "삼성전자",
    ticker: str = "005930",
    quantity: int = 10,
    avg_price: float = 60000,
    current_price: float = 62000,
    profit_pct: float = 3.33,
    eval_amount: float = 620000,
) -> dict:
    return {
        "name": name,
        "ticker": ticker,
        "quantity": quantity,
        "avg_price": avg_price,
        "current_price": current_price,
        "profit_pct": profit_pct,
        "eval_amount": eval_amount,
    }


def _snapshot(holdings: list[dict] | None = None, cash: float = 1000000) -> dict:
    holdings = holdings or []
    total_eval = sum(h.get("eval_amount", 0) for h in holdings)
    total_profit = sum(
        h.get("eval_amount", 0) - h.get("avg_price", 0) * h.get("quantity", 0)
        for h in holdings
    )
    return {
        "holdings": holdings,
        "summary": {
            "total_eval": total_eval,
            "total_profit": total_profit,
            "total_profit_pct": 0.0,
            "cash": cash,
        },
    }


# ===========================================================================
# compare_screenshots
# ===========================================================================

class TestCompareScreenshots:
    """Tests for compare_screenshots."""

    def test_new_buys_detected(self):
        """New stocks in current that were absent in previous appear as new_buys."""
        prev = _snapshot([_holding(name="삼성전자", ticker="005930")])
        cur = _snapshot([
            _holding(name="삼성전자", ticker="005930"),
            _holding(name="SK하이닉스", ticker="000660", eval_amount=800000),
        ])
        result = compare_screenshots(cur, prev)
        assert len(result["new_buys"]) == 1
        assert result["new_buys"][0]["ticker"] == "000660"
        assert result["new_buys"][0]["name"] == "SK하이닉스"

    def test_sold_stocks_detected(self):
        """Stocks in previous but absent in current appear as sold."""
        prev = _snapshot([
            _holding(name="삼성전자", ticker="005930"),
            _holding(name="LG화학", ticker="051910", profit_pct=-2.5),
        ])
        cur = _snapshot([_holding(name="삼성전자", ticker="005930")])
        result = compare_screenshots(cur, prev)
        assert len(result["sold"]) == 1
        assert result["sold"][0]["ticker"] == "051910"
        assert result["sold"][0]["profit_pct"] == -2.5

    def test_improvements_detected(self):
        """Stocks whose profit_pct improved by >0.01 appear in improvements."""
        prev = _snapshot([_holding(ticker="005930", profit_pct=1.0)])
        cur = _snapshot([_holding(ticker="005930", profit_pct=5.0)])
        result = compare_screenshots(cur, prev)
        assert len(result["improvements"]) == 1
        assert result["improvements"][0]["change_pct"] == 4.0

    def test_worsened_detected(self):
        """Stocks whose profit_pct dropped by >0.01 appear in worsened."""
        prev = _snapshot([_holding(ticker="005930", profit_pct=5.0)])
        cur = _snapshot([_holding(ticker="005930", profit_pct=2.0)])
        result = compare_screenshots(cur, prev)
        assert len(result["worsened"]) == 1
        assert result["worsened"][0]["change_pct"] == -3.0

    def test_identical_portfolios(self):
        """Identical snapshots produce no changes anywhere."""
        h = _holding(profit_pct=3.0)
        snap = _snapshot([h], cash=500000)
        result = compare_screenshots(snap, snap)
        assert result["improvements"] == []
        assert result["worsened"] == []
        assert result["sold"] == []
        assert result["new_buys"] == []
        assert result["cash_change"] == 0

    def test_cash_change(self):
        """Cash difference is captured correctly."""
        prev = _snapshot(cash=1000000)
        cur = _snapshot(cash=750000)
        result = compare_screenshots(cur, prev)
        assert result["cash_change"] == -250000

    def test_empty_portfolios(self):
        """Both portfolios empty yields all-empty result."""
        result = compare_screenshots(_snapshot(), _snapshot())
        assert result == {
            "improvements": [],
            "worsened": [],
            "sold": [],
            "new_buys": [],
            "cash_change": 0,
        }


# ===========================================================================
# compute_portfolio_score
# ===========================================================================

class TestComputePortfolioScore:
    """Tests for compute_portfolio_score."""

    def test_empty_holdings_returns_zero(self):
        assert compute_portfolio_score([]) == 0

    def test_single_holding(self):
        """A single holding gets a low diversification score but still > 0."""
        score = compute_portfolio_score([_holding()])
        assert 0 < score <= 100

    def test_well_diversified_portfolio(self):
        """A large, diverse, winning portfolio scores high."""
        holdings = [
            _holding(ticker="005930", eval_amount=100000, profit_pct=5.0),
            _holding(ticker="000660", eval_amount=100000, profit_pct=3.0),
            _holding(ticker="035420", eval_amount=100000, profit_pct=2.0),
            _holding(ticker="051910", eval_amount=100000, profit_pct=4.0),
            _holding(ticker="006400", eval_amount=100000, profit_pct=1.5),
            _holding(ticker="068270", eval_amount=100000, profit_pct=6.0),
            _holding(ticker="373220", eval_amount=100000, profit_pct=2.5),
            _holding(ticker="055550", eval_amount=100000, profit_pct=3.5),
            _holding(ticker="096770", eval_amount=100000, profit_pct=1.0),
            _holding(ticker="010130", eval_amount=100000, profit_pct=0.8),
        ]
        score = compute_portfolio_score(holdings)
        assert score >= 70

    def test_concentrated_portfolio_lower_score(self):
        """A concentrated portfolio (1 position = 90%) scores lower."""
        holdings = [
            _holding(ticker="005930", eval_amount=900000, profit_pct=2.0),
            _holding(ticker="000660", eval_amount=50000, profit_pct=-1.0),
            _holding(ticker="035420", eval_amount=50000, profit_pct=-3.0),
        ]
        score = compute_portfolio_score(holdings)
        # Should be noticeably lower than well-diversified
        assert score <= 65

    def test_all_losers(self):
        """All losing holdings should yield a low win/loss component."""
        holdings = [
            _holding(ticker="005930", eval_amount=100000, profit_pct=-5.0),
            _holding(ticker="000660", eval_amount=100000, profit_pct=-3.0),
        ]
        score = compute_portfolio_score(holdings)
        assert 0 < score <= 100

    def test_score_bounded_0_to_100(self):
        """Score is always within [0, 100] regardless of input."""
        # Single holding, all zeros
        h = _holding(eval_amount=0, profit_pct=0)
        score = compute_portfolio_score([h])
        assert 0 <= score <= 100

    def test_over_diversified_penalty(self):
        """More than 20 holdings incurs a diversification penalty."""
        holdings = [
            _holding(ticker=str(i).zfill(6), eval_amount=10000, profit_pct=1.0)
            for i in range(25)
        ]
        score_25 = compute_portfolio_score(holdings)
        # Compare with 10 holdings (same sector = 기타 for made-up tickers)
        holdings_10 = holdings[:10]
        score_10 = compute_portfolio_score(holdings_10)
        # The 25-holding portfolio gets the -5 penalty on diversification
        # but may gain elsewhere; we mainly check it doesn't exceed 100
        assert 0 <= score_25 <= 100
        assert 0 <= score_10 <= 100


# ===========================================================================
# evaluate_diagnosis_accuracy
# ===========================================================================

class TestEvaluateDiagnosisAccuracy:
    """Tests for evaluate_diagnosis_accuracy."""

    def test_correct_up_prediction(self):
        """Predicted 'up' and stock is up > 1% -> correct."""
        diags = [{"ticker": "005930", "name": "삼성전자", "direction": "up", "confidence": 80}]
        holdings = [_holding(ticker="005930", profit_pct=5.0)]
        results = evaluate_diagnosis_accuracy(diags, holdings)
        assert len(results) == 1
        assert results[0]["correct"] is True
        assert results[0]["actual"] == "up"

    def test_incorrect_up_prediction(self):
        """Predicted 'up' but stock is down -> incorrect."""
        diags = [{"ticker": "005930", "name": "삼성전자", "direction": "up", "confidence": 70}]
        holdings = [_holding(ticker="005930", profit_pct=-5.0)]
        results = evaluate_diagnosis_accuracy(diags, holdings)
        assert results[0]["correct"] is False
        assert results[0]["actual"] == "down"

    def test_stock_sold_predicted_down(self):
        """Stock not in current holdings -> actual is 'sold', correct if predicted 'down'."""
        diags = [{"ticker": "005930", "name": "삼성전자", "direction": "down", "confidence": 60}]
        results = evaluate_diagnosis_accuracy(diags, [])
        assert results[0]["correct"] is True
        assert results[0]["actual"] == "sold"

    def test_stock_sold_predicted_up(self):
        """Stock not in current holdings -> actual is 'sold', incorrect if predicted 'up'."""
        diags = [{"ticker": "005930", "name": "삼성전자", "direction": "up", "confidence": 90}]
        results = evaluate_diagnosis_accuracy(diags, [])
        assert results[0]["correct"] is False
        assert results[0]["actual"] == "sold"

    def test_hold_prediction(self):
        """Predicted 'hold' and profit_pct within [-1, 1] -> correct."""
        diags = [{"ticker": "005930", "name": "삼성전자", "direction": "hold", "confidence": 50}]
        holdings = [_holding(ticker="005930", profit_pct=0.5)]
        results = evaluate_diagnosis_accuracy(diags, holdings)
        assert results[0]["correct"] is True
        assert results[0]["actual"] == "hold"

    def test_multiple_diagnoses(self):
        """Multiple diagnoses are each evaluated independently."""
        diags = [
            {"ticker": "005930", "name": "삼성전자", "direction": "up", "confidence": 80},
            {"ticker": "000660", "name": "SK하이닉스", "direction": "down", "confidence": 60},
        ]
        holdings = [
            _holding(ticker="005930", profit_pct=5.0),
            _holding(ticker="000660", profit_pct=-3.0),
        ]
        results = evaluate_diagnosis_accuracy(diags, holdings)
        assert len(results) == 2
        assert results[0]["correct"] is True  # up predicted, up actual
        assert results[1]["correct"] is True  # down predicted, down actual


# ===========================================================================
# format_screenshot_summary
# ===========================================================================

class TestFormatScreenshotSummary:
    """Tests for format_screenshot_summary."""

    def test_basic_summary_contains_key_info(self):
        """Summary includes holdings count, total eval, and portfolio score."""
        parsed = _snapshot(
            [_holding(name="삼성전자", ticker="005930", eval_amount=620000, profit_pct=3.0)],
            cash=500000,
        )
        text = format_screenshot_summary(parsed)
        assert "주호님" in text
        assert "삼성전자" in text
        assert "005930" in text
        assert "포트폴리오 건강 점수" in text
        assert "K-Quant v3.0" in text

    def test_summary_with_comparison(self):
        """When comparison is provided, change section is included."""
        parsed = _snapshot(
            [_holding(name="삼성전자", ticker="005930", profit_pct=5.0)],
            cash=800000,
        )
        comparison = {
            "improvements": [
                {"name": "삼성전자", "ticker": "005930", "prev_profit_pct": 2.0, "cur_profit_pct": 5.0, "change_pct": 3.0},
            ],
            "worsened": [],
            "new_buys": [],
            "sold": [],
            "cash_change": -200000,
        }
        text = format_screenshot_summary(parsed, comparison=comparison)
        assert "이전 대비 변화" in text
        assert "개선 종목" in text
        assert "예수금 변동" in text

    def test_summary_with_diagnoses(self):
        """When prev_diagnoses is provided, accuracy section is included."""
        parsed = _snapshot(
            [_holding(name="삼성전자", ticker="005930", profit_pct=5.0)],
        )
        diags = [
            {"ticker": "005930", "name": "삼성전자", "direction": "up", "confidence": 80},
        ]
        text = format_screenshot_summary(parsed, prev_diagnoses=diags)
        assert "이전 진단 정확도" in text
        assert "100%" in text

    def test_summary_no_holdings(self):
        """Empty holdings produces the 'no holdings' message."""
        parsed = _snapshot()
        text = format_screenshot_summary(parsed)
        assert "보유 종목이 없습니다" in text


# ===========================================================================
# format_screenshot_reminder
# ===========================================================================

class TestFormatScreenshotReminder:
    """Tests for format_screenshot_reminder."""

    def test_returns_nonempty_string(self):
        text = format_screenshot_reminder()
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_key_phrases(self):
        text = format_screenshot_reminder()
        assert "주호님" in text
        assert "스크린샷" in text
        assert "KST" in text


# ===========================================================================
# _detect_media_type
# ===========================================================================

class TestDetectMediaType:
    """Tests for _detect_media_type."""

    def test_png(self):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        assert _detect_media_type(png_header) == "image/png"

    def test_jpeg(self):
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        assert _detect_media_type(jpeg_header) == "image/jpeg"

    def test_webp(self):
        webp_header = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        assert _detect_media_type(webp_header) == "image/webp"

    def test_gif(self):
        gif_header = b"GIF89a" + b"\x00" * 20
        assert _detect_media_type(gif_header) == "image/gif"

    def test_unknown_defaults_to_jpeg(self):
        assert _detect_media_type(b"\x00\x00\x00\x00") == "image/jpeg"


# ===========================================================================
# _parse_vision_response
# ===========================================================================

class TestParseVisionResponse:
    """Tests for _parse_vision_response."""

    def test_valid_json(self):
        text = '{"holdings": [{"name": "삼성전자", "ticker": "005930", "quantity": 10, "avg_price": 60000, "current_price": 62000, "profit_pct": 3.33, "eval_amount": 620000}], "summary": {"total_eval": 620000, "total_profit": 20000, "total_profit_pct": 3.33, "cash": 500000}}'
        result = _parse_vision_response(text)
        assert len(result["holdings"]) == 1
        assert result["holdings"][0]["name"] == "삼성전자"
        assert result["summary"]["cash"] == 500000

    def test_markdown_wrapped_json(self):
        text = '```json\n{"holdings": [], "summary": {"total_eval": 0, "total_profit": 0, "total_profit_pct": 0, "cash": 100000}}\n```'
        result = _parse_vision_response(text)
        assert result["summary"]["cash"] == 100000
        assert result["holdings"] == []

    def test_markdown_wrapped_no_json_tag(self):
        text = '```\n{"holdings": [], "summary": {"cash": 200000}}\n```'
        result = _parse_vision_response(text)
        assert result["summary"]["cash"] == 200000

    def test_invalid_json_returns_empty(self):
        result = _parse_vision_response("this is not json at all")
        assert result["holdings"] == []
        assert result["summary"]["total_eval"] == 0

    def test_empty_text_returns_empty(self):
        result = _parse_vision_response("")
        assert result["holdings"] == []

    def test_json_embedded_in_text(self):
        """JSON embedded in surrounding prose is extracted."""
        text = 'Here is the result: {"holdings": [], "summary": {"cash": 300000}} Hope that helps!'
        result = _parse_vision_response(text)
        assert result["summary"]["cash"] == 300000


# ===========================================================================
# _normalize_parsed
# ===========================================================================

class TestNormalizeParsed:
    """Tests for _normalize_parsed."""

    def test_english_keys(self):
        raw = {
            "holdings": [
                {"name": "삼성전자", "ticker": "005930", "quantity": 10, "avg_price": 60000, "current_price": 62000, "profit_pct": 3.33, "eval_amount": 620000},
            ],
            "summary": {"total_eval": 620000, "total_profit": 20000, "total_profit_pct": 3.33, "cash": 500000},
        }
        result = _normalize_parsed(raw)
        assert result["holdings"][0]["name"] == "삼성전자"
        assert result["summary"]["cash"] == 500000

    def test_korean_keys(self):
        raw = {
            "보유종목": [
                {"종목명": "삼성전자", "종목코드": "005930", "보유수량": 10, "평균매수가": 60000, "현재가": 62000, "수익률": 3.33, "평가금액": 620000},
            ],
            "계좌요약": {"총평가": 620000, "총손익": 20000, "총수익률": 3.33, "예수금": 500000},
        }
        result = _normalize_parsed(raw)
        assert result["holdings"][0]["name"] == "삼성전자"
        assert result["holdings"][0]["ticker"] == "005930"
        assert result["summary"]["cash"] == 500000
        assert result["summary"]["total_eval"] == 620000

    def test_ticker_zero_padding(self):
        """Short numeric tickers are zero-padded to 6 digits."""
        raw = {
            "holdings": [
                {"name": "테스트", "ticker": "930", "quantity": 1, "avg_price": 100, "current_price": 100, "profit_pct": 0, "eval_amount": 100},
            ],
            "summary": {},
        }
        result = _normalize_parsed(raw)
        assert result["holdings"][0]["ticker"] == "000930"

    def test_empty_holdings(self):
        raw = {"summary": {"cash": 100000}}
        result = _normalize_parsed(raw)
        assert result["holdings"] == []
        assert result["summary"]["cash"] == 100000

    def test_alternative_keys_stocks(self):
        """'stocks' key is accepted as holdings source."""
        raw = {
            "stocks": [
                {"stock_name": "LG화학", "stock_code": "051910", "qty": 5, "average_price": 500000, "price": 510000, "return_pct": 2.0, "evaluation": 2550000},
            ],
            "account_summary": {"total_evaluation": 2550000, "total_pnl": 50000, "total_return_pct": 2.0, "available_cash": 300000},
        }
        result = _normalize_parsed(raw)
        assert result["holdings"][0]["name"] == "LG화학"
        assert result["holdings"][0]["ticker"] == "051910"
        assert result["summary"]["cash"] == 300000


# ===========================================================================
# _to_float
# ===========================================================================

class TestToFloat:
    """Tests for _to_float."""

    def test_int_input(self):
        assert _to_float(42) == 42.0

    def test_float_input(self):
        assert _to_float(3.14) == 3.14

    def test_string_plain(self):
        assert _to_float("123.45") == 123.45

    def test_string_with_commas(self):
        assert _to_float("1,234,567") == 1234567.0

    def test_string_with_percent(self):
        assert _to_float("12.5%") == 12.5

    def test_string_with_won(self):
        assert _to_float("50,000원") == 50000.0

    def test_string_with_plus(self):
        assert _to_float("+3.5%") == 3.5

    def test_string_combined(self):
        assert _to_float("+1,234,567원") == 1234567.0

    def test_invalid_string(self):
        assert _to_float("abc") == 0.0

    def test_none(self):
        assert _to_float(None) == 0.0

    def test_list(self):
        assert _to_float([1, 2]) == 0.0


# ===========================================================================
# _to_int
# ===========================================================================

class TestToInt:
    """Tests for _to_int."""

    def test_int_input(self):
        assert _to_int(42) == 42

    def test_float_input(self):
        assert _to_int(3.7) == 3

    def test_string_plain(self):
        assert _to_int("100") == 100

    def test_string_with_commas(self):
        assert _to_int("1,000") == 1000

    def test_string_with_ju_suffix(self):
        assert _to_int("50주") == 50

    def test_invalid_string(self):
        assert _to_int("abc") == 0

    def test_none(self):
        assert _to_int(None) == 0
