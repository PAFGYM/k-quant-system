"""자동 매매 복기 + 신호 적중률 추적 (Self-Learning Loop).

매매 완료 시 자동으로:
1. 진입/퇴장 데이터 분석
2. AI로 복기 리뷰 생성 (Haiku — 저비용)
3. 등급(A~F) 부여
4. 교훈 + 개선점 추출
5. investor_profile 자동 업데이트

신호 적중률 추적:
1. 매수 추천 발생 시 signal_performance에 기록
2. D+1/3/5/10/20 가격을 추적하여 적중률 계산
3. 신호 소스별 가중치 자동 조정

v6.2 by K-Quant
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

USER_NAME = "주호님"

# 매매 등급 기준
GRADE_CRITERIA = {
    "A": {"min_pnl": 5.0, "desc": "우수 (수익 5%+, 계획대로 실행)"},
    "B": {"min_pnl": 1.0, "desc": "양호 (소폭 이익, 전략 준수)"},
    "C": {"min_pnl": -2.0, "desc": "보통 (소폭 손실, 개선 여지)"},
    "D": {"min_pnl": -5.0, "desc": "미흡 (큰 손실, 규칙 위반 가능)"},
    "F": {"min_pnl": -999, "desc": "실패 (심각한 손실, 전략 재검토 필요)"},
}

# 신호 소스 정의
SIGNAL_SOURCES = {
    "scan_engine": "스캔 엔진",
    "multi_agent": "멀티에이전트",
    "ml_prediction": "ML 예측",
    "manager_scalp": "리버모어(단타)",
    "manager_swing": "오닐(스윙)",
    "manager_position": "린치(포지션)",
    "manager_long_term": "버핏(장기)",
    "consensus": "증권사 컨센서스",
    "surge_detect": "급등 감지",
    "stealth_accumulation": "세력 포착",
    "sector_rotation": "섹터 로테이션",
    "contrarian": "역발상",
    "manual": "수동 매매",
}


@dataclass
class DebriefResult:
    """매매 복기 결과."""
    ticker: str
    name: str
    grade: str = "C"
    pnl_pct: float = 0
    ai_review: str = ""
    lessons: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)
    improvements: str = ""


# ---------------------------------------------------------------------------
# 등급 산정
# ---------------------------------------------------------------------------

def compute_grade(
    pnl_pct: float,
    hold_days: int,
    horizon: str = "swing",
    followed_plan: bool = True,
) -> str:
    """매매 등급 산정 (A~F).

    기본은 수익률 기준이며, 보유 기간 대비 효율도 고려.
    """
    # 계획 미준수 시 한 단계 하향
    penalty = 0 if followed_plan else 1

    # 기본 등급 (수익률)
    if pnl_pct >= 5.0:
        base = 0  # A
    elif pnl_pct >= 1.0:
        base = 1  # B
    elif pnl_pct >= -2.0:
        base = 2  # C
    elif pnl_pct >= -5.0:
        base = 3  # D
    else:
        base = 4  # F

    # 보너스: 단타인데 빠르게 수익 실현
    if horizon == "scalp" and hold_days <= 2 and pnl_pct > 0:
        base = max(0, base - 1)

    # 보너스: 장기인데 큰 수익
    if horizon == "long_term" and pnl_pct >= 15:
        base = max(0, base - 1)

    # 페널티: 단타인데 보유 기간 과다
    if horizon == "scalp" and hold_days > 5:
        base = min(4, base + 1)

    grade_idx = min(4, base + penalty)
    grades = ["A", "B", "C", "D", "F"]
    return grades[grade_idx]


# ---------------------------------------------------------------------------
# AI 복기 (Haiku — 저비용)
# ---------------------------------------------------------------------------

async def generate_ai_review(
    ticker: str,
    name: str,
    action: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    hold_days: int,
    horizon: str,
    manager: str,
    signal_source: str,
    market_regime: str,
    entry_reason: str = "",
    exit_reason: str = "",
    past_debriefs: list[dict] | None = None,
) -> dict:
    """AI로 매매 복기 리뷰 생성 (Haiku 모델).

    Returns:
        dict with keys: review, lessons, mistakes, improvements, grade_adj
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _fallback_review(pnl_pct, hold_days, horizon)

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
    except Exception as e:
        logger.warning("AI 복기 클라이언트 초기화 실패: %s", e)
        return _fallback_review(pnl_pct, hold_days, horizon)

    # 과거 복기에서 반복 패턴 추출
    past_context = ""
    if past_debriefs:
        recent = past_debriefs[:5]
        past_mistakes = []
        for d in recent:
            try:
                m = json.loads(d.get("mistakes_json", "[]"))
                past_mistakes.extend(m)
            except Exception:
                pass
        if past_mistakes:
            past_context = f"\n과거 반복 실수: {', '.join(past_mistakes[:5])}"

    horizon_kr = {
        "scalp": "초단타", "swing": "스윙", "position": "포지션", "long_term": "장기",
    }.get(horizon, horizon)

    prompt = f"""매매 복기를 분석해주세요.

종목: {name}({ticker})
매매 유형: {action} ({horizon_kr})
담당 매니저: {manager or '미지정'}
진입가: {entry_price:,.0f}원 → 청산가: {exit_price:,.0f}원
수익률: {pnl_pct:+.1f}%
보유일수: {hold_days}일
신호 출처: {SIGNAL_SOURCES.get(signal_source, signal_source)}
시장 레짐: {market_regime or '미확인'}
진입 사유: {entry_reason or '미기록'}
청산 사유: {exit_reason or '미기록'}
{past_context}

다음을 한국어로 간결하게 답변하세요:
1. 복기 요약 (2~3문장, 잘한 점 + 개선점)
2. 교훈 (2~3개, 리스트)
3. 실수 (있다면 1~2개)
4. 다음 매매에 적용할 개선사항 (1문장)

JSON 형식으로 답변:
{{"review": "...", "lessons": ["...", "..."], "mistakes": ["..."], "improvements": "..."}}"""

    try:
        response = await client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        # [v6.2.1] 토큰 추적
        try:
            from kstock.core.token_tracker import track_usage_global
            track_usage_global(
                provider="anthropic",
                model="claude-3-5-haiku-20241022",
                function_name="trade_debrief",
                response=response,
            )
        except Exception:
            pass
        text = response.content[0].text.strip()

        # JSON 파싱 (코드블록 제거)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        return {
            "review": result.get("review", ""),
            "lessons": result.get("lessons", []),
            "mistakes": result.get("mistakes", []),
            "improvements": result.get("improvements", ""),
        }
    except Exception as e:
        logger.warning("AI 복기 생성 실패: %s", e)
        return _fallback_review(pnl_pct, hold_days, horizon)


