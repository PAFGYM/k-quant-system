"""통합 테스트: SQLiteStore Mixin 간 상호작용 + async executor."""

from __future__ import annotations

import asyncio

import pytest

from kstock.store.sqlite import SQLiteStore


@pytest.fixture
def db(tmp_path):
    return SQLiteStore(db_path=tmp_path / "integ.db")


# ── Mixin 간 상호작용 ────────────────────────────────────────


class TestPortfolioTradingInteraction:
    """PortfolioMixin + TradingMixin 간 데이터 흐름."""

    def test_holding_then_trade(self, db):
        """보유종목 추가 → 매도 거래 기록 → 보유종목 상태 업데이트."""
        hid = db.add_holding("005930", "삼성전자", 70000, "2024-01-15")
        assert hid is not None

        # 매도 거래 기록
        tid = db.add_trade("005930", "삼성전자", "A", "sell", 75000, 75000, 100, 7.14)
        assert tid is not None

        # 보유종목 상태 업데이트
        db.update_holding(hid, status="sold", pnl_pct=7.14, sold_pct=100)
        h = db.get_holding(hid)
        assert h["status"] == "sold"
        assert h["pnl_pct"] == 7.14

    def test_recommendation_to_holding_flow(self, db):
        """추천 → 장바구니 → 보유종목 전환 흐름."""
        rid = db.add_recommendation("005930", "삼성전자", 70000, 85.0)
        assert rid is not None

        # 추천 결과 기록
        db.add_recommendation_result(rid, "005930", 70000, "A")

        # 보유종목으로 전환
        hid = db.add_holding("005930", "삼성전자", 70000, "2024-01-15")
        assert hid is not None

        # 추천 상태 업데이트 (profit = 완료)
        db.update_recommendation(rid, status="profit")
        recs = db.get_completed_recommendations()
        assert any(r["id"] == rid for r in recs)


class TestMarketPortfolioInteraction:
    """MarketMixin + PortfolioMixin 간 데이터 흐름."""

    def test_consensus_affects_portfolio_context(self, db):
        """컨센서스 데이터 + 보유종목 상호 조회."""
        db.add_holding("005930", "삼성전자", 70000, "2024-01-15")
        db.upsert_consensus(
            "005930", name="삼성전자",
            avg_target_price=85000, current_price=72000, upside_pct=18.1,
            buy_count=15, hold_count=3, sell_count=0,
        )

        holdings = db.get_active_holdings()
        assert len(holdings) == 1

        consensus = db.get_consensus("005930")
        assert consensus is not None
        assert consensus["avg_target_price"] == 85000

    def test_report_for_watchlist_ticker(self, db):
        """관심종목에 대한 증권사 리포트 조회."""
        db.add_watchlist("005930", "삼성전자", target_price=80000)
        db.add_report("naver", "삼성전자 목표가 상향", "미래에셋", "2024-01-15",
                       ticker="005930", target_price=85000, prev_target_price=80000)

        wl = db.get_watchlist()
        tickers = [w["ticker"] for w in wl]
        reports = db.get_reports_for_tickers(tickers)
        assert len(reports) >= 1
        assert reports[0]["ticker"] == "005930"


class TestMetaTradingInteraction:
    """MetaMixin + TradingMixin 간 데이터 흐름."""

    def test_event_log_for_trade(self, db):
        """거래 실행 + 이벤트 로그 기록 연동."""
        tid = db.add_trade("005930", "삼성전자", "A", "buy", 70000, 70000, 100, 0)
        assert tid is not None

        db.add_event("trade", "info", f"매수 체결: 삼성전자 70,000원",
                     source="trade_executor", ticker="005930")

        events = db.get_events(event_type="trade")
        assert len(events) >= 1
        assert "005930" in events[0]["ticker"]

    def test_holding_analysis_for_portfolio(self, db):
        """보유종목 + 분석 데이터 연동."""
        hid = db.add_holding("005930", "삼성전자", 70000, "2024-01-15")
        db.upsert_holding_analysis(hid, "005930", "삼성전자",
                                    ai_analysis="매수 유지", ai_suggestion="분할매수 추천")

        analysis = db.get_holding_analysis(hid)
        assert analysis is not None
        assert analysis["ticker"] == "005930"

        all_analyses = db.get_all_holding_analyses()
        assert len(all_analyses) >= 1


