"""Token usage tracker for all AI API calls.

v6.2.1: 모든 API 호출의 토큰 사용량/비용을 DB에 기록.
각 호출 사이트에서 track_usage()를 호출하면 자동으로 비용 계산 + DB 저장.

가격표 (2026-03 기준, USD per 1M tokens):
- Claude Sonnet 4.5: input $3, output $15, cache_read $0.30, cache_write $3.75
- Claude Haiku 4.5:  input $0.80, output $4, cache_read $0.08, cache_write $1.00
- Claude Haiku 3.5:  input $0.80, output $4, cache_read $0.08, cache_write $1.00
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# ── 글로벌 DB 참조 (봇 초기화 시 set_db()로 설정) ──
_global_db: Any = None


def set_db(db: Any) -> None:
    """글로벌 DB 참조 설정 (봇 초기화 시 호출)."""
    global _global_db
    _global_db = db


def get_db() -> Any:
    """글로벌 DB 참조 반환."""
    return _global_db

# ── 모델별 가격표 (USD per 1M tokens) ──
_PRICING: dict[str, dict[str, float]] = {
    # Sonnet 4.5
    "claude-sonnet-4-5-20250929": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.30, "cache_write": 3.75,
    },
    # Haiku 4.5
    "claude-haiku-4-5-20251001": {
        "input": 0.80, "output": 4.0,
        "cache_read": 0.08, "cache_write": 1.0,
    },
    # Haiku 3.5
    "claude-3-5-haiku-20241022": {
        "input": 0.80, "output": 4.0,
        "cache_read": 0.08, "cache_write": 1.0,
    },
    # Gemini 2.0 Flash (v11.0 벌크 학습용)
    "gemini-2.0-flash": {
        "input": 0.10, "output": 0.40,
        "cache_read": 0.0, "cache_write": 0.0,
    },
    # Gemini 2.0 Pro
    "gemini-2.0-pro": {
        "input": 1.25, "output": 10.0,
        "cache_read": 0.0, "cache_write": 0.0,
    },
    # GPT-4o / GPT-4o-mini
    "gpt-4o": {
        "input": 2.50, "output": 10.0,
        "cache_read": 0.0, "cache_write": 0.0,
    },
    "gpt-4o-mini": {
        "input": 0.15, "output": 0.60,
        "cache_read": 0.0, "cache_write": 0.0,
    },
    # Whisper (OpenAI, $0.006/minute → 분당 비용, 토큰 기반 아님)
    "whisper-1": {
        "input": 0.0, "output": 0.0,
        "cache_read": 0.0, "cache_write": 0.0,
    },
}

# 기본 가격 (알 수 없는 모델용)
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}


def calculate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """모델 + 토큰 수로 비용(USD) 계산."""
    pricing = _PRICING.get(model, _DEFAULT_PRICING)
    cost = (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
        + cache_read_tokens * pricing["cache_read"]
        + cache_write_tokens * pricing["cache_write"]
    ) / 1_000_000
    return round(cost, 6)


def track_usage(
    db: Any,
    provider: str,
    model: str,
    function_name: str,
    response: Any = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    latency_ms: float = 0,
    status: str = "success",
    error_message: str = "",
) -> None:
    """API 호출 후 토큰 사용량을 DB에 기록.

    response가 주어지면 자동으로 토큰 수를 추출합니다.
    """
    try:
        # Anthropic response 객체에서 자동 추출
        if response is not None and hasattr(response, "usage"):
            usage = response.usage
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
            cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0

        total_cost = calculate_cost(
            model, input_tokens, output_tokens,
            cache_read_tokens, cache_write_tokens,
        )

        # usage 메타데이터가 비어 있는 성공 호출은 기록하지 않는다.
        if (
            status == "success"
            and total_cost <= 0
            and input_tokens <= 0
            and output_tokens <= 0
            and cache_read_tokens <= 0
            and cache_write_tokens <= 0
            and not error_message
        ):
            logger.debug(
                "Skip zero-usage api log: provider=%s model=%s function=%s",
                provider, model, function_name,
            )
            return

        db.log_api_usage(
            provider=provider,
            model=model,
            function_name=function_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            total_cost_usd=total_cost,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
        )
    except Exception as e:
        logger.debug("토큰 사용량 기록 실패: %s", e)


def track_usage_global(
    provider: str,
    model: str,
    function_name: str,
    response: Any = None,
    **kwargs: Any,
) -> None:
    """글로벌 DB를 사용한 토큰 추적 (DB 접근 없는 모듈용)."""
    db = _global_db
    if db is None:
        return
    track_usage(db=db, provider=provider, model=model,
                function_name=function_name, response=response, **kwargs)


@contextmanager
def track_api_call(db: Any, provider: str, model: str, function_name: str):
    """API 호출 타이밍 자동 측정 컨텍스트 매니저.

    Usage::

        with track_api_call(db, "anthropic", model, "chat") as tracker:
            response = await client.messages.create(...)
            tracker["response"] = response
    """
    tracker: dict[str, Any] = {
        "response": None,
        "status": "success",
        "error": "",
    }
    start = time.time()
    try:
        yield tracker
    except Exception as e:
        tracker["status"] = "error"
        tracker["error"] = str(e)[:200]
        raise
    finally:
        elapsed_ms = (time.time() - start) * 1000
        track_usage(
            db=db,
            provider=provider,
            model=model,
            function_name=function_name,
            response=tracker.get("response"),
            latency_ms=elapsed_ms,
            status=tracker["status"],
            error_message=tracker["error"],
        )


def format_monthly_cost_report(db: Any, year_month: str = "") -> str:
    """월간 API 비용 리포트 포맷."""
    monthly = db.get_monthly_api_usage(year_month)
    by_model = db.get_api_usage_by_model(year_month)
    by_func = db.get_api_usage_by_function(year_month)

    if not year_month:
        from datetime import datetime
        year_month = datetime.utcnow().strftime("%Y-%m")

    total_cost = monthly.get("total_cost", 0)
    total_calls = monthly.get("total_calls", 0)
    total_input = monthly.get("total_input", 0)
    total_output = monthly.get("total_output", 0)
    total_cache_read = monthly.get("total_cache_read", 0)
    avg_latency = monthly.get("avg_latency", 0)
    error_count = monthly.get("error_count", 0)

    # 원화 환산 (1 USD ≈ 1,400 KRW)
    krw = total_cost * 1400

    lines = [
        f"💰 API 비용 리포트 ({year_month})",
        f"{'━' * 24}",
        "",
        f"📊 총 호출: {total_calls:,}회",
        f"💵 총 비용: ${total_cost:.4f} (≈{krw:,.0f}원)",
        f"⚡ 평균 응답: {avg_latency:.0f}ms",
        f"❌ 에러: {error_count}건",
        "",
        f"📝 토큰 사용량:",
        f"  입력: {total_input:,} tok",
        f"  출력: {total_output:,} tok",
        f"  캐시 절약: {total_cache_read:,} tok",
    ]

    # 캐시 절약률
    total_all = total_input + total_cache_read
    if total_all > 0:
        save_pct = total_cache_read / total_all * 100
        lines.append(f"  절약률: {save_pct:.1f}%")

    # 모델별 비용
    if by_model:
        lines.append("")
        lines.append("🤖 모델별 비용:")
        for m in by_model:
            model_name = m["model"]
            # 모델명 축약
            if "sonnet" in model_name:
                label = "Sonnet"
            elif "haiku" in model_name:
                label = "Haiku"
            else:
                label = model_name[:20]
            lines.append(
                f"  {label}: ${m['cost']:.4f} ({m['calls']}회)"
            )

    # 기능별 비용 (상위 5개)
    if by_func:
        lines.append("")
        lines.append("🔧 기능별 비용:")
        func_labels = {
            "chat": "💬 AI 채팅",
            "multi_agent": "🤖 멀티분석",
            "trade_debrief": "📋 매매복기",
            "investment_manager": "👨‍💼 매니저분석",
            "sentiment": "📰 감성분석",
            "pdf_report": "📄 PDF리포트",
            "live_market": "📡 실시간분석",
            "strategist": "🎯 전략분석",
        }
        for f in by_func[:8]:
            fname = f["function_name"]
            label = func_labels.get(fname, fname[:12])
            lines.append(f"  {label}: ${f['cost']:.4f} ({f['calls']}회)")

    # 일간 평균
    if total_calls > 0:
        import calendar
        from datetime import datetime
        try:
            y, m = int(year_month[:4]), int(year_month[5:7])
            now = datetime.utcnow()
            if y == now.year and m == now.month:
                days_elapsed = now.day
            else:
                days_elapsed = calendar.monthrange(y, m)[1]
            if days_elapsed > 0:
                daily_avg = total_cost / days_elapsed
                monthly_est = daily_avg * calendar.monthrange(y, m)[1]
                lines.append("")
                lines.append(f"📅 일평균: ${daily_avg:.4f} (≈{daily_avg * 1400:.0f}원)")
                lines.append(f"📈 월 예상: ${monthly_est:.4f} (≈{monthly_est * 1400:,.0f}원)")
        except Exception:
            pass

    return "\n".join(lines)