def _fallback_review(pnl_pct: float, hold_days: int, horizon: str) -> dict:
    """AI 없이 규칙 기반 복기."""
    lessons = []
    mistakes = []
    review = ""

    if pnl_pct > 5:
        review = "목표 수익률을 달성한 좋은 매매."
        lessons.append("수익 구간에서 분할 매도 전략 유지")
    elif pnl_pct > 0:
        review = "소폭 이익으로 마감. 진입 타이밍 개선 여지."
        lessons.append("수익률 극대화를 위한 진입 지점 최적화 필요")
    elif pnl_pct > -3:
        review = "소폭 손절. 리스크 관리는 적절."
        lessons.append("손절 기준 준수 확인")
    else:
        review = "큰 손실 발생. 손절 지연 또는 포지션 과다 가능성."
        mistakes.append("손절 기준 미준수 또는 지연")
        lessons.append("진입 전 손절가 반드시 설정")

    if horizon == "scalp" and hold_days > 3:
        mistakes.append(f"초단타 {hold_days}일 보유 — 계획 대비 과다")
        lessons.append("초단타는 당일~2일 내 청산 원칙 준수")

    return {
        "review": review,
        "lessons": lessons,
        "mistakes": mistakes,
        "improvements": "다음 매매에서 진입 전 체크리스트 확인",
    }


# ---------------------------------------------------------------------------
# 핵심 복기 함수
# ---------------------------------------------------------------------------

