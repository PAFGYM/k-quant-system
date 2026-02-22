"""Intraday monitoring system (장중 실시간 감시).

Monitors holdings based on investment horizon:
  scalp (1~3d): 30s interval, -3% trailing, +5~8% target
  swing (1~2w): 5min interval, -5% trailing, +10~15% target
  mid (1~3mo): 30min interval, -8% trailing, +20~30% target
  long (3mo+): daily, -15% trailing, +40~100% target
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

# ---------------------------------------------------------------------------
# Monitor settings per horizon
# ---------------------------------------------------------------------------

MONITOR_SETTINGS: dict[str, dict] = {
    "scalp": {
        "label": "스캘핑 (1~3일)",
        "interval_seconds": 30,
        "trailing_stop": 0.03,
        "target_profit": 0.05,
        "max_target": 0.08,
        "alerts": ["target_hit", "max_target", "trailing_stop", "volume_spike"],
        "auto_alert_cooldown": 60,
    },
    "swing": {
        "label": "스윙 (1~2주)",
        "interval_seconds": 300,
        "trailing_stop": 0.05,
        "target_profit": 0.10,
        "max_target": 0.15,
        "alerts": ["target_hit", "max_target", "trailing_stop", "trend_break", "volume_spike"],
        "auto_alert_cooldown": 600,
    },
    "mid": {
        "label": "중기 (1~3개월)",
        "interval_seconds": 1800,
        "trailing_stop": 0.08,
        "target_profit": 0.20,
        "max_target": 0.30,
        "alerts": ["target_hit", "max_target", "trailing_stop", "trend_break", "volume_spike"],
        "auto_alert_cooldown": 3600,
    },
    "long": {
        "label": "장기 (3개월+)",
        "interval_seconds": 86400,
        "trailing_stop": 0.15,
        "target_profit": 0.40,
        "max_target": 1.00,
        "alerts": ["target_hit", "max_target", "trailing_stop", "trend_break"],
        "auto_alert_cooldown": 86400,
    },
}

ALERT_TARGET_HIT = "TARGET_HIT"
ALERT_MAX_TARGET = "MAX_TARGET"
ALERT_TRAILING_STOP = "TRAILING_STOP"
ALERT_TREND_BREAK = "TREND_BREAK"
ALERT_VOLUME_SPIKE = "VOLUME_SPIKE"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MonitoredHolding:
    """감시 대상 보유 종목."""
    ticker: str = ""
    name: str = ""
    entry_price: float = 0.0
    quantity: int = 0
    horizon: str = "swing"
    peak_price: float = 0.0
    registered_at: str = ""

    def __post_init__(self) -> None:
        if not self.registered_at:
            self.registered_at = datetime.now(KST).isoformat()
        if self.peak_price <= 0 and self.entry_price > 0:
            self.peak_price = self.entry_price


@dataclass
class TradeAlert:
    """매매 알림."""
    ticker: str = ""
    name: str = ""
    alert_type: str = ""       # TARGET_HIT / MAX_TARGET / TRAILING_STOP / TREND_BREAK / VOLUME_SPIKE
    message: str = ""          # 한국어
    action: str = ""           # 한국어 액션 권고
    current_price: float = 0.0
    entry_price: float = 0.0
    profit_pct: float = 0.0
    severity: str = "info"     # info / warning / critical


@dataclass
class MonitorState:
    """전체 모니터 상태."""
    holdings: dict[str, MonitoredHolding] = field(default_factory=dict)
    peak_prices: dict[str, float] = field(default_factory=dict)
    last_alerts: dict[str, dict[str, str]] = field(default_factory=dict)

# ---------------------------------------------------------------------------
# Settings lookup
# ---------------------------------------------------------------------------

def get_settings_for_horizon(horizon: str) -> dict:
    """horizon에 맞는 설정 반환. 알 수 없으면 swing 기본값."""
    try:
        if horizon in MONITOR_SETTINGS:
            return MONITOR_SETTINGS[horizon]
        logger.warning("알 수 없는 horizon '%s', swing 기본값 사용", horizon)
        return MONITOR_SETTINGS["swing"]
    except Exception:
        logger.exception("get_settings_for_horizon 오류 (horizon=%s)", horizon)
        return MONITOR_SETTINGS["swing"]

# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def should_check(last_check_time: str, interval_seconds: int) -> bool:
    """마지막 확인 이후 interval_seconds 이상 경과했으면 True."""
    try:
        if not last_check_time:
            return True
        last_dt = datetime.fromisoformat(last_check_time)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=KST)
        elapsed = (datetime.now(KST) - last_dt).total_seconds()
        return elapsed >= interval_seconds
    except Exception:
        logger.exception("should_check 오류 (last=%s, interval=%d)", last_check_time, interval_seconds)
        return True


def update_peak_price(current_price: float, peak_price: float) -> float:
    """현재가와 기존 고점 중 큰 값 반환."""
    try:
        return max(current_price, peak_price)
    except Exception:
        logger.exception("update_peak_price 오류 (cur=%.2f, peak=%.2f)", current_price, peak_price)
        return peak_price


def check_target_hit(current_price: float, entry_price: float, target_pct: float) -> bool:
    """수익률이 target_pct 이상이면 True."""
    try:
        if entry_price <= 0:
            return False
        return (current_price - entry_price) / entry_price >= target_pct
    except Exception:
        logger.exception("check_target_hit 오류")
        return False


def check_max_target(current_price: float, entry_price: float, max_target_pct: float) -> bool:
    """수익률이 max_target_pct 이상이면 True."""
    try:
        if entry_price <= 0:
            return False
        return (current_price - entry_price) / entry_price >= max_target_pct
    except Exception:
        logger.exception("check_max_target 오류")
        return False


def check_trailing_stop(current_price: float, peak_price: float, trailing_pct: float) -> bool:
    """고점 대비 trailing_pct 이상 하락했으면 True."""
    try:
        if peak_price <= 0:
            return False
        return (peak_price - current_price) / peak_price >= trailing_pct
    except Exception:
        logger.exception("check_trailing_stop 오류")
        return False


def check_volume_spike(current_volume: float, avg_volume: float, threshold: float = 5.0) -> bool:
    """현재 거래량이 평균 대비 threshold배 이상이면 True."""
    try:
        if avg_volume <= 0:
            return False
        return current_volume / avg_volume >= threshold
    except Exception:
        logger.exception("check_volume_spike 오류")
        return False

# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

def is_cooldown(last_alerts: dict, ticker: str, alert_type: str, cooldown_seconds: int) -> bool:
    """동일 알림 쿨다운 중이면 True (재발송 불가)."""
    try:
        if ticker not in last_alerts or alert_type not in last_alerts[ticker]:
            return False
        last_time_str = last_alerts[ticker][alert_type]
        if not last_time_str:
            return False
        last_dt = datetime.fromisoformat(last_time_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=KST)
        return (datetime.now(KST) - last_dt).total_seconds() < cooldown_seconds
    except Exception:
        logger.exception("is_cooldown 오류 (ticker=%s, type=%s)", ticker, alert_type)
        return False

# ---------------------------------------------------------------------------
# Alert generation
# ---------------------------------------------------------------------------

def _calc_profit_pct(current_price: float, entry_price: float) -> float:
    """수익률 계산."""
    if entry_price <= 0:
        return 0.0
    return (current_price - entry_price) / entry_price


def _calc_drop_pct(current_price: float, peak_price: float) -> float:
    """고점 대비 하락률."""
    if peak_price <= 0:
        return 0.0
    return (peak_price - current_price) / peak_price


def generate_alerts(
    holding: MonitoredHolding,
    current_price: float,
    current_volume: float = 0,
    avg_volume: float = 0,
) -> list[TradeAlert]:
    """보유 종목의 horizon 설정에 따라 발동 알림 목록 반환."""
    try:
        alerts: list[TradeAlert] = []
        settings = get_settings_for_horizon(holding.horizon)
        alert_types = settings.get("alerts", [])
        profit_pct = _calc_profit_pct(current_price, holding.entry_price)
        drop_pct = _calc_drop_pct(current_price, holding.peak_price)

        # MAX_TARGET - 최대 목표 (최우선)
        if "max_target" in alert_types and check_max_target(
            current_price, holding.entry_price, settings["max_target"],
        ):
            alerts.append(TradeAlert(
                ticker=holding.ticker, name=holding.name,
                alert_type=ALERT_MAX_TARGET,
                message=f"{holding.name} 최대 목표 수익률 +{profit_pct * 100:.1f}% 도달!",
                action="전량 익절 강력 권장",
                current_price=current_price, entry_price=holding.entry_price,
                profit_pct=profit_pct, severity="critical",
            ))

        # TARGET_HIT - 목표 도달 (max_target 미발동 시에만)
        if "target_hit" in alert_types and check_target_hit(
            current_price, holding.entry_price, settings["target_profit"],
        ):
            if not any(a.alert_type == ALERT_MAX_TARGET for a in alerts):
                alerts.append(TradeAlert(
                    ticker=holding.ticker, name=holding.name,
                    alert_type=ALERT_TARGET_HIT,
                    message=f"{holding.name} 목표 수익 +{profit_pct * 100:.1f}% 도달!",
                    action="부분 익절 또는 트레일링 스탑 전환 권장",
                    current_price=current_price, entry_price=holding.entry_price,
                    profit_pct=profit_pct, severity="warning",
                ))

        # TRAILING_STOP
        if "trailing_stop" in alert_types and check_trailing_stop(
            current_price, holding.peak_price, settings["trailing_stop"],
        ):
            alerts.append(TradeAlert(
                ticker=holding.ticker, name=holding.name,
                alert_type=ALERT_TRAILING_STOP,
                message=f"{holding.name} 고점 대비 -{drop_pct * 100:.1f}% 하락! 트레일링 스탑 발동",
                action="즉시 매도 검토",
                current_price=current_price, entry_price=holding.entry_price,
                profit_pct=profit_pct, severity="critical",
            ))

        # TREND_BREAK - 트레일링 스탑 전 단계 경고 (60% 지점)
        if "trend_break" in alert_types:
            trend_break_pct = settings["trailing_stop"] * 0.6
            if drop_pct >= trend_break_pct and not check_trailing_stop(
                current_price, holding.peak_price, settings["trailing_stop"],
            ):
                alerts.append(TradeAlert(
                    ticker=holding.ticker, name=holding.name,
                    alert_type=ALERT_TREND_BREAK,
                    message=f"{holding.name} 고점 대비 -{drop_pct * 100:.1f}% 하락 중, 추세 이탈 주의",
                    action="분할 매도 또는 손절 라인 재점검",
                    current_price=current_price, entry_price=holding.entry_price,
                    profit_pct=profit_pct, severity="warning",
                ))

        # VOLUME_SPIKE
        if "volume_spike" in alert_types and check_volume_spike(current_volume, avg_volume):
            ratio = current_volume / avg_volume if avg_volume > 0 else 0
            alerts.append(TradeAlert(
                ticker=holding.ticker, name=holding.name,
                alert_type=ALERT_VOLUME_SPIKE,
                message=f"{holding.name} 거래량 {ratio:.1f}x 급증!",
                action="호가창 확인 및 방향성 판단 필요",
                current_price=current_price, entry_price=holding.entry_price,
                profit_pct=profit_pct, severity="info",
            ))

        return alerts
    except Exception:
        logger.exception("generate_alerts 오류 (ticker=%s)", holding.ticker)
        return []

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_num(value: float) -> str:
    """천단위 콤마 숫자."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.0f}"


