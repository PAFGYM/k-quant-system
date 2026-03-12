"""v11.0: 일일 학습 예산 관리.

$1/일 학습 예산과 시스템 전체 상한을 함께 본다.
api_usage_log 테이블에서 오늘 사용량 조회 → 예산 초과 시 학습 스킵.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DAILY_BUDGET_USD = 1.00
GLOBAL_DAILY_HARD_BUDGET_USD = 2.00
GLOBAL_MONTHLY_HARD_BUDGET_USD = 45.00

# 카테고리별 예산 배분 (가이드라인, 하드 리밋 아님)
CATEGORY_BUDGETS = {
    "youtube_tier1": 0.034,    # Gemini Flash 90건
    "youtube_tier2": 0.108,    # Claude Haiku 15건
    "whisper": 0.240,          # 자막없는 영상 5건
    "column_report": 0.020,    # 칼럼/리포트 80건
    "synthesis": 0.017,        # 일일/주간 합성
    "system_ai": 0.100,        # 기존 시스템 AI
}


def _load_float_env(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
        return value if value > 0 else default
    except Exception:
        return default


def get_global_budget_limits() -> dict[str, float]:
    """시스템 전체 API 상한(학습/채팅/리포트 공통) 반환."""
    return {
        "daily_hard": _load_float_env(
            "KQ_GLOBAL_DAILY_HARD_BUDGET_USD",
            GLOBAL_DAILY_HARD_BUDGET_USD,
        ),
        "monthly_hard": _load_float_env(
            "KQ_GLOBAL_MONTHLY_HARD_BUDGET_USD",
            GLOBAL_MONTHLY_HARD_BUDGET_USD,
        ),
    }


def get_today_usage(db: Any) -> dict:
    """오늘 API 사용량 조회."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        return db.get_daily_api_usage(today)
    except Exception as e:
        logger.debug("Budget check failed: %s", e)
        return {"total_cost": 0, "total_calls": 0}


def get_monthly_usage(db: Any) -> dict:
    """이번 달 API 사용량 조회."""
    year_month = datetime.now().strftime("%Y-%m")
    try:
        return db.get_monthly_api_usage(year_month)
    except Exception as e:
        logger.debug("Monthly budget check failed: %s", e)
        return {"total_cost": 0, "total_calls": 0}


def get_remaining_budget(db: Any) -> float:
    """오늘 남은 예산(USD) 반환."""
    usage = get_today_usage(db)
    spent = usage.get("total_cost", 0) or 0
    return max(0, DAILY_BUDGET_USD - spent)


def is_over_global_budget(db: Any) -> bool:
    """시스템 전체 API 상한을 넘었는지 확인."""
    caps = get_global_budget_limits()
    daily = get_today_usage(db)
    monthly = get_monthly_usage(db)
    daily_spent = float(daily.get("total_cost", 0) or 0)
    monthly_spent = float(monthly.get("total_cost", 0) or 0)
    return (
        daily_spent >= caps["daily_hard"]
        or monthly_spent >= caps["monthly_hard"]
    )


def can_spend(db: Any, estimated_cost: float) -> bool:
    """예상 비용이 남은 예산 내인지 확인."""
    if is_over_global_budget(db):
        return False
    remaining = get_remaining_budget(db)
    return estimated_cost <= remaining


def get_budget_pct(db: Any) -> float:
    """오늘 예산 사용률(%) 반환."""
    usage = get_today_usage(db)
    spent = usage.get("total_cost", 0) or 0
    return min(100.0, spent / DAILY_BUDGET_USD * 100)


def should_alert(db: Any) -> str | None:
    """예산 경고 메시지 반환. 정상이면 None."""
    pct = get_budget_pct(db)
    if pct >= 100:
        return f"🚨 일일 학습 예산 초과! ({pct:.0f}%) — 학습 일시중단"
    if pct >= 80:
        return f"⚠️ 일일 학습 예산 80% 도달 ({pct:.0f}%)"
    return None


def get_daily_summary(db: Any) -> dict:
    """일일 예산 요약."""
    usage = get_today_usage(db)
    spent = usage.get("total_cost", 0) or 0
    calls = usage.get("total_calls", 0) or 0
    remaining = max(0, DAILY_BUDGET_USD - spent)
    pct = min(100.0, spent / DAILY_BUDGET_USD * 100)
    return {
        "budget_usd": DAILY_BUDGET_USD,
        "spent_usd": round(spent, 4),
        "remaining_usd": round(remaining, 4),
        "usage_pct": round(pct, 1),
        "total_calls": calls,
        "is_over_budget": pct >= 100,
        "global_daily_hard_usd": round(get_global_budget_limits()["daily_hard"], 2),
        "is_over_global_budget": is_over_global_budget(db),
    }


def format_budget_status(db: Any) -> str:
    """텔레그램 표시용 예산 상태."""
    s = get_daily_summary(db)
    bar_len = 10
    filled = int(s["usage_pct"] / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    emoji = "🟢" if s["usage_pct"] < 60 else "🟡" if s["usage_pct"] < 80 else "🔴"
    return (
        f"{emoji} 학습 예산: ${s['spent_usd']:.3f} / ${s['budget_usd']:.2f}\n"
        f"  [{bar}] {s['usage_pct']:.0f}% ({s['total_calls']}회 호출)\n"
        f"  통합 상한: ${s['global_daily_hard_usd']:.2f}"
    )
