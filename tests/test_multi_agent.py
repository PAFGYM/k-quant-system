"""Tests for kstock.bot.multi_agent module.

Covers: AGENTS config, data formatting, response parsing,
score synthesis, strategist input, report formatting,
empty report creation, and cost estimation.
"""

import pytest

from kstock.bot.multi_agent import (
    AGENTS,
    AgentResult,
    MultiAgentReport,
    format_data_for_agent,
    parse_agent_score,
    parse_agent_signal,
    synthesize_scores,
    build_strategist_input,
    format_multi_agent_report,
    create_empty_report,
    estimate_analysis_cost,
)


# ---------------------------------------------------------------------------
# TestAgents
# ---------------------------------------------------------------------------
class TestAgents:
    """AGENTS configuration dict validation."""

    def test_four_agents_exist(self):
        assert len(AGENTS) == 4
        assert set(AGENTS.keys()) == {"technical", "fundamental", "sentiment", "strategist"}

    def test_each_agent_has_required_keys(self):
        for key, agent in AGENTS.items():
            assert "name" in agent, f"{key} missing 'name'"
            assert "model" in agent, f"{key} missing 'model'"
            assert "system_prompt" in agent, f"{key} missing 'system_prompt'"

    def test_first_three_use_haiku(self):
        for key in ("technical", "fundamental", "sentiment"):
            assert "haiku" in AGENTS[key]["model"], f"{key} should use haiku model"

    def test_strategist_uses_sonnet(self):
        assert "sonnet" in AGENTS["strategist"]["model"]


# ---------------------------------------------------------------------------
# TestFormatDataForAgent
# ---------------------------------------------------------------------------
class TestFormatDataForAgent:
    """format_data_for_agent returns agent-specific subsets of stock_data."""

    SAMPLE_DATA = {
        "ma5": 50000, "ma20": 49000, "ma60": 48000, "ma120": 47000,
        "rsi": 55, "macd": 120, "macd_signal": 100,
        "volume": 1000000, "avg_volume_20": 500000,
        "high_52w": 60000, "low_52w": 40000,
        "price": 51000, "prices_5d": [50000, 50500, 51000, 50800, 51000],
        "per": 12.5, "sector_per": 15.0, "pbr": 1.2, "roe": 18.0,
        "revenue_growth": 0.15, "op_growth": 0.20,
        "debt_ratio": 0.35, "target_price": 60000,
        "recent_earnings": "1Q 서프라이즈",
        "news_summary": "긍정적 뉴스",
        "inst_net_5d": 50000, "foreign_net_5d": 30000,
        "short_change": -5000, "margin_change": 2000,
        "sector_trend": "상승 추세",
    }

    def test_technical_contains_ma_rsi_macd(self):
        result = format_data_for_agent("technical", self.SAMPLE_DATA)
        assert "이동평균" in result or "ma5" in result.lower() or "50000" in result
        assert "RSI" in result
        assert "MACD" in result

    def test_fundamental_contains_per_pbr_roe(self):
        result = format_data_for_agent("fundamental", self.SAMPLE_DATA)
        assert "PER" in result
        assert "PBR" in result
        assert "ROE" in result

    def test_sentiment_contains_news_inst_foreign(self):
        result = format_data_for_agent("sentiment", self.SAMPLE_DATA)
        assert "뉴스" in result
        assert "기관" in result
        assert "외인" in result


# ---------------------------------------------------------------------------
# TestParseAgentScore
# ---------------------------------------------------------------------------
class TestParseAgentScore:
    """parse_agent_score extracts numeric score from response text."""

    def test_korean_format_score(self):
        assert parse_agent_score("기술적 점수: 72") == 72

    def test_english_format_score(self):
        assert parse_agent_score("score: 85") == 85

    def test_no_match_returns_50(self):
        assert parse_agent_score("아무런 정보가 없습니다") == 50

    def test_clamps_to_100(self):
        assert parse_agent_score("점수: 150") == 100

    def test_empty_string_returns_50(self):
        assert parse_agent_score("") == 50


# ---------------------------------------------------------------------------
# TestParseAgentSignal
# ---------------------------------------------------------------------------
class TestParseAgentSignal:
    """parse_agent_signal extracts signal label from response text."""

    def test_signal_maesu(self):
        assert parse_agent_signal("신호: 매수") == "매수"

    def test_valuation_low(self):
        assert parse_agent_signal("밸류에이션: 저평가") == "저평가"

    def test_market_sentiment_greed(self):
        assert parse_agent_signal("시장 심리: 탐욕") == "탐욕"

    def test_no_match_returns_neutral(self):
        assert parse_agent_signal("아무런 신호 없음") == "중립"


# ---------------------------------------------------------------------------
# TestSynthesizeScores
# ---------------------------------------------------------------------------
class TestSynthesizeScores:
    """synthesize_scores combines 3 agent results into combined score, verdict, confidence."""

    @staticmethod
    def _make_results(tech: int, fund: int, sent: int) -> dict:
        return {
            "technical": AgentResult(agent_key="technical", score=tech),
            "fundamental": AgentResult(agent_key="fundamental", score=fund),
            "sentiment": AgentResult(agent_key="sentiment", score=sent),
        }

    def test_all_50_gives_midrange_gwanmang(self):
        score, verdict, confidence = synthesize_scores(self._make_results(50, 50, 50))
        # weighted_sum = 50*0.35 + 50*0.30 + 50*0.35 = 50.0
        # combined = round(50.0 * 2.15) = 108
        assert 100 <= score <= 115
        assert verdict == "관망"

    def test_all_90_gives_high_maesu(self):
        score, verdict, confidence = synthesize_scores(self._make_results(90, 90, 90))
        # weighted_sum = 90 -> combined = round(90 * 2.15) = 194
        assert score >= 160
        assert verdict == "매수"

    def test_all_10_gives_low_maedo(self):
        score, verdict, confidence = synthesize_scores(self._make_results(10, 10, 10))
        # weighted_sum = 10 -> combined = round(10 * 2.15) = 22
        assert score < 80
        assert verdict == "매도"

    def test_mixed_scores(self):
        score, verdict, confidence = synthesize_scores(self._make_results(80, 60, 40))
        # weighted_sum = 80*0.35 + 60*0.30 + 40*0.35 = 28+18+14 = 60
        # combined = round(60 * 2.15) = 129
        assert 120 <= score <= 140
        assert verdict in ("홀딩", "관망")

    def test_confidence_varies_with_spread(self):
        # tight spread (all same) -> confidence 상
        _, _, conf_tight = synthesize_scores(self._make_results(50, 50, 50))
        assert conf_tight == "상"

        # wide spread -> confidence 하
        _, _, conf_wide = synthesize_scores(self._make_results(10, 50, 90))
        assert conf_wide == "하"


