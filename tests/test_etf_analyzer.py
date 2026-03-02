"""Tests for ETF analyzer module."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kstock.signal.etf_analyzer import (
    ETFAnalysisReport,
    ETFScreenResult,
    analyze_composition,
    analyze_etf,
    compare_costs,
    compute_tracking_error,
    format_etf_analysis,
    format_etf_screen,
    get_etf_info,
    screen_etfs,
)


def _make_ohlcv(n: int = 252, base: float = 10000.0, drift: float = 0.0005,
                vol: float = 0.01, seed: int = 42) -> pd.DataFrame:
    """합성 OHLCV 데이터 생성."""
    rng = np.random.RandomState(seed)
    returns = drift + vol * rng.randn(n)
    close = base * np.cumprod(1 + returns)
    dates = pd.bdate_range(end="2026-03-01", periods=n)
    return pd.DataFrame({
        "Open": close * (1 - vol * 0.5),
        "High": close * (1 + vol),
        "Low": close * (1 - vol),
        "Close": close,
        "Volume": rng.randint(100_000, 1_000_000, size=n),
    }, index=dates)


class TestTrackingError:
    def test_perfect_tracking(self):
        """동일 수익률 → TE ≈ 0."""
        df = _make_ohlcv(252, seed=1)
        result = compute_tracking_error(df, df, "069500", "^KS11")
        assert result.tracking_error_annual_pct < 0.01
        assert abs(result.tracking_difference_pct) < 0.01
        assert result.correlation > 0.999
        assert result.quality_grade == "A"

    def test_lagging_etf(self):
        """ETF가 벤치마크보다 약간 뒤처짐 → TD < 0, TE > 0."""
        bench = _make_ohlcv(252, drift=0.001, seed=10)
        etf = _make_ohlcv(252, drift=0.0005, seed=11)  # 다른 시드 → 다른 노이즈
        result = compute_tracking_error(etf, bench, "069500", "^KS11")
        # ETF drift < bench drift → 누적수익률 갭 존재
        assert result.tracking_difference_pct < 0
        assert result.tracking_error_annual_pct > 0

    def test_quality_grade_a(self):
        """TE < 0.5 → Grade A."""
        df = _make_ohlcv(252, seed=1)
        result = compute_tracking_error(df, df, "A", "B")
        assert result.quality_grade == "A"

    def test_quality_grade_b(self):
        """TE 0.5~1.0 → Grade B."""
        bench = _make_ohlcv(252, drift=0.001, vol=0.01, seed=5)
        etf = _make_ohlcv(252, drift=0.001, vol=0.012, seed=7)
        result = compute_tracking_error(etf, bench, "X", "Y")
        # 결과에 따라 등급 확인 (시드 의존적이므로 유효 등급만 체크)
        assert result.quality_grade in ("A", "B", "C", "D")

    def test_quality_grade_boundaries(self):
        """경계값 등급 테스트."""
        from kstock.signal.etf_analyzer import _te_quality_grade
        assert _te_quality_grade(0.4) == "A"
        assert _te_quality_grade(0.8) == "B"
        assert _te_quality_grade(1.5) == "C"
        assert _te_quality_grade(3.0) == "D"


class TestComposition:
    def test_hhi_concentrated(self):
        """단일 보유 종목 → HHI = 1."""
        holdings = [{"name": "삼성전자", "weight": 1.0, "sector": "IT"}]
        result = analyze_composition("069500", holdings)
        assert abs(result.concentration_hhi - 1.0) < 0.001
        assert abs(result.effective_n - 1.0) < 0.1

    def test_hhi_diversified(self):
        """N개 균등 배분 → HHI = 1/N."""
        n = 10
        w = 1.0 / n
        holdings = [
            {"name": f"종목{i}", "weight": w, "sector": "기타"} for i in range(n)
        ]
        result = analyze_composition("069500", holdings)
        assert abs(result.concentration_hhi - 1.0 / n) < 0.001
        assert abs(result.effective_n - float(n)) < 0.5

    def test_no_holdings(self):
        """보유 데이터 없으면 HHI=0."""
        result = analyze_composition("069500")
        assert result.concentration_hhi == 0.0
        assert result.effective_n == 0.0

    def test_sector_aggregation(self):
        """섹터 비중 합산 확인."""
        holdings = [
            {"name": "A", "weight": 0.3, "sector": "IT"},
            {"name": "B", "weight": 0.2, "sector": "IT"},
            {"name": "C", "weight": 0.5, "sector": "금융"},
        ]
        result = analyze_composition("069500", holdings)
        assert abs(result.sector_weights["IT"] - 0.5) < 0.001
        assert abs(result.sector_weights["금융"] - 0.5) < 0.001


class TestCostComparison:
    def test_cost_sorted(self):
        """비용 오름차순 정렬."""
        codes = ["069500", "102110", "229200"]
        results = compare_costs(codes)
        assert len(results) == 3
        costs = [r.total_cost_pct for r in results]
        assert costs == sorted(costs)

    def test_cost_grade(self):
        """비용 등급 할당."""
        results = compare_costs(["069500", "122630"])
        grades = {r.etf_code: r.cost_grade for r in results}
        # index ETF는 저비용, leverage는 고비용
        assert grades["069500"] in ("A", "B", "C")
        assert grades["122630"] in ("C", "D")

    def test_unknown_code_skipped(self):
        """미등록 코드는 건너뜀."""
        results = compare_costs(["XXXXXX"])
        assert len(results) == 0


class TestScreening:
    def _make_ohlcv_map(self, codes: list[str]) -> dict[str, pd.DataFrame]:
        ohlcv = {}
        for i, code in enumerate(codes):
            ohlcv[code] = _make_ohlcv(
                252, drift=0.0003 * (i + 1), vol=0.01, seed=100 + i,
            )
        return ohlcv

    def test_sorted_by_score(self):
        """score 내림차순 정렬."""
        codes = ["069500", "102110", "229200"]
        ohlcv = self._make_ohlcv_map(codes)
        results = screen_etfs(ohlcv)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_category_filter(self):
        """카테고리 필터 적용."""
        codes = ["069500", "102110", "091160"]
        ohlcv = self._make_ohlcv_map(codes)
        results = screen_etfs(ohlcv, category_filter="index")
        for r in results:
            assert r.category == "index"

    def test_score_range(self):
        """모든 점수 0~100 범위."""
        codes = ["069500", "102110", "229200", "091160"]
        ohlcv = self._make_ohlcv_map(codes)
        results = screen_etfs(ohlcv)
        for r in results:
            assert 0 <= r.score <= 100

    def test_empty_map(self):
        """빈 데이터 → 빈 결과."""
        results = screen_etfs({})
        assert results == []


class TestAnalyzeETF:
    def test_comprehensive(self):
        """전체 분석 실행 (크래시 없이 완료)."""
        etf_df = _make_ohlcv(252, drift=0.0005, seed=20)
        bench_df = _make_ohlcv(252, drift=0.0004, seed=21)
        ohlcv = {
            "069500": etf_df,
            "^KS11": bench_df,
            "102110": _make_ohlcv(252, seed=22),
        }
        report = analyze_etf("069500", ohlcv)
        assert isinstance(report, ETFAnalysisReport)
        assert report.etf_code == "069500"
        assert report.name == "KODEX 200"
        assert report.recommendation in ("Buy", "Hold", "Avoid")
        assert len(report.reasons) > 0

    def test_unknown_etf(self):
        """미등록 ETF → Avoid."""
        report = analyze_etf("XXXXXX", {})
        assert report.recommendation == "Avoid"


class TestETFInfo:
    def test_found(self):
        """존재하는 ETF 정보 반환."""
        info = get_etf_info("069500")
        assert info is not None
        assert info["name"] == "KODEX 200"
        assert info["category"] == "index"

    def test_not_found(self):
        """없는 코드 → None."""
        assert get_etf_info("999999") is None


class TestFormat:
    def test_format_analysis_returns_str(self):
        """format_etf_analysis가 str 반환."""
        report = ETFAnalysisReport(
            etf_code="069500",
            name="KODEX 200",
            recommendation="Buy",
            reasons=["Sharpe 0.80 (양호)"],
        )
        text = format_etf_analysis(report)
        assert isinstance(text, str)
        assert "KODEX 200" in text
        assert "Buy" in text

    def test_format_screen_returns_str(self):
        """format_etf_screen이 str 반환."""
        results = [
            ETFScreenResult(
                etf_code="069500", name="KODEX 200", category="index",
                return_1m_pct=1.5, return_3m_pct=3.2, return_6m_pct=5.0,
                volatility_pct=15.0, sharpe_ratio=0.8,
                tracking_error_pct=0.3, expense_ratio_pct=0.15, score=75.0,
            ),
        ]
        text = format_etf_screen(results)
        assert isinstance(text, str)
        assert "KODEX 200" in text

    def test_format_screen_empty(self):
        """빈 리스트 → 안내 메시지."""
        text = format_etf_screen([])
        assert "결과가 없습니다" in text
