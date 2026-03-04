"""매매일지 AI 복기 엔진 - Phase 2-1.

매매 이력을 분석하여 패턴 인사이트를 도출하고,
AI를 통해 개인 매매 습관을 복기/개선하는 시스템.

주요 기능:
  1. 매매 기록 자동 수집 & 정리
  2. 승/패 패턴 분석 (시간대, 요일, 섹터, 전략별)
  3. AI 복기 리포트 생성 (주간/월간)
  4. 실수 반복 감지 + 개선점 제안
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

USER_NAME = "주호님"


# ── 데이터 구조 ───────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """정규화된 매매 기록."""
    ticker: str
    name: str
    action: str        # buy, sell, stop_loss, hold_through
    strategy: str      # A~G
    entry_price: float
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    sector: str = ""
    horizon: str = "swing"
    market_regime: str = "neutral"
    trade_date: str = ""
    weekday: int = 0    # 0=월 ~ 4=금
    hour: int = 0


@dataclass
class PatternInsight:
    """발견된 매매 패턴."""
    category: str       # "winning", "losing", "timing", "strategy", "sector"
    title: str
    description: str
    confidence: float   # 0~1
    data: dict = field(default_factory=dict)


@dataclass
class JournalReport:
    """AI 복기 리포트."""
    period: str          # "weekly" / "monthly"
    date_range: str
    total_trades: int
    win_rate: float
    avg_pnl: float
    best_trade: dict | None
    worst_trade: dict | None
    patterns: list[PatternInsight]
    ai_review: str = ""  # AI가 생성한 복기 텍스트
    improvement_tips: list[str] = field(default_factory=list)
    repeat_mistakes: list[str] = field(default_factory=list)


@dataclass
class TradeJournalEntry:
    """개별 거래 저널 엔트리."""
    trade_id: str = ""
    ticker: str = ""
    name: str = ""
    action: str = ""         # BUY / SELL
    quantity: int = 0
    price: float = 0.0
    timestamp: str = ""
    # 매수 시 기록
    buy_reason: str = ""       # 매수 사유 (전략명 + 핵심 이유)
    strategy: str = ""         # A/B/C/D/E/F/G
    signal_score: float = 0.0  # 신호 점수 (0-100)
    market_regime: str = ""    # 매수 시점 시장 레짐
    vix_at_entry: float = 0.0  # 매수 시점 VIX
    kelly_fraction: float = 0.0
    # 매도 시 기록
    sell_reason: str = ""       # 매도 사유
    exit_signal: str = ""       # trailing_stop / stop_loss / target / manual
    pnl_pct: float = 0.0       # 수익률
    pnl_amount: float = 0.0    # 수익 금액
    hold_days: int = 0          # 보유 일수
    # 비용
    commission: float = 0.0
    slippage_pct: float = 0.0
    transaction_cost_total: float = 0.0
    # 메타
    is_win: bool = False
    lessons_learned: str = ""   # AI가 자동 생성하는 교훈


# ── SECTOR_MAP (risk_manager 호환) ────────────────────────────────────

SECTOR_MAP: dict[str, str] = {
    "005930": "반도체", "000660": "반도체",
    "373220": "2차전지", "006400": "2차전지",
    "247540": "2차전지", "086520": "2차전지",
    "035420": "소프트웨어", "035720": "소프트웨어",
    "207940": "바이오", "068270": "바이오",
    "005380": "자동차", "000270": "자동차",
    "055550": "금융", "105560": "금융", "316140": "금융",
    "005490": "철강", "051910": "화학",
    "017670": "통신", "030200": "통신",
    "352820": "엔터", "009540": "조선", "012450": "방산",
}


class TradeJournal:
    """매매일지 AI 복기 시스템.

    거래 저널 — 모든 매매를 기록하고 학습에 활용.
    """

    def __init__(self, db=None):
        self.db = db
        self._entries: list[TradeJournalEntry] = []

    # ── 거래 저널 기록/조회 ────────────────────────────────────────

    def record_entry(self, entry: TradeJournalEntry) -> None:
        """거래 기록 추가."""
        if not entry.trade_id:
            import uuid
            entry.trade_id = str(uuid.uuid4())[:8]
        if not entry.timestamp:
            entry.timestamp = datetime.now().isoformat()
        self._entries.append(entry)
        logger.info("Trade journal: %s %s %s @ %s",
                     entry.action, entry.ticker, entry.quantity, entry.price)

    def get_recent_trades(self, n: int = 30, ticker: str = "") -> list[dict]:
        """최근 N개 거래를 Dynamic Kelly용 dict 리스트로 반환."""
        entries = self._entries
        if ticker:
            entries = [e for e in entries if e.ticker == ticker]
        # 매도 완료된 거래만 (pnl 있는 것)
        completed = [e for e in entries if e.action == "SELL" and e.pnl_pct != 0]
        recent = completed[-n:]
        return [
            {"pnl_pct": e.pnl_pct, "is_win": e.is_win}
            for e in recent
        ]

    def get_strategy_performance(self) -> dict[str, dict]:
        """전략별 성과 통계."""
        from collections import defaultdict
        stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0})
        for e in self._entries:
            if e.action != "SELL" or e.strategy == "":
                continue
            s = stats[e.strategy]
            s["trades"] += 1
            s["total_pnl"] += e.pnl_pct
            if e.is_win:
                s["wins"] += 1
            else:
                s["losses"] += 1

        result = {}
        for strat, s in stats.items():
            result[strat] = {
                **s,
                "win_rate": s["wins"] / s["trades"] if s["trades"] > 0 else 0,
                "avg_pnl": s["total_pnl"] / s["trades"] if s["trades"] > 0 else 0,
            }
        return result

    # ── 매매 기록 수집 ─────────────────────────────────────────────

    def collect_trades(
        self, days: int = 7, limit: int = 200,
    ) -> list[TradeRecord]:
        """DB에서 최근 매매 기록을 수집하여 정규화."""
        if not self.db:
            return []

        trades_raw = self.db.get_trades(limit=limit)
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        records = []
        for t in trades_raw:
            created = t.get("created_at", "")
            if created < cutoff:
                continue

            ticker = t.get("ticker", "")
            try:
                dt = datetime.fromisoformat(created)
                weekday = dt.weekday()
                hour = dt.hour
                trade_date = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                weekday = 0
                hour = 10
                trade_date = created[:10] if len(created) >= 10 else ""

            records.append(TradeRecord(
                ticker=ticker,
                name=t.get("name", ""),
                action=t.get("action", ""),
                strategy=t.get("strategy_type", "A"),
                entry_price=t.get("recommended_price", 0) or t.get("action_price", 0),
                exit_price=t.get("action_price", 0),
                pnl_pct=t.get("pnl_pct", 0),
                sector=SECTOR_MAP.get(ticker, "기타"),
                trade_date=trade_date,
                weekday=weekday,
                hour=hour,
            ))

        return records

    # ── 패턴 분석 ─────────────────────────────────────────────────

    def analyze_patterns(self, trades: list[TradeRecord]) -> list[PatternInsight]:
        """매매 기록에서 패턴 인사이트 추출."""
        if not trades:
            return []

        insights: list[PatternInsight] = []

        # 1. 전략별 성과
        insights.extend(self._analyze_by_strategy(trades))
        # 2. 섹터별 성과
        insights.extend(self._analyze_by_sector(trades))
        # 3. 요일별 성과
        insights.extend(self._analyze_by_weekday(trades))
        # 4. 승패 패턴
        insights.extend(self._analyze_win_loss_patterns(trades))
        # 5. 실수 반복 감지
        insights.extend(self._detect_repeated_mistakes(trades))

        # 신뢰도 순 정렬
        insights.sort(key=lambda x: x.confidence, reverse=True)
        return insights

    def _analyze_by_strategy(self, trades: list[TradeRecord]) -> list[PatternInsight]:
        """전략별 승률/수익률 분석."""
        from collections import defaultdict
        strat_stats: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            if t.pnl_pct != 0:
                strat_stats[t.strategy].append(t.pnl_pct)

        insights = []
        strategy_names = {
            "A": "단기반등", "B": "ETF레버리지", "C": "장기우량주",
            "D": "섹터로테이션", "E": "글로벌분산", "F": "모멘텀", "G": "돌파",
        }
        for strat, pnls in strat_stats.items():
            if len(pnls) < 2:
                continue
            wins = sum(1 for p in pnls if p > 0)
            win_rate = wins / len(pnls) * 100
            avg_pnl = sum(pnls) / len(pnls)
            name = strategy_names.get(strat, strat)

            if win_rate >= 70:
                insights.append(PatternInsight(
                    category="strategy",
                    title=f"✅ {name} 전략 강점",
                    description=(
                        f"{name} 전략 승률 {win_rate:.0f}% "
                        f"(평균 {avg_pnl:+.1f}%, {len(pnls)}회)"
                    ),
                    confidence=min(0.9, len(pnls) / 10),
                    data={"strategy": strat, "win_rate": win_rate, "avg_pnl": avg_pnl},
                ))
            elif win_rate <= 30 and len(pnls) >= 3:
                insights.append(PatternInsight(
                    category="strategy",
                    title=f"⚠️ {name} 전략 약점",
                    description=(
                        f"{name} 전략 승률 {win_rate:.0f}% "
                        f"(평균 {avg_pnl:+.1f}%, {len(pnls)}회) — 재점검 필요"
                    ),
                    confidence=min(0.9, len(pnls) / 10),
                    data={"strategy": strat, "win_rate": win_rate, "avg_pnl": avg_pnl},
                ))

        return insights

    def _analyze_by_sector(self, trades: list[TradeRecord]) -> list[PatternInsight]:
        """섹터별 성과 분석."""
        from collections import defaultdict
        sector_stats: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            if t.pnl_pct != 0 and t.sector:
                sector_stats[t.sector].append(t.pnl_pct)

        insights = []
        for sector, pnls in sector_stats.items():
            if len(pnls) < 2:
                continue
            avg = sum(pnls) / len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            win_rate = wins / len(pnls) * 100

            if avg > 3:
                insights.append(PatternInsight(
                    category="sector",
                    title=f"🏆 {sector} 섹터 강세",
                    description=f"{sector} 평균 수익 {avg:+.1f}%, 승률 {win_rate:.0f}%",
                    confidence=min(0.85, len(pnls) / 8),
                    data={"sector": sector, "avg_pnl": avg, "trades": len(pnls)},
                ))
            elif avg < -3:
                insights.append(PatternInsight(
                    category="sector",
                    title=f"⛔ {sector} 섹터 약세",
                    description=f"{sector} 평균 손실 {avg:+.1f}%, 승률 {win_rate:.0f}%",
                    confidence=min(0.85, len(pnls) / 8),
                    data={"sector": sector, "avg_pnl": avg, "trades": len(pnls)},
                ))

        return insights

    def _analyze_by_weekday(self, trades: list[TradeRecord]) -> list[PatternInsight]:
        """요일별 매매 성과 분석."""
        from collections import defaultdict
        day_names = ["월", "화", "수", "목", "금"]
        day_stats: dict[int, list[float]] = defaultdict(list)
        for t in trades:
            if t.pnl_pct != 0:
                day_stats[t.weekday].append(t.pnl_pct)

        insights = []
        for day, pnls in day_stats.items():
            if len(pnls) < 3:
                continue
            avg = sum(pnls) / len(pnls)
            if day < len(day_names) and abs(avg) > 2:
                direction = "수익" if avg > 0 else "손실"
                insights.append(PatternInsight(
                    category="timing",
                    title=f"📅 {day_names[day]}요일 {direction} 경향",
                    description=(
                        f"{day_names[day]}요일 매매 평균 {avg:+.1f}% "
                        f"({len(pnls)}회)"
                    ),
                    confidence=min(0.7, len(pnls) / 10),
                    data={"weekday": day, "avg_pnl": avg},
                ))

        return insights

    def _analyze_win_loss_patterns(self, trades: list[TradeRecord]) -> list[PatternInsight]:
        """연승/연패 패턴, 평균 보유기간 분석."""
        insights = []

        # 수익/손실 거래 분리
        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct < 0]

        if wins:
            avg_win = sum(t.pnl_pct for t in wins) / len(wins)
            insights.append(PatternInsight(
                category="winning",
                title="💰 평균 수익 거래",
                description=f"수익 거래 {len(wins)}건, 평균 +{avg_win:.1f}%",
                confidence=0.9,
                data={"count": len(wins), "avg_pnl": avg_win},
            ))

        if losses:
            avg_loss = sum(t.pnl_pct for t in losses) / len(losses)
            insights.append(PatternInsight(
                category="losing",
                title="📉 평균 손실 거래",
                description=f"손실 거래 {len(losses)}건, 평균 {avg_loss:.1f}%",
                confidence=0.9,
                data={"count": len(losses), "avg_pnl": avg_loss},
            ))

        # 손익비
        if wins and losses:
            avg_win = sum(t.pnl_pct for t in wins) / len(wins)
            avg_loss_abs = abs(sum(t.pnl_pct for t in losses) / len(losses))
            if avg_loss_abs > 0:
                rr_ratio = avg_win / avg_loss_abs
                quality = "양호" if rr_ratio >= 1.5 else "개선필요" if rr_ratio < 1.0 else "보통"
                insights.append(PatternInsight(
                    category="winning",
                    title=f"⚖️ 손익비 {rr_ratio:.1f} ({quality})",
                    description=(
                        f"평균수익 {avg_win:+.1f}% vs 평균손실 -{avg_loss_abs:.1f}%"
                    ),
                    confidence=0.85,
                    data={"rr_ratio": rr_ratio},
                ))

        return insights

    def _detect_repeated_mistakes(self, trades: list[TradeRecord]) -> list[PatternInsight]:
        """반복되는 실수 패턴 감지."""
        insights = []

        # 1. 손절 미이행 (hold_through 비율)
        stop_events = [t for t in trades if t.action in ("stop_loss", "hold_through")]
        if len(stop_events) >= 2:
            hold_throughs = sum(1 for t in stop_events if t.action == "hold_through")
            if hold_throughs > 0:
                rate = hold_throughs / len(stop_events) * 100
                if rate > 30:
                    insights.append(PatternInsight(
                        category="losing",
                        title="🔴 손절 미이행 반복",
                        description=(
                            f"손절 이벤트 {len(stop_events)}회 중 "
                            f"{hold_throughs}회({rate:.0f}%) 홀딩 — 원칙 준수 필요"
                        ),
                        confidence=0.95,
                        data={"hold_through_rate": rate},
                    ))

        # 2. 동일 종목 반복 매매 (같은 종목 3회 이상)
        from collections import Counter
        ticker_counts = Counter(t.ticker for t in trades if t.action == "buy")
        for ticker, count in ticker_counts.items():
            if count >= 3:
                ticker_trades = [t for t in trades if t.ticker == ticker]
                avg = sum(t.pnl_pct for t in ticker_trades if t.pnl_pct != 0)
                avg = avg / max(1, sum(1 for t in ticker_trades if t.pnl_pct != 0))
                name = ticker_trades[0].name if ticker_trades else ticker
                if avg < 0:
                    insights.append(PatternInsight(
                        category="losing",
                        title=f"🔁 {name} 반복 매매 (평균 {avg:+.1f}%)",
                        description=f"{name} {count}회 매수, 평균 수익 {avg:+.1f}% — 복수매매 주의",
                        confidence=0.8,
                        data={"ticker": ticker, "count": count, "avg_pnl": avg},
                    ))

        return insights

    # ── AI 복기 프롬프트 생성 ─────────────────────────────────────

    def build_review_prompt(
        self,
        trades: list[TradeRecord],
        patterns: list[PatternInsight],
        period: str = "weekly",
    ) -> str:
        """AI 복기를 위한 프롬프트 생성."""
        if not trades:
            return ""

        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct < 0]
        total = len([t for t in trades if t.pnl_pct != 0])
        win_rate = len(wins) / total * 100 if total else 0
        avg_pnl = sum(t.pnl_pct for t in trades if t.pnl_pct != 0) / max(1, total)

        # 거래 요약
        trade_lines = []
        for t in trades[:30]:  # 최대 30건
            if t.pnl_pct != 0:
                emoji = "✅" if t.pnl_pct > 0 else "❌"
                trade_lines.append(
                    f"  {emoji} {t.trade_date} {t.name}({t.ticker}) "
                    f"전략{t.strategy} {t.action} {t.pnl_pct:+.1f}% "
                    f"섹터:{t.sector}"
                )

        # 패턴 요약
        pattern_lines = []
        for p in patterns[:10]:
            pattern_lines.append(f"  - {p.title}: {p.description}")

        period_label = "주간" if period == "weekly" else "월간"

        prompt = f"""한국 개인투자자 {USER_NAME}의 {period_label} 매매일지 AI 복기를 작성해주세요.