async def auto_debrief_trade(
    db: Any,
    ticker: str,
    name: str,
    action: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    hold_days: int = 0,
    horizon: str = "swing",
    manager: str = "",
    signal_source: str = "",
    signal_score: float = 0,
    market_regime: str = "",
    entry_reason: str = "",
    exit_reason: str = "",
    trade_id: int | None = None,
) -> DebriefResult:
    """매매 완료 시 자동 복기 실행.

    1. 등급 산정
    2. AI 복기 생성
    3. DB 저장
    4. investor_profile 업데이트
    """
    # 1. 등급 산정
    grade = compute_grade(pnl_pct, hold_days, horizon)

    # 2. 과거 복기 가져오기 (반복 패턴 분석용)
    past_debriefs = db.get_trade_debriefs(ticker=None, limit=10)

    # 3. AI 복기
    ai_result = await generate_ai_review(
        ticker=ticker, name=name, action=action,
        entry_price=entry_price, exit_price=exit_price,
        pnl_pct=pnl_pct, hold_days=hold_days,
        horizon=horizon, manager=manager,
        signal_source=signal_source, market_regime=market_regime,
        entry_reason=entry_reason, exit_reason=exit_reason,
        past_debriefs=past_debriefs,
    )

    # AI가 등급 조정 제안하면 반영
    ai_review = ai_result.get("review", "")
    lessons = ai_result.get("lessons", [])
    mistakes = ai_result.get("mistakes", [])
    improvements = ai_result.get("improvements", "")

    # 4. DB 저장
    db.save_trade_debrief(
        ticker=ticker, name=name, action=action,
        entry_price=entry_price, exit_price=exit_price,
        pnl_pct=pnl_pct, hold_days=hold_days,
        horizon=horizon, manager=manager,
        signal_source=signal_source, signal_score=signal_score,
        market_regime=market_regime,
        entry_reason=entry_reason, exit_reason=exit_reason,
        ai_review=ai_review,
        lessons_json=json.dumps(lessons, ensure_ascii=False),
        mistakes_json=json.dumps(mistakes, ensure_ascii=False),
        improvements=improvements,
        grade=grade,
        trade_id=trade_id,
    )

    # 5. trade_lessons에도 교훈 기록 (기존 호환)
    if lessons:
        db.add_trade_lesson(
            ticker=ticker, name=name, action=action,
            pnl_pct=pnl_pct, hold_days=hold_days,
            lesson="; ".join(lessons[:3]),
        )

    # 6. investor_profile 업데이트
    _update_investor_profile(db, pnl_pct, hold_days)

    logger.info(
        "Auto debrief: %s(%s) %s grade=%s pnl=%.1f%%",
        name, ticker, action, grade, pnl_pct,
    )

    return DebriefResult(
        ticker=ticker, name=name, grade=grade,
        pnl_pct=pnl_pct, ai_review=ai_review,
        lessons=lessons, mistakes=mistakes,
        improvements=improvements,
    )


def _update_investor_profile(db: Any, pnl_pct: float, hold_days: int) -> None:
    """매매 결과로 investor_profile 자동 갱신."""
    try:
        profile = db.get_investor_profile()
        if not profile:
            return

        trade_count = (profile.get("trade_count") or 0) + 1
        old_win_rate = profile.get("win_rate") or 0
        old_avg_profit = profile.get("avg_profit_pct") or 0
        old_avg_loss = profile.get("avg_loss_pct") or 0
        old_avg_hold = profile.get("avg_hold_days") or 0

        # 이동 평균 업데이트
        if pnl_pct > 0:
            win_count = int(old_win_rate / 100 * (trade_count - 1)) + 1
            new_win_rate = round(win_count / trade_count * 100, 1)
            # 이익 평균 갱신
            profit_count = win_count
            new_avg_profit = round(
                (old_avg_profit * (profit_count - 1) + pnl_pct) / profit_count, 2
            ) if profit_count > 0 else pnl_pct
            new_avg_loss = old_avg_loss
        else:
            win_count = int(old_win_rate / 100 * (trade_count - 1))
            new_win_rate = round(win_count / trade_count * 100, 1) if trade_count > 0 else 0
            loss_count = trade_count - win_count
            new_avg_loss = round(
                (old_avg_loss * (loss_count - 1) + pnl_pct) / loss_count, 2
            ) if loss_count > 0 else pnl_pct
            new_avg_profit = old_avg_profit

        new_avg_hold = round(
            (old_avg_hold * (trade_count - 1) + hold_days) / trade_count, 1
        )

        db.upsert_investor_profile(
            trade_count=trade_count,
            win_rate=new_win_rate,
            avg_profit_pct=new_avg_profit,
            avg_loss_pct=new_avg_loss,
            avg_hold_days=new_avg_hold,
        )
        logger.info(
            "Investor profile updated: trades=%d, win_rate=%.1f%%",
            trade_count, new_win_rate,
        )
    except Exception as e:
        logger.warning("Investor profile 업데이트 실패: %s", e)


# ---------------------------------------------------------------------------
# 신호 적중률 평가
# ---------------------------------------------------------------------------

