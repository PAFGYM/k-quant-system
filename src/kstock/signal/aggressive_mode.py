"""30억 목표 공격 모드 (Section 43 - Aggressive Mode).

Manages goal tracking, scoring overrides, and safety limits
for the concentrated aggressive strategy targeting 30억 from 1.75억.
Reads configuration from config/user_goal.yaml.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path("config/user_goal.yaml")


@dataclass
class AggressiveConfig:
    """Portfolio rules for aggressive mode."""

    max_positions: int = 5
    max_single_pct: float = 50
    max_leverage_pct: float = 30
    min_cash_pct: float = 5
    min_score_to_buy: int = 120
    stop_loss_single: float = -10
    stop_loss_portfolio: float = -15
    stop_loss_leverage: float = -7
    daily_loss_halt: float = -5


@dataclass
class GoalProgress:
    """Tracks progress toward the 30억 target."""

    start_asset: float
    current_asset: float
    target_asset: float
    progress_pct: float
    current_milestone: str
    milestone_progress_pct: float
    monthly_return_pct: float
    needed_monthly_pct: float


def load_goal_config(path: Path | None = None) -> dict:
    """Load user goal configuration from YAML.

    Args:
        path: Path to user_goal.yaml. Uses default if None.

    Returns:
        Configuration dict. Returns defaults if file not found.
    """
    config_path = path or _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        logger.warning("user_goal.yaml not found at %s, using defaults", config_path)
        return {
            "goal": {
                "current_asset": 175_000_000,
                "target_asset": 3_000_000_000,
                "target_years": 3,
                "risk_tolerance": "aggressive",
            },
            "milestones": {},
            "portfolio_rules": {
                "max_positions": 5,
                "max_single_pct": 50,
                "max_leverage_pct": 30,
                "min_cash_pct": 5,
                "min_score_to_buy": 120,
                "stop_loss_single": -10,
                "stop_loss_portfolio": -15,
                "stop_loss_leverage": -7,
                "daily_loss_halt": -5,
            },
            "scoring_override": {
                "tenbagger_bonus": 20,
                "profit_target_pct": 15,
                "trailing_stop_pct": 10,
                "momentum_weight": 2.0,
                "breakout_weight": 1.5,
                "bounce_min_drop": -15,
                "ml_min_probability": 70,
            },
        }
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    logger.debug("Loaded user_goal config from %s", config_path)
    return config


def _build_aggressive_config(config: dict) -> AggressiveConfig:
    """Build AggressiveConfig from the portfolio_rules section."""
    rules = config.get("portfolio_rules", {})
    return AggressiveConfig(
        max_positions=rules.get("max_positions", 5),
        max_single_pct=rules.get("max_single_pct", 50),
        max_leverage_pct=rules.get("max_leverage_pct", 30),
        min_cash_pct=rules.get("min_cash_pct", 5),
        min_score_to_buy=rules.get("min_score_to_buy", 120),
        stop_loss_single=rules.get("stop_loss_single", -10),
        stop_loss_portfolio=rules.get("stop_loss_portfolio", -15),
        stop_loss_leverage=rules.get("stop_loss_leverage", -7),
        daily_loss_halt=rules.get("daily_loss_halt", -5),
    )


def _get_current_milestone(config: dict, today: date) -> tuple[str, float, float]:
    """Determine current milestone and its start/target values.

    Returns:
        (milestone_name, milestone_start, milestone_target)
    """
    milestones = config.get("milestones", {})
    year = today.year

    # Check yearly milestones
    for key in ("year_1", "year_2", "year_3"):
        ms = milestones.get(key, {})
        if ms.get("year") == year:
            # Check quarterly milestones
            quarterly = ms.get("quarterly", {})
            month = today.month
            if month <= 3:
                q_key = "Q1"
            elif month <= 6:
                q_key = "Q2"
            elif month <= 9:
                q_key = "Q3"
            else:
                q_key = "Q4"

            q_data = quarterly.get(q_key)
            if q_data:
                q_start = q_data.get("start", ms.get("start", 175_000_000))
                q_target = q_data.get("target", ms.get("target", 525_000_000))
                label = f"{year}년 {q_key} ({key})"
                return label, q_start, q_target

            # No quarterly data, use yearly
            label = f"{year}년 ({key})"
            return label, ms.get("start", 175_000_000), ms.get("target", 525_000_000)

    # Default fallback
    return "1차 목표", 175_000_000, 525_000_000


def compute_goal_progress(
    current_asset: float,
    start_date: str = "2026-02-22",
    config: dict | None = None,
) -> GoalProgress:
    """Compute progress toward the 30억 goal.

    Args:
        current_asset: Current total asset value (KRW).
        start_date: Investment start date in YYYY-MM-DD format.
        config: Goal config dict. Loaded from YAML if None.

    Returns:
        GoalProgress with computed metrics.
    """
    if config is None:
        config = load_goal_config()

    goal = config.get("goal", {})
    start_asset = goal.get("current_asset", 175_000_000)
    target_asset = goal.get("target_asset", 3_000_000_000)
    target_years = goal.get("target_years", 3)

    # Overall progress
    total_range = target_asset - start_asset
    if total_range > 0:
        progress_pct = ((current_asset - start_asset) / total_range) * 100
    else:
        progress_pct = 100.0
    progress_pct = round(max(0.0, min(100.0, progress_pct)), 1)

    # Current milestone
    today = date.today()
    milestone_name, ms_start, ms_target = _get_current_milestone(config, today)
    ms_range = ms_target - ms_start
    if ms_range > 0:
        milestone_progress_pct = ((current_asset - ms_start) / ms_range) * 100
    else:
        milestone_progress_pct = 100.0
    milestone_progress_pct = round(max(0.0, min(100.0, milestone_progress_pct)), 1)

    # Monthly return calculation
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        start_dt = today

    days_elapsed = max(1, (today - start_dt).days)
    months_elapsed = max(1.0, days_elapsed / 30.44)

    if start_asset > 0 and current_asset > 0:
        total_return = (current_asset / start_asset) - 1
        monthly_return_pct = (total_return / months_elapsed) * 100
    else:
        monthly_return_pct = 0.0
    monthly_return_pct = round(monthly_return_pct, 1)

    # Needed monthly return to reach target
    months_remaining = max(1.0, (target_years * 12) - months_elapsed)
    if current_asset > 0 and target_asset > current_asset:
        remaining_multiplier = target_asset / current_asset
        needed_monthly_pct = (remaining_multiplier ** (1 / months_remaining) - 1) * 100
    else:
        needed_monthly_pct = 0.0
    needed_monthly_pct = round(needed_monthly_pct, 1)

    return GoalProgress(
        start_asset=start_asset,
        current_asset=current_asset,
        target_asset=target_asset,
        progress_pct=progress_pct,
        current_milestone=milestone_name,
        milestone_progress_pct=milestone_progress_pct,
        monthly_return_pct=monthly_return_pct,
        needed_monthly_pct=needed_monthly_pct,
    )


def get_scoring_override(config: dict | None = None) -> dict:
    """Return scoring overrides from user_goal.yaml.

    Provides tenbagger_bonus, momentum_weight, breakout_weight,
    and other scoring adjustments for aggressive mode.

    Args:
        config: Goal config dict. Loaded from YAML if None.

    Returns:
        Scoring override dict with keys like tenbagger_bonus,
        momentum_weight, etc.
    """
    if config is None:
        config = load_goal_config()
    overrides = config.get("scoring_override", {})

    # Ensure expected keys have defaults
    defaults = {
        "tenbagger_bonus": 20,
        "profit_target_pct": 15,
        "trailing_stop_pct": 10,
        "momentum_weight": 2.0,
        "breakout_weight": 1.5,
        "bounce_min_drop": -15,
        "ml_min_probability": 70,
    }
    for key, default_val in defaults.items():
        overrides.setdefault(key, default_val)

    return overrides


def check_safety_limits(
    holdings: list[dict],
    total_eval: float,
    daily_pnl_pct: float,
) -> list[str]:
    """Check aggressive mode safety limits and return triggered warnings.

    Safety rules:
        - Single stock -10% -> "절반 손절"
        - Portfolio -15% -> "전종목 50% 현금화"
        - Leverage ETF -7% -> "전량 손절"
        - Daily -5% -> "당일 추가 매매 중단"

    Args:
        holdings: List of holding dicts, each with keys:
            name (str), ticker (str), pnl_pct (float),
            is_leverage (bool, optional).
        total_eval: Total portfolio evaluation value (KRW).
        daily_pnl_pct: Today's portfolio P&L in percent.

    Returns:
        List of warning strings. Empty list if all within limits.
    """
    config = load_goal_config()
    ac = _build_aggressive_config(config)
    warnings: list[str] = []

    # Check daily loss halt first (most urgent)
    if daily_pnl_pct <= ac.daily_loss_halt:
        warnings.append(
            f"일일 손실 {daily_pnl_pct:+.1f}% (한도 {ac.daily_loss_halt}%) "
            f"-> 당일 추가 매매 중단"
        )

    # Check portfolio-level stop loss
    portfolio_pnl = 0.0
    total_cost = 0.0
    for h in holdings:
        pnl_pct = h.get("pnl_pct", 0.0)
        eval_val = h.get("eval", 0.0)
        if eval_val > 0:
            cost = eval_val / (1 + pnl_pct / 100) if pnl_pct != -100 else eval_val
            total_cost += cost

    if total_cost > 0:
        portfolio_pnl = ((total_eval - total_cost) / total_cost) * 100

    if portfolio_pnl <= ac.stop_loss_portfolio:
        warnings.append(
            f"포트폴리오 손실 {portfolio_pnl:+.1f}% (한도 {ac.stop_loss_portfolio}%) "
            f"-> 전종목 50% 현금화"
        )

    # Check individual holdings
    for h in holdings:
        name = h.get("name", "")
        ticker = h.get("ticker", "")
        pnl_pct = h.get("pnl_pct", 0.0)
        is_leverage = h.get("is_leverage", False)

        # Leverage ETF stop loss
        if is_leverage and pnl_pct <= ac.stop_loss_leverage:
            warnings.append(
                f"{name}({ticker}) 레버리지 손실 {pnl_pct:+.1f}% "
                f"(한도 {ac.stop_loss_leverage}%) -> 전량 손절"
            )

        # Single stock stop loss
        if pnl_pct <= ac.stop_loss_single:
            warnings.append(
                f"{name}({ticker}) 손실 {pnl_pct:+.1f}% "
                f"(한도 {ac.stop_loss_single}%) -> 절반 손절"
            )

    if warnings:
        logger.warning("Safety limits triggered: %d warning(s)", len(warnings))
        for w in warnings:
            logger.warning("  %s", w)

    return warnings


def _make_progress_bar(pct: float, width: int = 10) -> str:
    """Build a text progress bar using block characters.

    Args:
        pct: Progress percentage (0~100).
        width: Total bar width in characters.

    Returns:
        String like "███░░░░░░░"
    """
    filled = max(0, min(width, round(pct / 100 * width)))
    empty = width - filled
    return "\u2588" * filled + "\u2591" * empty


def _format_krw(amount: float) -> str:
    """Format KRW amount in 억 units.

    Args:
        amount: Amount in KRW.

    Returns:
        Formatted string like "1.75억" or "30억".
    """
    eok = amount / 1e8
    if eok >= 100:
        return f"{eok:.0f}억"
    elif eok >= 10:
        return f"{eok:.1f}억"
    else:
        return f"{eok:.2f}억"


def format_goal_dashboard(
    progress: GoalProgress,
    holdings: list[dict] | None = None,
    tenbagger_count: int = 0,
) -> str:
    """Format the 30억 goal dashboard for Telegram /goal command.

    Args:
        progress: GoalProgress with computed metrics.
        holdings: Optional list of current holding dicts.
        tenbagger_count: Number of active tenbagger candidates.

    Returns:
        Formatted multi-line string without ** bold markers.
    """
    bar = _make_progress_bar(progress.progress_pct)

    lines = [
        "30억 목표 대시보드",
        "",
        f"시작: {_format_krw(progress.start_asset)}",
        f"현재: {_format_krw(progress.current_asset)}",
        f"목표: {_format_krw(progress.target_asset)}",
        f"진행률: {bar} {progress.progress_pct:.1f}%",
        "",
        f"현재 마일스톤: {progress.current_milestone}",
        f"마일스톤 진행: {_make_progress_bar(progress.milestone_progress_pct)} "
        f"{progress.milestone_progress_pct:.1f}%",
        "",
        f"월간 수익률: {progress.monthly_return_pct:+.1f}%",
        f"필요 월간 수익률: {progress.needed_monthly_pct:+.1f}%",
    ]

    if holdings:
        lines.append("")
        lines.append(f"보유 종목: {len(holdings)}개")
        for h in holdings:
            name = h.get("name", "")
            pnl_pct = h.get("pnl_pct", 0.0)
            lines.append(f"  {name} {pnl_pct:+.1f}%")

    if tenbagger_count > 0:
        lines.append("")
        lines.append(f"텐배거 후보 추적 중: {tenbagger_count}개")

    lines.append("")
    lines.append("주호님, 목표를 향해 꾸준히 갑시다!")

    return "\n".join(lines)