def _fmt_profit(pct: float) -> str:
    """수익률 부호 문자열."""
    if pct >= 0:
        return f"+{pct * 100:.1f}%"
    return f"{pct * 100:.1f}%"

# ---------------------------------------------------------------------------
# Alert formatting (Telegram)
# ---------------------------------------------------------------------------

def format_trade_alert(alert: TradeAlert, holding: MonitoredHolding) -> str:
    """TradeAlert를 텔레그램 전송용 한국어 문자열로 포맷."""
    try:
        settings = get_settings_for_horizon(holding.horizon)
        h_label = settings.get("label", holding.horizon)
        cur = _fmt_num(alert.current_price)
        ent = _fmt_num(alert.entry_price)
        prf = _fmt_profit(alert.profit_pct)
        ts = datetime.now(KST).strftime("%H:%M:%S")
        lines: list[str] = []

        if alert.alert_type == ALERT_TARGET_HIT:
            lines.append(f"[목표 도달] {alert.name} ({alert.ticker})")
            lines.append(f"투자기간: {h_label}")
            lines.append(f"현재가: {cur}원 (수익률 {prf})")
            lines.append(f"매수단가: {ent}원")
            lines.append("")
            lines.append(f"목표 수익 {prf} 도달!")
            lines.append("")
            lines.append("선택지:")
            lines.append("  1) 50% 부분 익절 후 나머지 트레일링")
            lines.append("  2) 전량 익절")
            lines.append(f"  3) 트레일링 스탑 전환 (고점 대비 -{settings['trailing_stop'] * 100:.0f}%)")
            lines.append("")
            lines.append(f"{USER_NAME}, 부분 익절 또는 트레일링 스탑 전환을 권장합니다.")

        elif alert.alert_type == ALERT_MAX_TARGET:
            lines.append(f"[최대 목표 도달] {alert.name} ({alert.ticker})")
            lines.append(f"투자기간: {h_label}")
            lines.append(f"현재가: {cur}원 (수익률 {prf})")
            lines.append(f"매수단가: {ent}원")
            lines.append("")
            lines.append("최대 목표 도달! 전량 익절 강력 권장")
            lines.append(f"목표 수익률 범위: +{settings['target_profit'] * 100:.0f}% ~ +{settings['max_target'] * 100:.0f}%")
            lines.append("현재 수익률이 최대 목표를 초과했습니다.")
            lines.append("")
            lines.append(f"{USER_NAME}, 욕심 부리지 마시고 전량 정리하세요!")

        elif alert.alert_type == ALERT_TRAILING_STOP:
            drop = _calc_drop_pct(alert.current_price, holding.peak_price)
            d_str = f"-{drop * 100:.1f}%"
            pk = _fmt_num(holding.peak_price)
            lines.append(f"[트레일링 스탑] {alert.name} ({alert.ticker})")
            lines.append(f"투자기간: {h_label}")
            lines.append(f"현재가: {cur}원 (수익률 {prf})")
            lines.append(f"고점: {pk}원 -> 현재: {cur}원 ({d_str})")
            lines.append("")
            lines.append(f"고점 대비 {d_str} 하락! 트레일링 스탑 발동")
            lines.append("")
            lines.append("즉시 매도를 검토하세요.")
            lines.append(f"{USER_NAME}, 손실 확대 전에 빠른 판단이 필요합니다.")

        elif alert.alert_type == ALERT_TREND_BREAK:
            drop = _calc_drop_pct(alert.current_price, holding.peak_price)
            d_str = f"-{drop * 100:.1f}%"
            lines.append(f"[추세 이탈 주의] {alert.name} ({alert.ticker})")
            lines.append(f"투자기간: {h_label}")
            lines.append(f"현재가: {cur}원 (수익률 {prf})")
            lines.append(f"고점 대비 {d_str} 하락 중")
            lines.append("")
            lines.append("추세가 약해지고 있습니다.")
            lines.append(f"트레일링 스탑 라인: 고점 대비 -{settings['trailing_stop'] * 100:.0f}%")
            lines.append("")
            lines.append(f"{USER_NAME}, 분할 매도 또는 손절 라인을 재점검하세요.")

        elif alert.alert_type == ALERT_VOLUME_SPIKE:
            lines.append(f"[거래량 급증] {alert.name} ({alert.ticker})")
            lines.append(f"투자기간: {h_label}")
            lines.append(f"현재가: {cur}원 (수익률 {prf})")
            lines.append("")
            lines.append(alert.message)
            lines.append("")
            lines.append("거래량 급증은 방향 전환 신호일 수 있습니다.")
            lines.append("호가창과 체결 강도를 확인하세요.")
            lines.append("")
            lines.append(f"{USER_NAME}, 호가창 확인 및 방향성 판단이 필요합니다.")

        else:
            lines.append(f"[알림] {alert.name} ({alert.ticker})")
            lines.append(alert.message)
            lines.append(alert.action)

        lines.append(f"({ts} KST)")
        return "\n".join(lines)
    except Exception:
        logger.exception("format_trade_alert 오류 (ticker=%s, type=%s)", alert.ticker, alert.alert_type)
        return f"[알림 오류] {alert.name} ({alert.ticker}) - {alert.message}"


