"""Tests for core/scenario_analyzer.py - Macro scenario simulation."""

import pytest
from kstock.core.scenario_analyzer import (
    SCENARIOS,
    simulate_scenario,
    stress_test,
    generate_defense_strategy,
    format_scenario_report,
    format_scenario_menu,
)


# ---------------------------------------------------------------------------
# TestScenarios
# ---------------------------------------------------------------------------

class TestScenarios:
    """SCENARIOS: 4가지 시나리오 정의 존재 여부."""

    def test_tariff_increase_exists(self):
        assert "tariff_increase" in SCENARIOS

    def test_rate_cut_exists(self):
        assert "rate_cut" in SCENARIOS

    def test_msci_inclusion_exists(self):
        assert "msci_inclusion" in SCENARIOS

    def test_crash_exists(self):
        assert "crash" in SCENARIOS

    def test_all_have_name(self):
        for key, scenario in SCENARIOS.items():
            assert "name" in scenario, f"{key}에 name 없음"

    def test_all_have_impact(self):
        for key, scenario in SCENARIOS.items():
            assert "impact" in scenario, f"{key}에 impact 없음"


# ---------------------------------------------------------------------------
# TestSimulateScenario
# ---------------------------------------------------------------------------

class TestSimulateScenario:
    """simulate_scenario: 시나리오별 포트폴리오 영향."""

    HOLDINGS = [
        {"ticker": "373220", "name": "LG에너지솔루션", "sector": "2차전지", "value": 10_000_000},
        {"ticker": "000660", "name": "SK하이닉스", "sector": "반도체", "value": 10_000_000},
        {"ticker": "012330", "name": "현대모비스", "sector": "자동차", "value": 5_000_000},
    ]

    def test_tariff_on_battery(self):
        """tariff_increase -> 2차전지 -15%."""
        result = simulate_scenario(self.HOLDINGS, "tariff_increase")
        battery = [
            h for h in result["holdings_impact"]
            if h["sector"] == "2차전지"
        ]
        assert len(battery) == 1
        assert battery[0]["impact_pct"] == -0.15

    def test_rate_cut_on_semiconductor(self):
        """rate_cut -> 반도체 +10%."""
        result = simulate_scenario(self.HOLDINGS, "rate_cut")
        semi = [
            h for h in result["holdings_impact"]
            if h["sector"] == "반도체"
        ]
        assert len(semi) == 1
        assert semi[0]["impact_pct"] == 0.10

    def test_crash_all_negative(self):
        """crash -> 모든 종목 부정적 영향."""
        result = simulate_scenario(self.HOLDINGS, "crash")
        for h in result["holdings_impact"]:
            assert h["impact_pct"] < 0

    def test_unknown_sector_zero_impact(self):
        """tariff_increase에서 정의되지 않은 섹터 -> 0% 영향."""
        holdings = [
            {"ticker": "XXX", "name": "기타종목", "sector": "음식료", "value": 10_000_000},
        ]
        result = simulate_scenario(holdings, "tariff_increase")
        assert result["holdings_impact"][0]["impact_pct"] == 0.0

    def test_unknown_scenario(self):
        """존재하지 않는 시나리오 -> 빈 결과."""
        result = simulate_scenario(self.HOLDINGS, "nonexistent")
        assert result["total_impact"] == 0.0
        assert result["holdings_impact"] == []

    def test_total_impact_computed(self):
        """전체 영향 금액이 합산됨."""
        result = simulate_scenario(self.HOLDINGS, "tariff_increase")
        total = sum(h["impact_amount"] for h in result["holdings_impact"])
        assert result["total_impact"] == total


# ---------------------------------------------------------------------------
# TestStressTest
# ---------------------------------------------------------------------------

class TestStressTest:
    """stress_test: 모든 시나리오에 대한 스트레스 테스트."""

    def test_runs_all_scenarios(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "value": 50_000_000},
        ]
        results = stress_test(holdings, total_value=50_000_000)
        assert len(results) == len(SCENARIOS)

    def test_returns_list_of_dicts(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "value": 50_000_000},
        ]
        results = stress_test(holdings, total_value=50_000_000)
        for r in results:
            assert "scenario_key" in r
            assert "total_impact_pct" in r


# ---------------------------------------------------------------------------
# TestGenerateDefenseStrategy
# ---------------------------------------------------------------------------

class TestGenerateDefenseStrategy:
    """generate_defense_strategy: 방어 전략 생성."""

    def test_returns_korean_strategies_for_tariff(self):
        """tariff 시나리오 -> 한국어 방어 전략."""
        holdings = [
            {"ticker": "373220", "name": "LG에너지솔루션", "sector": "2차전지", "value": 50_000_000},
        ]
        result = simulate_scenario(holdings, "tariff_increase")
        strategies = generate_defense_strategy(result)
        assert len(strategies) > 0
        for s in strategies:
            assert isinstance(s, str)
            assert len(s) > 0

    def test_defense_for_crash(self):
        """crash 시나리오 -> 현금 비중 확대 권장 포함."""
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "value": 50_000_000},
        ]
        result = simulate_scenario(holdings, "crash")
        strategies = generate_defense_strategy(result)
        combined = " ".join(strategies)
        assert "현금" in combined or "비중" in combined


# ---------------------------------------------------------------------------
# TestFormatScenarioReport
# ---------------------------------------------------------------------------

class TestFormatScenarioReport:
    """format_scenario_report: 시나리오 보고서 포맷."""

    def test_no_bold_markers(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "value": 50_000_000},
        ]
        result = simulate_scenario(holdings, "tariff_increase")
        report = format_scenario_report("tariff_increase", result)
        assert "**" not in report

    def test_user_name_present(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "value": 50_000_000},
        ]
        result = simulate_scenario(holdings, "tariff_increase")
        report = format_scenario_report("tariff_increase", result)
        assert "주호님" in report

    def test_contains_scenario_name(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "sector": "반도체", "value": 50_000_000},
        ]
        result = simulate_scenario(holdings, "tariff_increase")
        report = format_scenario_report("tariff_increase", result)
        scenario_name = SCENARIOS["tariff_increase"]["name"]
        assert scenario_name in report


# ---------------------------------------------------------------------------
# TestFormatScenarioMenu
# ---------------------------------------------------------------------------

class TestFormatScenarioMenu:
    """format_scenario_menu: 시나리오 선택 메뉴."""

    def test_no_bold_markers(self):
        menu = format_scenario_menu()
        assert "**" not in menu

    def test_lists_all_scenarios(self):
        menu = format_scenario_menu()
        for key, scenario in SCENARIOS.items():
            assert scenario["name"] in menu

    def test_contains_user_name(self):
        menu = format_scenario_menu()
        assert "주호님" in menu
