"""통합 이벤트 로그 — v5.0-6.

시스템의 모든 중요 이벤트(주문, 시그널, 리컨실, 안전모드 등)를
단일 시계열 로그로 기록한다.

핵심 기능:
  1. EventLog — 이벤트 기록 + 조회
  2. EventType — 이벤트 유형 분류
  3. EventQuery — 필터링 + 집계
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 메모리 이벤트 최대 보관 수
MAX_MEMORY_EVENTS = 5000


class EventType(str, Enum):
    """이벤트 유형."""
    # 주문 관련
    ORDER_CREATED = "order.created"
    ORDER_VALIDATED = "order.validated"
    ORDER_BLOCKED = "order.blocked"
    ORDER_PLACED = "order.placed"
    ORDER_FILLED = "order.filled"
    ORDER_REJECTED = "order.rejected"
    ORDER_CANCELLED = "order.cancelled"

    # 데이터 관련
    DATA_FETCH = "data.fetch"
    DATA_FALLBACK = "data.fallback"
    DATA_STALE = "data.stale"
    DATA_PIT_VIOLATION = "data.pit_violation"

    # 리스크 관련
    RISK_VIOLATION = "risk.violation"
    RISK_KILL_SWITCH = "risk.kill_switch"
    SAFETY_MODE_CHANGE = "risk.safety_mode"

    # 리컨실 관련
    RECONCILIATION_OK = "recon.ok"
    RECONCILIATION_MISMATCH = "recon.mismatch"

    # 시그널 관련
    SIGNAL_GENERATED = "signal.generated"
    SIGNAL_REFINED = "signal.refined"

    # 시스템 관련
    SYSTEM_START = "system.start"
    SYSTEM_ERROR = "system.error"
    SYSTEM_HEALTH = "system.health"

    # 사용자 관련
    USER_ACTION = "user.action"
    USER_CONFIRM = "user.confirm"


class EventSeverity(str, Enum):
    """이벤트 심각도."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Event:
    """단일 이벤트."""
    event_type: EventType
    severity: EventSeverity
    message: str
    timestamp: str = ""
    source: str = ""          # 발생 모듈
    ticker: str = ""          # 관련 종목
    order_id: str = ""        # 관련 주문
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(KST).isoformat()

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp,
            "source": self.source,
            "ticker": self.ticker,
            "order_id": self.order_id,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventLog:
    """통합 이벤트 로그.

    메모리(deque) + DB 하이브리드.
    빠른 조회는 메모리, 영속 저장은 DB.
    """

    def __init__(self, db=None, max_memory: int = MAX_MEMORY_EVENTS):
        self.db = db
        self._events: deque[Event] = deque(maxlen=max_memory)
        self._listeners: list = []

    def log(self, event: Event) -> None:
        """이벤트 기록."""
        self._events.append(event)

        # Python 로거 연동
        log_level = {
            EventSeverity.DEBUG: logging.DEBUG,
            EventSeverity.INFO: logging.INFO,
            EventSeverity.WARNING: logging.WARNING,
            EventSeverity.ERROR: logging.ERROR,
            EventSeverity.CRITICAL: logging.CRITICAL,
        }.get(event.severity, logging.INFO)

        logger.log(
            log_level,
            "[%s] %s%s",
            event.event_type.value,
            event.message,
            f" | ticker={event.ticker}" if event.ticker else "",
        )

        # DB 저장
        self._save_to_db(event)

    def log_quick(
        self,
        event_type: EventType,
        message: str,
        severity: EventSeverity = EventSeverity.INFO,
        **kwargs,
    ) -> None:
        """간편 이벤트 기록."""
        self.log(Event(
            event_type=event_type,
            severity=severity,
            message=message,
            **kwargs,
        ))

    # ── 조회 ─────────────────────────────────────────────

    def query(
        self,
        event_type: EventType | None = None,
        severity: EventSeverity | None = None,
        ticker: str = "",
        limit: int = 50,
    ) -> list[Event]:
        """이벤트 필터 조회."""
        results = []
        for event in reversed(self._events):
            if event_type and event.event_type != event_type:
                continue
            if severity and event.severity != severity:
                continue
            if ticker and event.ticker != ticker:
                continue
            results.append(event)
            if len(results) >= limit:
                break
        return results

    def get_recent(self, limit: int = 20) -> list[Event]:
        """최근 이벤트."""
        return list(reversed(list(self._events)))[:limit]

    def get_errors(self, limit: int = 20) -> list[Event]:
        """에러 이벤트."""
        return self.query(severity=EventSeverity.ERROR, limit=limit)

    def get_order_trail(self, order_id: str) -> list[Event]:
        """주문 추적 로그."""
        return [
            e for e in self._events
            if e.order_id == order_id
        ]

    def count_by_type(self) -> dict[str, int]:
        """유형별 이벤트 수."""
        counts: dict[str, int] = {}
        for event in self._events:
            key = event.event_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def count_by_severity(self) -> dict[str, int]:
        """심각도별 이벤트 수."""
        counts: dict[str, int] = {}
        for event in self._events:
            key = event.severity.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    @property
    def total_events(self) -> int:
        return len(self._events)

    # ── DB 저장 ──────────────────────────────────────────

    def _save_to_db(self, event: Event) -> None:
        """이벤트를 DB에 저장."""
        if not self.db:
            return
        try:
            with self.db._connect() as conn:
                conn.execute(
                    """INSERT INTO event_log
                       (event_type, severity, message, source, ticker,
                        order_id, data_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_type.value,
                        event.severity.value,
                        event.message,
                        event.source,
                        event.ticker,
                        event.order_id,
                        json.dumps(event.data, ensure_ascii=False) if event.data else None,
                        event.timestamp,
                    ),
                )
        except Exception as e:
            # DB 에러가 이벤트 로깅을 막지 않도록
            logger.debug("이벤트 DB 저장 실패: %s", e)


# ── 글로벌 인스턴스 ───────────────────────────────────────

_event_log: EventLog | None = None


def get_event_log(db=None) -> EventLog:
    """글로벌 EventLog 반환."""
    global _event_log
    if _event_log is None:
        _event_log = EventLog(db=db)
    return _event_log


# ── 텔레그램 포맷 ─────────────────────────────────────────

def format_event_summary(event_log: EventLog | None = None) -> str:
    """이벤트 로그 요약을 텔레그램 포맷."""
    log = event_log or get_event_log()

    severity_counts = log.count_by_severity()
    type_counts = log.count_by_type()

    lines = [
        "📋 이벤트 로그 요약",
        "━" * 25,
        f"총 이벤트: {log.total_events}건",
        "",
        "심각도별:",
    ]

    severity_icons = {
        "critical": "🔴", "error": "🟠",
        "warning": "🟡", "info": "🟢", "debug": "⚪",
    }
    for sev, count in sorted(severity_counts.items()):
        icon = severity_icons.get(sev, "❓")
        lines.append(f"  {icon} {sev}: {count}건")

    # 주요 이벤트 유형
    top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:5]
    if top_types:
        lines.extend(["", "유형별 (상위 5):"])
        for etype, count in top_types:
            lines.append(f"  {etype}: {count}건")

    # 최근 에러
    errors = log.get_errors(limit=3)
    if errors:
        lines.extend(["", "최근 에러:"])
        for e in errors:
            lines.append(f"  • {e.message[:60]}")

    return "\n".join(lines)


def format_recent_events(event_log: EventLog | None = None, limit: int = 10) -> str:
    """최근 이벤트를 텔레그램 포맷."""
    log = event_log or get_event_log()
    recent = log.get_recent(limit)

    if not recent:
        return "📋 이벤트: 없음"

    lines = [f"📋 최근 이벤트 ({len(recent)}건)", "━" * 25, ""]

    severity_icons = {
        "critical": "🔴", "error": "🟠",
        "warning": "🟡", "info": "🟢", "debug": "⚪",
    }

    for e in recent:
        icon = severity_icons.get(e.severity.value, "❓")
        time_str = e.timestamp[11:19] if len(e.timestamp) > 19 else e.timestamp
        msg = e.message[:50]
        lines.append(f"  {icon} [{time_str}] {msg}")

    return "\n".join(lines)
