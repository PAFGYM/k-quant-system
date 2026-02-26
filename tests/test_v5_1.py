"""v5.1 테스트 — 폴백 매수 차단, 리스크 정책 통일, 이벤트로그 내구성.

전문가 피드백 기반:
  1. 폴백(yfinance/naver) 데이터로 신규 매수 차단
  2. 리스크 임계치 단일 소스 (RiskPolicy)
  3. 이벤트로그 즉시 flush + 내구성 강화
"""

import pytest
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════
# 1. 폴백 데이터 매수 차단 테스트
# ═══════════════════════════════════════════════════════════

class TestFallbackBuyBlock:
    """DataRouter의 폴백 데이터 매수 차단 기능."""

    def test_realtime_source_allows_buy(self):
        """KIS 실시간 소스는 매수 허용."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        router._last_source_used = "kis_realtime"
        can_buy, reason = router.can_buy_with_current_data()
        assert can_buy is True
        assert reason == ""

    def test_yfinance_blocks_buy(self):
        """yfinance 소스는 매수 차단."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        router._last_source_used = "yfinance"
        can_buy, reason = router.can_buy_with_current_data()
        assert can_buy is False
        assert "지연 데이터" in reason
        assert "yfinance" in reason

    def test_naver_blocks_buy(self):
        """Naver Finance 소스는 매수 차단."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        router._last_source_used = "naver"
        can_buy, reason = router.can_buy_with_current_data()
        assert can_buy is False
        assert "naver" in reason

    def test_no_source_blocks_buy(self):
        """소스 미확인 시 매수 차단."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        router._last_source_used = ""
        can_buy, reason = router.can_buy_with_current_data()
        assert can_buy is False
        assert "미확인" in reason

    def test_is_realtime_property(self):
        """is_realtime 프로퍼티 확인."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        router._last_source_used = "kis_realtime"
        assert router.is_realtime is True
        assert router.is_delayed is False

    def test_is_delayed_property(self):
        """is_delayed 프로퍼티 확인."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        router._last_source_used = "yfinance"
        assert router.is_delayed is True
        assert router.is_realtime is False

    def test_estimated_delay_seconds(self):
        """소스별 추정 지연 시간."""
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()

        router._last_source_used = "kis_realtime"
        assert router.estimated_delay_seconds == 0.5

        router._last_source_used = "yfinance"
        assert router.estimated_delay_seconds == 900  # 15분

        router._last_source_used = "naver"
        assert router.estimated_delay_seconds == 1200  # 20분


class TestPreTradeDataQuality:
    """PreTradeValidator의 데이터 품질 체크."""

    def test_buy_blocked_with_delayed_data(self):
        """지연 데이터 사용 시 매수 차단."""
        from kstock.broker.order_manager import PreTradeValidator

        validator = PreTradeValidator()
        # 지연 데이터 체커 연결
        validator.set_data_source_checker(
            lambda: (False, "지연 데이터 소스(yfinance) — 매수 차단")
        )

        result = validator.validate("005930", "buy", 10, 75000)
        assert result.approved is False
        assert any("데이터품질" in r for r in result.reasons)

    def test_sell_allowed_with_delayed_data(self):
        """지연 데이터여도 매도는 허용."""
        from kstock.broker.order_manager import PreTradeValidator

        validator = PreTradeValidator()
        validator.set_data_source_checker(
            lambda: (False, "지연 데이터 소스(yfinance) — 매수 차단")
        )

        result = validator.validate("005930", "sell", 10, 75000)
        # 매도는 데이터 품질 체크 안 함
        assert result.approved is True

    def test_buy_allowed_with_realtime_data(self):
        """실시간 데이터 사용 시 매수 허용."""
        from kstock.broker.order_manager import PreTradeValidator

        validator = PreTradeValidator()
        validator.set_data_source_checker(lambda: (True, ""))

        result = validator.validate("005930", "buy", 10, 75000)
        assert result.approved is True

    def test_no_checker_allows_buy(self):
        """데이터 체커 미설정 시 매수 허용 (하위호환)."""
        from kstock.broker.order_manager import PreTradeValidator

        validator = PreTradeValidator()
        # checker 미설정
        result = validator.validate("005930", "buy", 10, 75000)
        assert result.approved is True

    def test_checker_exception_allows_buy(self):
        """데이터 체커 예외 시에도 매수 허용 (안전)."""
        from kstock.broker.order_manager import PreTradeValidator

        validator = PreTradeValidator()
        validator.set_data_source_checker(lambda: (_ for _ in ()).throw(RuntimeError("test")))

        result = validator.validate("005930", "buy", 10, 75000)
        # 예외 시 차단하지 않음 (안전 방향)
        assert result.approved is True