## 매매 요약
- 기간: {period_label}
- 총 거래: {total}회 (승 {len(wins)} / 패 {len(losses)})
- 승률: {win_rate:.0f}%
- 평균 수익률: {avg_pnl:+.1f}%

## 거래 내역
{chr(10).join(trade_lines) if trade_lines else "  (거래 없음)"}

## 발견된 패턴
{chr(10).join(pattern_lines) if pattern_lines else "  (패턴 없음)"}

## 작성 지침
1. 잘한 점 (2~3가지): 원칙 준수, 성공 전략 분석
2. 개선할 점 (2~3가지): 반복 실수, 감정적 매매, 타이밍
3. 다음 주 액션플랜 (구체적 3가지)
4. 손익비 개선 방안
5. 톤: 친근하고 코치 같은 톤. {USER_NAME} 호칭 사용.

**중요**: 구체적인 종목/수치를 인용하면서 분석. 추상적 조언 대신 데이터 기반 인사이트.
"""
        return prompt

    # ── 리포트 생성 ───────────────────────────────────────────────

    def generate_report(
        self,
        trades: list[TradeRecord],
        patterns: list[PatternInsight],
        ai_review: str = "",
        period: str = "weekly",
    ) -> JournalReport:
        """매매일지 리포트 생성."""
        pnl_trades = [t for t in trades if t.pnl_pct != 0]
        wins = [t for t in pnl_trades if t.pnl_pct > 0]
        total = len(pnl_trades)
        win_rate = len(wins) / total * 100 if total else 0
        avg_pnl = sum(t.pnl_pct for t in pnl_trades) / max(1, total)

        best = max(pnl_trades, key=lambda t: t.pnl_pct) if pnl_trades else None
        worst = min(pnl_trades, key=lambda t: t.pnl_pct) if pnl_trades else None

        # 날짜 범위
        dates = [t.trade_date for t in trades if t.trade_date]
        date_range = f"{min(dates)} ~ {max(dates)}" if dates else "N/A"

        # 실수 반복 추출
        repeat_mistakes = [
            p.description for p in patterns
            if p.category == "losing" and p.confidence >= 0.7
        ]

        # 개선 팁 추출
        tips = []
        for p in patterns:
            if p.category == "strategy" and "약점" in p.title:
                tips.append(f"{p.title}: 비중 축소 또는 전략 재검토")
            elif p.category == "timing":
                tips.append(f"{p.title}: 해당 요일 매매 주의")

        return JournalReport(
            period=period,
            date_range=date_range,
            total_trades=total,
            win_rate=round(win_rate, 1),
            avg_pnl=round(avg_pnl, 2),
            best_trade={"name": best.name, "pnl": best.pnl_pct} if best else None,
            worst_trade={"name": worst.name, "pnl": worst.pnl_pct} if worst else None,
            patterns=patterns,
            ai_review=ai_review,
            improvement_tips=tips,
            repeat_mistakes=repeat_mistakes,
        )


# ── 텔레그램 포맷 ─────────────────────────────────────────────────

def format_journal_report(report: JournalReport) -> str:
    """매매일지 리포트를 텔레그램 메시지로 포맷."""
    period_emoji = "📅" if report.period == "weekly" else "📆"
    period_label = "주간" if report.period == "weekly" else "월간"

    pnl_emoji = "🟢" if report.avg_pnl > 0 else "🔴" if report.avg_pnl < 0 else "⚪"

    lines = [
        f"{period_emoji} {USER_NAME} {period_label} 매매일지",
        "━" * 25,
        f"기간: {report.date_range}",
        "",
        f"📊 거래 {report.total_trades}회",
        f"🎯 승률: {report.win_rate:.0f}%",
        f"{pnl_emoji} 평균 수익: {report.avg_pnl:+.1f}%",
    ]

    if report.best_trade:
        lines.append(
            f"🏆 최고: {report.best_trade['name']} "
            f"{report.best_trade['pnl']:+.1f}%"
        )
    if report.worst_trade:
        lines.append(
            f"💀 최저: {report.worst_trade['name']} "
            f"{report.worst_trade['pnl']:+.1f}%"
        )

    # 패턴 인사이트
    if report.patterns:
        lines.extend(["", "━" * 25, "🔍 발견된 패턴"])
        for p in report.patterns[:5]:
            lines.append(f"  {p.title}")
            lines.append(f"    {p.description}")

    # AI 복기 (요약만)
    if report.ai_review:
        lines.extend(["", "━" * 25, "🤖 AI 복기"])
        # AI 리뷰 텍스트 최대 500자
        review = report.ai_review[:500]
        if len(report.ai_review) > 500:
            review += "..."
        lines.append(review)

    # 실수 반복
    if report.repeat_mistakes:
        lines.extend(["", "⚠️ 반복 실수 주의"])
        for m in report.repeat_mistakes[:3]:
            lines.append(f"  🔴 {m}")

    return "\n".join(lines)


def format_journal_short(report: JournalReport) -> str:
    """매매일지 간략 요약 (알림용)."""
    pnl_emoji = "🟢" if report.avg_pnl > 0 else "🔴"
    period_label = "주간" if report.period == "weekly" else "월간"
    return (
        f"📋 {period_label} 매매일지 도착!\n"
        f"거래 {report.total_trades}회 | 승률 {report.win_rate:.0f}% | "
        f"{pnl_emoji} 평균 {report.avg_pnl:+.1f}%"
    )


# ── Closed-Loop 학습: 신호 → 거래 → 피드백 → 가중치 ─────────────

@dataclass
class SignalFeedback:
    """신호 품질 피드백 — 거래 결과로 신호 가중치를 조정."""
    strategy: str
    ticker: str
    signal_action: str      # 신호가 추천한 action (BUY/SELL)
    actual_pnl_pct: float   # 실제 수익률
    signal_score: float     # 당시 신호 점수
    was_correct: bool       # 방향성 맞았는지
    feedback_date: str = ""


def compute_signal_quality(
    feedbacks: list[dict],
    min_samples: int = 10,
) -> dict[str, dict]:
    """전략별 신호 품질 지표를 산출.

    Args:
        feedbacks: list of {strategy, actual_pnl_pct, was_correct, signal_score}
        min_samples: 최소 샘플 수 (미달 시 기본값 반환)

    Returns:
        {strategy: {hit_rate, avg_pnl, sharpe, quality_score, weight_adj}}
    """
    from collections import defaultdict

    by_strategy: dict[str, list] = defaultdict(list)
    for f in feedbacks:
        by_strategy[f.get("strategy", "unknown")].append(f)

    results: dict[str, dict] = {}
    for strat, records in by_strategy.items():
        if len(records) < min_samples:
            results[strat] = {
                "hit_rate": 0.5, "avg_pnl": 0.0, "sharpe": 0.0,
                "quality_score": 50.0, "weight_adj": 1.0,
                "sample_size": len(records), "sufficient": False,
            }
            continue

        hits = sum(1 for r in records if r.get("was_correct", False))
        pnls = [r.get("actual_pnl_pct", 0.0) for r in records]

        hit_rate = hits / len(records)
        avg_pnl = sum(pnls) / len(pnls)

        # Sharpe-like ratio
        import numpy as np
        std_pnl = float(np.std(pnls)) if len(pnls) > 1 else 1.0
        sharpe = avg_pnl / std_pnl if std_pnl > 0.001 else 0.0

        # Quality score: 0-100 (hit_rate 40% + sharpe 30% + consistency 30%)
        consistency = 1.0 - (std_pnl / (abs(avg_pnl) + 0.01))
        consistency = max(0.0, min(1.0, consistency))

        quality = (hit_rate * 40) + (min(sharpe, 2.0) / 2.0 * 30) + (consistency * 30)
        quality = max(0.0, min(100.0, quality))

        # Weight adjustment: quality_score / 50 (baseline)
        weight_adj = max(0.3, min(2.0, quality / 50.0))

        results[strat] = {
            "hit_rate": round(hit_rate, 4),
            "avg_pnl": round(avg_pnl, 4),
            "sharpe": round(sharpe, 4),
            "quality_score": round(quality, 2),
            "weight_adj": round(weight_adj, 4),
            "sample_size": len(records),
            "sufficient": True,
        }

    return results


def generate_feedback_report(quality: dict[str, dict]) -> str:
    """Closed-Loop 학습 결과 텔레그램 리포트."""
    lines = [
        "🔄 Closed-Loop 신호 품질 분석",
        "━" * 25,
        "",
    ]

    for strat, q in sorted(quality.items()):
        emoji = "🟢" if q["quality_score"] >= 60 else "🟡" if q["quality_score"] >= 40 else "🔴"
        sufficient = "✓" if q.get("sufficient") else "⚠️부족"
        lines.append(
            f"{emoji} {strat}: {q['quality_score']:.0f}점 "
            f"(적중 {q['hit_rate']:.0%}, Sharpe {q['sharpe']:.2f}) "
            f"→ 가중치 x{q['weight_adj']:.2f} [{sufficient}]"
        )

    lines.append("")
    lines.append("가중치 자동 조정이 다음 스캔부터 적용됩니다.")
    return "\n".join(lines)


# ── 백테스팅 프레임워크: VaR 검증 + 전략 적중률 ─────────────────

@dataclass
class BacktestResult:
    """백테스트 검증 결과."""
    period_days: int = 0
    var_violations: int = 0          # VaR 한도 초과 일수
    var_violation_rate: float = 0.0  # 실제 초과율 (목표: ≤5%)
    var_model_valid: bool = True     # VaR 모델 유효 여부
    strategy_hit_rates: dict = field(default_factory=dict)  # {strategy: hit_rate}
    daily_pnl_sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    total_return_pct: float = 0.0


def backtest_var_accuracy(
    daily_pnl_history: list[float],
    daily_var_estimates: list[float],
    confidence: float = 0.95,
) -> dict:
    """VaR 모델 정확도 백테스트.

    실제 손실이 VaR 추정치를 초과한 날의 비율을 검증.
    정상: 초과율 ≤ (1 - confidence) (95% VaR면 5% 이하)

    Args:
        daily_pnl_history: 일일 수익률 리스트 (음수 = 손실)
        daily_var_estimates: 해당 날짜의 VaR 추정치 (양수)
        confidence: VaR 신뢰 수준 (기본 95%)

    Returns:
        dict with: violations, violation_rate, expected_rate,
        model_valid, kupiec_test_pass
    """
    try:
        import numpy as np

        n = min(len(daily_pnl_history), len(daily_var_estimates))
        if n < 20:
            return {
                "violations": 0, "violation_rate": 0.0,
                "expected_rate": 1 - confidence, "model_valid": True,
                "n_days": n, "sufficient": False,
            }

        violations = 0
        for i in range(n):
            actual_loss = -daily_pnl_history[i]  # 양수로 변환
            var_est = abs(daily_var_estimates[i])
            if actual_loss > var_est:
                violations += 1

        violation_rate = violations / n
        expected_rate = 1 - confidence

        # Kupiec POF test (간소화): 비율이 기대치의 2배 이내면 통과
        model_valid = violation_rate <= expected_rate * 2.0

        return {
            "violations": violations,
            "violation_rate": round(violation_rate, 4),
            "expected_rate": expected_rate,
            "model_valid": model_valid,
            "n_days": n,
            "sufficient": True,
        }

    except Exception:
        logger.exception("VaR 백테스트 실패")
        return {
            "violations": 0, "violation_rate": 0.0,
            "expected_rate": 1 - confidence, "model_valid": True,
            "n_days": 0, "sufficient": False,
        }


def backtest_strategy_accuracy(
    trade_records: list[dict],
) -> dict[str, dict]:
    """전략별 실제 적중률/손익 백테스트.

    Args:
        trade_records: [{strategy, action, pnl_pct, ticker, date}]

    Returns:
        {strategy: {total, wins, hit_rate, avg_win, avg_loss, profit_factor}}
    """
    try:
        from collections import defaultdict
        import numpy as np

        by_strat: dict[str, list] = defaultdict(list)
        for t in trade_records:
            by_strat[t.get("strategy", "unknown")].append(t)

        results = {}
        for strat, trades in by_strat.items():
            pnls = [t.get("pnl_pct", 0.0) for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            hit_rate = len(wins) / len(pnls) if pnls else 0.0
            avg_win = float(np.mean(wins)) if wins else 0.0
            avg_loss = float(np.mean(losses)) if losses else 0.0
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            pf = gross_win / gross_loss if gross_loss > 0 else float('inf')

            results[strat] = {
                "total": len(pnls),
                "wins": len(wins),
                "losses": len(losses),
                "hit_rate": round(hit_rate, 4),
                "avg_win_pct": round(avg_win, 4),
                "avg_loss_pct": round(avg_loss, 4),
                "profit_factor": round(pf, 2),
                "net_pnl_pct": round(sum(pnls), 4),
            }

        return results

    except Exception:
        logger.exception("전략 백테스트 실패")
        return {}


def format_backtest_report(
    var_result: dict,
    strategy_results: dict[str, dict],
) -> str:
    """백테스트 결과 텔레그램 포맷."""
    lines = [
        "📊 백테스트 검증 리포트",
        "━" * 25,
        "",
    ]

    # VaR 검증
    if var_result.get("sufficient"):
        var_emoji = "✅" if var_result["model_valid"] else "⛔"
        lines.append(
            f"{var_emoji} VaR 모델 검증\n"
            f"   초과 일수: {var_result['violations']}일 / {var_result['n_days']}일\n"
            f"   초과율: {var_result['violation_rate']:.1%} "
            f"(목표 ≤{var_result['expected_rate']:.0%})\n"
        )
    else:
        lines.append("⚠️ VaR 검증: 데이터 부족 (20일 이상 필요)\n")

    # 전략별 적중률
    lines.append("📈 전략별 성과")
    for strat, r in sorted(strategy_results.items()):
        emoji = "🟢" if r["hit_rate"] >= 0.55 else "🟡" if r["hit_rate"] >= 0.45 else "🔴"
        lines.append(
            f"   {emoji} {strat}: 적중 {r['hit_rate']:.0%} "
            f"| PF {r['profit_factor']:.1f} "
            f"| 순손익 {r['net_pnl_pct']:+.1f}% "
            f"({r['total']}건)"
        )

    return "\n".join(lines)
