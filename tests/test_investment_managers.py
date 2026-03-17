"""Tests for manager discovery differentiation and action digest."""

from datetime import datetime

from kstock.bot.bot_imports import ScanResult
from kstock.bot.investment_managers import (
    build_daily_action_shortcuts,
    build_manager_shortcuts,
    build_manager_stance_snapshots,
    enrich_watchlist_candidate,
    filter_discovery_candidates,
    format_manager_action_digest,
    scan_manager_domain,
)
from kstock.bot.messages import format_daily_actions
from kstock.features.technical import TechnicalIndicators
from kstock.ingest.kis_client import StockInfo
from kstock.signal.scoring import FlowData, ScoreBreakdown


def _make_scan_result(
    *,
    ticker: str,
    name: str,
    composite: float,
    rsi: float,
    vol_ratio: float,
    bb_pctb: float,
    macd_cross: int,
    current_price: float,
    ma20: float,
    return_3m: float,
    roe: float,
    debt_ratio: float,
    per: float,
    market_cap: float = 2_0000_0000_0000,
    market: str = "KOSPI",
    consensus_target: float = 0,
    foreign_days: int = 0,
    inst_days: int = 0,
):
    return ScanResult(
        ticker=ticker,
        name=name,
        score=ScoreBreakdown(
            macro=0.7,
            flow=0.7,
            fundamental=0.7,
            technical=0.7,
            risk=0.7,
            composite=composite,
            signal="BUY",
        ),
        tech=TechnicalIndicators(
            rsi=rsi,
            bb_pctb=bb_pctb,
            bb_bandwidth=0.22,
            macd_histogram=0.2,
            macd_signal_cross=macd_cross,
            atr=2.0,
            atr_pct=2.0,
            high_52w=current_price * 1.2,
            volume_ratio=vol_ratio,
            return_3m_pct=return_3m,
            ma20=ma20,
            rsi_divergence=1 if macd_cross > 0 else 0,
            weekly_trend="up" if current_price >= ma20 else "neutral",
            mtf_aligned=current_price >= ma20,
        ),
        info=StockInfo(
            ticker=ticker,
            name=name,
            market=market,
            market_cap=market_cap,
            per=per,
            roe=roe,
            debt_ratio=debt_ratio,
            consensus_target=consensus_target or current_price * 1.15,
            current_price=current_price,
        ),
        flow=FlowData(
            foreign_net_buy_days=foreign_days,
            institution_net_buy_days=inst_days,
            avg_trade_value_krw=12_0000_0000,
        ),
    )


def test_filter_discovery_candidates_reads_scanresult_fields():
    momentum = _make_scan_result(
        ticker="111111",
        name="모멘텀주",
        composite=76,
        rsi=58,
        vol_ratio=2.8,
        bb_pctb=0.72,
        macd_cross=1,
        current_price=10500,
        ma20=9800,
        return_3m=16,
        roe=12,
        debt_ratio=85,
        per=17,
        foreign_days=3,
        inst_days=2,
    )

    picks = filter_discovery_candidates([momentum], "scalp")
    assert len(picks) == 1
    assert picks[0]["ticker"] == "111111"
    assert picks[0]["fit_score"] >= 56
    assert "거래량" in " ".join(picks[0]["fit_reasons"])


def test_filter_discovery_candidates_separates_manager_roles():
    quality = _make_scan_result(
        ticker="222222",
        name="품질주",
        composite=74,
        rsi=49,
        vol_ratio=1.1,
        bb_pctb=0.46,
        macd_cross=0,
        current_price=98000,
        ma20=97000,
        return_3m=6,
        roe=21,
        debt_ratio=38,
        per=11,
        market_cap=9_0000_0000_0000,
        foreign_days=1,
        inst_days=2,
    )

    scalp = filter_discovery_candidates([quality], "scalp")
    long_term = filter_discovery_candidates([quality], "long_term")

    assert scalp == []
    assert len(long_term) == 1
    assert long_term[0]["ticker"] == "222222"
    assert long_term[0]["fit_score"] >= 62


def test_enrich_watchlist_candidate_generates_actionable_fields():
    enriched = enrich_watchlist_candidate("swing", {
        "ticker": "333333",
        "name": "눌림목",
        "price": 24000,
        "day_change": -1.2,
        "rsi": 34,
        "vol_ratio": 165,
        "bb_pctb": 0.18,
        "macd_cross": 1,
        "drop_from_high": -22,
        "recovery_score": 58,
    })

    assert enriched["fit_score"] > 0
    assert enriched["action_hint"]
    assert enriched["lane"]
    assert enriched["fit_reasons"]