async def evaluate_pending_signals(db: Any) -> int:
    """미평가 신호의 가격 추적 + 적중률 계산.

    스케줄러에서 매일 장 마감 후 호출.
    yfinance로 현재가를 조회하여 D+N 수익률을 계산하고
    D+5 기준 양수면 hit=1로 마킹.
    """
    pending = db.get_pending_signal_evaluations(days_ago=1)
    if not pending:
        return 0

    # 고유 티커 추출
    tickers = list({s["ticker"] for s in pending})

    # 현재가 일괄 조회
    prices = _fetch_current_prices(tickers)
    if not prices:
        logger.warning("신호 평가: 가격 조회 실패")
        return 0

    evaluated_count = 0
    today = datetime.utcnow()

    for sig in pending:
        ticker = sig["ticker"]
        signal_price = sig.get("signal_price") or 0
        if signal_price <= 0 or ticker not in prices:
            continue

        current_price = prices[ticker]
        signal_date = sig.get("signal_date", "")
        try:
            sig_dt = datetime.strptime(signal_date[:10], "%Y-%m-%d")
            days_since = (today - sig_dt).days
        except (ValueError, TypeError):
            continue

        if days_since < 1:
            continue

        # 수익률 계산
        ret = lambda p: round((p - signal_price) / signal_price * 100, 2) if signal_price > 0 else None  # noqa: E731

        price_d1 = current_price if days_since >= 1 else None
        price_d3 = current_price if days_since >= 3 else None
        price_d5 = current_price if days_since >= 5 else None
        price_d10 = current_price if days_since >= 10 else None
        price_d20 = current_price if days_since >= 20 else None

        return_d1 = ret(price_d1) if price_d1 else None
        return_d3 = ret(price_d3) if price_d3 else None
        return_d5 = ret(price_d5) if price_d5 else None
        return_d10 = ret(price_d10) if price_d10 else None
        return_d20 = ret(price_d20) if price_d20 else None

        # hit 판정: D+5 기준 (5일 미경과 시 최신 수익률)
        eval_return = return_d5 or return_d3 or return_d1
        hit = 1 if eval_return and eval_return > 0 else 0

        # max_return / max_drawdown (현재 시점까지의 값)
        current_return = round(
            (current_price - signal_price) / signal_price * 100, 2
        ) if signal_price > 0 else 0

        # 기존 값과 비교하여 max/min 갱신
        old_max = sig.get("max_return") or current_return
        old_dd = sig.get("max_drawdown") or current_return
        max_return = max(old_max, current_return)
        max_drawdown = min(old_dd, current_return)

        # D+20 이상 또는 D+5 평가 완료 시 최종 평가
        should_finalize = days_since >= 5

        if should_finalize:
            db.update_signal_evaluation(
                signal_id=sig["id"],
                price_d1=price_d1, price_d3=price_d3,
                price_d5=price_d5, price_d10=price_d10, price_d20=price_d20,
                return_d1=return_d1, return_d3=return_d3,
                return_d5=return_d5, return_d10=return_d10, return_d20=return_d20,
                max_return=max_return, max_drawdown=max_drawdown,
                hit=hit,
            )
            evaluated_count += 1
        else:
            # 임시 업데이트 (max/min 갱신만, evaluated_at은 NULL 유지)
            try:
                with db._connect() as conn:
                    conn.execute(
                        "UPDATE signal_performance SET "
                        "price_d1=COALESCE(?, price_d1), "
                        "return_d1=COALESCE(?, return_d1), "
                        "max_return=?, max_drawdown=? "
                        "WHERE id=?",
                        (price_d1, return_d1, max_return, max_drawdown, sig["id"]),
                    )
            except Exception:
                pass

    logger.info("신호 적중률 평가 완료: %d건 평가", evaluated_count)
    return evaluated_count


def _fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """yfinance로 현재가 일괄 조회."""
    try:
        import yfinance as yf

        # 한국 종목 코드 변환 (6자리 → .KS/.KQ)
        yf_tickers = []
        ticker_map = {}
        for t in tickers:
            if len(t) == 6 and t.isdigit():
                yf_t = f"{t}.KS"
                yf_tickers.append(yf_t)
                ticker_map[yf_t] = t
            else:
                yf_tickers.append(t)
                ticker_map[t] = t

        if not yf_tickers:
            return {}

        data = yf.download(
            yf_tickers, period="1d", progress=False, auto_adjust=True,
        )

        prices = {}
        if len(yf_tickers) == 1:
            close = data["Close"]
            if not close.empty:
                val = close.iloc[-1]
                if hasattr(val, "item"):
                    val = val.item()
                prices[ticker_map[yf_tickers[0]]] = float(val)
        else:
            for yf_t in yf_tickers:
                try:
                    close = data["Close"][yf_t].dropna()
                    if not close.empty:
                        val = close.iloc[-1]
                        if hasattr(val, "item"):
                            val = val.item()
                        prices[ticker_map[yf_t]] = float(val)
                except Exception:
                    continue

        return prices
    except Exception as e:
        logger.warning("가격 조회 실패: %s", e)
        return {}


