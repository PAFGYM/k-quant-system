"""Auto rebalancing engine (자동 리밸런싱 엔진).

Evaluates 6 trigger conditions for portfolio rebalancing and generates
actionable alerts. Integrates with 30억 goal milestones.

All functions are pure computation with no external API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RebalanceAction:
    """A single rebalancing action recommendation."""

    trigger_type: str       # Trigger code
    trigger_name: str       # Korean name
    description: str        # What was detected
    action: str             # Recommended action
    tickers: list[str] = field(default_factory=list)
    urgency: str = "normal"  # "low", "normal", "high", "critical"
    score_impact: int = 0


@dataclass
class RebalanceResult:
    """Result of rebalance evaluation."""

    actions: list[RebalanceAction] = field(default_factory=list)
    needs_rebalance: bool = False
    message: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONCENTRATION_THRESHOLD_PCT = 40.0   # Single position > 40% → rebalance
_PORTFOLIO_STOP_LOSS_PCT = -15.0     # Portfolio-level stop loss
_LEVERAGE_SAFE_RATIO = 30.0           # Max leverage ratio
_CORRELATION_SPIKE_THRESHOLD = 0.85  # Correlation > 0.85 → warning
_MILESTONE_TAKE_PROFIT_PCT = 10.0     # Take 10% profit at milestone


# ---------------------------------------------------------------------------
# Trigger evaluation functions
# ---------------------------------------------------------------------------

def _check_concentration(holdings: list[dict], total_eval: float) -> RebalanceAction | None:
    """Trigger 1: Single position concentration exceeds threshold.

    Args:
        holdings: List of holdings with 'eval_amount' or 'current_price' * 'qty'.
        total_eval: Total portfolio evaluation amount.
    """
    if total_eval <= 0 or not holdings:
        return None

    for h in holdings:
        eval_amount = h.get("eval_amount", 0)
        if eval_amount <= 0:
            price = h.get("current_price", 0)
            qty = h.get("quantity", 0)
            eval_amount = price * qty

        pct = eval_amount / total_eval * 100 if total_eval > 0 else 0

        if pct >= _CONCENTRATION_THRESHOLD_PCT:
            return RebalanceAction(
                trigger_type="concentration",
                trigger_name="집중도 초과",
                description=f"{h.get('name', '?')} 비중 {pct:.1f}% (기준 {_CONCENTRATION_THRESHOLD_PCT}%)",
                action=f"{h.get('name', '?')} 일부 매도하여 비중 축소",
                tickers=[h.get("ticker", "")],
                urgency="high",
                score_impact=-5,
            )

    return None


def _check_portfolio_stop_loss(
    total_eval: float,
    total_invested: float,
) -> RebalanceAction | None:
    """Trigger 2: Portfolio-level stop loss triggered."""
    if total_invested <= 0:
        return None

    pnl_pct = (total_eval - total_invested) / total_invested * 100

    if pnl_pct <= _PORTFOLIO_STOP_LOSS_PCT:
        return RebalanceAction(
            trigger_type="portfolio_stop",
            trigger_name="포트폴리오 손절",
            description=f"전체 수익률 {pnl_pct:+.1f}% (기준 {_PORTFOLIO_STOP_LOSS_PCT}%)",
            action="전체 포지션 50% 축소 + 현금 확보",
            urgency="critical",
            score_impact=-15,
        )

    return None


def _check_milestone_reached(
    current_asset: float,
    milestones: list[dict],
) -> RebalanceAction | None:
    """Trigger 3: Goal milestone reached → take partial profits.

    Args:
        current_asset: Current total asset value.
        milestones: List of milestone dicts with 'target', 'name', 'reached'.
    """
    for ms in milestones:
        target = ms.get("target", 0)
        reached = ms.get("reached", False)
        name = ms.get("name", "")

        if not reached and current_asset >= target:
            return RebalanceAction(
                trigger_type="milestone",
                trigger_name="마일스톤 달성",
                description=f"{name}: 목표 {target / 100_000_000:.1f}억 달성!",
                action=f"수익금 {_MILESTONE_TAKE_PROFIT_PCT:.0f}% 실현 + 안전자산 분산",
                urgency="normal",
                score_impact=0,
            )

    return None


def _check_correlation_spike(holdings: list[dict]) -> RebalanceAction | None:
    """Trigger 4: High correlation between positions.

    Uses a simplified sector-based correlation check.
    Stocks in the same sector with similar profit patterns are flagged.
    """
    if len(holdings) < 2:
        return None

    # Group by sector
    sectors: dict[str, list[dict]] = {}
    for h in holdings:
        sector = h.get("sector", "기타")
        sectors.setdefault(sector, []).append(h)

    for sector, group in sectors.items():
        if len(group) >= 3:
            tickers = [h.get("ticker", "") for h in group]
            names = [h.get("name", "?") for h in group]
            return RebalanceAction(
                trigger_type="correlation",
                trigger_name="섹터 집중 위험",
                description=f"{sector} 섹터에 {len(group)}종목 집중: {', '.join(names[:3])}",
                action=f"{sector} 외 섹터로 분산 투자 권장",
                tickers=tickers,
                urgency="normal",
                score_impact=-3,
            )

    return None


def _check_leverage_excess(
    credit_ratio: float = 0.0,
    margin_ratio: float = 0.0,
) -> RebalanceAction | None:
    """Trigger 5: Margin/leverage exceeds safe level."""
    total_leverage = credit_ratio + margin_ratio

    if total_leverage >= _LEVERAGE_SAFE_RATIO:
        return RebalanceAction(
            trigger_type="leverage_excess",
            trigger_name="레버리지 초과",
            description=f"총 레버리지 비율 {total_leverage:.1f}% (기준 {_LEVERAGE_SAFE_RATIO}%)",
            action="신용 매수 종목 우선 정리, 현금 비중 확대",
            urgency="high",
            score_impact=-10,
        )

    return None


def _check_short_squeeze_opportunity(
    short_signals: list[dict],
) -> RebalanceAction | None:
    """Trigger 6: Short squeeze opportunity detected.

    Args:
        short_signals: List of short pattern results with 'code' and 'detected'.
    """
    squeeze_tickers = []
    squeeze_names = []

    for sig in short_signals:
        patterns = sig.get("patterns", [])
        for p in patterns:
            code = p.get("code", "") if isinstance(p, dict) else getattr(p, "code", "")
            detected = p.get("detected", False) if isinstance(p, dict) else getattr(p, "detected", False)
            if code == "short_squeeze" and detected:
                squeeze_tickers.append(sig.get("ticker", ""))
                squeeze_names.append(sig.get("name", "?"))

    if squeeze_tickers:
        return RebalanceAction(
            trigger_type="short_squeeze",
            trigger_name="숏스퀴즈 기회",
            description=f"숏스퀴즈 감지: {', '.join(squeeze_names)}",
            action="해당 종목 추가 매수 검토 (단기 트레이딩)",
            tickers=squeeze_tickers,
            urgency="normal",
            score_impact=5,
        )

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_rebalance_triggers(
    holdings: list[dict] | None = None,
    total_eval: float = 0,
    total_invested: float = 0,
    current_asset: float = 0,
    milestones: list[dict] | None = None,
    credit_ratio: float = 0.0,
    margin_ratio: float = 0.0,
    short_signals: list[dict] | None = None,
) -> RebalanceResult:
    """Evaluate all 6 rebalancing triggers.

    Args:
        holdings: Current holdings list.
        total_eval: Total portfolio evaluation.
        total_invested: Total invested amount.
        current_asset: Current total asset for milestone check.
        milestones: Goal milestones.
        credit_ratio: Current credit ratio.
        margin_ratio: Current margin ratio.
        short_signals: Short selling pattern results.

    Returns:
        RebalanceResult with triggered actions.
    """
    holdings = holdings or []
    milestones = milestones or []
    short_signals = short_signals or []

    actions: list[RebalanceAction] = []

    # Check all 6 triggers
    checks = [
        _check_concentration(holdings, total_eval),
        _check_portfolio_stop_loss(total_eval, total_invested),
        _check_milestone_reached(current_asset, milestones),
        _check_correlation_spike(holdings),
        _check_leverage_excess(credit_ratio, margin_ratio),
        _check_short_squeeze_opportunity(short_signals),
    ]

    for action in checks:
        if action is not None:
            actions.append(action)

    needs_rebalance = len(actions) > 0

    if actions:
        summaries = [a.trigger_name for a in actions]
        message = f"리밸런싱 트리거 {len(actions)}개 감지: " + ", ".join(summaries)
    else:
        message = "리밸런싱 필요 없음"

    result = RebalanceResult(
        actions=actions,
        needs_rebalance=needs_rebalance,
        message=message,
    )

    logger.info("Rebalance evaluation: %d triggers", len(actions))

    return result


# ---------------------------------------------------------------------------
# 30억 목표 마일스톤 정의
# ---------------------------------------------------------------------------

DEFAULT_MILESTONES = [
    {"name": "1단계: 5억 돌파", "target": 500_000_000, "reached": False},
    {"name": "2단계: 10억 돌파", "target": 1_000_000_000, "reached": False},
    {"name": "3단계: 15억 돌파", "target": 1_500_000_000, "reached": False},
    {"name": "4단계: 20억 돌파", "target": 2_000_000_000, "reached": False},
    {"name": "5단계: 25억 돌파", "target": 2_500_000_000, "reached": False},
    {"name": "최종: 30억 달성", "target": 3_000_000_000, "reached": False},
]


def get_milestones_with_status(current_asset: float) -> list[dict]:
    """Return milestones with 'reached' status updated based on current asset."""
    result = []
    for ms in DEFAULT_MILESTONES:
        entry = {**ms}
        if current_asset >= ms["target"]:
            entry["reached"] = True
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def format_rebalance_alert(result: RebalanceResult) -> str:
    """Format rebalance alert for Telegram.

    No ** bold markers. Korean text throughout.
    """
    lines: list[str] = []

    if not result.needs_rebalance:
        lines.append("\U0001f7e2 포트폴리오 리밸런싱 불필요")
        lines.append("현재 포트폴리오 구성이 적절합니다.")
        return "\n".join(lines)

    # Count urgency levels
    critical = sum(1 for a in result.actions if a.urgency == "critical")
    high = sum(1 for a in result.actions if a.urgency == "high")

    if critical > 0:
        header_emoji = "\U0001f6a8"
        header = "긴급 리밸런싱 필요"
    elif high > 0:
        header_emoji = "\U0001f534"
        header = "리밸런싱 권고"
    else:
        header_emoji = "\U0001f7e1"
        header = "리밸런싱 검토"

    lines.append(f"{header_emoji} {header}")
    lines.append(f"감지된 트리거: {len(result.actions)}개")
    lines.append("")

    urgency_emoji = {
        "critical": "\U0001f6a8",
        "high": "\U0001f534",
        "normal": "\U0001f7e1",
        "low": "\u26aa",
    }

    for i, action in enumerate(result.actions, 1):
        emoji = urgency_emoji.get(action.urgency, "\u26aa")
        lines.append(f"{i}. {emoji} {action.trigger_name}")
        lines.append(f"   {action.description}")
        lines.append(f"   \u27a1\ufe0f {action.action}")
        if action.tickers:
            lines.append(f"   관련 종목: {', '.join(action.tickers)}")
        lines.append("")

    lines.append("주호님, 위 트리거를 검토하시고 조치해주세요.")

    return "\n".join(lines)