class TestOrderLedgerDataQuality:
    """OrderLedger에서 폴백 매수 차단 통합."""

    def test_order_blocked_with_delayed_data(self):
        """OrderLedger 통해 폴백 데이터 매수 시 주문 차단."""
        from kstock.broker.order_manager import OrderLedger

        ledger = OrderLedger()
        ledger.validator.set_data_source_checker(
            lambda: (False, "yfinance 지연 데이터 — 매수 차단")
        )

        order, msg = ledger.create_order(
            ticker="005930", name="삼성전자",
            side="buy", quantity=10, price=75000,
        )
        assert order is not None
        assert order.state.value == "blocked"
        assert "데이터품질" in order.block_reason

    def test_sell_order_passes_with_delayed_data(self):
        """매도 주문은 폴백 데이터여도 통과."""
        from kstock.broker.order_manager import OrderLedger

        ledger = OrderLedger()
        ledger.validator.set_data_source_checker(
            lambda: (False, "yfinance 지연 데이터 — 매수 차단")
        )

        order, msg = ledger.create_order(
            ticker="005930", name="삼성전자",
            side="sell", quantity=10, price=75000,
        )
        assert order is not None
        assert order.state.value == "validated"


# ═══════════════════════════════════════════════════════════
# 2. 리스크 정책 통일 테스트
# ═══════════════════════════════════════════════════════════

