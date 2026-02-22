"""Tests for signal/future_tech.py - Future technology sector watchlist engine."""

import pytest
from kstock.signal.future_tech import (
    FUTURE_SECTORS,
    TIER_CONFIG,
    FutureStockScore,
    EntrySignal,
    get_all_watchlist_tickers,
    get_sector_watchlist,
    get_ticker_info,
    find_tier_for_ticker,
    assess_tech_maturity,
    score_future_stock,
    format_sector_overview,
    format_full_watchlist,
    format_sector_detail,
)


# ---------------------------------------------------------------------------
# Sector / watchlist structure tests
# ---------------------------------------------------------------------------

class TestFutureSectors:
    """Test 3 sector definitions."""

    def test_three_sectors_defined(self):
        assert len(FUTURE_SECTORS) == 3
        assert "autonomous_driving" in FUTURE_SECTORS
        assert "space_aerospace" in FUTURE_SECTORS
        assert "quantum_computing" in FUTURE_SECTORS

    def test_each_sector_has_required_keys(self):
        required = {"name", "phase", "timeline", "trigger_keywords", "watchlist", "emoji"}
        for key, sector in FUTURE_SECTORS.items():
            for rk in required:
                assert rk in sector, f"{key} missing {rk}"

    def test_each_sector_has_three_tiers(self):
        for key, sector in FUTURE_SECTORS.items():
            wl = sector["watchlist"]
            assert "tier1_platform" in wl, f"{key} missing tier1_platform"
            assert "tier2_core" in wl, f"{key} missing tier2_core"
            assert "tier3_emerging" in wl, f"{key} missing tier3_emerging"

    def test_each_stock_has_ticker_and_reason(self):
        for sector_key, sector in FUTURE_SECTORS.items():
            for tier_key, stocks in sector["watchlist"].items():
                for name, info in stocks.items():
                    assert "ticker" in info, f"{name} missing ticker"
                    assert "reason" in info, f"{name} missing reason"
                    assert len(info["ticker"]) == 6, f"{name} ticker not 6 digits"

    def test_trigger_keywords_non_empty(self):
        for key, sector in FUTURE_SECTORS.items():
            assert len(sector["trigger_keywords"]) >= 5, f"{key} needs more keywords"

    def test_sector_names_korean(self):
        assert FUTURE_SECTORS["autonomous_driving"]["name"] == "자율주행"
        assert FUTURE_SECTORS["space_aerospace"]["name"] == "우주항공"
        assert FUTURE_SECTORS["quantum_computing"]["name"] == "양자컴퓨터"

    def test_autonomous_driving_has_hyundai(self):
        ad = FUTURE_SECTORS["autonomous_driving"]
        t1 = ad["watchlist"]["tier1_platform"]
        assert "현대차" in t1

    def test_space_has_kai(self):
        sp = FUTURE_SECTORS["space_aerospace"]
        t1 = sp["watchlist"]["tier1_platform"]
        assert "한국항공우주" in t1

    def test_quantum_has_skt(self):
        qc = FUTURE_SECTORS["quantum_computing"]
        t1 = qc["watchlist"]["tier1_platform"]
        assert "SK텔레콤" in t1


class TestTierConfig:
    """Test tier configuration."""

    def test_three_tiers(self):
        assert len(TIER_CONFIG) == 3

    def test_tier_labels_korean(self):
        assert "대형" in TIER_CONFIG["tier1_platform"]["label"]
        assert "핵심" in TIER_CONFIG["tier2_core"]["label"]
        assert "소형" in TIER_CONFIG["tier3_emerging"]["label"]

    def test_tier_weights_ordered(self):
        t1 = TIER_CONFIG["tier1_platform"]
        t2 = TIER_CONFIG["tier2_core"]
        t3 = TIER_CONFIG["tier3_emerging"]
        assert t1["weight_max"] > t2["weight_max"] > t3["weight_max"]


class TestWatchlistHelpers:
    """Test watchlist helper functions."""

    def test_get_all_tickers_non_empty(self):
        all_tickers = get_all_watchlist_tickers()
        assert len(all_tickers) >= 30  # At least 30 stocks across all sectors

    def test_get_all_tickers_has_sector_info(self):
        all_tickers = get_all_watchlist_tickers()
        for ticker, info in all_tickers.items():
            assert "name" in info
            assert "sector" in info
            assert "tier" in info
            assert "reason" in info

    def test_get_sector_watchlist(self):
        ad = get_sector_watchlist("autonomous_driving")
        assert len(ad) >= 10
        for ticker, info in ad.items():
            assert "name" in info
            assert "tier" in info

    def test_get_sector_watchlist_empty_for_unknown(self):
        result = get_sector_watchlist("unknown_sector")
        assert result == {}

    def test_get_ticker_info_found(self):
        info = get_ticker_info("005380")  # 현대차
        assert info is not None
        assert info["name"] == "현대차"
        assert info["sector"] == "autonomous_driving"

    def test_get_ticker_info_not_found(self):
        info = get_ticker_info("999999")
        assert info is None

    def test_find_tier_for_ticker(self):
        tier = find_tier_for_ticker("047810")  # 한국항공우주
        assert tier == "tier1_platform"

    def test_find_tier_for_unknown(self):
        tier = find_tier_for_ticker("999999")
        assert tier == ""