def test_swing_candidate_rejects_overheated_chase_setup():
    overheated = _make_scan_result(
        ticker="343434",
        name="급등추격주",
        composite=64,
        rsi=43,
        vol_ratio=3.1,
        bb_pctb=0.28,
        macd_cross=1,
        current_price=10600,
        ma20=9600,
        return_3m=52,
        roe=9,
        debt_ratio=140,
        per=38,
        foreign_days=0,
        inst_days=0,
    )
    overheated.day_change_pct = 7.4

    picks = filter_discovery_candidates([overheated], "swing")
    assert picks == []


def test_tenbagger_prefers_domestic_small_cap_zone():
    candidate = _make_scan_result(
        ticker="555555",
        name="국내텐배거",
        composite=72,
        rsi=54,
        vol_ratio=2.2,
        bb_pctb=0.58,
        macd_cross=1,
        current_price=18200,
        ma20=17100,
        return_3m=18,
        roe=14,
        debt_ratio=72,
        per=19,
        market_cap=1_2000_0000_0000,
        market="KOSDAQ",
        foreign_days=2,
        inst_days=1,
    )

    picks = filter_discovery_candidates([candidate], "tenbagger")
    assert len(picks) == 1
    assert picks[0]["listing_market"] == "KOSDAQ"
    assert "국내 스몰캡 핵심 구간" in " ".join(picks[0]["fit_reasons"])


def test_format_manager_action_digest_includes_fast_signals():
    text = format_manager_action_digest(
        {
            "tenbagger": [{
                "ticker": "444444",
                "name": "미래주",
                "price": 12345,
                "day_change": 3.2,
                "rsi": 57,
                "vol_ratio": 240,
                "composite": 78,
                "fit_score": 84,
                "fit_reasons": ["시총 5조 미만", "이벤트 AI"],
                "action_hint": "정책·산업 이벤트 전 씨앗 포지션 구축",
                "event_tags": ["AI", "GTC"],
                "youtube_mentions": 4,
                "news_hits": 2,
                "community_hits": 2,
                "board_signal": "토론방 매집 감지",
                "board_posts": 8,
                "herd_pattern": "진성 세력",
                "yt_outlook": "bullish",
                "flow_signal": "외인+기관 순유입",
                "short_pattern_labels": ["숏커버링 랠리", "숏스퀴즈"],
                "crowd_signal": "커뮤니티+테마 공명",
                "listing_market": "KOSDAQ",
                "market_cap_label": "1.2조",
                "entry_stage": "선점 구간",
            }],
        },
        title="🔍 매니저 신규 발굴 레이더",
        market_context="VIX=18.2, 나스닥=+1.2%",
        fast_event_lines=["GTC | 엔비디아 AI 로드맵 발표"],
        crowd_lines=["미래주 | 유튜브 4회 | 긍정 우위"],
    )

    assert "매니저 신규 발굴 레이더" in text
    assert "빠른신호" in text
    assert "이벤트 레이더" in text
    assert "군집 레이더" in text
    assert "외인+기관 순유입" in text
    assert "군집 진성 세력" in text
    assert "토론방 매집 감지 8건" in text
    assert "시황 bullish" in text
    assert "숏패턴 숏커버링 랠리/숏스퀴즈" in text
    assert "국내 스몰캡 레이더" in text
    assert "선점 구간" in text
    assert "커뮤니티 2건" in text


def test_build_manager_shortcuts_reflects_top_hint():
    shortcuts = build_manager_shortcuts({
        "scalp": [{
            "ticker": "111111",
            "name": "돌파주",
            "event_tags": ["GTC"],
        }],
        "tenbagger": [{
            "ticker": "222222",
            "name": "미래주",
            "entry_stage": "선점 구간",
        }],
    })

    assert any(item["callback_data"] == "mgr_tab:scalp" for item in shortcuts)
    assert any("GTC" in item["label"] for item in shortcuts)
    assert any("선점 구간" in item["label"] for item in shortcuts)


def test_build_manager_stance_snapshots_uses_top_pick_reason_and_action():
    snapshots = build_manager_stance_snapshots({
        "tenbagger": [{
            "ticker": "222222",
            "name": "미래주",
            "fit_score": 84,
            "fit_reasons": ["이벤트 AI", "유튜브 4회"],
            "action_hint": "정책·산업 이벤트 전 씨앗 포지션 구축",
        }],
    }, market_context="방어장")

    stance = snapshots["tenbagger"]
    assert "방어장" in stance
    assert "미래주(222222)" in stance
    assert "적합도 84" in stance
    assert "정책·산업 이벤트 전 씨앗 포지션 구축" in stance


