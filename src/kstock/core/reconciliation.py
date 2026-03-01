"""리컨실레이션 + 킬스위치 — v5.0-3.

내부 DB(holdings, orders)와 브로커 실제 포지션을 대조하여
불일치를 감지하고, 심각한 불일치 시 자동으로 안전모드(킬스위치)를 발동한다.

핵심 기능:
  1. PositionReconciler — 내부 vs 브로커 포지션 대조
  2. KillSwitch — 비상 시 모든 주문 차단
  3. SafetyMode — 레벨별 안전모드 (NORMAL → CAUTION → SAFE → LOCKDOWN)
  4. ReconciliationReport — 대조 결과 + 불일치 상세
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"


# ── 안전모드 레벨 ─────────────────────────────────────────

class SafetyLevel(IntEnum):
    """시스템 안전모드 레벨."""
    NORMAL = 0       # 정상 운영
    CAUTION = 1      # 주의 — 경고 로깅, 매매 허용
    SAFE = 2         # 안전 — 신규 매수 차단, 매도만 허용
    LOCKDOWN = 3     # 잠금 — 모든 자동매매 차단


# ── 불일치 유형 ───────────────────────────────────────────

class MismatchType(str, Enum):
    """리컨실레이션 불일치 유형."""
    QUANTITY_DIFF = "quantity_diff"        # 수량 차이
    POSITION_MISSING = "position_missing"  # 내부에만 존재 (브로커에 없음)
    PHANTOM_POSITION = "phantom_position"  # 브로커에만 존재 (내부에 없음)
    PRICE_DIFF = "price_diff"             # 평균가 큰 차이 (>5%)
    VALUE_DIFF = "value_diff"             # 평가액 큰 차이 (>10%)


@dataclass
class Mismatch:
    """단일 불일치 항목."""
    mismatch_type: MismatchType
    severity: str       # "critical", "high", "medium", "low"
    ticker: str
    name: str
    description: str
    internal_value: float = 0.0    # DB 기록 값
    broker_value: float = 0.0      # 브로커 실제 값
    diff: float = 0.0              # 차이
    details: dict = field(default_factory=dict)


@dataclass
class ReconciliationReport:
    """리컨실레이션 결과."""
    timestamp: str
    status: str = "ok"              # "ok", "mismatch", "error"
    mismatches: list[Mismatch] = field(default_factory=list)
    internal_positions: int = 0
    broker_positions: int = 0
    matched_positions: int = 0
    safety_level_before: SafetyLevel = SafetyLevel.NORMAL
    safety_level_after: SafetyLevel = SafetyLevel.NORMAL
    details: dict = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return any(m.severity == "critical" for m in self.mismatches)

    @property
    def mismatch_count(self) -> int:
        return len(self.mismatches)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "status": self.status,
            "mismatch_count": self.mismatch_count,
            "internal_positions": self.internal_positions,
            "broker_positions": self.broker_positions,
            "matched_positions": self.matched_positions,
            "safety_level": self.safety_level_after.name,
            "mismatches": [
                {
                    "type": m.mismatch_type.value,
                    "severity": m.severity,
                    "ticker": m.ticker,
                    "description": m.description,
                }
                for m in self.mismatches
            ],
        }


# ── 킬스위치 ─────────────────────────────────────────────

class KillSwitch:
    """비상 킬스위치.

    활성화 시:
      - 모든 신규 주문 차단
      - OrderLedger의 PreTradeValidator에 전파
      - 텔레그램 알림 발송 (caller 책임)
    """

    def __init__(self):
        self._active = False
        self._activated_at: str = ""
        self._reason: str = ""
        self._activated_by: str = ""  # "reconciliation", "manual", "risk_limit"
        self._history: list[dict] = []

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def activated_at(self) -> str:
        return self._activated_at

    def activate(self, reason: str, activated_by: str = "system") -> None:
        """킬스위치 활성화."""
        if self._active:
            logger.warning("킬스위치 이미 활성 상태: %s", self._reason)
            return

        self._active = True
        self._reason = reason
        self._activated_by = activated_by
        self._activated_at = datetime.now(KST).isoformat()

        self._history.append({
            "action": "activate",
            "reason": reason,
            "by": activated_by,
            "timestamp": self._activated_at,
        })

        logger.critical(
            "🔴 킬스위치 활성화: %s (by %s)", reason, activated_by,
        )

    def deactivate(self, reason: str = "수동 해제") -> None:
        """킬스위치 해제."""
        if not self._active:
            return

        self._active = False
        now = datetime.now(KST).isoformat()

        self._history.append({
            "action": "deactivate",
            "reason": reason,
            "timestamp": now,
        })

        logger.info("🟢 킬스위치 해제: %s", reason)
        self._reason = ""
        self._activated_at = ""
        self._activated_by = ""

    def get_status(self) -> dict:
        return {
            "active": self._active,
            "reason": self._reason,
            "activated_by": self._activated_by,
            "activated_at": self._activated_at,
            "history_count": len(self._history),
        }


# ── 안전모드 관리자 ───────────────────────────────────────

class SafetyModeManager:
    """안전모드 레벨 관리.

    레벨별 제한:
      NORMAL(0): 제한 없음
      CAUTION(1): 경고 로깅, 매매 허용
      SAFE(2): 신규 매수 차단, 매도만 허용
      LOCKDOWN(3): 모든 자동매매 차단 + 킬스위치 연동
    """

    def __init__(self, kill_switch: KillSwitch | None = None):
        self._level = SafetyLevel.NORMAL
        self.kill_switch = kill_switch or KillSwitch()
        self._history: list[dict] = []

    @property
    def level(self) -> SafetyLevel:
        return self._level

    @property
    def is_buy_allowed(self) -> bool:
        return self._level < SafetyLevel.SAFE

    @property
    def is_sell_allowed(self) -> bool:
        return self._level < SafetyLevel.LOCKDOWN

    @property
    def is_trading_allowed(self) -> bool:
        return self._level < SafetyLevel.LOCKDOWN

    def set_level(self, level: SafetyLevel, reason: str = "") -> None:
        """안전모드 레벨 설정."""
        old = self._level
        self._level = level

        self._history.append({
            "from": old.name,
            "to": level.name,
            "reason": reason,
            "timestamp": datetime.now(KST).isoformat(),
        })

        # LOCKDOWN 시 킬스위치 자동 활성화
        if level == SafetyLevel.LOCKDOWN and not self.kill_switch.is_active:
            self.kill_switch.activate(
                reason=f"안전모드 LOCKDOWN: {reason}",
                activated_by="safety_mode",
            )

        # NORMAL/CAUTION 복귀 시 킬스위치 해제
        if level <= SafetyLevel.CAUTION and self.kill_switch.is_active:
            if self.kill_switch._activated_by == "safety_mode":
                self.kill_switch.deactivate(reason=f"안전모드 {level.name} 복귀")

        if old != level:
            logger.warning(
                "안전모드 변경: %s → %s%s",
                old.name, level.name,
                f" ({reason})" if reason else "",
            )

    def escalate(self, reason: str = "") -> SafetyLevel:
        """한 단계 상승."""
        if self._level < SafetyLevel.LOCKDOWN:
            new_level = SafetyLevel(self._level + 1)
            self.set_level(new_level, reason)
        return self._level

    def de_escalate(self, reason: str = "") -> SafetyLevel:
        """한 단계 하강."""
        if self._level > SafetyLevel.NORMAL:
            new_level = SafetyLevel(self._level - 1)
            self.set_level(new_level, reason)
        return self._level

    def get_status(self) -> dict:
        return {
            "level": self._level.name,
            "level_value": int(self._level),
            "buy_allowed": self.is_buy_allowed,
            "sell_allowed": self.is_sell_allowed,
            "kill_switch": self.kill_switch.get_status(),
            "history_count": len(self._history),
        }


# ── 포지션 리컨실러 ───────────────────────────────────────

class PositionReconciler:
    """내부 DB vs 브로커 포지션 대조 엔진.

    사용법:
        reconciler = PositionReconciler(safety_manager)
        report = reconciler.reconcile(internal_holdings, broker_holdings)
        # report.mismatches 확인 후 조치
    """

    # 불일치 수에 따른 안전모드 임계값
    THRESHOLDS = {
        "caution": 1,   # 1건 이상 → CAUTION
        "safe": 3,      # 3건 이상 → SAFE
        "lockdown": 5,  # 5건 이상 또는 critical → LOCKDOWN
    }

    PRICE_DIFF_THRESHOLD = 0.05    # 5% 이상 평균가 차이
    VALUE_DIFF_THRESHOLD = 0.10    # 10% 이상 평가액 차이

    def __init__(self, safety_manager: SafetyModeManager | None = None):
        self.safety = safety_manager or SafetyModeManager()
        self._last_report: ReconciliationReport | None = None

    @property
    def last_report(self) -> ReconciliationReport | None:
        return self._last_report

    def reconcile(
        self,
        internal: list[dict],
        broker: list[dict],
    ) -> ReconciliationReport:
        """포지션 대조 실행.

        Args:
            internal: 내부 DB 보유 종목
                [{ticker, name, quantity, avg_price, eval_amount}, ...]
            broker: 브로커 보유 종목
                [{ticker, name, quantity, avg_price, eval_amount}, ...]

        Returns:
            ReconciliationReport.
        """
        now = datetime.now(KST).isoformat()
        report = ReconciliationReport(
            timestamp=now,
            internal_positions=len(internal),
            broker_positions=len(broker),
            safety_level_before=self.safety.level,
        )

        # 인덱싱
        internal_map = {h["ticker"]: h for h in internal if h.get("ticker")}
        broker_map = {h["ticker"]: h for h in broker if h.get("ticker")}

        all_tickers = set(internal_map.keys()) | set(broker_map.keys())
        matched = 0

        for ticker in all_tickers:
            i_pos = internal_map.get(ticker)
            b_pos = broker_map.get(ticker)
            name = (i_pos or b_pos or {}).get("name", ticker)

            if i_pos and not b_pos:
                # 내부에만 존재
                report.mismatches.append(Mismatch(
                    mismatch_type=MismatchType.POSITION_MISSING,
                    severity="high",
                    ticker=ticker,
                    name=name,
                    description=f"{name} — 내부 DB에 {i_pos.get('quantity', 0)}주 기록, 브로커에 없음",
                    internal_value=i_pos.get("quantity", 0),
                    broker_value=0,
                    diff=i_pos.get("quantity", 0),
                ))

            elif b_pos and not i_pos:
                # 브로커에만 존재
                report.mismatches.append(Mismatch(
                    mismatch_type=MismatchType.PHANTOM_POSITION,
                    severity="critical",
                    ticker=ticker,
                    name=name,
                    description=f"{name} — 브로커에 {b_pos.get('quantity', 0)}주 존재, 내부 DB에 없음 (팬텀)",
                    internal_value=0,
                    broker_value=b_pos.get("quantity", 0),
                    diff=-b_pos.get("quantity", 0),
                ))

            else:
                # 양쪽 모두 존재 → 수치 대조
                i_qty = i_pos.get("quantity", 0)
                b_qty = b_pos.get("quantity", 0)

                if i_qty != b_qty:
                    severity = "critical" if abs(i_qty - b_qty) > max(i_qty, b_qty, 1) * 0.5 else "high"
                    report.mismatches.append(Mismatch(
                        mismatch_type=MismatchType.QUANTITY_DIFF,
                        severity=severity,
                        ticker=ticker,
                        name=name,
                        description=f"{name} 수량: 내부 {i_qty}주 vs 브로커 {b_qty}주",
                        internal_value=i_qty,
                        broker_value=b_qty,
                        diff=i_qty - b_qty,
                    ))
                else:
                    matched += 1

                # 평균가 차이
                i_price = i_pos.get("avg_price", 0)
                b_price = b_pos.get("avg_price", 0)
                if i_price > 0 and b_price > 0:
                    price_diff_pct = abs(i_price - b_price) / b_price
                    if price_diff_pct > self.PRICE_DIFF_THRESHOLD:
                        report.mismatches.append(Mismatch(
                            mismatch_type=MismatchType.PRICE_DIFF,
                            severity="medium",
                            ticker=ticker,
                            name=name,
                            description=(
                                f"{name} 평균가: 내부 {i_price:,.0f} vs "
                                f"브로커 {b_price:,.0f} ({price_diff_pct:.1%} 차이)"
                            ),
                            internal_value=i_price,
                            broker_value=b_price,
                            diff=i_price - b_price,
                        ))

        report.matched_positions = matched
        report.status = "ok" if not report.mismatches else "mismatch"

        # 안전모드 자동 조정
        self._adjust_safety(report)
        report.safety_level_after = self.safety.level

        self._last_report = report

        logger.info(
            "리컨실레이션: 내부 %d / 브로커 %d / 매칭 %d / 불일치 %d → %s",
            report.internal_positions, report.broker_positions,
            matched, report.mismatch_count,
            self.safety.level.name,
        )

        return report

    def _adjust_safety(self, report: ReconciliationReport) -> None:
        """불일치에 따라 안전모드 자동 조정."""
        if report.has_critical:
            self.safety.set_level(
                SafetyLevel.LOCKDOWN,
                f"리컨실레이션 critical 불일치 감지 ({report.mismatch_count}건)",
            )
        elif report.mismatch_count >= self.THRESHOLDS["lockdown"]:
            self.safety.set_level(
                SafetyLevel.LOCKDOWN,
                f"리컨실레이션 불일치 {report.mismatch_count}건 (임계값 {self.THRESHOLDS['lockdown']})",
            )
        elif report.mismatch_count >= self.THRESHOLDS["safe"]:
            self.safety.set_level(
                SafetyLevel.SAFE,
                f"리컨실레이션 불일치 {report.mismatch_count}건",
            )
        elif report.mismatch_count >= self.THRESHOLDS["caution"]:
            self.safety.set_level(
                SafetyLevel.CAUTION,
                f"리컨실레이션 불일치 {report.mismatch_count}건",
            )
        elif report.mismatch_count == 0:
            if self.safety.level > SafetyLevel.NORMAL:
                self.safety.set_level(
                    SafetyLevel.NORMAL,
                    "리컨실레이션 정상 — 불일치 없음",
                )


# ── 글로벌 인스턴스 ───────────────────────────────────────

_kill_switch = KillSwitch()
_safety_manager = SafetyModeManager(kill_switch=_kill_switch)
_reconciler = PositionReconciler(safety_manager=_safety_manager)


def get_kill_switch() -> KillSwitch:
    return _kill_switch


def get_safety_manager() -> SafetyModeManager:
    return _safety_manager


def get_reconciler() -> PositionReconciler:
    return _reconciler


# ── 텔레그램 포맷 ─────────────────────────────────────────

def format_reconciliation_report(report: ReconciliationReport) -> str:
    """리컨실레이션 결과를 텔레그램 포맷."""
    if report.status == "ok":
        return (
            f"✅ 리컨실레이션 정상\n"
            f"  내부 {report.internal_positions}종목 / "
            f"브로커 {report.broker_positions}종목 / "
            f"매칭 {report.matched_positions}종목"
        )

    lines = [
        "🔍 리컨실레이션 결과",
        "━" * 25,
        f"⏰ {report.timestamp}",
        f"내부: {report.internal_positions}종목 | 브로커: {report.broker_positions}종목",
        f"매칭: {report.matched_positions}종목 | 불일치: {report.mismatch_count}건",
        "",
    ]

    # 불일치 상세
    severity_icons = {
        "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢",
    }
    for m in report.mismatches[:10]:
        icon = severity_icons.get(m.severity, "⚪")
        lines.append(f"  {icon} {m.description}")

    # 안전모드 변경
    if report.safety_level_before != report.safety_level_after:
        lines.extend([
            "", "━" * 25,
            f"🛡️ 안전모드: {report.safety_level_before.name} → {report.safety_level_after.name}",
        ])

    return "\n".join(lines)


def format_safety_status(manager: SafetyModeManager | None = None) -> str:
    """안전모드 상태를 텔레그램 포맷."""
    mgr = manager or _safety_manager
    level_icons = {
        SafetyLevel.NORMAL: "🟢",
        SafetyLevel.CAUTION: "🟡",
        SafetyLevel.SAFE: "🟠",
        SafetyLevel.LOCKDOWN: "🔴",
    }
    level_desc = {
        SafetyLevel.NORMAL: "정상 운영",
        SafetyLevel.CAUTION: "주의 — 매매 허용",
        SafetyLevel.SAFE: "안전 — 매도만 허용",
        SafetyLevel.LOCKDOWN: "잠금 — 모든 매매 차단",
    }

    icon = level_icons.get(mgr.level, "❓")
    desc = level_desc.get(mgr.level, "알 수 없음")

    lines = [
        f"🛡️ 안전모드: {icon} {mgr.level.name}",
        f"  상태: {desc}",
        f"  매수: {'✅' if mgr.is_buy_allowed else '❌'}",
        f"  매도: {'✅' if mgr.is_sell_allowed else '❌'}",
    ]

    ks = mgr.kill_switch
    if ks.is_active:
        lines.extend([
            f"  킬스위치: 🔴 활성",
            f"  사유: {ks.reason}",
        ])
    else:
        lines.append(f"  킬스위치: 🟢 해제")

    return "\n".join(lines)
