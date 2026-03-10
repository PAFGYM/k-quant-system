"""v11.0: 일일 학습 예산 관리.

$1/일 예산 내에서 학습 비용을 추적·제한.
api_usage_log 테이블에서 오늘 사용량 조회 → 예산 초과 시 학습 스킵.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DAILY_BUDGET_USD = 1.00

# 카테고리별 예산 배분 (가이드라인, 하드 리밋 아님)
CATEGORY_BUDGETS = {
    "youtube_tier1": 0.034,    # Gemini Flash 90건
    "youtube_tier2": 0.108,    # Claude Haiku 15건
    "whisper": 0.240,          # 자막없는 영상 5건
    "column_report": 0.020,    # 칼럼/리포트 80건
    "synthesis": 0.017,        # 일일/주간 합성
    "system_ai": 0.100,        # 기존 시스템 AI
}


def get_today_usage(db: Any) -> dict:
    """오늘 API 사용량 조회."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        return db.get_daily_api_usage(today)
    except Exception as e:
        logger.debug("Budget check failed: %s", e)
        return {"total_cost": 0, "total_calls": 0}


def get_remaining_budget(db: Any) -> float:
    """오늘 남은 예산(USD) 반환."""
    usage = get_today_usage(db)
    spent = usage.get("total_cost", 0) or 0
    return max(0, DAILY_BUDGET_USD - spent)


def can_spend(db: Any, estimated_cost: float) -> bool:
    """예상 비용이 남은 예산 내인지 확인."""
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
        f"  [{bar}] {s['usage_pct']:.0f}% ({s['total_calls']}회 호출)"
    )