def test_build_daily_action_shortcuts_prefers_button_label_and_dedupes():
    shortcuts = build_daily_action_shortcuts([
        {
            "priority": "urgent",
            "name": "급등주",
            "action": "손절 필요",
            "button_label": "⚡ 급등주 손절",
            "callback_data": "detail:111111",
        },
        {
            "priority": "caution",
            "name": "급등주",
            "action": "재점검",
            "callback_data": "detail:111111",
        },
    ])

    assert len(shortcuts) == 1
    assert shortcuts[0]["callback_data"] == "detail:111111"
    assert "급등주 손절" in shortcuts[0]["label"]


def test_format_daily_actions_includes_manager_label():
    text = format_daily_actions([
        {
            "priority": "urgent",
            "name": "미래주",
            "action": "손절 필요",
            "reason": "-8.1% (매니저 손절 -7%)",
            "manager_label": "🔥 스윙 매니저",
        },
    ])

    assert "🔥 스윙 매니저" in text
    assert "미래주: 손절 필요" in text


def test_format_daily_actions_renders_coach_lines_and_next_step():
    text = format_daily_actions(
        [
            {
                "priority": "opportunity",
                "name": "성장주",
                "action": "씨앗 포지션 검토",
                "reason": "정책 이벤트 · 초기 수급 유입",
                "execution_window": "오전: 10시 이후 체결 강도 유지 시 1차만",
                "next_step": "시초 추격 대신 2회 분할 접근",
            },
        ],
        coach_lines=[
            "기본 태세: 변동성 소화 후 강한 종목 선별",
            "회피: 지수 레버리지 추격",
        ],
    )

    assert "🧭 자동 코치" in text
    assert "기본 태세: 변동성 소화 후 강한 종목 선별" in text
    assert "회피: 지수 레버리지 추격" in text
    assert "타이밍: 오전: 10시 이후 체결 강도 유지 시 1차만" in text
    assert "다음 행동: 시초 추격 대신 2회 분할 접근" in text


def test_format_daily_actions_renders_allocation_lines():
    text = format_daily_actions(
        [
            {
                "priority": "opportunity",
                "name": "성장주",
                "action": "씨앗 포지션 검토",
                "reason": "정책 이벤트 · 초기 수급 유입",
                "allocation_summary": "권장 비중 5.0% · 기준 예산 10,000,000원 · 현금 바닥 20%",
                "allocation_split": "씨앗 2.5% · 눌림 1.5% · 확인 1.0%",
            },
        ],
    )

    assert "권장 비중 5.0%" in text
    assert "분할: 씨앗 2.5%" in text


def test_format_daily_actions_uses_holiday_title_when_market_closed():
    text = format_daily_actions(
        [
            {
                "priority": "check",
                "name": "포트폴리오",
                "action": "비중 점검",
                "reason": "주말 복기",
            },
        ],
        market_open=False,
        current_dt=datetime(2026, 3, 15, 11, 0),
    )

    assert "일요일 점검" in text
    assert "시장 상태: 휴장일" in text


def test_scan_manager_domain_prompt_prefers_momentum_for_swing_wartime(monkeypatch):
    captured = {}

    class _Response:
        status_code = 200

        def json(self):
            return {"content": [{"text": "현재 매수 타이밍 종목 없음"}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured["system"] = json.get("system", "")
            captured["user"] = json.get("messages", [{}])[0].get("content", "")
            return _Response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    import sys

    class _HttpxModule:
        @staticmethod
        def AsyncClient(timeout=20):
            return _Client()

    monkeypatch.setitem(sys.modules, "httpx", _HttpxModule())

    stocks = [
        {
            "ticker": "000660",
            "name": "SK하이닉스",
            "price": 910000,
            "rsi": 56,
            "vol_ratio": 210,
            "fit_score": 88,
            "composite": 81,
            "target_upside": 12.5,
            "foreign_days": 3,
            "inst_days": 2,
        }
    ]
    import asyncio
    text = asyncio.run(scan_manager_domain("swing", stocks, "VIX=18.0", alert_mode="wartime"))
    assert "윌리엄 오닐 매수 스캔" in text
    assert "주도주 눌림/추세 지속 후보" in captured["user"]
    assert "스윙은 단순 과매도 반등보다 주도주 눌림과 추세 지속을 우선한다" in captured["system"]
