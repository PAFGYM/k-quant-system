"""Tests for manager discovery differentiation and action digest."""

from kstock.bot.bot_imports import ScanResult
from kstock.bot.investment_managers import (
    enrich_watchlist_candidate,
    filter_discovery_candidates,
    format_manager_action_digest,
)
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
    assert "국내 스몰캡 레이더" in text
    assert "선점 구간" in text