class TestRiskPolicy:
    """RiskPolicy 단일 소스 테스트."""

    def test_default_policy(self):
        """기본 정책 값 확인."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy()

        # 주문 레벨
        assert policy.order_max_single_pct == 15.0
        assert policy.order_max_daily_count == 10
        assert policy.order_daily_loss_limit_pct == -3.0

        # 포트폴리오 레벨
        assert policy.portfolio_max_mdd == -0.15
        assert policy.portfolio_emergency_mdd == -0.20
        assert policy.portfolio_max_daily_loss == -0.05
        assert policy.portfolio_max_stock_weight == 0.40
        assert policy.portfolio_max_sector_weight == 0.60

    def test_to_risk_limits_dict(self):
        """RISK_LIMITS 형식 변환 확인."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy()
        limits = policy.to_risk_limits_dict()

        assert limits["max_portfolio_mdd"] == -0.15
        assert limits["emergency_mdd"] == -0.20
        assert limits["max_single_stock_weight"] == 0.40
        assert limits["max_sector_weight"] == 0.60

    def test_to_safety_limits_kwargs(self):
        """SafetyLimits kwargs 변환 확인."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy()
        kwargs = policy.to_safety_limits_kwargs()

        assert kwargs["max_order_pct"] == 15.0
        assert kwargs["max_daily_orders"] == 10
        assert kwargs["daily_loss_limit_pct"] == -3.0

    def test_custom_policy(self):
        """커스텀 정책 생성."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy(
            order_max_single_pct=20.0,
            portfolio_max_stock_weight=0.50,
        )
        assert policy.order_max_single_pct == 20.0
        assert policy.portfolio_max_stock_weight == 0.50

    def test_global_instance(self):
        """글로벌 인스턴스 관리."""
        from kstock.core import risk_policy
        # 글로벌 초기화
        risk_policy._policy = None
        p1 = risk_policy.get_risk_policy()
        p2 = risk_policy.get_risk_policy()
        assert p1 is p2  # 싱글턴

    def test_set_global_policy(self):
        """글로벌 정책 교체."""
        from kstock.core.risk_policy import (
            RiskPolicy, get_risk_policy, set_risk_policy,
        )
        import kstock.core.risk_policy as mod

        mod._policy = None
        custom = RiskPolicy(order_max_single_pct=25.0)
        set_risk_policy(custom)
        assert get_risk_policy().order_max_single_pct == 25.0
        # 정리
        mod._policy = None

    def test_format_summary(self):
        """텔레그램 포맷 출력."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy()
        summary = policy.format_summary()
        assert "리스크 정책" in summary
        assert "15.0%" in summary
        assert "40%" in summary

    def test_risk_manager_reads_policy(self):
        """RiskManager가 RiskPolicy에서 한도를 읽는지 확인."""
        from kstock.core.risk_manager import _get_risk_limits
        limits = _get_risk_limits()
        assert "max_portfolio_mdd" in limits
        assert "max_single_stock_weight" in limits
        assert limits["max_portfolio_mdd"] == -0.15

    def test_policy_consistency(self):
        """주문 레벨과 포트폴리오 레벨 임계치의 일관성 확인."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy()

        # 주문 일일 손실 < 포트폴리오 일일 손실 (주문 레벨이 더 보수적)
        assert abs(policy.order_daily_loss_limit_pct / 100) < abs(policy.portfolio_max_daily_loss)

    def test_safety_thresholds_ordered(self):
        """안전모드 임계치 순서 정합성."""
        from kstock.core.risk_policy import RiskPolicy
        policy = RiskPolicy()
        assert policy.safety_caution_threshold < policy.safety_safe_threshold
        assert policy.safety_safe_threshold < policy.safety_lockdown_threshold


# ═══════════════════════════════════════════════════════════
# 3. 이벤트로그 내구성 테스트
# ═══════════════════════════════════════════════════════════

class TestEventLogDurability:
    """이벤트로그 즉시 flush + 내구성."""

    def test_log_returns_save_status(self):
        """이벤트 로그 DB 없이도 동작."""
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity

        log = EventLog()  # DB 없음
        event = Event(
            event_type=EventType.SYSTEM_START,
            severity=EventSeverity.INFO,
            message="시스템 시작",
        )
        log.log(event)
        assert log.total_events == 1
        # DB 없으면 pending에 추가
        assert len(log._pending_flush) == 1

    def test_pending_flush_with_db(self):
        """DB 연결 후 미저장 이벤트 flush."""
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity

        log = EventLog()  # DB 없음
        event = Event(
            event_type=EventType.RISK_KILL_SWITCH,
            severity=EventSeverity.CRITICAL,
            message="킬스위치 활성화",
        )
        log.log(event)
        assert len(log._pending_flush) == 1

        # DB 연결 시뮬레이션
        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_db._connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db._connect.return_value.__exit__ = MagicMock(return_value=False)
        log.db = mock_db

        saved = log.flush_pending()
        assert saved == 1
        assert len(log._pending_flush) == 0

    def test_listener_notification(self):
        """이벤트 리스너 알림."""
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity

        log = EventLog()
        received = []
        log.add_listener(lambda e: received.append(e))

        event = Event(
            event_type=EventType.ORDER_BLOCKED,
            severity=EventSeverity.WARNING,
            message="매수 차단",
        )
        log.log(event)
        assert len(received) == 1
        assert received[0].message == "매수 차단"

    def test_listener_exception_doesnt_block(self):
        """리스너 예외가 로깅을 막지 않음."""
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity

        log = EventLog()
        log.add_listener(lambda e: (_ for _ in ()).throw(RuntimeError("bad listener")))

        event = Event(
            event_type=EventType.SYSTEM_ERROR,
            severity=EventSeverity.ERROR,
            message="에러 발생",
        )
        # 예외가 나도 이벤트는 기록됨
        log.log(event)
        assert log.total_events == 1

    def test_data_buy_blocked_event_type(self):
        """v5.1 새 이벤트 타입 확인."""
        from kstock.core.event_log import EventType
        assert EventType.DATA_BUY_BLOCKED.value == "data.buy_blocked"

    def test_flush_pending_empty(self):
        """미저장 이벤트 없으면 flush 0."""
        from kstock.core.event_log import EventLog
        log = EventLog()
        assert log.flush_pending() == 0