# ---------------------------------------------------------------------------
# TestBuildStrategistInput
# ---------------------------------------------------------------------------
class TestBuildStrategistInput:
    """build_strategist_input constructs the combined prompt for the strategist agent."""

    def _make_results(self):
        return {
            "technical": AgentResult(
                agent_key="technical", agent_name="기술적", score=70,
                signal="매수", raw_response="기술적 분석 결과 매수 추천",
            ),
            "fundamental": AgentResult(
                agent_key="fundamental", agent_name="기본적", score=60,
                signal="적정", raw_response="기본적 분석 결과 적정 판단",
            ),
            "sentiment": AgentResult(
                agent_key="sentiment", agent_name="센티먼트", score=55,
                signal="낙관", raw_response="센티먼트 분석 결과 낙관",
            ),
        }

    def test_contains_all_three_agent_results(self):
        result = build_strategist_input(
            self._make_results(),
            {"name": "삼성전자", "ticker": "005930", "price": 70000},
        )
        assert "기술적 분석" in result
        assert "기본적 분석" in result
        assert "뉴스/센티먼트 분석" in result

    def test_includes_feedback_data(self):
        result = build_strategist_input(
            self._make_results(),
            {"name": "삼성전자", "ticker": "005930", "price": 70000},
            feedback_data="과거 분석 정확도 80%",
        )
        assert "과거 피드백 데이터" in result
        assert "과거 분석 정확도 80%" in result

    def test_includes_stock_name(self):
        result = build_strategist_input(
            self._make_results(),
            {"name": "에코프로", "ticker": "086520", "price": 178000},
        )
        assert "에코프로" in result
        assert "086520" in result


# ---------------------------------------------------------------------------
# TestFormatMultiAgentReport
# ---------------------------------------------------------------------------
class TestFormatMultiAgentReport:
    """format_multi_agent_report generates a Telegram-friendly report string."""

    def _make_report(self):
        results = {
            "technical": AgentResult(agent_key="technical", score=72, signal="매수", summary="이평선 정배열"),
            "fundamental": AgentResult(agent_key="fundamental", score=65, signal="적정", summary="PER 적정"),
            "sentiment": AgentResult(agent_key="sentiment", score=58, signal="낙관", summary="뉴스 긍정"),
        }
        return MultiAgentReport(
            ticker="005930",
            name="삼성전자",
            price=70000,
            results=results,
            strategist_result=AgentResult(
                agent_key="strategist", score=65, signal="홀딩",
                summary="종합적으로 홀딩 추천",
            ),
            combined_score=140,
            verdict="홀딩",
            confidence="중",
            action="트레일링 스탑 설정",
            risk_note="반도체 경기 둔화 가능성",
        )

    def test_no_bold_markers(self):
        text = format_multi_agent_report(self._make_report())
        assert "**" not in text

    def test_contains_user_name(self):
        text = format_multi_agent_report(self._make_report())
        assert "주호님" in text

    def test_contains_combined_verdict(self):
        text = format_multi_agent_report(self._make_report())
        assert "종합 판단" in text

    def test_contains_agent_scores(self):
        text = format_multi_agent_report(self._make_report())
        assert "72점" in text
        assert "65점" in text
        assert "58점" in text


# ---------------------------------------------------------------------------
# TestCreateEmptyReport
# ---------------------------------------------------------------------------
class TestCreateEmptyReport:
    """create_empty_report produces a valid fallback report."""

    def test_returns_valid_report(self):
        report = create_empty_report("005930", "삼성전자", 70000.0)
        assert isinstance(report, MultiAgentReport)
        assert report.ticker == "005930"
        assert report.name == "삼성전자"

    def test_all_sub_scores_50(self):
        report = create_empty_report("005930", "삼성전자", 70000.0)
        for key in ("technical", "fundamental", "sentiment"):
            assert report.results[key].score == 50

    def test_verdict_is_gwanmang(self):
        report = create_empty_report("005930", "삼성전자", 70000.0)
        assert report.verdict == "관망"


# ---------------------------------------------------------------------------
# TestEstimateAnalysisCost
# ---------------------------------------------------------------------------
class TestEstimateAnalysisCost:
    """estimate_analysis_cost returns cost estimates."""

    def test_single_stock_cost(self):
        cost = estimate_analysis_cost(1)
        assert cost["per_stock_usd"] == 0.018
        assert cost["total_usd"] == 0.018
        assert cost["haiku_calls"] == 3
        assert cost["sonnet_calls"] == 1

    def test_multi_stock_scaling(self):
        cost = estimate_analysis_cost(5)
        assert cost["total_usd"] == pytest.approx(0.018 * 5, abs=0.001)
        assert cost["haiku_calls"] == 15
        assert cost["sonnet_calls"] == 5