# ---------------------------------------------------------------------------
# Tech maturity assessment
# ---------------------------------------------------------------------------

class TestTechMaturity:
    """Test technology maturity assessment."""

    def test_revenue_with_profit(self):
        fin = {"revenue_growth_pct": 15, "operating_profit": 100}
        score, desc = assess_tech_maturity("054450", fin)
        assert score == 25
        assert "매출" in desc or "흑자" in desc

    def test_high_revenue_growth(self):
        fin = {"revenue_growth_pct": 25, "operating_profit": 0}
        score, desc = assess_tech_maturity("054450", fin)
        assert score == 20

    def test_moderate_revenue_growth(self):
        fin = {"revenue_growth_pct": 5, "operating_profit": 0}
        score, desc = assess_tech_maturity("054450", fin)
        assert score == 15

    def test_reports_with_contract_keywords(self):
        reports = [{"title": "텔레칩스 수주 확대 전망"}]
        score, desc = assess_tech_maturity("054450", reports=reports)
        assert score == 20

    def test_reports_with_rnd_keywords(self):
        reports = [{"title": "양자컴퓨터 연구 성과"}]
        score, desc = assess_tech_maturity("203650", reports=reports)
        assert score == 10

    def test_no_data_defaults_to_related(self):
        score, desc = assess_tech_maturity("999999")
        assert score == 5
        assert "관련성" in desc


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    """Test future stock scoring."""

    def test_full_score_max_100(self):
        score = score_future_stock(
            ticker="054450",
            sector_key="autonomous_driving",
            financial_data={"revenue_growth_pct": 20, "operating_profit": 100, "debt_ratio": 50},
            reports=[{"title": "텔레칩스 매수 의견 유지"}],
            is_national_project=True,
            foreign_net_buy_days=5,
            psr=2.0,
        )
        assert score.total_score <= 100
        assert score.total_score >= 70

    def test_minimal_score(self):
        score = score_future_stock(
            ticker="999999",
            sector_key="quantum_computing",
        )
        assert score.total_score == 5  # Only related maturity

    def test_score_breakdown_adds_up(self):
        score = score_future_stock(
            ticker="047810",
            sector_key="space_aerospace",
            financial_data={"revenue_growth_pct": 15, "operating_profit": 200, "debt_ratio": 80},
            has_gov_contract=True,
            foreign_net_buy_days=3,
            psr=5.0,
        )
        expected = (
            score.tech_maturity
            + score.financial_stability
            + score.policy_benefit
            + score.momentum
            + score.valuation
        )
        assert score.total_score == expected

    def test_national_project_gives_20(self):
        score = score_future_stock(
            ticker="082800",
            sector_key="space_aerospace",
            is_national_project=True,
        )
        assert score.policy_benefit == 20

    def test_gov_contract_gives_15(self):
        score = score_future_stock(
            ticker="082800",
            sector_key="space_aerospace",
            has_gov_contract=True,
        )
        assert score.policy_benefit == 15

    def test_psr_scoring(self):
        s1 = score_future_stock("054450", "autonomous_driving", psr=2.0)
        s2 = score_future_stock("054450", "autonomous_driving", psr=5.0)
        s3 = score_future_stock("054450", "autonomous_driving", psr=10.0)
        assert s1.valuation == 15
        assert s2.valuation == 10
        assert s3.valuation == 5

    def test_details_populated(self):
        score = score_future_stock(
            "054450", "autonomous_driving",
            financial_data={"revenue_growth_pct": 20, "operating_profit": 50, "debt_ratio": 80},
        )
        assert len(score.details) >= 2

    def test_score_has_name_from_watchlist(self):
        score = score_future_stock("005380", "autonomous_driving")
        assert score.name == "현대차"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatting:
    """Test formatting functions."""

    def test_format_sector_overview_contains_name(self):
        text = format_sector_overview("autonomous_driving")
        assert "자율주행" in text

    def test_format_sector_overview_contains_tiers(self):
        text = format_sector_overview("space_aerospace")
        assert "Tier 1" in text or "대형" in text

    def test_format_full_watchlist_has_all_sectors(self):
        text = format_full_watchlist()
        assert "자율주행" in text
        assert "우주항공" in text
        assert "양자컴퓨터" in text

    def test_format_full_watchlist_has_subcmd_help(self):
        text = format_full_watchlist()
        assert "/future ad" in text
        assert "/future space" in text
        assert "/future qc" in text

    def test_format_full_watchlist_no_bold(self):
        text = format_full_watchlist()
        assert "**" not in text

    def test_format_sector_detail_shows_stocks(self):
        text = format_sector_detail("quantum_computing")
        assert "양자컴퓨터" in text
        assert "SK텔레콤" in text or "017670" in text

    def test_format_sector_detail_no_bold(self):
        text = format_sector_detail("autonomous_driving")
        assert "**" not in text

    def test_format_with_scores(self):
        scores = {
            "054450": FutureStockScore(
                ticker="054450", name="텔레칩스", sector="autonomous_driving",
                tier="tier2_core", total_score=72,
            ),
        }
        text = format_sector_overview("autonomous_driving", scores=scores)
        assert "72점" in text