# ═══════════════════════════════════════════════════════════
# 4. 통합 테스트
# ═══════════════════════════════════════════════════════════

class TestV51Integration:
    """v5.1 모듈 간 통합 테스트."""

    def test_data_router_to_pretrade_integration(self):
        """DataRouter → PreTradeValidator 연동."""
        from kstock.ingest.data_router import DataRouter
        from kstock.broker.order_manager import OrderLedger

        router = DataRouter()
        router._last_source_used = "naver"  # 지연 소스

        ledger = OrderLedger()
        ledger.validator.set_data_source_checker(router.can_buy_with_current_data)

        # 매수 차단
        order, msg = ledger.create_order(
            ticker="005930", name="삼성전자",
            side="buy", quantity=10, price=75000,
        )
        assert order.state.value == "blocked"
        assert "데이터품질" in order.block_reason

        # 소스를 실시간으로 변경
        router._last_source_used = "kis_realtime"
        order2, msg2 = ledger.create_order(
            ticker="000660", name="SK하이닉스",
            side="buy", quantity=5, price=200000,
        )
        assert order2.state.value == "validated"

    def test_risk_policy_consistency_check(self):
        """RiskPolicy 임계치 일관성 전체 체크."""
        from kstock.core.risk_policy import RiskPolicy

        policy = RiskPolicy()

        # 주문 레벨은 포트폴리오 레벨보다 보수적이어야 함
        assert abs(policy.order_daily_loss_limit_pct / 100) < abs(policy.portfolio_max_daily_loss)

        # 안전모드 임계치는 오름차순
        assert (policy.safety_caution_threshold
                < policy.safety_safe_threshold
                < policy.safety_lockdown_threshold)

        # 모든 비율 한도가 양수
        assert policy.portfolio_max_stock_weight > 0
        assert policy.portfolio_max_sector_weight > 0
        assert policy.portfolio_max_correlation > 0

    def test_event_log_data_buy_blocked_flow(self):
        """폴백 매수 차단 → 이벤트 로그 기록 흐름."""
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity

        log = EventLog()

        # 폴백 매수 차단 이벤트 기록
        log.log_quick(
            EventType.DATA_BUY_BLOCKED,
            "yfinance 지연 데이터로 매수 시도 차단",
            severity=EventSeverity.WARNING,
            ticker="005930",
            source="PreTradeValidator",
        )

        events = log.query(event_type=EventType.DATA_BUY_BLOCKED)
        assert len(events) == 1
        assert events[0].ticker == "005930"
        assert events[0].source == "PreTradeValidator"

    def test_source_constants_consistency(self):
        """REALTIME_SOURCES와 DELAYED_SOURCES가 겹치지 않는지 확인."""
        from kstock.ingest.data_router import REALTIME_SOURCES, DELAYED_SOURCES
        overlap = REALTIME_SOURCES & DELAYED_SOURCES
        assert len(overlap) == 0, f"겹치는 소스: {overlap}"

    def test_all_sources_have_delay(self):
        """모든 소스에 지연 시간이 정의되어 있는지 확인."""
        from kstock.ingest.data_router import (
            REALTIME_SOURCES, DELAYED_SOURCES, SOURCE_DELAY_SECONDS,
        )
        for src in REALTIME_SOURCES | DELAYED_SOURCES:
            assert src in SOURCE_DELAY_SECONDS, f"{src}에 지연 시간 미정의"
