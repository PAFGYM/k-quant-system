"""봇 자가진단 보고서 - 매일 21:00 KST.

매일 봇의 성과, 부족한 점, 개선 제안, 시스템 상태를 분석하여
텔레그램으로 자동 전송하는 보고서.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
USER_NAME = "주호님"


async def generate_daily_self_report(db, macro_client=None, ws=None) -> str:
    """봇 자가진단 + 개선 제안 보고서 생성.

    Args:
        db: SQLiteStore instance.
        macro_client: MacroClient instance (optional).

    Returns:
        Formatted self-report string for Telegram.
    """
    now = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")

    lines = [
        f"\U0001f4cb 봇 자가진단 보고서 ({today_str})",
        "\u2500" * 25,
        "",
    ]

    # 1. 오늘 봇 성과 요약
    lines.append("\U0001f4ca 1. 오늘의 성과")
    try:
        chat_usage = db.get_chat_usage_count(today_str)
        lines.append(f"  \u2192 AI 질문 응답: {chat_usage}회")
    except Exception:
        lines.append("  \u2192 AI 질문 응답: 조회 실패")

    try:
        recs = db.get_active_recommendations()
        rec_count = len(recs) if recs else 0
        lines.append(f"  \u2192 활성 추천 종목: {rec_count}개")

        if recs:
            wins = sum(1 for r in recs if r.get("pnl_pct", 0) > 0)
            total = len(recs)
            win_rate = (wins / total * 100) if total > 0 else 0
            lines.append(f"  \u2192 추천 적중률: {win_rate:.0f}% ({wins}/{total})")
    except Exception:
        lines.append("  \u2192 추천 데이터 조회 실패")

    try:
        from kstock.signal.market_pulse import MARKET_STATES
        job_runs = db.get_job_runs(today_str)
        pulse_runs = [j for j in job_runs if j.get("job_name") == "market_pulse"] if job_runs else []
        lines.append(f"  \u2192 시장 맥박 체크: {len(pulse_runs)}회")
    except Exception:
        lines.append("  \u2192 시장 맥박 체크: 조회 실패")

    lines.append("")

    # 2. 부족했던 점 분석
    lines.append("\u26a0\ufe0f 2. 부족한 점")
    issues = []

    holdings = []
    no_fin_count = 0
    try:
        holdings = db.get_active_holdings()
        no_fin_count = 0
        for h in holdings:
            fin = db.get_financials(h.get("ticker", ""))
            if not fin:
                no_fin_count += 1
        if no_fin_count > 0:
            issues.append(
                f"  \u2192 재무 데이터 없는 보유종목: {no_fin_count}개 "
                "\u2192 데이터 수집 필요"
            )
    except Exception:
        pass

    try:
        job_runs = db.get_job_runs(today_str)
        if job_runs:
            errors = [j for j in job_runs if j.get("status") == "error"]
            if errors:
                issues.append(f"  \u2192 잡 오류: {len(errors)}건")
                for e in errors[:3]:
                    issues.append(f"     {e.get('job_name', '')}: {e.get('message', '')[:50]}")
    except Exception:
        pass

    if not issues:
        issues.append("  \u2705 특이사항 없음")
    lines.extend(issues)
    lines.append("")

    # 3. 개선 제안
    lines.append("\U0001f4a1 3. 개선 제안")
    suggestions = []

    try:
        if no_fin_count > 0:
            names = [h.get("name", "") for h in holdings if not db.get_financials(h.get("ticker", ""))]
            suggestions.append(
                f"  \u2192 {', '.join(names[:3])} 재무 데이터 수집 필요"
            )
    except Exception:
        pass

    # 매크로 데이터 상태
    if macro_client:
        try:
            snap = await macro_client.get_snapshot()
            if snap and snap.is_cached:
                suggestions.append("  \u2192 매크로 데이터: 캐시 사용 중 (정상)")
            elif snap:
                suggestions.append("  \u2192 매크로 데이터: 실시간 갱신 중 (정상)")
        except Exception:
            suggestions.append("  \u2192 매크로 데이터 갱신 실패 \u2192 점검 필요")

    # 동적 추천 (상태 기반)
    if ws and not getattr(ws, 'is_connected', False):
        suggestions.append("  \u2192 WebSocket 미연결 → 재연결 필요")
    lstm_path = os.path.join(os.getcwd(), "models", "lstm_stock.pt")
    if not os.path.exists(lstm_path):
        suggestions.append("  \u2192 LSTM 모델 파일 없음 → 재학습 필요")

    lines.extend(suggestions)
    lines.append("")

    # 4. 시스템 상태
    lines.append("\u2699\ufe0f 4. 시스템 상태")
    try:
        db_path = db.db_path if hasattr(db, "db_path") else "N/A"
        if db_path != "N/A" and os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            lines.append(f"  \u2192 DB 크기: {size_mb:.1f}MB")
        else:
            lines.append(f"  \u2192 DB 경로: {db_path}")
    except Exception:
        lines.append("  \u2192 DB 상태: 확인 불가")

    try:
        job_runs = db.get_job_runs(today_str)
        if job_runs:
            success = sum(1 for j in job_runs if j.get("status") == "success")
            total = len(job_runs)
            lines.append(f"  \u2192 스케줄러: {success}/{total} 잡 성공")
        else:
            lines.append("  \u2192 스케줄러: 오늘 실행 기록 없음")
    except Exception:
        lines.append("  \u2192 스케줄러 상태: 확인 불가")

    kis_configured = bool(os.getenv("KIS_APP_KEY"))
    anthropic_configured = bool(os.getenv("ANTHROPIC_API_KEY"))
    lines.append(f"  \u2192 AI (Anthropic): {'정상' if anthropic_configured else '미설정'}")
    lines.append(f"  \u2192 KIS API: {'설정됨' if kis_configured else '미설정'}")

    lines.append("")

    # 5. 추천 사항 (동적)
    lines.append("\U0001f680 5. 추천 사항")
    openai_ok = bool(os.getenv("OPENAI_API_KEY"))
    gemini_ok = bool(os.getenv("GEMINI_API_KEY"))
    if not openai_ok:
        lines.append("  \u2192 OpenAI API 키 미설정 → Multi-AI 불완전")
    if not gemini_ok:
        lines.append("  \u2192 Gemini API 키 미설정 → Multi-AI 불완전")
    if openai_ok and gemini_ok and anthropic_configured:
        lines.append("  \u2192 3엔진 Multi-AI 정상 가동 중")

    lines.extend([
        "",
        "\u2500" * 25,
        f"\U0001f916 K-Quant v3.10 | {now.strftime('%H:%M')} 자가진단 완료",
    ])

    return "\n".join(lines)
