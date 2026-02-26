"""v5.0 테스트 — 데이터 시점성 / 주문 상태머신 / 리컨실 / 리플레이 / 시그널 정제 / 이벤트 로그.

테스트 대상:
  - point_in_time (DataPoint, AsOfJoinEngine, PITValidator, SourceRegistry)
  - order_manager (OrderStateMachine, IdempotencyGuard, PreTradeValidator, OrderLedger)
  - reconciliation (PositionReconciler, KillSwitch, SafetyModeManager)
  - execution_replay (ReplayEngine, SlippageAnalysis, StrategyDrift, DeflatedSharpe)
  - signal_refinery (SignalCorrelationMatrix, SignalPruner, PurgedKFoldCV)
  - event_log (EventLog, EventType, EventQuery)
"""

import math
import time
from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

KST = timezone(timedelta(hours=9))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. Point-in-Time (v5.0-1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPointInTime:
    """DataPoint + AsOfJoin + PITValidator + SourceRegistry."""

    def test_datapoint_creation(self):
        from kstock.ingest.point_in_time import DataPoint
        dp = DataPoint.now(75000, source="kis_realtime", ticker="005930", field_name="close")
        assert dp.value == 75000
        assert dp.source == "kis_realtime"
        assert dp.ticker == "005930"
        assert dp.field_name == "close"

    def test_datapoint_latency(self):
        from kstock.ingest.point_in_time import DataPoint
        now = datetime.now(KST)
        dp = DataPoint(
            value=100, event_time=now - timedelta(seconds=30),
            ingest_time=now, source="yfinance",
        )
        assert 29 < dp.latency_seconds < 31

    def test_datapoint_stale(self):
        from kstock.ingest.point_in_time import DataPoint
        now = datetime.now(KST)
        dp = DataPoint(
            value=100, event_time=now - timedelta(minutes=10),
            ingest_time=now, source="yfinance",
        )
        assert dp.is_stale is True

    def test_datapoint_not_stale(self):
        from kstock.ingest.point_in_time import DataPoint
        now = datetime.now(KST)
        dp = DataPoint(
            value=100, event_time=now - timedelta(seconds=60),
            ingest_time=now, source="kis_realtime",
        )
        assert dp.is_stale is False

    def test_datapoint_to_dict(self):
        from kstock.ingest.point_in_time import DataPoint
        dp = DataPoint.now(100, source="yfinance", ticker="005930")
        d = dp.to_dict()
        assert "value" in d
        assert "event_time" in d
        assert d["source"] == "yfinance"

    def test_asof_tag_dataframe(self):
        from kstock.ingest.point_in_time import AsOfJoinEngine
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=5),
            "close": [100, 101, 102, 103, 104],
        })
        tagged = AsOfJoinEngine.tag_dataframe(df, source="yfinance", ticker="005930")
        assert "_pit_event_time" in tagged.columns
        assert "_pit_ingest_time" in tagged.columns
        assert "_pit_source" in tagged.columns
        assert tagged["_pit_source"].iloc[0] == "yfinance"

    def test_asof_filter(self):
        from kstock.ingest.point_in_time import AsOfJoinEngine
        df = pd.DataFrame({
            "close": [100, 101, 102, 103, 104],
            "_pit_event_time": pd.date_range("2024-01-01", periods=5, tz=KST),
        })
        cutoff = datetime(2024, 1, 3, tzinfo=KST)
        filtered = AsOfJoinEngine.filter_asof(df, cutoff)
        assert len(filtered) == 3  # Jan 1, 2, 3

    def test_asof_latest(self):
        from kstock.ingest.point_in_time import AsOfJoinEngine
        df = pd.DataFrame({
            "close": [100, 101, 102, 103, 104],
            "_pit_event_time": pd.date_range("2024-01-01", periods=5, tz=KST),
        })
        cutoff = datetime(2024, 1, 3, tzinfo=KST)
        row = AsOfJoinEngine.latest_asof(df, cutoff)
        assert row is not None
        assert row["close"] == 102

    def test_asof_empty_df(self):
        from kstock.ingest.point_in_time import AsOfJoinEngine
        df = pd.DataFrame()
        tagged = AsOfJoinEngine.tag_dataframe(df, source="yfinance")
        assert tagged.empty

    def test_pit_validator_no_violation(self):
        from kstock.ingest.point_in_time import PITValidator
        df = pd.DataFrame({
            "_pit_event_time": pd.date_range("2024-01-01", periods=5, tz=KST),
            "_pit_ingest_time": pd.date_range("2024-01-01", periods=5, tz=KST),
            "_pit_source": ["yfinance"] * 5,
        })
        v = PITValidator()
        ref = datetime(2024, 12, 31, tzinfo=KST)
        violations = v.validate(df, reference_time=ref)
        critical = [x for x in violations if x.severity == "critical"]
        assert len(critical) == 0

    def test_pit_validator_future_data(self):
        from kstock.ingest.point_in_time import PITValidator
        ref = datetime(2024, 1, 3, tzinfo=KST)
        df = pd.DataFrame({
            "_pit_event_time": pd.date_range("2024-01-01", periods=5, tz=KST),
            "_pit_ingest_time": pd.date_range("2024-01-01", periods=5, tz=KST),
            "_pit_source": ["yfinance"] * 5,
        })
        v = PITValidator()
        violations = v.validate(df, reference_time=ref)
        future = [x for x in violations if x.violation_type == "future_data"]
        assert len(future) == 1
        assert future[0].details["count"] == 2  # Jan 4, 5

    def test_pit_validator_source_mismatch(self):
        from kstock.ingest.point_in_time import PITValidator
        df = pd.DataFrame({
            "_pit_event_time": pd.date_range("2024-01-01", periods=3, tz=KST),
            "_pit_ingest_time": pd.date_range("2024-01-01", periods=3, tz=KST),
            "_pit_source": ["yfinance", "naver", "yfinance"],
        })
        v = PITValidator()
        ref = datetime(2025, 1, 1, tzinfo=KST)
        violations = v.validate(df, reference_time=ref)
        src_issues = [x for x in violations if x.violation_type == "source_mismatch"]
        assert len(src_issues) == 1

    def test_source_registry(self):
        from kstock.ingest.point_in_time import SourceRegistry
        reg = SourceRegistry()
        reg.record_fetch("yfinance", "005930", True, 120.0, 100)
        reg.record_fetch("yfinance", "005930", True, 150.0, 100)
        reg.record_fetch("yfinance", "005930", False, 0.0)
        assert reg.get_reliability("yfinance", "005930") == pytest.approx(2/3, abs=0.01)
        assert reg.get_avg_latency_ms("yfinance", "005930") == pytest.approx(135.0)

    def test_source_registry_summary(self):
        from kstock.ingest.point_in_time import SourceRegistry
        reg = SourceRegistry()
        reg.record_fetch("yfinance", "005930", True, 100.0)
        summary = reg.get_summary()
        assert len(summary) == 1
        assert summary[0]["source"] == "yfinance"

    def test_format_pit_status(self):
        from kstock.ingest.point_in_time import format_pit_status
        assert "이상 없음" in format_pit_status([])

    def test_format_source_summary(self):
        from kstock.ingest.point_in_time import format_source_summary, SourceRegistry
        reg = SourceRegistry()
        reg.record_fetch("yfinance", "005930", True, 100.0)
        text = format_source_summary(reg)
        assert "yfinance" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. Order Manager (v5.0-2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestOrderManager:
    """OrderStateMachine + IdempotencyGuard + PreTradeValidator + OrderLedger."""

    def test_order_state_transitions(self):
        from kstock.broker.order_manager import ManagedOrder, OrderStateMachine, OrderState
        order = ManagedOrder(
            order_id="test-001", idempotency_key="key1",
            ticker="005930", side="buy", quantity=10, price=75000,
        )
        sm = OrderStateMachine(order)
        assert order.state == OrderState.INTENT
        assert sm.validate()
        assert order.state == OrderState.VALIDATED
        assert sm.place("BROKER-001")
        assert order.state == OrderState.PLACED
        assert sm.fill(10, 75100)
        assert order.state == OrderState.FILLED
        assert order.is_terminal

    def test_invalid_transition(self):
        from kstock.broker.order_manager import ManagedOrder, OrderStateMachine, OrderState
        order = ManagedOrder(
            order_id="test-002", idempotency_key="key2",
            ticker="005930", side="buy", quantity=10, price=75000,
        )
        sm = OrderStateMachine(order)
        # INTENT → FILLED is invalid (must go through VALIDATED → PLACED first)
        result = sm.fill(10, 75000)
        assert result is False
        assert order.state == OrderState.INTENT

    def test_block_transition(self):
        from kstock.broker.order_manager import ManagedOrder, OrderStateMachine, OrderState
        order = ManagedOrder(
            order_id="test-003", idempotency_key="key3",
            ticker="005930", side="buy", quantity=10, price=75000,
        )
        sm = OrderStateMachine(order)
        sm.block("리스크 한도 초과")
        assert order.state == OrderState.BLOCKED
        assert order.is_terminal
        assert order.block_reason == "리스크 한도 초과"

    def test_partial_fill(self):
        from kstock.broker.order_manager import ManagedOrder, OrderStateMachine, OrderState
        order = ManagedOrder(
            order_id="test-004", idempotency_key="key4",
            ticker="005930", side="buy", quantity=20, price=75000,
        )
        sm = OrderStateMachine(order)
        sm.validate()
        sm.place("B-004")
        sm.fill(10, 75100)  # 부분 체결
        assert order.state == OrderState.PARTIAL
        assert order.filled_quantity == 10
        sm.fill(10, 75200)  # 나머지 체결
        assert order.state == OrderState.FILLED
        assert order.filled_quantity == 20

    def test_transition_history(self):
        from kstock.broker.order_manager import ManagedOrder, OrderStateMachine
        order = ManagedOrder(
            order_id="test-005", idempotency_key="key5",
            ticker="005930", side="buy", quantity=10, price=75000,
        )
        sm = OrderStateMachine(order)
        sm.validate()
        sm.place("B-005")
        sm.fill(10, 75000)
        assert len(order.transitions) == 3

    def test_idempotency_guard(self):
        from kstock.broker.order_manager import IdempotencyGuard
        guard = IdempotencyGuard(window_seconds=5)
        key = IdempotencyGuard.generate_key("005930", "buy", 10)
        allowed, msg = guard.check_and_register(key)
        assert allowed is True
        # Same key again → blocked
        allowed2, msg2 = guard.check_and_register(key)
        assert allowed2 is False
        assert "중복" in msg2

    def test_idempotency_guard_different_keys(self):
        from kstock.broker.order_manager import IdempotencyGuard
        guard = IdempotencyGuard(window_seconds=5)
        k1 = IdempotencyGuard.generate_key("005930", "buy", 10)
        k2 = IdempotencyGuard.generate_key("005930", "sell", 10)
        a1, _ = guard.check_and_register(k1)
        a2, _ = guard.check_and_register(k2)
        assert a1 is True
        assert a2 is True

    def test_idempotency_release(self):
        from kstock.broker.order_manager import IdempotencyGuard
        guard = IdempotencyGuard(window_seconds=5)
        key = IdempotencyGuard.generate_key("005930", "buy", 10)
        guard.check_and_register(key)
        guard.release(key)
        # Now should be allowed again
        allowed, _ = guard.check_and_register(key)
        assert allowed is True

    def test_pretrade_validator_pass(self):
        from kstock.broker.order_manager import PreTradeValidator
        v = PreTradeValidator()
        result = v.validate("005930", "buy", 10, 75000, total_eval=100_000_000)
        assert result.approved is True

    def test_pretrade_validator_kill_switch(self):
        from kstock.broker.order_manager import PreTradeValidator
        v = PreTradeValidator()
        v.kill_switch_active = True
        result = v.validate("005930", "buy", 10, 75000)
        assert result.approved is False
        assert "킬스위치" in result.reasons[0]

    def test_pretrade_validator_zero_quantity(self):
        from kstock.broker.order_manager import PreTradeValidator
        v = PreTradeValidator()
        result = v.validate("005930", "buy", 0, 75000)
        assert result.approved is False

    def test_order_ledger_create(self):
        from kstock.broker.order_manager import OrderLedger, OrderState
        ledger = OrderLedger()
        order, msg = ledger.create_order(
            "005930", "삼성전자", "buy", 10, 75000,
        )
        assert order is not None
        assert order.state == OrderState.VALIDATED
        assert "통과" in msg

    def test_order_ledger_block_duplicate(self):
        from kstock.broker.order_manager import OrderLedger, OrderState
        ledger = OrderLedger()
        o1, _ = ledger.create_order("005930", "삼성전자", "buy", 10, 75000)
        assert o1.state == OrderState.VALIDATED
        # Same order again → blocked
        o2, msg = ledger.create_order("005930", "삼성전자", "buy", 10, 75000)
        assert o2.state == OrderState.BLOCKED
        assert "중복" in msg

    def test_order_ledger_stats(self):
        from kstock.broker.order_manager import OrderLedger
        ledger = OrderLedger()
        ledger.create_order("005930", "삼성전자", "buy", 10, 75000)
        stats = ledger.get_stats()
        assert stats["total_orders"] >= 1
        assert stats["today_orders"] >= 1

    def test_order_to_dict(self):
        from kstock.broker.order_manager import ManagedOrder
        order = ManagedOrder(
            order_id="test", idempotency_key="key",
            ticker="005930", side="buy", quantity=10, price=75000,
        )
        d = order.to_dict()
        assert d["ticker"] == "005930"
        assert d["state"] == "intent"

    def test_format_order_status(self):
        from kstock.broker.order_manager import ManagedOrder, format_order_status
        order = ManagedOrder(
            order_id="test", idempotency_key="key",
            ticker="005930", name="삼성전자", side="buy", quantity=10, price=75000,
        )
        text = format_order_status(order)
        assert "삼성전자" in text
        assert "매수" in text

    def test_format_ledger_summary(self):
        from kstock.broker.order_manager import OrderLedger, format_order_ledger_summary
        ledger = OrderLedger()
        text = format_order_ledger_summary(ledger)
        assert "주문 원장" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. Reconciliation (v5.0-3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestReconciliation:
    """PositionReconciler + KillSwitch + SafetyModeManager."""

    def test_kill_switch_activate(self):
        from kstock.core.reconciliation import KillSwitch
        ks = KillSwitch()
        assert ks.is_active is False
        ks.activate("테스트 활성화", "manual")
        assert ks.is_active is True
        assert ks.reason == "테스트 활성화"

    def test_kill_switch_deactivate(self):
        from kstock.core.reconciliation import KillSwitch
        ks = KillSwitch()
        ks.activate("테스트")
        ks.deactivate()
        assert ks.is_active is False

    def test_kill_switch_status(self):
        from kstock.core.reconciliation import KillSwitch
        ks = KillSwitch()
        status = ks.get_status()
        assert status["active"] is False

    def test_safety_mode_normal(self):
        from kstock.core.reconciliation import SafetyModeManager, SafetyLevel
        mgr = SafetyModeManager()
        assert mgr.level == SafetyLevel.NORMAL
        assert mgr.is_buy_allowed is True
        assert mgr.is_sell_allowed is True

    def test_safety_mode_escalation(self):
        from kstock.core.reconciliation import SafetyModeManager, SafetyLevel
        mgr = SafetyModeManager()
        mgr.escalate("테스트")
        assert mgr.level == SafetyLevel.CAUTION
        mgr.escalate("테스트2")
        assert mgr.level == SafetyLevel.SAFE
        assert mgr.is_buy_allowed is False

    def test_safety_mode_lockdown_kill_switch(self):
        from kstock.core.reconciliation import SafetyModeManager, SafetyLevel
        mgr = SafetyModeManager()
        mgr.set_level(SafetyLevel.LOCKDOWN, "비상")
        assert mgr.kill_switch.is_active is True
        assert mgr.is_trading_allowed is False

    def test_safety_mode_de_escalation(self):
        from kstock.core.reconciliation import SafetyModeManager, SafetyLevel
        mgr = SafetyModeManager()
        mgr.set_level(SafetyLevel.SAFE)
        mgr.de_escalate()
        assert mgr.level == SafetyLevel.CAUTION

    def test_reconciler_perfect_match(self):
        from kstock.core.reconciliation import PositionReconciler
        rec = PositionReconciler()
        internal = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000},
        ]
        broker = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000},
        ]
        report = rec.reconcile(internal, broker)
        assert report.status == "ok"
        assert report.mismatch_count == 0
        assert report.matched_positions == 1

    def test_reconciler_quantity_diff(self):
        from kstock.core.reconciliation import PositionReconciler
        rec = PositionReconciler()
        internal = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000},
        ]
        broker = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 80, "avg_price": 75000},
        ]
        report = rec.reconcile(internal, broker)
        assert report.status == "mismatch"
        assert report.mismatch_count >= 1
        assert any(m.mismatch_type.value == "quantity_diff" for m in report.mismatches)

    def test_reconciler_phantom_position(self):
        from kstock.core.reconciliation import PositionReconciler, SafetyLevel
        rec = PositionReconciler()
        internal = []
        broker = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000},
        ]
        report = rec.reconcile(internal, broker)
        assert report.has_critical  # phantom is critical
        assert report.safety_level_after == SafetyLevel.LOCKDOWN

    def test_reconciler_missing_position(self):
        from kstock.core.reconciliation import PositionReconciler
        rec = PositionReconciler()
        internal = [
            {"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000},
        ]
        broker = []
        report = rec.reconcile(internal, broker)
        assert report.mismatch_count >= 1

    def test_reconciler_auto_normal_recovery(self):
        from kstock.core.reconciliation import PositionReconciler, SafetyLevel
        rec = PositionReconciler()
        # First: mismatch → escalation
        internal = [{"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000}]
        broker = [{"ticker": "005930", "name": "삼성전자", "quantity": 80, "avg_price": 75000}]
        rec.reconcile(internal, broker)
        assert rec.safety.level >= SafetyLevel.CAUTION

        # Then: perfect match → recovery to NORMAL
        broker_fix = [{"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000}]
        # Need to reset kill switch if it was from safety_mode
        if rec.safety.kill_switch.is_active and rec.safety.kill_switch._activated_by == "safety_mode":
            rec.safety.kill_switch.deactivate()
        rec.safety.set_level(SafetyLevel.CAUTION)  # Reset for test
        report2 = rec.reconcile(internal, broker_fix)
        assert report2.status == "ok"
        assert rec.safety.level == SafetyLevel.NORMAL

    def test_format_reconciliation(self):
        from kstock.core.reconciliation import (
            ReconciliationReport, format_reconciliation_report,
        )
        report = ReconciliationReport(timestamp="2024-01-01", status="ok")
        text = format_reconciliation_report(report)
        assert "정상" in text

    def test_format_safety_status(self):
        from kstock.core.reconciliation import format_safety_status, SafetyModeManager
        mgr = SafetyModeManager()
        text = format_safety_status(mgr)
        assert "NORMAL" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. Execution Replay (v5.0-4)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExecutionReplay:
    """ReplayEngine + SlippageAnalysis + StrategyDrift + DeflatedSharpe."""

    @pytest.fixture
    def engine(self):
        from kstock.core.execution_replay import ReplayEngine
        return ReplayEngine()

    def test_execution_record_slippage(self):
        from kstock.core.execution_replay import ExecutionRecord
        rec = ExecutionRecord(
            trade_id="t1", ticker="005930", name="삼성전자",
            side="buy", strategy="A",
            signal_time="2024-01-01 09:00", execution_time="2024-01-01 09:01",
            signal_price=75000, execution_price=75375,  # 0.5% 슬리피지
            quantity=10,
        )
        assert rec.slippage_pct == pytest.approx(0.005, abs=0.001)

    def test_slippage_analysis(self, engine):
        from kstock.core.execution_replay import ExecutionRecord
        for i in range(10):
            engine.add_execution(ExecutionRecord(
                trade_id=f"t{i}", ticker="005930", name="삼성전자",
                side="buy", strategy="A",
                signal_time="", execution_time="",
                signal_price=75000, execution_price=75000 + i * 100,
                quantity=10, pnl_pct=0.01 * (i - 5),
            ))
        analysis = engine.analyze_slippage()
        assert analysis.total_trades == 10
        assert analysis.avg_slippage_pct >= 0

    def test_drift_analysis(self, engine):
        from kstock.core.execution_replay import ExecutionRecord, BacktestPrediction
        # 백테스트: 높은 성능
        for i in range(20):
            engine.add_prediction(BacktestPrediction(
                ticker="005930", strategy="A",
                prediction_date=f"2024-01-{i+1:02d}",
                predicted_return_pct=0.03,
                predicted_win_prob=0.7,
            ))
        # 실전: 낮은 성능
        for i in range(20):
            engine.add_execution(ExecutionRecord(
                trade_id=f"t{i}", ticker="005930", name="삼성전자",
                side="buy", strategy="A",
                signal_time="", execution_time="",
                signal_price=75000, execution_price=75000,
                quantity=10,
                pnl_pct=(-0.01 if i % 2 == 0 else 0.005),
            ))
        drifts = engine.analyze_drift()
        assert len(drifts) >= 1
        drift_a = [d for d in drifts if d.strategy == "A"]
        assert len(drift_a) == 1

    def test_accuracy(self, engine):
        from kstock.core.execution_replay import BacktestPrediction
        # 방향 맞음
        engine.add_prediction(BacktestPrediction(
            ticker="005930", strategy="A", prediction_date="2024-01-01",
            predicted_return_pct=0.05, predicted_win_prob=0.7,
            actual_return_pct=0.03,
        ))
        # 방향 틀림
        engine.add_prediction(BacktestPrediction(
            ticker="005930", strategy="A", prediction_date="2024-01-02",
            predicted_return_pct=0.05, predicted_win_prob=0.7,
            actual_return_pct=-0.02,
        ))
        assert engine.compute_accuracy() == pytest.approx(0.5)

    def test_deflated_sharpe_basic(self, engine):
        dsr = engine.compute_deflated_sharpe(1.5, 10, [0.01, -0.005, 0.02, 0.03, -0.01, 0.01, 0.015, -0.002, 0.005, 0.008])
        assert 0 <= dsr <= 1

    def test_deflated_sharpe_single_trial(self, engine):
        dsr = engine.compute_deflated_sharpe(1.5, 1)
        assert dsr == 1.5  # No deflation with 1 trial

    def test_deflated_sharpe_zero(self, engine):
        dsr = engine.compute_deflated_sharpe(0, 10)
        assert dsr == 0

    def test_dashboard_creation(self, engine):
        from kstock.core.execution_replay import ExecutionRecord
        engine.add_execution(ExecutionRecord(
            trade_id="t1", ticker="005930", name="삼성전자",
            side="buy", strategy="A",
            signal_time="", execution_time="",
            signal_price=75000, execution_price=75100,
            quantity=10, pnl_pct=0.01,
        ))
        dashboard = engine.create_dashboard()
        assert dashboard.total_live_trades == 1

    def test_format_dashboard(self):
        from kstock.core.execution_replay import ReplayDashboard, format_replay_dashboard
        dashboard = ReplayDashboard(timestamp="2024-01-01 09:00")
        text = format_replay_dashboard(dashboard)
        assert "Execution Replay" in text

    def test_format_slippage(self):
        from kstock.core.execution_replay import SlippageAnalysis, format_slippage_report
        s = SlippageAnalysis(total_trades=10, avg_slippage_pct=0.003)
        text = format_slippage_report(s)
        assert "슬리피지" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. Signal Refinery (v5.0-5)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSignalRefinery:
    """SignalCorrelationMatrix + SignalPruner + PurgedKFoldCV."""

    def test_pearson_perfect(self):
        from kstock.signal.signal_refinery import _pearson
        x = [1, 2, 3, 4, 5]
        y = [2, 4, 6, 8, 10]
        assert _pearson(x, y) == pytest.approx(1.0, abs=0.001)

    def test_pearson_negative(self):
        from kstock.signal.signal_refinery import _pearson
        x = [1, 2, 3, 4, 5]
        y = [10, 8, 6, 4, 2]
        assert _pearson(x, y) == pytest.approx(-1.0, abs=0.001)

    def test_pearson_uncorrelated(self):
        from kstock.signal.signal_refinery import _pearson
        x = [1, 2, 3, 4, 5]
        y = [3, 1, 4, 1, 5]  # ~random
        r = _pearson(x, y)
        assert abs(r) < 0.7

    def test_correlation_matrix(self):
        from kstock.signal.signal_refinery import SignalCorrelationMatrix
        signals = {
            "a": [1, 2, 3, 4, 5],
            "b": [2, 4, 6, 8, 10],  # 완전 상관
            "c": [5, 4, 3, 2, 1],   # 완전 역상관
        }
        corr = SignalCorrelationMatrix.compute(signals)
        assert corr[("a", "b")] == pytest.approx(1.0, abs=0.001)
        assert corr[("a", "c")] == pytest.approx(-1.0, abs=0.001)

    def test_high_correlation_pairs(self):
        from kstock.signal.signal_refinery import SignalCorrelationMatrix
        signals = {
            "a": [1, 2, 3, 4, 5],
            "b": [1.1, 2.05, 3.02, 4.01, 5.0],  # 매우 높은 상관
            "c": [5, 4, 3, 2, 1],                 # 역상관
        }
        corr = SignalCorrelationMatrix.compute(signals)
        pairs = SignalCorrelationMatrix.find_high_correlation_pairs(corr, 0.9)
        assert len(pairs) >= 1  # a-b pair

    def test_signal_pruner_basic(self):
        from kstock.signal.signal_refinery import SignalPruner
        # 3개 시그널: a, b 고상관 → 하나 제거
        signals = {
            "rsi_14": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "rsi_7": [1.1, 2.1, 3.05, 4.02, 5.01, 6, 7, 8, 9, 10],  # rsi_14와 거의 동일
            "macd": [10, 8, 6, 4, 2, 3, 5, 7, 9, 11],  # 다른 패턴
        }
        pruner = SignalPruner(correlation_threshold=0.9)
        report = pruner.prune(signals)
        assert report.total_signals == 3
        assert report.selected_signals == 2
        assert report.removed_signals == 1

    def test_signal_pruner_with_quality(self):
        from kstock.signal.signal_refinery import SignalPruner
        signals = {
            "a": [1, 2, 3, 4, 5],
            "b": [1, 2, 3, 4, 5],  # 완전 동일 → a와 같은 클러스터
            "c": [5, 1, 3, 2, 4],  # 약한 상관 → 별도 독립
        }
        quality = {"a": 80, "b": 90, "c": 70}  # b가 더 높은 품질
        pruner = SignalPruner(correlation_threshold=0.9)
        report = pruner.prune(signals, quality_map=quality)
        # b가 대표 (a와 같은 클러스터에서 품질 더 높음), c는 독립
        selected = [q.name for q in report.quality_scores if q.is_selected]
        assert "b" in selected
        assert "c" in selected
        assert report.removed_signals >= 1

    def test_signal_pruner_target_count(self):
        from kstock.signal.signal_refinery import SignalPruner
        # 5개 독립 시그널 → target 3개로 추가 제거
        import random
        random.seed(42)
        signals = {f"sig_{i}": [random.random() for _ in range(20)] for i in range(5)}
        pruner = SignalPruner(correlation_threshold=0.9)
        report = pruner.prune(signals, target_count=3)
        assert report.selected_signals == 3

    def test_purged_kfold_basic(self):
        from kstock.signal.signal_refinery import PurgedKFoldCV
        cv = PurgedKFoldCV(n_splits=3, purge_gap=2)
        splits = cv.split(30)
        assert len(splits) == 3
        for train, test in splits:
            assert len(train) > 0
            assert len(test) > 0
            # No overlap
            assert not set(train) & set(test)

    def test_purged_kfold_purge_gap(self):
        from kstock.signal.signal_refinery import PurgedKFoldCV
        cv = PurgedKFoldCV(n_splits=3, purge_gap=5)
        splits = cv.split(60)
        for train, test in splits:
            # Train indices should not be adjacent to test
            test_min, test_max = min(test), max(test)
            for t_idx in train:
                assert t_idx < test_min - 5 or t_idx > test_max + 5 or t_idx in test

    def test_purged_kfold_insufficient_data(self):
        from kstock.signal.signal_refinery import PurgedKFoldCV
        cv = PurgedKFoldCV(n_splits=5, purge_gap=2)
        splits = cv.split(5)  # Too few samples
        assert len(splits) == 0

    def test_signal_catalog(self):
        from kstock.signal.signal_refinery import get_signal_catalog
        catalog = get_signal_catalog()
        assert len(catalog) >= 40  # 45개 목표

    def test_format_refinery_report(self):
        from kstock.signal.signal_refinery import RefineryReport, format_refinery_report
        report = RefineryReport(total_signals=45, selected_signals=22, removed_signals=23)
        text = format_refinery_report(report)
        assert "45" in text
        assert "22" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. Event Log (v5.0-6)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestEventLog:
    """EventLog + EventType + 조회."""

    def test_event_creation(self):
        from kstock.core.event_log import Event, EventType, EventSeverity
        e = Event(
            event_type=EventType.ORDER_CREATED,
            severity=EventSeverity.INFO,
            message="주문 생성",
            ticker="005930",
        )
        assert e.event_type == EventType.ORDER_CREATED
        assert e.ticker == "005930"
        assert len(e.timestamp) > 0

    def test_event_to_dict(self):
        from kstock.core.event_log import Event, EventType, EventSeverity
        e = Event(
            event_type=EventType.DATA_FETCH,
            severity=EventSeverity.INFO,
            message="데이터 수집",
        )
        d = e.to_dict()
        assert d["event_type"] == "data.fetch"
        assert d["severity"] == "info"

    def test_event_log_basic(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=100)
        log.log(Event(
            event_type=EventType.ORDER_CREATED,
            severity=EventSeverity.INFO,
            message="테스트 주문",
            ticker="005930",
        ))
        assert log.total_events == 1

    def test_event_log_quick(self):
        from kstock.core.event_log import EventLog, EventType, EventSeverity
        log = EventLog(max_memory=100)
        log.log_quick(EventType.SYSTEM_START, "시스템 시작")
        assert log.total_events == 1

    def test_event_log_query_by_type(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=100)
        log.log(Event(EventType.ORDER_CREATED, EventSeverity.INFO, "주문1"))
        log.log(Event(EventType.DATA_FETCH, EventSeverity.INFO, "데이터"))
        log.log(Event(EventType.ORDER_FILLED, EventSeverity.INFO, "체결"))
        results = log.query(event_type=EventType.ORDER_CREATED)
        assert len(results) == 1

    def test_event_log_query_by_severity(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=100)
        log.log(Event(EventType.SYSTEM_ERROR, EventSeverity.ERROR, "에러"))
        log.log(Event(EventType.DATA_FETCH, EventSeverity.INFO, "정보"))
        errors = log.get_errors()
        assert len(errors) == 1

    def test_event_log_query_by_ticker(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=100)
        log.log(Event(EventType.ORDER_CREATED, EventSeverity.INFO, "주문1", ticker="005930"))
        log.log(Event(EventType.ORDER_CREATED, EventSeverity.INFO, "주문2", ticker="000660"))
        results = log.query(ticker="005930")
        assert len(results) == 1

    def test_event_log_order_trail(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=100)
        oid = "order-123"
        log.log(Event(EventType.ORDER_CREATED, EventSeverity.INFO, "생성", order_id=oid))
        log.log(Event(EventType.ORDER_VALIDATED, EventSeverity.INFO, "검증", order_id=oid))
        log.log(Event(EventType.ORDER_PLACED, EventSeverity.INFO, "접수", order_id=oid))
        trail = log.get_order_trail(oid)
        assert len(trail) == 3

    def test_event_log_count_by_type(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=100)
        log.log(Event(EventType.ORDER_CREATED, EventSeverity.INFO, "1"))
        log.log(Event(EventType.ORDER_CREATED, EventSeverity.INFO, "2"))
        log.log(Event(EventType.DATA_FETCH, EventSeverity.INFO, "3"))
        counts = log.count_by_type()
        assert counts["order.created"] == 2
        assert counts["data.fetch"] == 1

    def test_event_log_max_memory(self):
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity
        log = EventLog(max_memory=5)
        for i in range(10):
            log.log(Event(EventType.DATA_FETCH, EventSeverity.INFO, f"이벤트 {i}"))
        assert log.total_events == 5  # Capped at 5

    def test_format_event_summary(self):
        from kstock.core.event_log import EventLog, EventType, EventSeverity, format_event_summary
        log = EventLog(max_memory=100)
        log.log_quick(EventType.ORDER_CREATED, "주문")
        text = format_event_summary(log)
        assert "이벤트 로그" in text

    def test_format_recent_events(self):
        from kstock.core.event_log import EventLog, EventType, EventSeverity, format_recent_events
        log = EventLog(max_memory=100)
        log.log_quick(EventType.SYSTEM_START, "시작")
        text = format_recent_events(log)
        assert "최근 이벤트" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. DataRouter PIT Integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDataRouterPIT:
    """DataRouter의 PIT 태깅 통합."""

    def test_data_router_last_source(self):
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        assert router.last_source_used == ""

    def test_data_router_pit_source_in_info(self):
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        # get_stock_info with no sources → default with _pit_source
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(
            router.get_stock_info("005930")
        )
        assert "_pit_source" in result

    def test_data_router_format_source(self):
        from kstock.ingest.data_router import DataRouter
        router = DataRouter()
        text = router.format_source_status()
        assert "yfinance" in text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. DB Schema (v5.0 tables)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDBSchema:
    """v5.0 DB 테이블 + CRUD."""

    @pytest.fixture
    def db(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        return SQLiteStore(db_path=tmp_path / "test_v5.db")

    def test_event_log_table(self, db):
        db.add_event("order.created", "info", "테스트 주문", ticker="005930")
        events = db.get_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "order.created"

    def test_event_log_filter(self, db):
        db.add_event("order.created", "info", "주문1")
        db.add_event("data.fetch", "info", "데이터")
        events = db.get_events(event_type="order.created")
        assert len(events) == 1

    def test_reconciliation_log(self, db):
        db.add_reconciliation("ok", 5, 5, 5, 0, "NORMAL")
        recs = db.get_reconciliations()
        assert len(recs) == 1
        assert recs[0]["status"] == "ok"

    def test_execution_replay(self, db):
        db.add_execution_replay(
            "005930", "A", "buy",
            75000, 75100, 0.0013, 0.01,
            bt_predicted_return=0.03, bt_win_prob=0.7,
            direction_match=1,
        )
        replays = db.get_execution_replays()
        assert len(replays) == 1
        assert replays[0]["ticker"] == "005930"

    def test_execution_replay_by_strategy(self, db):
        db.add_execution_replay("005930", "A", "buy", 75000, 75100, 0.001, 0.01)
        db.add_execution_replay("000660", "B", "sell", 200000, 199500, 0.002, -0.01)
        strat_a = db.get_execution_replays_by_strategy("A")
        assert len(strat_a) == 1
        assert strat_a[0]["strategy"] == "A"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. Integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestV5Integration:
    """v5.0 모듈 간 통합 테스트."""

    def test_all_modules_import(self):
        from kstock.ingest.point_in_time import (
            DataPoint, AsOfJoinEngine, PITValidator, SourceRegistry,
        )
        from kstock.broker.order_manager import (
            OrderLedger, OrderStateMachine, IdempotencyGuard, PreTradeValidator,
        )
        from kstock.core.reconciliation import (
            PositionReconciler, KillSwitch, SafetyModeManager,
        )
        from kstock.core.execution_replay import (
            ReplayEngine, ExecutionRecord, BacktestPrediction,
        )
        from kstock.signal.signal_refinery import (
            SignalPruner, PurgedKFoldCV, SignalCorrelationMatrix,
        )
        from kstock.core.event_log import EventLog, EventType
        # All imports successful
        assert True

    def test_order_to_event_log_flow(self):
        """주문 생성 → 이벤트 로그 기록 흐름."""
        from kstock.broker.order_manager import OrderLedger, OrderState
        from kstock.core.event_log import EventLog, Event, EventType, EventSeverity

        log = EventLog(max_memory=100)
        ledger = OrderLedger()
        order, msg = ledger.create_order("005930", "삼성전자", "buy", 10, 75000)

        # 이벤트 로그에 기록
        log.log(Event(
            event_type=EventType.ORDER_CREATED,
            severity=EventSeverity.INFO,
            message=f"주문 생성: {order.name} {order.side} {order.quantity}주",
            ticker=order.ticker,
            order_id=order.order_id,
        ))

        assert log.total_events == 1
        trail = log.get_order_trail(order.order_id)
        assert len(trail) == 1

    def test_reconciliation_to_kill_switch_flow(self):
        """리컨실 불일치 → 킬스위치 → 주문 차단 흐름."""
        from kstock.core.reconciliation import PositionReconciler, SafetyLevel
        from kstock.broker.order_manager import OrderLedger

        rec = PositionReconciler()
        # Phantom position → LOCKDOWN
        report = rec.reconcile(
            internal=[],
            broker=[{"ticker": "005930", "name": "삼성전자", "quantity": 100, "avg_price": 75000}],
        )
        assert rec.safety.level == SafetyLevel.LOCKDOWN
        assert rec.safety.kill_switch.is_active

        # 킬스위치 상태에서 주문 → 차단
        ledger = OrderLedger()
        ledger.validator.kill_switch_active = True
        order, msg = ledger.create_order("000660", "SK하이닉스", "buy", 5, 200000)
        assert "킬스위치" in msg or "차단" in msg

    def test_data_pit_to_backtest_flow(self):
        """PIT 태깅 → 백테스트 데이터 필터링 흐름."""
        from kstock.ingest.point_in_time import AsOfJoinEngine, PITValidator

        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=10, tz=KST),
            "close": list(range(100, 110)),
        })
        tagged = AsOfJoinEngine.tag_dataframe(df, source="yfinance", ticker="005930")
        assert "_pit_event_time" in tagged.columns

        # cutoff 적용
        cutoff = datetime(2024, 1, 5, tzinfo=KST)
        safe = AsOfJoinEngine.filter_asof(tagged, cutoff)
        assert len(safe) == 5

    def test_signal_refinery_with_catalog(self):
        """시그널 카탈로그 → 정제 흐름."""
        from kstock.signal.signal_refinery import get_signal_catalog, SignalPruner
        import random
        random.seed(42)

        catalog = get_signal_catalog()
        # 시뮬레이션: 각 시그널에 랜덤 값 생성
        signals = {s.name: [random.random() for _ in range(50)] for s in catalog}

        # 일부 시그널을 고의로 유사하게 만들기
        signals["rsi_7"] = [v * 1.01 + 0.001 for v in signals["rsi_14"]]
        signals["stochastic_d"] = [v * 0.99 + 0.002 for v in signals["stochastic_k"]]

        pruner = SignalPruner(correlation_threshold=0.7)
        report = pruner.prune(signals, target_count=25)
        assert report.selected_signals <= 25
        assert report.removed_signals > 0

    def test_global_instances(self):
        """글로벌 인스턴스 접근."""
        from kstock.ingest.point_in_time import get_registry, get_validator, get_asof_engine
        from kstock.broker.order_manager import get_order_ledger
        from kstock.core.reconciliation import get_kill_switch, get_safety_manager, get_reconciler
        from kstock.core.execution_replay import get_replay_engine
        from kstock.core.event_log import get_event_log

        assert get_registry() is not None
        assert get_validator() is not None
        assert get_asof_engine() is not None
        assert get_order_ledger() is not None
        assert get_kill_switch() is not None
        assert get_safety_manager() is not None
        assert get_reconciler() is not None
        assert get_replay_engine() is not None
        assert get_event_log() is not None
