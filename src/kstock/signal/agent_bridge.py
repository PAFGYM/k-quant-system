"""멀티에이전트 → 신호 엔진 연동 브릿지 (v6.2).

멀티에이전트 분석 결과를 신호 엔진의 composite score 보너스로 변환합니다.

기능:
1. multi_agent_results DB에서 최신 분석 결과 조회
2. 0-215 스케일 → 보너스 점수 (-10 ~ +15) 변환
3. signal_performance 기반 가중치 반영 (자가 학습)
4. 스캔 엔진에서 호출하여 composite_score에 반영

v6.2 by K-Quant
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def get_multi_agent_bonus(
    db: Any,
    ticker: str,
    max_age_hours: int = 48,
) -> int:
    """멀티에이전트 분석 결과를 신호 엔진 보너스로 변환.

    Args:
        db: SQLiteStore 인스턴스
        ticker: 종목 코드
        max_age_hours: 최대 데이터 유효 기간 (시간)

    Returns:
        보너스 점수 (-10 ~ +15)
    """
    try:
        results = db.get_multi_agent_results(ticker=ticker, limit=1)
        if not results:
            return 0

        latest = results[0]
        created_at = latest.get("created_at", "")

        # 유효 기간 확인
        try:
            created_dt = datetime.fromisoformat(created_at)
            age = datetime.utcnow() - created_dt
            if age > timedelta(hours=max_age_hours):
                return 0  # 오래된 데이터는 무시
        except (ValueError, TypeError):
            return 0

        combined_score = latest.get("combined_score", 0) or 0
        confidence = latest.get("confidence", "하")

        # 0-215 스케일 → 보너스 변환
        base_bonus = _score_to_bonus(combined_score)

        # 신뢰도 가중
        confidence_mult = {"상": 1.0, "중": 0.7, "하": 0.4}.get(confidence, 0.5)
        adjusted = int(round(base_bonus * confidence_mult))

        # 자가 학습 가중치 반영
        try:
            weight = _get_source_weight(db, "multi_agent")
            adjusted = int(round(adjusted * weight))
        except Exception:
            pass

        # 범위 제한
        adjusted = max(-10, min(15, adjusted))

        logger.debug(
            "Multi-agent bonus for %s: combined=%d, conf=%s, bonus=%d",
            ticker, combined_score, confidence, adjusted,
        )
        return adjusted

    except Exception as e:
        logger.debug("Multi-agent bonus lookup failed for %s: %s", ticker, e)
        return 0


def _score_to_bonus(combined_score: int) -> int:
    """0-215 스케일 → 보너스 점수 변환.

    160+ (매수) → +15
    140-159     → +10
    120-139     → +5
    80-119      → 0 (중립)
    60-79       → -5
    <60 (매도)  → -10
    """
    if combined_score >= 160:
        return 15
    elif combined_score >= 140:
        return 10
    elif combined_score >= 120:
        return 5
    elif combined_score >= 80:
        return 0
    elif combined_score >= 60:
        return -5
    else:
        return -10


def _get_source_weight(db: Any, source: str) -> float:
    """신호 소스 가중치 조회 (자가 학습)."""
    try:
        weights = db.get_signal_weight_adjustments()
        return weights.get(source, 1.0)
    except Exception:
        return 1.0


def get_all_agent_bonuses(
    db: Any,
    tickers: list[str],
    max_age_hours: int = 48,
) -> dict[str, int]:
    """여러 종목의 멀티에이전트 보너스를 일괄 조회.

    스캔 엔진에서 유니버스 전체에 대해 일괄 호출할 때 사용.

    Args:
        db: SQLiteStore 인스턴스
        tickers: 종목 코드 리스트
        max_age_hours: 최대 데이터 유효 기간

    Returns:
        {ticker: bonus_score} dict
    """
    bonuses: dict[str, int] = {}
    for ticker in tickers:
        bonus = get_multi_agent_bonus(db, ticker, max_age_hours)
        if bonus != 0:
            bonuses[ticker] = bonus
    return bonuses


async def run_and_record_multi_agent(
    db: Any,
    ticker: str,
    name: str,
    price: float,
    stock_data: dict,
) -> int:
    """멀티에이전트 분석 실행 + 결과를 DB에 저장 + 신호 성과 기록.

    스케줄러나 매수 추천 시 호출하여 분석 → 저장 → 추적을 원스톱으로 수행.

    Returns:
        combined_score (0-215)
    """
    try:
        from kstock.bot.multi_agent import run_multi_agent_analysis

        report = await run_multi_agent_analysis(
            ticker=ticker,
            name=name,
            price=price,
            stock_data=stock_data,
        )

        if not report:
            return 0

        # DB에 결과 저장
        tech_score = 0
        fund_score = 0
        sent_score = 0
        if report.results:
            tech_r = report.results.get("technical")
            fund_r = report.results.get("fundamental")
            sent_r = report.results.get("sentiment")
            if tech_r:
                tech_score = tech_r.score
            if fund_r:
                fund_score = fund_r.score
            if sent_r:
                sent_score = sent_r.score

        strategist_text = ""
        if report.strategist_result:
            strategist_text = report.strategist_result.summary

        db.add_multi_agent_result(
            ticker=ticker,
            name=name,
            technical_score=tech_score,
            fundamental_score=fund_score,
            sentiment_score=sent_score,
            combined_score=report.combined_score,
            verdict=report.verdict,
            confidence=report.confidence,
            strategist_summary=strategist_text,
        )

        # 신호 성과 추적 기록
        try:
            from kstock.core.tz import KST
            db.save_signal_performance(
                signal_source="multi_agent",
                signal_type="analysis",
                ticker=ticker,
                name=name,
                signal_date=datetime.now(KST).strftime("%Y-%m-%d"),
                signal_score=report.combined_score,
                signal_price=price,
            )
        except Exception:
            pass

        logger.info(
            "Multi-agent analysis for %s: score=%d, verdict=%s",
            ticker, report.combined_score, report.verdict,
        )
        return report.combined_score

    except Exception as e:
        logger.error("Multi-agent run failed for %s: %s", ticker, e)
        return 0


def format_agent_integration_status(db: Any) -> str:
    """멀티에이전트 ↔ 신호 엔진 연동 현황을 텔레그램 메시지로 포맷."""
    try:
        # 최근 멀티에이전트 결과 수
        results = db.get_multi_agent_results(limit=100)
        recent_24h = 0
        cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        for r in results:
            if r.get("created_at", "") > cutoff:
                recent_24h += 1

        # 신호 가중치
        weights = db.get_signal_weight_adjustments()
        ma_weight = weights.get("multi_agent", 1.0)

        # 신호 적중률
        stats = db.get_signal_source_stats(signal_source="multi_agent", days=30)
        hit_rate = 0
        evaluated = 0
        if stats:
            s = stats[0]
            evaluated = s.get("evaluated") or 0
            hits = s.get("hits") or 0
            hit_rate = round(hits / evaluated * 100, 1) if evaluated > 0 else 0

        lines = [
            "🔗 멀티에이전트 ↔ 신호 엔진 연동",
            f"{'━' * 22}",
            "",
            f"📊 최근 24시간 분석: {recent_24h}건",
            f"📈 전체 분석 이력: {len(results)}건",
            "",
            f"🎯 적중률: {hit_rate:.0f}% ({evaluated}건 평가)",
            f"⚖️ 가중치: {ma_weight:.1f}x",
            "",
        ]

        if ma_weight >= 1.5:
            lines.append("✅ 멀티에이전트 신뢰도 높음 → 가중치 상향됨")
        elif ma_weight <= 0.7:
            lines.append("⚠️ 멀티에이전트 적중률 부진 → 가중치 하향됨")
        else:
            lines.append("📋 멀티에이전트 가중치 정상 범위")

        return "\n".join(lines)

    except Exception as e:
        return f"연동 현황 조회 실패: {e}"
