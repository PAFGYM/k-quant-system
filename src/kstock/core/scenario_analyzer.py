"""Macro scenario simulation for portfolio stress testing (시나리오 분석).

Provides predefined macro scenarios (tariff, rate cut, MSCI, crash),
portfolio impact simulation, and defense strategy generation.
All functions are pure computation with no external API calls.

Rules:
- Korean messages, "주호님" personalized
- No ** bold, no Markdown parse_mode
- try-except wrappers, dataclasses, logging
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

SCENARIOS: dict[str, dict] = {
    "tariff_increase": {
        "name": "트럼프 관세 50% 인상",
        "impact": {
            "2차전지": -0.15,
            "자동차": -0.20,
            "반도체": -0.08,
            "방산": 0.05,
        },
    },
    "rate_cut": {
        "name": "기준금리 0.25%p 인하",
        "impact": {
            "2차전지": 0.08,
            "자동차": 0.05,
            "반도체": 0.10,
            "건설": 0.12,
        },
    },
    "msci_inclusion": {
        "name": "MSCI 코스닥 편입",
        "impact": {
            "바이오": 0.15,
            "소프트웨어": 0.12,
            "2차전지": 0.20,
        },
    },
    "crash": {
        "name": "코로나급 폭락 재현",
        "impact": {
            "default": -0.35,
        },
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class HoldingImpact:
    """개별 종목의 시나리오 영향."""

    ticker: str = ""
    name: str = ""
    sector: str = ""
    current_value: float = 0.0
    impact_pct: float = 0.0
    impact_amount: float = 0.0
    projected_value: float = 0.0


@dataclass
class ScenarioResult:
    """시나리오 시뮬레이션 결과."""

    scenario_key: str = ""
    scenario_name: str = ""
    holdings_impact: list[HoldingImpact] = field(default_factory=list)
    total_impact: float = 0.0
    total_impact_pct: float = 0.0
    total_before: float = 0.0
    total_after: float = 0.0


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate_scenario(
    holdings: list[dict],
    scenario_key: str,
    sector_map: dict[str, str] | None = None,
) -> dict:
    """특정 시나리오를 포트폴리오에 적용합니다.

    Args:
        holdings: [{"ticker": str, "name": str, "sector": str, "value": float}, ...]
        scenario_key: SCENARIOS 키
        sector_map: {ticker: sector} 별도 섹터 매핑 (optional)

    Returns:
        dict with keys: holdings_impact, total_impact, total_impact_pct,
        total_before, total_after
    """
    try:
        if scenario_key not in SCENARIOS:
            logger.warning("알 수 없는 시나리오: %s", scenario_key)
            return {
                "holdings_impact": [],
                "total_impact": 0.0,
                "total_impact_pct": 0.0,
                "total_before": 0.0,
                "total_after": 0.0,
            }

        scenario = SCENARIOS[scenario_key]
        impact_map = scenario["impact"]
        sector_map = sector_map or {}

        holdings_impact: list[dict] = []
        total_before = 0.0
        total_after = 0.0

        for h in holdings:
            ticker = h.get("ticker", "")
            name = h.get("name", "")
            sector = sector_map.get(ticker, h.get("sector", ""))
            value = h.get("value", 0.0)

            total_before += value

            # Determine impact percentage
            if sector in impact_map:
                impact_pct = impact_map[sector]
            elif "default" in impact_map:
                impact_pct = impact_map["default"]
            else:
                impact_pct = 0.0

            impact_amount = round(value * impact_pct, 0)
            projected = round(value + impact_amount, 0)
            total_after += projected

            holdings_impact.append({
                "ticker": ticker,
                "name": name,
                "sector": sector,
                "current_value": value,
                "impact_pct": round(impact_pct, 4),
                "impact_amount": impact_amount,
                "projected_value": projected,
            })

        total_impact = round(total_after - total_before, 0)
        total_impact_pct = round(
            total_impact / total_before, 4
        ) if total_before > 0 else 0.0

        logger.info(
            "시나리오 [%s] 시뮬레이션: 총 영향 %+,.0f원 (%+.1f%%)",
            scenario_key, total_impact, total_impact_pct * 100,
        )

        return {
            "holdings_impact": holdings_impact,
            "total_impact": total_impact,
            "total_impact_pct": total_impact_pct,
            "total_before": total_before,
            "total_after": total_after,
        }

    except Exception as e:
        logger.error("시나리오 시뮬레이션 실패: %s", e, exc_info=True)
        return {
            "holdings_impact": [],
            "total_impact": 0.0,
            "total_impact_pct": 0.0,
            "total_before": 0.0,
            "total_after": 0.0,
        }


# ---------------------------------------------------------------------------
# Stress test (all scenarios)
# ---------------------------------------------------------------------------

def stress_test(
    holdings: list[dict],
    total_value: float,
    sector_map: dict[str, str] | None = None,
) -> list[dict]:
    """모든 시나리오에 대해 스트레스 테스트를 수행합니다.

    Returns:
        list of {scenario_key, scenario_name, total_impact, total_impact_pct, ...}
    """
    try:
        results: list[dict] = []

        for key in SCENARIOS:
            result = simulate_scenario(holdings, key, sector_map)
            result["scenario_key"] = key
            result["scenario_name"] = SCENARIOS[key]["name"]
            results.append(result)

        # Sort by impact (worst first)
        results.sort(key=lambda r: r.get("total_impact_pct", 0))

        logger.info("스트레스 테스트 완료: %d개 시나리오", len(results))
        return results

    except Exception as e:
        logger.error("스트레스 테스트 실패: %s", e, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Defense strategy
# ---------------------------------------------------------------------------

def generate_defense_strategy(scenario_result: dict) -> list[str]:
    """시나리오 결과에 따른 방어 전략을 제안합니다."""
    try:
        strategies: list[str] = []
        impact_pct = scenario_result.get("total_impact_pct", 0)
        holdings = scenario_result.get("holdings_impact", [])

        # Find the most negatively impacted holdings
        negative_holdings = [
            h for h in holdings if h.get("impact_pct", 0) < -0.05
        ]
        negative_holdings.sort(key=lambda h: h.get("impact_pct", 0))

        if impact_pct < -0.20:
            strategies.append("포트폴리오 현금 비중을 30% 이상으로 확대")
            strategies.append("인버스 ETF (KODEX 200선물인버스2X) 헤지 비중 10~15% 검토")
        elif impact_pct < -0.10:
            strategies.append("포트폴리오 현금 비중을 20% 이상으로 확대")
            strategies.append("방어 섹터 (유틸리티, 필수소비재) 비중 확대 검토")
        elif impact_pct < -0.05:
            strategies.append("취약 섹터 비중을 점진적으로 축소")
        else:
            strategies.append("현재 포트폴리오 유지, 모니터링 강화")

        # Specific holding recommendations
        for h in negative_holdings[:3]:
            name = h.get("name", "")
            pct = h.get("impact_pct", 0)
            if pct < -0.15:
                strategies.append(f"{name}: 비중 50% 이상 축소 권장")
            elif pct < -0.10:
                strategies.append(f"{name}: 비중 30% 축소 또는 손절 라인 설정")
            else:
                strategies.append(f"{name}: 추가 매수 보류, 모니터링")

        # Positive impact holdings
        positive_holdings = [
            h for h in holdings if h.get("impact_pct", 0) > 0.05
        ]
        if positive_holdings:
            names = [h.get("name", "") for h in positive_holdings[:3]]
            strategies.append(f"수혜 종목 ({', '.join(names)}) 비중 확대 검토")

        return strategies

    except Exception as e:
        logger.error("방어 전략 생성 실패: %s", e, exc_info=True)
        return ["방어 전략 생성 중 오류가 발생했습니다. 수동으로 검토해 주세요."]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_scenario_report(scenario_key: str, result: dict) -> str:
    """시나리오 분석 결과를 텔레그램 형식으로 생성합니다."""
    try:
        scenario = SCENARIOS.get(scenario_key, {})
        scenario_name = scenario.get("name", scenario_key)
        now = datetime.now(tz=KST).strftime("%Y.%m.%d %H:%M")

        total_before = result.get("total_before", 0)
        total_after = result.get("total_after", 0)
        total_impact = result.get("total_impact", 0)
        total_impact_pct = result.get("total_impact_pct", 0)

        lines = [
            f"[시나리오 분석] {now}",
            f"{USER_NAME}, '{scenario_name}' 시나리오 분석 결과입니다.",
            "",
            f"  시나리오: {scenario_name}",
            f"  현재 평가액: {total_before:,.0f}원",
            f"  예상 평가액: {total_after:,.0f}원",
            f"  예상 손익: {total_impact:+,.0f}원 ({total_impact_pct * 100:+.1f}%)",
            "",
            "-- 종목별 영향 --",
        ]

        holdings = result.get("holdings_impact", [])
        # Sort by impact amount
        sorted_holdings = sorted(
            holdings, key=lambda h: h.get("impact_amount", 0)
        )

        for h in sorted_holdings:
            name = h.get("name", "")
            sector = h.get("sector", "")
            impact_pct = h.get("impact_pct", 0)
            impact_amount = h.get("impact_amount", 0)

            sector_label = f" [{sector}]" if sector else ""
            lines.append(
                f"  {name}{sector_label}: {impact_pct * 100:+.1f}% ({impact_amount:+,.0f}원)"
            )

        # Defense strategies
        strategies = generate_defense_strategy(result)
        if strategies:
            lines.append("")
            lines.append("-- 대응 전략 --")
            for i, s in enumerate(strategies, 1):
                lines.append(f"  {i}. {s}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("시나리오 리포트 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 시나리오 리포트 생성 중 오류가 발생했습니다."


def format_scenario_menu() -> str:
    """시나리오 선택 메뉴를 생성합니다."""
    try:
        lines = [
            f"[시나리오 분석] 메뉴",
            f"{USER_NAME}, 분석할 시나리오를 선택해 주세요.",
            "",
        ]

        for i, (key, scenario) in enumerate(SCENARIOS.items(), 1):
            name = scenario["name"]
            impacts = scenario["impact"]

            # Summarize impact sectors
            if "default" in impacts:
                summary = f"전체 {impacts['default'] * 100:+.0f}%"
            else:
                parts = []
                for sector, pct in list(impacts.items())[:3]:
                    parts.append(f"{sector} {pct * 100:+.0f}%")
                summary = ", ".join(parts)

            lines.append(f"  {i}. {name}")
            lines.append(f"     영향: {summary}")
            lines.append(f"     명령: /scenario {key}")
            lines.append("")

        lines.append("번호 또는 명령어로 선택해 주세요.")

        return "\n".join(lines)

    except Exception as e:
        logger.error("시나리오 메뉴 생성 실패: %s", e, exc_info=True)
        return f"{USER_NAME}, 시나리오 메뉴 생성 중 오류가 발생했습니다."