def format_urgent_stop_alert(alert: TradeAlert, holding: MonitoredHolding) -> str:
    """긴급 트레일링 스탑 알림 - 강조 포맷."""
    try:
        settings = get_settings_for_horizon(holding.horizon)
        h_label = settings.get("label", holding.horizon)
        cur = _fmt_num(alert.current_price)
        ent = _fmt_num(alert.entry_price)
        prf = _fmt_profit(alert.profit_pct)
        pk = _fmt_num(holding.peak_price)
        drop = _calc_drop_pct(alert.current_price, holding.peak_price)
        d_str = f"-{drop * 100:.1f}%"
        ts = datetime.now(KST).strftime("%H:%M:%S")

        pnl = (alert.current_price - alert.entry_price) * holding.quantity
        pnl_sign = "+" if pnl >= 0 else "-"
        pnl_str = _fmt_num(abs(pnl))

        lines: list[str] = [
            "===========================",
            "  TRAILING STOP - 긴급 알림",
            "===========================",
            "",
            f"종목: {alert.name} ({alert.ticker})",
            f"투자기간: {h_label}",
            "",
            f"현재가: {cur}원",
            f"매수단가: {ent}원",
            f"수익률: {prf}",
            "",
            f"고점: {pk}원",
            f"고점 대비: {d_str}",
            f"스탑 기준: -{settings['trailing_stop'] * 100:.0f}%",
            "",
            f"보유수량: {holding.quantity:,}주",
            f"평가손익: {pnl_sign}{pnl_str}원",
            "",
            "===========================",
            f"고점 대비 {d_str} 하락!",
            "트레일링 스탑이 발동되었습니다.",
            "===========================",
            "",
            f"{USER_NAME},",
            "즉시 매도를 강력히 권고합니다.",
            "손실이 더 확대되기 전에 빠르게 대응하세요.",
            "",
            f"({ts} KST)",
        ]
        return "\n".join(lines)
    except Exception:
        logger.exception("format_urgent_stop_alert 오류 (ticker=%s)", alert.ticker)
        return f"[긴급] {alert.name} ({alert.ticker}) 트레일링 스탑 발동 - 즉시 매도 검토 필요"
