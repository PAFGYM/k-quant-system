"""서킷 브레이커 패턴 — 외부 API 장애 격리.

yfinance, KIS API, Naver Finance 등 외부 서비스 호출에 적용.
연속 실패 시 일시적으로 호출을 차단하고 자동 복구.

상태 전이:
  CLOSED (정상) → OPEN (차단) → HALF_OPEN (시도) → CLOSED
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """서킷 브레이커 상태."""
    CLOSED = "closed"       # 정상 — 모든 호출 허용
    OPEN = "open"           # 차단 — 모든 호출 즉시 실패
    HALF_OPEN = "half_open"  # 시험 — 일부 호출 허용, 성공 시 CLOSED


@dataclass
class CircuitStats:
    """서킷 브레이커 통계."""
    name: str
    state: str
    failure_count: int
    success_count: int
    total_calls: int
    last_failure_time: float
    last_success_time: float
    open_until: float
    consecutive_failures: int


class CircuitBreaker:
    """서킷 브레이커 구현.

    사용법:
        cb = CircuitBreaker("yfinance", failure_threshold=5, recovery_timeout=60)

        async def get_price(ticker):
            if not cb.can_execute():
                return fallback_price(ticker)
            try:
                price = await yfinance_get_price(ticker)
                cb.record_success()
                return price
            except Exception:
                cb.record_failure()
                return fallback_price(ticker)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 2,
    ) -> None:
        """
        Args:
            name: 서비스 이름 (로깅용)
            failure_threshold: OPEN 전환까지 허용 연속 실패 횟수
            recovery_timeout: OPEN → HALF_OPEN 전환 대기 시간 (초)
            half_open_max_calls: HALF_OPEN에서 허용할 시험 호출 수
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_calls = 0
        self._consecutive_failures = 0
        self._last_failure_time = 0.0
        self._last_success_time = 0.0
        self._open_time = 0.0
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        """현재 상태 (자동 전이 포함)."""
        if self._state == CircuitState.OPEN:
            if time.time() - self._open_time >= self.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
        return self._state

    def can_execute(self) -> bool:
        """호출 가능 여부 확인."""
        current = self.state  # 자동 전이 트리거

        if current == CircuitState.CLOSED:
            return True

        if current == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

        # OPEN
        return False

    def record_success(self) -> None:
        """성공 기록."""
        self._total_calls += 1
        self._success_count += 1
        self._consecutive_failures = 0
        self._last_success_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.CLOSED)

    def record_failure(self) -> None:
        """실패 기록."""
        self._total_calls += 1
        self._failure_count += 1
        self._consecutive_failures += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            # 시험 호출 실패 → 다시 OPEN
            self._transition_to(CircuitState.OPEN)
        elif self._consecutive_failures >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)

    def reset(self) -> None:
        """수동 리셋."""
        self._transition_to(CircuitState.CLOSED)
        self._consecutive_failures = 0
        self._failure_count = 0

    def get_stats(self) -> CircuitStats:
        """통계 반환."""
        return CircuitStats(
            name=self.name,
            state=self.state.value,
            failure_count=self._failure_count,
            success_count=self._success_count,
            total_calls=self._total_calls,
            last_failure_time=self._last_failure_time,
            last_success_time=self._last_success_time,
            open_until=self._open_time + self.recovery_timeout if self._state == CircuitState.OPEN else 0,
            consecutive_failures=self._consecutive_failures,
        )

    def _transition_to(self, new_state: CircuitState) -> None:
        """상태 전이."""
        old_state = self._state
        self._state = new_state

        if new_state == CircuitState.OPEN:
            self._open_time = time.time()
            self._half_open_calls = 0
            logger.warning(
                "CircuitBreaker [%s]: %s → OPEN (연속 실패 %d회, %.0f초 후 재시도)",
                self.name, old_state.value,
                self._consecutive_failures, self.recovery_timeout,
            )
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            logger.info(
                "CircuitBreaker [%s]: OPEN → HALF_OPEN (시험 호출 허용)",
                self.name,
            )
        elif new_state == CircuitState.CLOSED:
            logger.info(
                "CircuitBreaker [%s]: %s → CLOSED (정상 복구)",
                self.name, old_state.value,
            )


# ── 글로벌 서킷 브레이커 레지스트리 ──────────────────────

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """이름으로 서킷 브레이커 조회/생성."""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
    return _breakers[name]


def get_all_stats() -> list[CircuitStats]:
    """전체 서킷 브레이커 상태."""
    return [cb.get_stats() for cb in _breakers.values()]


def format_circuit_status() -> str:
    """텔레그램용 서킷 브레이커 상태 포맷."""
    stats = get_all_stats()
    if not stats:
        return "🔌 서킷 브레이커: 미등록"

    lines = ["🔌 서킷 브레이커 상태", "━" * 22, ""]
    for s in stats:
        icon = "🟢" if s.state == "closed" else "🔴" if s.state == "open" else "🟡"
        lines.append(
            f"{icon} {s.name}: {s.state} "
            f"(실패 {s.consecutive_failures}/{s.failure_count})"
        )

    return "\n".join(lines)