class TestGlobalNewsIntegration:
    """글로벌 뉴스 + 시장 데이터 통합."""

    def test_news_save_and_retrieve(self, db):
        """뉴스 저장 → 조회 → 정리 흐름."""
        items = [
            {"title": "Fed 금리 동결", "source": "Reuters", "url": "http://ex.com/1",
             "category": "macro", "lang": "en", "impact_score": 8, "is_urgent": True},
            {"title": "삼성전자 실적 발표", "source": "연합뉴스", "url": "http://ex.com/2",
             "category": "earnings", "lang": "ko", "impact_score": 6, "is_urgent": False},
        ]
        saved = db.save_global_news(items)
        assert saved == 2

        # 중복 저장 안됨
        saved2 = db.save_global_news(items)
        assert saved2 == 0

        # 긴급 뉴스만 조회
        urgent = db.get_recent_global_news(urgent_only=True)
        assert len(urgent) == 1
        assert urgent[0]["title"] == "Fed 금리 동결"

    def test_news_cleanup(self, db):
        """오래된 뉴스 정리."""
        db.save_global_news([
            {"title": "old news", "source": "test", "url": "http://old.com/1",
             "impact_score": 1},
        ])
        # 뉴스가 저장되었는지 확인
        news = db.get_recent_global_news(hours=1)
        assert len(news) >= 1
        # days=365로 정리하면 최근 1년 이내는 유지됨 (삭제 안됨)
        deleted = db.cleanup_old_news(days=365)
        assert deleted == 0


class TestMixinComposition:
    """Mixin 합성 레이어 호환성."""

    def test_sqlite_store_has_all_methods(self, db):
        """SQLiteStore가 모든 Mixin 메서드를 보유."""
        # Portfolio
        assert hasattr(db, "add_holding")
        assert hasattr(db, "get_portfolio")
        assert hasattr(db, "add_watchlist")

        # Trading
        assert hasattr(db, "add_trade")
        assert hasattr(db, "add_recommendation")
        assert hasattr(db, "add_swing_trade")

        # Market
        assert hasattr(db, "add_report")
        assert hasattr(db, "upsert_consensus")
        assert hasattr(db, "save_global_news")

        # Meta
        assert hasattr(db, "insert_alert")
        assert hasattr(db, "add_event")
        assert hasattr(db, "add_chat_message")

        # Base
        assert hasattr(db, "upsert_job_run")
        assert hasattr(db, "run_in_executor")

    def test_schema_complete(self, db):
        """모든 테이블이 생성됨."""
        with db._connect() as conn:
            tables = [
                row[0] for row in
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
        expected = [
            "job_runs", "portfolio", "alerts", "holdings", "watchlist",
            "trades", "orders", "recommendations", "reports", "consensus",
            "global_news", "event_log",
        ]
        for t in expected:
            assert t in tables, f"테이블 {t} 누락"


# ── Async Executor 테스트 ────────────────────────────────────


class TestAsyncExecutor:
    """run_in_executor를 통한 비동기 DB 호출."""

    def test_run_in_executor_basic(self, db):
        """기본 async executor 동작."""
        async def _test():
            result = await db.run_in_executor(db.get_active_holdings)
            assert isinstance(result, list)
            assert len(result) == 0

        asyncio.get_event_loop().run_until_complete(_test())

    def test_run_in_executor_with_write(self, db):
        """async executor로 데이터 쓰기/읽기."""
        async def _test():
            hid = await db.run_in_executor(
                db.add_holding, "005930", "삼성전자", 70000, "2024-01-15"
            )
            assert hid is not None

            holdings = await db.run_in_executor(db.get_active_holdings)
            assert len(holdings) == 1
            assert holdings[0]["ticker"] == "005930"

        asyncio.get_event_loop().run_until_complete(_test())

    def test_run_in_executor_concurrent(self, db):
        """여러 async DB 호출 동시 실행."""
        # 데이터 먼저 세팅
        db.add_holding("005930", "삼성전자", 70000, "2024-01-15")
        db.upsert_portfolio("005930", name="삼성전자", score=85.0, signal="BUY")

        async def _test():
            results = await asyncio.gather(
                db.run_in_executor(db.get_active_holdings),
                db.run_in_executor(db.get_portfolio),
                db.run_in_executor(db.get_last_job_run, "test"),
            )
            assert len(results[0]) == 1  # holdings
            assert len(results[1]) == 1  # portfolio
            assert results[2] is None    # no job run

        asyncio.get_event_loop().run_until_complete(_test())


# ── Timezone 모듈 테스트 ─────────────────────────────────────


class TestTimezoneModule:
    """centralized tz module 동작."""

    def test_kst_offset(self):
        from kstock.core.tz import KST
        from datetime import timedelta
        assert KST.utcoffset(None) == timedelta(hours=9)

    def test_us_eastern_is_dst_aware(self):
        from kstock.core.tz import US_EASTERN, now_us_eastern
        from datetime import datetime
        now = now_us_eastern()
        assert now.tzinfo is not None
        # offset should be either -5 (EST) or -4 (EDT)
        offset_hours = now.utcoffset().total_seconds() / 3600
        assert offset_hours in (-5, -4)

    def test_now_kst_returns_aware(self):
        from kstock.core.tz import now_kst
        result = now_kst()
        assert result.tzinfo is not None
