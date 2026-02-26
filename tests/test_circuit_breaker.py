"""서킷 브레이커 테스트."""
import time
from kstock.core.circuit_breaker import (
    CircuitBreaker, CircuitState, get_breaker, get_all_stats,
    format_circuit_status,
)


def test_initial_state():
    cb = CircuitBreaker("test_init")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute()


def test_stays_closed_on_success():
    cb = CircuitBreaker("test_success", failure_threshold=3)
    for _ in range(10):
        assert cb.can_execute()
        cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_opens_after_failures():
    cb = CircuitBreaker("test_open", failure_threshold=3, recovery_timeout=1.0)
    for _ in range(3):
        assert cb.can_execute()
        cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert not cb.can_execute()


def test_half_open_after_timeout():
    cb = CircuitBreaker("test_half", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute()


def test_half_open_success_closes():
    cb = CircuitBreaker("test_close", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_failure_reopens():
    cb = CircuitBreaker("test_reopen", failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    assert cb.can_execute()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_reset():
    cb = CircuitBreaker("test_reset", failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute()


def test_get_stats():
    cb = CircuitBreaker("test_stats", failure_threshold=5)
    cb.record_success()
    cb.record_failure()
    stats = cb.get_stats()
    assert stats.name == "test_stats"
    assert stats.success_count == 1
    assert stats.failure_count == 1
    assert stats.total_calls == 2


def test_global_registry():
    cb1 = get_breaker("global_test_1")
    cb2 = get_breaker("global_test_1")
    assert cb1 is cb2

    cb3 = get_breaker("global_test_2")
    assert cb3 is not cb1


def test_format_status():
    get_breaker("format_test")
    text = format_circuit_status()
    assert "서킷 브레이커" in text
    assert "format_test" in text
