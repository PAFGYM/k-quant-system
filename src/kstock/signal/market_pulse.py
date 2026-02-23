"""시장 맥박 (Market Pulse) 엔진 - Phase 8.

5분마다 시장 전체 상태를 체크하고, 분위기가 바뀌면
즉시 알림 + 보유종목 영향 분석.

감지하는 변화:
  1. 지수 추세 반전 (상승->하락, 하락->상승)
  2. 프로그램 매매 급변 (외인/기관 대량 매도 전환)
  3. 변동성 급등
  4. 글로벌 이벤트 실시간 반영 (미 선물, 환율)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"

MARKET_STATES = {
    "STRONG_BULL": {"label": "강한 상승", "emoji": "\U0001f525"},
    "BULL": {"label": "상승", "emoji": "\U0001f7e2"},
    "NEUTRAL": {"label": "보합", "emoji": "\u26aa"},
    "BEAR": {"label": "하락", "emoji": "\U0001f7e0"},
    "STRONG_BEAR": {"label": "강한 하락", "emoji": "\U0001f534"},
    "REVERSAL_UP": {"label": "반전 상승", "emoji": "\U0001f4a5"},
    "REVERSAL_DOWN": {"label": "반전 하락!", "emoji": "\u26a0\ufe0f"},
}


@dataclass
class PulseRecord:
    """5분 간격 시장 상태 기록."""
    time: datetime
    state: str
    score: float
    spx_change: float = 0.0
    vix: float = 0.0
    usdkrw_change: float = 0.0
    btc_change: float = 0.0
    kospi_proxy: float = 0.0  # proxy from macro snapshot


@dataclass
class MarketChange:
    """시장 변화 이벤트."""
    from_state: str
    to_state: str
    from_label: str
    to_label: str
    severity: int  # 1=minor, 2=moderate, 3=critical
    score_delta: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(KST))


@dataclass
class PortfolioImpact:
    """보유종목 영향 분석 결과."""
    ticker: str
    name: str
    pnl_pct: float
    action: str  # "익절 검토", "손절 검토", "관망", "반등 기대"
    urgency: str  # "high", "medium", "low"


class MarketPulse:
    """실시간 시장 분위기 감지 엔진."""

    def __init__(self) -> None:
        self.prev_state: str | None = None
        self.prev_score: float = 0.0
        self.state_history: list[PulseRecord] = []
        self.alert_cooldown: dict[str, datetime] = {}
        self._cooldown_minutes = 15

    def check_pulse(self, macro_snapshot) -> MarketChange | None:
        """시장 상태 체크 및 변화 감지.

        Args:
            macro_snapshot: MacroSnapshot instance from macro_client.

        Returns:
            MarketChange if a significant state change detected, None otherwise.
        """
        now = datetime.now(KST)

        # 장 시간 체크 (09:05~15:25)
        if now.weekday() >= 5:
            return None
        market_start = now.replace(hour=9, minute=5, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=25, second=0, microsecond=0)
        if not (market_start <= now <= market_end):
            return None

        # 현재 상태 판단
        score = self._compute_score(macro_snapshot)
        current_state = self._determine_state(score)

        # 기록
        record = PulseRecord(
            time=now,
            state=current_state,
            score=score,
            spx_change=macro_snapshot.spx_change_pct,
            vix=macro_snapshot.vix,
            usdkrw_change=macro_snapshot.usdkrw_change_pct,
            btc_change=macro_snapshot.btc_change_pct,
        )
        self.state_history.append(record)

        # 오래된 기록 정리 (최근 2시간만 유지)
        cutoff = now - timedelta(hours=2)
        self.state_history = [r for r in self.state_history if r.time > cutoff]

        # 상태 변화 감지
        change = None
        if self.prev_state and current_state != self.prev_state:
            change = self._analyze_change(self.prev_state, current_state, score)

            # 쿨다운 체크 (같은 종류 알림 반복 방지)
            if change and change.severity >= 2:
                cooldown_key = f"{change.from_state}->{change.to_state}"
                last_sent = self.alert_cooldown.get(cooldown_key)
                if last_sent and (now - last_sent).total_seconds() < self._cooldown_minutes * 60:
                    change = None
                else:
                    self.alert_cooldown[cooldown_key] = now

        self.prev_state = current_state
        self.prev_score = score

        return change

    def _compute_score(self, macro) -> float:
        """시장 종합 점수 계산 (-100 ~ +100)."""
        score = 0.0

        # S&P500 변화 (가중 40%)
        score += macro.spx_change_pct * 30

        # 나스닥 변화 (가중 20%)
        score += macro.nasdaq_change_pct * 15

        # VIX 레벨 (가중 20%)
        if macro.vix < 18:
            score += 15
        elif macro.vix < 22:
            score += 5
        elif macro.vix < 28:
            score -= 10
        else:
            score -= 25

        # 환율 변화 (가중 10%) - 원화 약세 = 부정적
        score -= macro.usdkrw_change_pct * 10

        # BTC 모멘텀 (가중 10%)
        score += macro.btc_change_pct * 5

        # 추세 반영 (최근 30분)
        trend = self._calculate_trend(minutes=30)
        score += trend * 20

        return max(-100, min(100, score))

    def _determine_state(self, score: float) -> str:
        """점수 기반 시장 상태 판단."""
        # 반전 감지 (핵심!)
        if self.prev_state in ("STRONG_BULL", "BULL") and score < -15:
            return "REVERSAL_DOWN"
        if self.prev_state in ("STRONG_BEAR", "BEAR") and score > 15:
            return "REVERSAL_UP"

        # 일반 상태
        if score > 40:
            return "STRONG_BULL"
        if score > 15:
            return "BULL"
        if score > -15:
            return "NEUTRAL"
        if score > -40:
            return "BEAR"
        return "STRONG_BEAR"

    def _calculate_trend(self, minutes: int = 30) -> float:
        """최근 N분간 추세 (-1 ~ +1)."""
        now = datetime.now(KST)
        cutoff = now - timedelta(minutes=minutes)
        points = [r for r in self.state_history if r.time > cutoff]

        if len(points) < 3:
            return 0.0

        scores = [p.score for p in points]
        if len(scores) < 2:
            return 0.0

        # 선형 추세
        first_avg = sum(scores[: len(scores) // 2]) / max(len(scores) // 2, 1)
        last_avg = sum(scores[len(scores) // 2 :]) / max(len(scores) - len(scores) // 2, 1)

        diff = last_avg - first_avg
        return max(-1.0, min(1.0, diff / 30))

    def _analyze_change(
        self, from_state: str, to_state: str, current_score: float,
    ) -> MarketChange:
        """상태 변화 분석."""
        from_info = MARKET_STATES.get(from_state, {"label": from_state})
        to_info = MARKET_STATES.get(to_state, {"label": to_state})

        # 심각도 판단
        severity = 1
        if to_state in ("REVERSAL_DOWN", "REVERSAL_UP"):
            severity = 3
        elif to_state in ("STRONG_BEAR",) and from_state in ("BULL", "STRONG_BULL"):
            severity = 3
        elif to_state in ("STRONG_BULL",) and from_state in ("BEAR", "STRONG_BEAR"):
            severity = 2
        elif from_state != "NEUTRAL" and to_state != "NEUTRAL":
            severity = 2

        return MarketChange(
            from_state=from_state,
            to_state=to_state,
            from_label=from_info["label"],
            to_label=to_info["label"],
            severity=severity,
            score_delta=current_score - self.prev_score,
        )

    def get_recent_history(self, minutes: int = 60) -> list[PulseRecord]:
        """최근 N분 기록 반환."""
        cutoff = datetime.now(KST) - timedelta(minutes=minutes)
        return [r for r in self.state_history if r.time > cutoff]

    def get_current_state(self) -> str:
        """현재 상태 반환."""
        return self.prev_state or "NEUTRAL"

    def analyze_portfolio_impact(
        self, change: MarketChange, holdings: list[dict],
    ) -> list[PortfolioImpact]:
        """시장 변화가 보유종목에 미치는 영향 분석.

        Args:
            change: MarketChange event.
            holdings: List of holding dicts with keys:
                ticker, name, buy_price, current_price, pnl_pct.

        Returns:
            List of PortfolioImpact results.
        """
        impacts: list[PortfolioImpact] = []

        for h in holdings:
            # pnl_pct는 항상 퍼센트 단위 (예: 8.0 = 8%)
            pnl = h.get("pnl_pct", 0) / 100

            if change.to_state == "REVERSAL_DOWN":
                if pnl > 0.05:
                    action = "익절 검토"
                    urgency = "high"
                elif pnl < -0.03:
                    action = "손절 검토"
                    urgency = "high"
                else:
                    action = "관망"
                    urgency = "medium"
            elif change.to_state in ("BEAR", "STRONG_BEAR"):
                if pnl > 0.10:
                    action = "일부 익절 검토"
                    urgency = "medium"
                elif pnl < -0.05:
                    action = "손절 경고"
                    urgency = "high"
                else:
                    action = "관망"
                    urgency = "low"
            elif change.to_state == "REVERSAL_UP":
                action = "반등 기대"
                urgency = "low"
            elif change.to_state in ("BULL", "STRONG_BULL"):
                action = "홀딩"
                urgency = "low"
            else:
                action = "관망"
                urgency = "low"

            impacts.append(PortfolioImpact(
                ticker=h.get("ticker", ""),
                name=h.get("name", ""),
                pnl_pct=h.get("pnl_pct", 0),
                action=action,
                urgency=urgency,
            ))

        return impacts


def format_pulse_alert(
    change: MarketChange,
    macro_snapshot,
    impacts: list[PortfolioImpact] | None = None,
    history: list[PulseRecord] | None = None,
) -> str:
    """시장 변화 알림 메시지 포맷."""
    to_info = MARKET_STATES.get(change.to_state, {"label": change.to_label, "emoji": ""})
    from_info = MARKET_STATES.get(change.from_state, {"label": change.from_label, "emoji": ""})

    lines = [
        f"\u26a0\ufe0f [시장 분위기 변화]",
        f"{from_info.get('emoji', '')} {change.from_label} \u2192 "
        f"{to_info.get('emoji', '')} {change.to_label}",
        "",
        f"S&P500: {macro_snapshot.spx_change_pct:+.2f}%"
        f" | 나스닥: {macro_snapshot.nasdaq_change_pct:+.2f}%",
        f"VIX: {macro_snapshot.vix:.1f} | 환율: {macro_snapshot.usdkrw:,.0f}원",
        f"변화 시점: {change.timestamp.strftime('%H:%M')}",
    ]

    # 추세 미니 차트
    if history and len(history) >= 3:
        lines.append("")
        lines.append("직전 흐름:")
        for rec in history[-6:]:
            bar_len = int(abs(rec.score) / 10)
            if rec.score >= 0:
                bar = "\u2588" * bar_len
                lines.append(f"  {rec.time.strftime('%H:%M')} {bar} {rec.score:+.0f}")
            else:
                bar = "\u25bc" * bar_len
                lines.append(f"  {rec.time.strftime('%H:%M')} {bar} {rec.score:+.0f}")

    # 보유종목 영향
    if impacts:
        lines.append("")
        lines.append("[보유종목 영향 분석]")
        high_urgency = []
        for imp in impacts:
            emoji = "\U0001f534" if imp.urgency == "high" else "\U0001f7e1" if imp.urgency == "medium" else "\U0001f7e2"
            lines.append(f"  {emoji} {imp.name}: {imp.pnl_pct:+.1f}% \u2192 {imp.action}")
            if imp.urgency == "high":
                high_urgency.append(imp)

        if change.to_state == "REVERSAL_DOWN" and high_urgency:
            lines.append("")
            lines.append(f"{USER_NAME}, 시장이 하락 반전했습니다.")
            profit_names = [i.name for i in high_urgency if i.pnl_pct > 0]
            loss_names = [i.name for i in high_urgency if i.pnl_pct <= 0]
            if profit_names:
                lines.append(f"수익 종목 익절 검토: {', '.join(profit_names)}")
            if loss_names:
                lines.append(f"손실 종목 손절 검토: {', '.join(loss_names)}")

        elif change.to_state == "REVERSAL_UP":
            lines.append("")
            lines.append(f"{USER_NAME}, 시장이 상승 반전 중입니다.")
            lines.append("보유종목 반등을 기대해볼 수 있습니다.")

    return "\n".join(lines)
