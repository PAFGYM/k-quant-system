"""Phase 2 & 3 통합 테스트.

Phase 2-1: 매매일지 AI 복기
Phase 2-2: 섹터 로테이션
Phase 3-1: 역발상 시그널
Phase 3-2: 전략 백테스트 고도화
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


# ════════════════════════════════════════════════════════════════════
# Phase 2-1: 매매일지 AI 복기 테스트
# ════════════════════════════════════════════════════════════════════

class TestTradeJournal:
    """매매일지 AI 복기 시스템 테스트."""

    def _make_record(self, **kwargs):
        from kstock.core.trade_journal import TradeRecord
        defaults = dict(
            ticker="005930", name="삼성전자", action="buy",
            strategy="A", entry_price=70000, exit_price=73000,
            pnl_pct=4.3, hold_days=5, sector="반도체",
            horizon="swing", market_regime="neutral",
            trade_date="2025-01-15", weekday=2, hour=10,
        )
        defaults.update(kwargs)
        return TradeRecord(**defaults)

    def test_trade_record_creation(self):
        r = self._make_record()
        assert r.ticker == "005930"
        assert r.pnl_pct == 4.3
        assert r.sector == "반도체"

    def test_pattern_insight_creation(self):
        from kstock.core.trade_journal import PatternInsight
        p = PatternInsight(
            category="strategy", title="테스트",
            description="설명", confidence=0.8,
        )
        assert p.confidence == 0.8
        assert p.data == {}

    def test_journal_report_creation(self):
        from kstock.core.trade_journal import JournalReport
        r = JournalReport(
            period="weekly", date_range="2025-01-13 ~ 2025-01-17",
            total_trades=10, win_rate=60.0, avg_pnl=2.5,
            best_trade={"name": "삼성전자", "pnl": 8.5},
            worst_trade={"name": "LG화학", "pnl": -4.2},
            patterns=[],
        )
        assert r.win_rate == 60.0
        assert r.best_trade["pnl"] == 8.5

    def test_analyze_patterns_empty(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        result = journal.analyze_patterns([])
        assert result == []

    def test_analyze_by_strategy_winning(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(strategy="A", pnl_pct=5.0),
            self._make_record(strategy="A", pnl_pct=3.0),
            self._make_record(strategy="A", pnl_pct=4.0),
            self._make_record(strategy="A", pnl_pct=2.0),
        ]
        insights = journal._analyze_by_strategy(trades)
        assert len(insights) >= 1
        assert any("강점" in p.title for p in insights)

    def test_analyze_by_strategy_losing(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(strategy="B", pnl_pct=-3.0),
            self._make_record(strategy="B", pnl_pct=-5.0),
            self._make_record(strategy="B", pnl_pct=-2.0),
            self._make_record(strategy="B", pnl_pct=-4.0),
        ]
        insights = journal._analyze_by_strategy(trades)
        assert len(insights) >= 1
        assert any("약점" in p.title for p in insights)

    def test_analyze_by_sector(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(sector="반도체", pnl_pct=5.0),
            self._make_record(sector="반도체", pnl_pct=4.0),
            self._make_record(sector="바이오", pnl_pct=-4.0),
            self._make_record(sector="바이오", pnl_pct=-5.0),
        ]
        insights = journal._analyze_by_sector(trades)
        assert len(insights) >= 1

    def test_analyze_by_weekday(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(weekday=0, pnl_pct=5.0),
            self._make_record(weekday=0, pnl_pct=4.0),
            self._make_record(weekday=0, pnl_pct=3.0),
        ]
        insights = journal._analyze_by_weekday(trades)
        assert len(insights) >= 1
        assert any("월요일" in p.title for p in insights)

    def test_win_loss_patterns(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(pnl_pct=5.0),
            self._make_record(pnl_pct=-3.0),
            self._make_record(pnl_pct=8.0),
            self._make_record(pnl_pct=-2.0),
        ]
        insights = journal._analyze_win_loss_patterns(trades)
        assert len(insights) >= 2  # 수익 + 손실 + 손익비

    def test_rr_ratio_calculation(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(pnl_pct=10.0),
            self._make_record(pnl_pct=-5.0),
        ]
        insights = journal._analyze_win_loss_patterns(trades)
        rr_insights = [p for p in insights if "손익비" in p.title]
        assert len(rr_insights) == 1
        assert "2.0" in rr_insights[0].title  # 10/5 = 2.0

    def test_detect_repeated_mistakes_hold_through(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(action="hold_through", pnl_pct=-5.0),
            self._make_record(action="hold_through", pnl_pct=-3.0),
            self._make_record(action="stop_loss", pnl_pct=-4.0),
        ]
        insights = journal._detect_repeated_mistakes(trades)
        assert any("손절 미이행" in p.title for p in insights)

    def test_detect_repeated_mistakes_same_ticker(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(ticker="373220", name="에코프로", action="buy", pnl_pct=-2.0),
            self._make_record(ticker="373220", name="에코프로", action="buy", pnl_pct=-3.0),
            self._make_record(ticker="373220", name="에코프로", action="buy", pnl_pct=-1.5),
        ]
        insights = journal._detect_repeated_mistakes(trades)
        assert any("반복 매매" in p.title for p in insights)

    def test_build_review_prompt(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(pnl_pct=5.0),
            self._make_record(pnl_pct=-3.0),
        ]
        patterns = journal.analyze_patterns(trades)
        prompt = journal.build_review_prompt(trades, patterns)
        assert "주호님" in prompt
        assert "주간" in prompt

    def test_build_review_prompt_empty(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        assert journal.build_review_prompt([], []) == ""

    def test_generate_report(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal()
        trades = [
            self._make_record(pnl_pct=5.0, trade_date="2025-01-15"),
            self._make_record(pnl_pct=-3.0, trade_date="2025-01-16"),
            self._make_record(pnl_pct=2.0, trade_date="2025-01-17"),
        ]
        patterns = journal.analyze_patterns(trades)
        report = journal.generate_report(trades, patterns)
        assert report.total_trades == 3
        assert report.win_rate > 0
        assert report.best_trade is not None
        assert report.best_trade["pnl"] == 5.0

    def test_format_journal_report(self):
        from kstock.core.trade_journal import JournalReport, format_journal_report
        report = JournalReport(
            period="weekly", date_range="2025-01-13 ~ 2025-01-17",
            total_trades=10, win_rate=60.0, avg_pnl=2.5,
            best_trade={"name": "삼성전자", "pnl": 8.5},
            worst_trade={"name": "LG화학", "pnl": -4.2},
            patterns=[], ai_review="좋은 주간이었습니다.",
        )
        text = format_journal_report(report)
        assert "주간" in text
        assert "60%" in text
        assert "삼성전자" in text

    def test_format_journal_short(self):
        from kstock.core.trade_journal import JournalReport, format_journal_short
        report = JournalReport(
            period="weekly", date_range="test",
            total_trades=5, win_rate=80.0, avg_pnl=3.5,
            best_trade=None, worst_trade=None, patterns=[],
        )
        text = format_journal_short(report)
        assert "🟢" in text
        assert "80%" in text

    def test_collect_trades_without_db(self):
        from kstock.core.trade_journal import TradeJournal
        journal = TradeJournal(db=None)
        assert journal.collect_trades() == []


# ════════════════════════════════════════════════════════════════════
# Phase 2-2: 섹터 로테이션 테스트
# ════════════════════════════════════════════════════════════════════

class TestSectorRotation:
    """섹터 로테이션 엔진 테스트."""

    def _make_ohlcv(self, prices: list[float]) -> pd.DataFrame:
        """간단한 OHLCV DataFrame 생성."""
        n = len(prices)
        return pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=n),
            "open": prices,
            "high": [p * 1.02 for p in prices],
            "low": [p * 0.98 for p in prices],
            "close": prices,
            "volume": [1000000] * n,
        })

    def test_sector_momentum_creation(self):
        from kstock.core.sector_rotation import SectorMomentum
        s = SectorMomentum(sector="반도체", etf_code="091160")
        assert s.momentum_score == 0.0
        assert s.signal == ""

    def test_compute_momentum_empty(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()
        result = engine.compute_momentum({})
        assert result == []

    def test_compute_momentum_basic(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()

        # 상승 추세
        up_prices = list(range(100, 130))
        # 하락 추세
        down_prices = list(range(130, 100, -1))

        ohlcv_map = {
            "091160": self._make_ohlcv(up_prices),   # 반도체 상승
            "305540": self._make_ohlcv(down_prices),  # 2차전지 하락
        }

        result = engine.compute_momentum(ohlcv_map)
        assert len(result) >= 1
        # 반도체가 1등
        assert result[0].sector == "반도체"
        assert result[0].rank == 1
        assert result[0].return_1w_pct > 0

    def test_momentum_ranking(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()

        ohlcv_map = {
            "091160": self._make_ohlcv(list(range(100, 130))),  # +30%
            "305540": self._make_ohlcv([100] * 30),             # flat
            "244580": self._make_ohlcv(list(range(130, 100, -1))),  # -23%
        }

        result = engine.compute_momentum(ohlcv_map)
        assert len(result) == 3
        assert result[0].rank == 1
        assert result[2].rank == 3

    def test_momentum_signal_assignment(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()

        # 강한 상승: 모멘텀 > 5
        up_prices = list(range(100, 145))
        ohlcv_map = {"091160": self._make_ohlcv(up_prices)}
        result = engine.compute_momentum(ohlcv_map)
        assert len(result) == 1
        assert result[0].signal == "강세"

    def test_mean_reversion_signal(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()

        # 3개월 급락 후 최근 1주 반등
        prices = list(range(200, 140, -1))  # 60일 하락
        prices[-5:] = [142, 144, 146, 148, 150]  # 최근 5일 반등
        ohlcv_map = {"091160": self._make_ohlcv(prices)}
        result = engine.compute_momentum(ohlcv_map)
        assert len(result) == 1
        # 3개월 급락 + 1주 반등 = "반등 기대" (조건에 따라)

    def test_generate_signals_empty(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()
        assert engine.generate_signals([]) == []

    def test_generate_signals_overweight(self):
        from kstock.core.sector_rotation import SectorRotationEngine, SectorMomentum
        engine = SectorRotationEngine()
        sectors = [
            SectorMomentum(
                sector="반도체", etf_code="091160",
                momentum_score=10.0, rank=1, total_sectors=2,
            ),
            SectorMomentum(
                sector="바이오", etf_code="244580",
                momentum_score=-8.0, rank=2, total_sectors=2,
            ),
        ]
        signals = engine.generate_signals(sectors, {"바이오": 30})
        assert len(signals) >= 1
        # 반도체 overweight
        assert any(s.direction == "overweight" for s in signals)

    def test_portfolio_concentration_warning(self):
        from kstock.core.sector_rotation import SectorRotationEngine, SectorMomentum
        engine = SectorRotationEngine()
        sectors = [SectorMomentum(sector="반도체", etf_code="091160")]
        signals = engine.generate_signals(
            sectors, {"2차전지": 65},  # 65% 편중
        )
        assert any(s.signal_type == "overweight" for s in signals)

    def test_compute_portfolio_sectors(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()
        holdings = [
            {"ticker": "005930", "eval_amount": 50000000},   # 반도체
            {"ticker": "373220", "eval_amount": 80000000},   # 2차전지
            {"ticker": "207940", "eval_amount": 20000000},   # 바이오
        ]
        result = engine.compute_portfolio_sectors(holdings)
        assert "2차전지" in result
        assert result["2차전지"] > 50  # 80M / 150M = 53%

    def test_compute_portfolio_sectors_empty(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()
        assert engine.compute_portfolio_sectors([]) == {}

    def test_create_dashboard(self):
        from kstock.core.sector_rotation import SectorRotationEngine
        engine = SectorRotationEngine()
        ohlcv_map = {
            "091160": self._make_ohlcv(list(range(100, 130))),
        }
        dashboard = engine.create_dashboard(ohlcv_map)
        assert dashboard.timestamp
        assert len(dashboard.sectors) >= 1

    def test_format_sector_dashboard(self):
        from kstock.core.sector_rotation import (
            SectorDashboard, SectorMomentum, format_sector_dashboard,
        )
        dashboard = SectorDashboard(
            timestamp="2025-01-15 09:00",
            sectors=[
                SectorMomentum(
                    sector="반도체", etf_code="091160",
                    return_1w_pct=3.5, return_1m_pct=8.2,
                    momentum_score=7.5, rank=1, total_sectors=3,
                    signal="강세",
                ),
            ],
            signals=[],
            portfolio_sectors={"반도체": 40, "2차전지": 60},
            recommendations=["🟢 반도체 비중 확대 고려"],
        )
        text = format_sector_dashboard(dashboard)
        assert "섹터 로테이션" in text
        assert "반도체" in text
        assert "🔥" in text

    def test_format_sector_brief(self):
        from kstock.core.sector_rotation import (
            SectorDashboard, SectorMomentum, format_sector_brief,
        )
        dashboard = SectorDashboard(
            timestamp="2025-01-15",
            sectors=[
                SectorMomentum(sector="반도체", etf_code="091160", return_1m_pct=8.0, rank=1, total_sectors=2, signal="강세"),
                SectorMomentum(sector="바이오", etf_code="244580", return_1m_pct=-5.0, rank=2, total_sectors=2, signal="약세"),
            ],
            signals=[], portfolio_sectors={}, recommendations=[],
        )
        text = format_sector_brief(dashboard)
        assert "반도체" in text
        assert "바이오" in text


# ════════════════════════════════════════════════════════════════════
# Phase 3-1: 역발상 시그널 테스트
# ════════════════════════════════════════════════════════════════════

class TestContrarianSignal:
    """역발상 시그널 엔진 테스트."""

    def test_signal_creation(self):
        from kstock.signal.contrarian_signal import ContrarianSignal
        sig = ContrarianSignal(
            signal_type="fear_buy", ticker="005930", name="삼성전자",
            direction="BUY", strength=0.8, score_adj=15,
            reasons=["VIX 32 극단공포"],
        )
        assert sig.strength == 0.8
        assert sig.direction == "BUY"

    def test_fear_buy_signal(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            vix=35.0, rsi=18.0,          # 극단 공포 + 극단 과매도
            foreign_net_days=-7,          # 외인 7일 순매도
            bb_pctb=0.05,                 # BB 하단 이탈
        )
        buy_sigs = [s for s in signals if s.direction == "BUY" and s.signal_type == "fear_buy"]
        assert len(buy_sigs) >= 1
        assert buy_sigs[0].strength >= 0.5
        assert buy_sigs[0].score_adj > 0

    def test_no_fear_buy_weak_signal(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            vix=20.0, rsi=45.0,  # 모든 지표 정상
        )
        fear_sigs = [s for s in signals if s.signal_type == "fear_buy"]
        assert len(fear_sigs) == 0

    def test_greed_sell_signal(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            vix=11.0, rsi=82.0,                  # 극탐욕 + 과매수
            retail_net_buy_krw=80e8,              # 개인 800억 순매수
            bb_pctb=0.98,                         # BB 상단 돌파
        )
        sell_sigs = [s for s in signals if s.direction == "SELL" and s.signal_type == "greed_sell"]
        assert len(sell_sigs) >= 1

    def test_panic_selling_signal(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            volume_ratio=6.0,                     # 거래량 6배
            price_change_pct=-7.0,                # 7% 급락
            foreign_net_days=-4,
            institution_net_days=-4,
        )
        panic_sigs = [s for s in signals if s.signal_type == "panic_buy"]
        assert len(panic_sigs) >= 1

    def test_deep_value_buy(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            per=4.0, pbr=0.4, roe=12.0, debt_ratio=80.0,
            rsi=22.0,
        )
        value_sigs = [s for s in signals if s.signal_type == "value_buy"]
        assert len(value_sigs) >= 1

    def test_overvalued_warning(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="999999", name="테스트",
            per=55.0, pbr=6.0,  # 고평가
        )
        sell_sigs = [s for s in signals if s.signal_type == "value_sell"]
        assert len(sell_sigs) >= 1

    def test_margin_contrarian_buy(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            margin_change_pct=-20.0,  # 신용 급감
        )
        margin_sigs = [s for s in signals if s.signal_type == "margin_contrarian"]
        assert len(margin_sigs) >= 1
        assert margin_sigs[0].direction == "BUY"

    def test_margin_contrarian_sell(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            margin_change_pct=25.0,  # 신용 급증
        )
        margin_sigs = [s for s in signals if s.signal_type == "margin_contrarian"]
        assert len(margin_sigs) >= 1
        assert margin_sigs[0].direction == "SELL"

    def test_program_contrarian_buy(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            program_net_buy_krw=-80e8,  # 프로그램 800억 순매도
            price_change_pct=-3.0,
        )
        prog_sigs = [s for s in signals if s.signal_type == "program_contrarian"]
        assert len(prog_sigs) >= 1
        assert prog_sigs[0].direction == "BUY"

    def test_program_contrarian_sell(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            program_net_buy_krw=100e8,  # 프로그램 1000억 순매수
            price_change_pct=4.0,
        )
        prog_sigs = [s for s in signals if s.signal_type == "program_contrarian"]
        assert len(prog_sigs) >= 1
        assert prog_sigs[0].direction == "SELL"

    def test_market_fear_analysis(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        dashboard = engine.analyze_market(
            vix=35.0, fear_greed_label="극단공포",
            foreign_net_total_krw=-600e8,
        )
        assert dashboard.market_fear_level == "극단공포"
        assert len(dashboard.signals) >= 1
        assert any(s.direction == "BUY" for s in dashboard.signals)

    def test_market_greed_analysis(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        dashboard = engine.analyze_market(
            vix=11.0, fear_greed_label="극단탐욕",
        )
        assert any(s.direction == "SELL" for s in dashboard.signals)

    def test_market_neutral(self):
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        dashboard = engine.analyze_market(
            vix=20.0, fear_greed_label="중립",
        )
        assert "특이사항 없음" in dashboard.summary

    def test_format_contrarian_dashboard(self):
        from kstock.signal.contrarian_signal import (
            ContrarianDashboard, ContrarianSignal, format_contrarian_dashboard,
        )
        dashboard = ContrarianDashboard(
            timestamp="2025-01-15 14:00",
            market_fear_level="공포",
            vix=27.0,
            signals=[ContrarianSignal(
                signal_type="fear_buy", ticker="005930", name="삼성전자",
                direction="BUY", strength=0.7, score_adj=12,
                reasons=["VIX 27 공포", "RSI 22 과매도"],
            )],
            summary="🟢 역발상 매수 구간",
        )
        text = format_contrarian_dashboard(dashboard)
        assert "역발상" in text
        assert "삼성전자" in text

    def test_format_contrarian_alert(self):
        from kstock.signal.contrarian_signal import ContrarianSignal, format_contrarian_alert
        sig = ContrarianSignal(
            signal_type="fear_buy", ticker="005930", name="삼성전자",
            direction="BUY", strength=0.8, score_adj=15,
            reasons=["VIX 32 극단공포", "RSI 18 과매도"],
        )
        text = format_contrarian_alert(sig)
        assert "역발상" in text
        assert "BUY" in text

    def test_multiple_signals(self):
        """여러 시그널 동시 발생 테스트."""
        from kstock.signal.contrarian_signal import ContrarianEngine
        engine = ContrarianEngine()
        signals = engine.analyze(
            ticker="005930", name="삼성전자",
            vix=35.0, rsi=18.0,           # 공포 매수
            foreign_net_days=-8,
            bb_pctb=0.03,
            volume_ratio=6.0,             # 패닉셀링
            price_change_pct=-8.0,
            institution_net_days=-5,
            per=5.0, pbr=0.4,            # 딥밸류
            roe=12.0, debt_ratio=70.0,
        )
        assert len(signals) >= 2  # 최소 2개 시그널


# ════════════════════════════════════════════════════════════════════
# Phase 3-2: 전략 백테스트 고도화 테스트
# ════════════════════════════════════════════════════════════════════

class TestAdvancedBacktest:
    """고급 백테스트 엔진 테스트."""

    def _sample_pnls(self, n: int = 50, seed: int = 42) -> list[float]:
        """테스트용 거래 수익률 리스트 생성."""
        rng = np.random.default_rng(seed)
        return list(rng.normal(1.5, 5.0, size=n).round(2))

    # Monte Carlo 테스트 ──────────────────────────────────────────

    def test_monte_carlo_basic(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls()
        result = bt.run_monte_carlo(pnls, n_simulations=1000, seed=42)
        assert result.n_simulations == 1000
        assert result.n_trades == 50
        assert result.probability_positive > 0

    def test_monte_carlo_percentiles_ordered(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls()
        result = bt.run_monte_carlo(pnls, n_simulations=5000, seed=42)
        assert result.percentile_5 <= result.percentile_25
        assert result.percentile_25 <= result.percentile_75
        assert result.percentile_75 <= result.percentile_95

    def test_monte_carlo_empty(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        result = bt.run_monte_carlo([])
        assert result.n_simulations == 0

    def test_monte_carlo_positive_bias(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        # 모든 거래 수익
        pnls = [3.0, 5.0, 2.0, 4.0, 6.0, 1.0, 3.5, 2.5]
        result = bt.run_monte_carlo(pnls, n_simulations=1000, seed=42)
        assert result.probability_positive > 90

    def test_monte_carlo_negative_bias(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        # 모든 거래 손실
        pnls = [-3.0, -5.0, -2.0, -4.0, -6.0, -1.0]
        result = bt.run_monte_carlo(pnls, n_simulations=1000, seed=42)
        assert result.probability_positive < 10

    def test_monte_carlo_custom_trades(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls()
        result = bt.run_monte_carlo(pnls, n_simulations=500, n_trades=30, seed=42)
        assert result.n_trades == 30

    def test_monte_carlo_target_probability(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls()
        result = bt.run_monte_carlo(
            pnls, n_simulations=1000, target_return_pct=50.0, seed=42,
        )
        assert 0 <= result.probability_target <= 100

    # Walk-Forward 테스트 ─────────────────────────────────────────

    def test_walk_forward_basic(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls(100)
        result = bt.run_walk_forward(pnls, n_windows=4)
        assert result.n_windows >= 2
        assert result.robustness in ("robust", "moderate", "fragile")

    def test_walk_forward_too_few_trades(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        result = bt.run_walk_forward([1.0, 2.0, -1.0])
        assert result.n_windows == 0
        assert result.robustness == "fragile"

    def test_walk_forward_consistency(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        # 안정적인 양의 수익
        pnls = list(np.random.default_rng(42).normal(2.0, 1.0, 100))
        result = bt.run_walk_forward(pnls, n_windows=5)
        assert result.consistency_score >= 0.5

    def test_walk_forward_decay(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls(80)
        result = bt.run_walk_forward(pnls, n_windows=4)
        assert isinstance(result.sharpe_decay_pct, float)

    # 리스크 지표 테스트 ──────────────────────────────────────────

    def test_risk_metrics_basic(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls()
        metrics = bt.compute_risk_metrics(pnls)
        assert isinstance(metrics.sharpe_ratio, float)
        assert isinstance(metrics.sortino_ratio, float)
        assert isinstance(metrics.calmar_ratio, float)

    def test_risk_metrics_empty(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        metrics = bt.compute_risk_metrics([])
        assert metrics.sharpe_ratio == 0
        assert metrics.max_consecutive_losses == 0

    def test_max_consecutive_losses(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = [3.0, -1.0, -2.0, -3.0, -4.0, 5.0, -1.0]
        metrics = bt.compute_risk_metrics(pnls)
        assert metrics.max_consecutive_losses == 4  # 4연패

    def test_omega_ratio(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        # 수익 > 손실
        pnls = [5.0, 3.0, 4.0, -1.0, -2.0]
        metrics = bt.compute_risk_metrics(pnls)
        assert metrics.omega_ratio > 1.0

    def test_risk_metrics_with_benchmark(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        pnls = self._sample_pnls(20)
        benchmark = [0.5] * 20  # 벤치마크 매일 +0.5%
        metrics = bt.compute_risk_metrics(pnls, benchmark_pnls=benchmark)
        assert isinstance(metrics.information_ratio, float)

    # 전략 비교 테스트 ────────────────────────────────────────────

    def test_strategy_comparison(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        rng = np.random.default_rng(42)
        strategy_results = {
            "A": list(rng.normal(2.0, 3.0, 30)),
            "F": list(rng.normal(1.5, 4.0, 30)),
            "G": list(rng.normal(0.5, 5.0, 30)),
        }
        comp = bt.compare_strategies(strategy_results)
        assert comp.best_strategy in ("A", "F", "G")
        assert len(comp.ranking) == 3
        assert comp.strategies["A"]["trades"] == 30

    def test_strategy_comparison_empty(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        comp = bt.compare_strategies({"A": [], "B": []})
        assert comp.best_strategy == ""
        assert comp.ranking == []

    def test_strategy_comparison_ranking_order(self):
        from kstock.backtest.advanced import AdvancedBacktester
        bt = AdvancedBacktester()
        comp = bt.compare_strategies({
            "A": [5.0, 3.0, 4.0, 2.0, 6.0],     # 좋은 성과
            "B": [-3.0, -5.0, -2.0, 1.0, -4.0],  # 나쁜 성과
        })
        assert comp.ranking[0][0] == "A"  # A가 1등

    # 포맷 테스트 ────────────────────────────────────────────────

    def test_format_monte_carlo(self):
        from kstock.backtest.advanced import MonteCarloResult, format_monte_carlo
        result = MonteCarloResult(
            n_simulations=5000, n_trades=50,
            median_return_pct=15.3, mean_return_pct=16.1, std_return_pct=12.5,
            percentile_5=-8.2, percentile_25=5.1,
            percentile_75=25.3, percentile_95=42.1,
            probability_positive=72.3, probability_target=45.2,
            max_drawdown_median=-8.5, var_95=-8.2,
        )
        text = format_monte_carlo(result)
        assert "Monte Carlo" in text
        assert "5,000" in text
        assert "72" in text

    def test_format_monte_carlo_empty(self):
        from kstock.backtest.advanced import MonteCarloResult, format_monte_carlo
        result = MonteCarloResult(
            n_simulations=0, n_trades=0,
            median_return_pct=0, mean_return_pct=0, std_return_pct=0,
            percentile_5=0, percentile_25=0, percentile_75=0, percentile_95=0,
            probability_positive=0, probability_target=0,
            max_drawdown_median=0, var_95=0,
        )
        text = format_monte_carlo(result)
        assert "불가" in text

    def test_format_walk_forward(self):
        from kstock.backtest.advanced import WalkForwardResult, format_walk_forward
        result = WalkForwardResult(
            n_windows=4,
            window_results=[
                {"window": 1, "train_size": 15, "test_size": 5,
                 "train_sharpe": 1.5, "test_sharpe": 1.2,
                 "train_return": 10.5, "test_return": 3.2},
            ],
            avg_train_sharpe=1.5, avg_test_sharpe=1.2,
            sharpe_decay_pct=20.0, consistency_score=0.75,
            robustness="moderate",
        )
        text = format_walk_forward(result)
        assert "Walk-Forward" in text
        assert "보통" in text

    def test_format_strategy_comparison(self):
        from kstock.backtest.advanced import StrategyComparison, format_strategy_comparison
        comp = StrategyComparison(
            strategies={
                "A": {"name": "단기반등", "trades": 30, "win_rate": 65.0,
                       "avg_pnl": 2.5, "total_return": 35.0,
                       "sharpe": 1.8, "max_drawdown": 8.5, "profit_factor": 2.1},
            },
            best_strategy="A",
            best_sharpe=1.8,
            ranking=[("A", 1.8)],
        )
        text = format_strategy_comparison(comp)
        assert "전략 성과 비교" in text
        assert "🥇" in text

    def test_format_risk_metrics(self):
        from kstock.backtest.advanced import RiskAdjustedMetrics, format_risk_metrics
        metrics = RiskAdjustedMetrics(
            sharpe_ratio=1.5, sortino_ratio=2.1, calmar_ratio=1.8,
            omega_ratio=2.5, information_ratio=0.8,
            max_consecutive_losses=3, recovery_factor=4.2,
        )
        text = format_risk_metrics(metrics)
        assert "Sharpe" in text
        assert "Sortino" in text


# ════════════════════════════════════════════════════════════════════
# 통합 테스트
# ════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Phase 2 & 3 통합 테스트."""

    def test_all_modules_importable(self):
        """모든 새 모듈이 임포트 가능한지 확인."""
        from kstock.core.trade_journal import TradeJournal, format_journal_report
        from kstock.core.sector_rotation import SectorRotationEngine, format_sector_dashboard
        from kstock.signal.contrarian_signal import ContrarianEngine, format_contrarian_dashboard
        from kstock.backtest.advanced import AdvancedBacktester, format_monte_carlo

    def test_journal_with_contrarian(self):
        """매매일지에서 역발상 시그널 패턴 분석."""
        from kstock.core.trade_journal import TradeJournal, TradeRecord

        journal = TradeJournal()
        # 공포 매수 → 성공 패턴
        trades = [
            TradeRecord(
                ticker="005930", name="삼성전자", action="buy",
                strategy="A", entry_price=68000, exit_price=74000,
                pnl_pct=8.8, sector="반도체",
                trade_date="2025-01-10", weekday=4, hour=14,
            ),
            TradeRecord(
                ticker="000660", name="SK하이닉스", action="buy",
                strategy="A", entry_price=130000, exit_price=142000,
                pnl_pct=9.2, sector="반도체",
                trade_date="2025-01-13", weekday=0, hour=10,
            ),
        ]
        patterns = journal.analyze_patterns(trades)
        report = journal.generate_report(trades, patterns)
        assert report.win_rate == 100.0

    def test_sector_rotation_with_portfolio(self):
        """실 포트폴리오 섹터 비중과 로테이션 시그널."""
        from kstock.core.sector_rotation import SectorRotationEngine
        import pandas as pd

        engine = SectorRotationEngine()
        holdings = [
            {"ticker": "373220", "eval_amount": 80000000},  # 에코프로 2차전지
            {"ticker": "005380", "eval_amount": 30000000},  # 현대차
        ]
        weights = engine.compute_portfolio_sectors(holdings)
        assert "2차전지" in weights
        assert weights["2차전지"] > 70  # 72.7%

    def test_backtest_with_contrarian(self):
        """역발상 시그널 + 백테스트 통합."""
        from kstock.backtest.advanced import AdvancedBacktester

        bt = AdvancedBacktester()
        # 역발상 전략 거래 수익률
        contrarian_pnls = [5.0, -2.0, 8.0, 3.0, -1.0, 12.0, -3.0, 6.0]
        mc = bt.run_monte_carlo(contrarian_pnls, n_simulations=1000, seed=42)
        assert mc.probability_positive > 50

        metrics = bt.compute_risk_metrics(contrarian_pnls)
        assert metrics.sharpe_ratio > 0