# ---------------------------------------------------------------------------
# 신호 소스 가중치 계산
# ---------------------------------------------------------------------------

def compute_signal_weights(db: Any, period_days: int = 90) -> dict[str, float]:
    """신호 소스별 가중치 계산 + 저장.

    적중률이 높은 소스의 가중치를 높이고, 낮은 소스는 감소.
    기본 가중치 1.0, 범위 0.3 ~ 2.0.
    """
    stats = db.get_signal_source_stats(days=period_days)
    weights: dict[str, float] = {}

    for s in stats:
        source = s["signal_source"]
        evaluated = s.get("evaluated") or 0
        hits = s.get("hits") or 0

        if evaluated < 5:
            # 데이터 부족 → 기본 가중치
            weights[source] = 1.0
            continue

        hit_rate = hits / evaluated * 100

        # 가중치 계산: 적중률 60% 기준으로 선형 조정
        if hit_rate >= 80:
            weight = 2.0
        elif hit_rate >= 70:
            weight = 1.5
        elif hit_rate >= 60:
            weight = 1.2
        elif hit_rate >= 50:
            weight = 1.0
        elif hit_rate >= 40:
            weight = 0.7
        else:
            weight = 0.3

        weights[source] = weight

        # DB에 저장
        db.save_signal_source_stats(
            signal_source=source,
            period="auto",
            total_signals=s.get("total", 0),
            evaluated=evaluated,
            hits=hits,
            hit_rate=round(hit_rate, 1),
            avg_return_d5=s.get("avg_d5") or 0,
            avg_return_d10=s.get("avg_d10") or 0,
            avg_return_d20=s.get("avg_d20") or 0,
            avg_max_return=s.get("avg_max_ret") or 0,
            avg_max_dd=s.get("avg_max_dd") or 0,
            weight_adj=weight,
        )

    logger.info("신호 가중치 계산 완료: %s", weights)
    return weights


# ---------------------------------------------------------------------------
# 텔레그램 포맷
# ---------------------------------------------------------------------------

def format_debrief_message(result: DebriefResult) -> str:
    """복기 결과를 텔레그램 메시지로 포맷."""
    grade_emoji = {
        "A": "🏆", "B": "👍", "C": "📋", "D": "⚠️", "F": "🚨",
    }.get(result.grade, "📋")

    lines = [
        f"{grade_emoji} 매매 복기: {result.name}({result.ticker})",
        f"{'━' * 22}",
        "",
        f"등급: {result.grade} | 수익률: {result.pnl_pct:+.1f}%",
        "",
    ]

    if result.ai_review:
        lines.append(f"📝 {result.ai_review}")
        lines.append("")

    if result.lessons:
        lines.append("💡 교훈:")
        for i, lesson in enumerate(result.lessons[:3], 1):
            lines.append(f"  {i}. {lesson}")
        lines.append("")

    if result.mistakes:
        lines.append("❌ 실수:")
        for m in result.mistakes[:2]:
            lines.append(f"  • {m}")
        lines.append("")

    if result.improvements:
        lines.append(f"🔧 개선: {result.improvements}")

    return "\n".join(lines)


def format_signal_stats_message(stats: list[dict]) -> str:
    """신호 소스별 적중률 통계를 텔레그램 메시지로 포맷."""
    if not stats:
        return "📊 신호 적중률 데이터가 아직 없습니다."

    lines = [
        "📊 신호 소스별 적중률",
        f"{'━' * 22}",
        "",
    ]

    for s in stats:
        source = s.get("signal_source", "")
        source_kr = SIGNAL_SOURCES.get(source, source)
        evaluated = s.get("evaluated") or 0
        hits = s.get("hits") or 0
        hit_rate = round(hits / evaluated * 100, 1) if evaluated > 0 else 0
        avg_d5 = s.get("avg_d5") or 0

        if evaluated == 0:
            continue

        emoji = "🟢" if hit_rate >= 60 else "🟡" if hit_rate >= 45 else "🔴"
        lines.append(
            f"  {emoji} {source_kr}: {hit_rate:.0f}% ({hits}/{evaluated}건) "
            f"D5 {avg_d5:+.1f}%"
        )

    lines.append("")
    lines.append("🤖 K-Quant 자가 학습 시스템")

    return "\n".join(lines)
