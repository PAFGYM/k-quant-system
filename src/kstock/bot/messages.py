"""Telegram message formatting - K-Quant v11.0 with ML, sentiment, KIS, screenshot.

Rules:
- No ** bold, no Markdown parse_mode
- Use emojis and line breaks for readability
- Commas in numbers (58,000)
- "주호님" personalized greeting
- Direct action instructions (not vague)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kstock.features.technical import TechnicalIndicators
    from kstock.ingest.kis_client import StockInfo
    from kstock.ingest.macro_client import MacroSnapshot
    from kstock.signal.scoring import FlowData, ScoreBreakdown
    from kstock.signal.strategies import StrategySignal

from kstock import APP_NAME, DISPLAY_VERSION, SYSTEM_NAME
from kstock.core.tz import KST

USER_NAME = "주호님"

STRATEGY_LABELS = {
    "A": "\U0001f525단기반등",
    "B": "\u26a1ETF",
    "C": "\U0001f3e6장기",
    "D": "\U0001f504섹터",
    "E": "\U0001f30e글로벌",
    "F": "\U0001f680모멘텀",
    "G": "\U0001f4a5돌파",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _won(price: float) -> str:
    return f"\u20a9{price:,.0f}"


def _억(amount: float) -> str:
    eok = amount / 100_000_000
    if abs(eok) >= 10000:
        return f"{eok / 10000:+,.1f}조"
    return f"{eok:+,.0f}억"


def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")


def _confidence_stars(score: float) -> tuple:
    if score >= 90:
        return "\u2605\u2605\u2605\u2605\u2605", "강한 매수"
    if score >= 80:
        return "\u2605\u2605\u2605\u2605\u2606", "매수 추천"
    if score >= 70:
        return "\u2605\u2605\u2605\u2606\u2606", "관심"
    if score >= 60:
        return "\u2605\u2605\u2606\u2606\u2606", "약한 관심"
    return "\u2605\u2606\u2606\u2606\u2606", "대기"


def _strategy_tag(strategy_type: str) -> str:
    return f"[{STRATEGY_LABELS.get(strategy_type, strategy_type)}]"


def _reliability_badge(grade: str, emoji: str = "", score: float = 0) -> str:
    """신뢰도 등급 배지 (한 줄)."""
    if not grade:
        return ""
    if not emoji:
        emoji = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴"}.get(grade, "⚪")
    return f"{emoji} 신뢰도 {grade}등급 ({score:.0f}점)"


def generate_buy_reasons(tech, info, flow, macro) -> list:
    reasons = []
    if tech.rsi <= 30:
        reasons.append(f"RSI {tech.rsi:.1f} 진입 (과매도 깊음)")
    if tech.bb_pctb <= 0.2:
        reasons.append("볼린저밴드 하단 터치")
    if flow.foreign_net_buy_days <= -3:
        reasons.append(
            f"외인 리스크오프 매도 {abs(flow.foreign_net_buy_days)}일차 -> 반등 임박"
        )
    if flow.institution_net_buy_days >= 2:
        reasons.append(f"기관 순매수 {flow.institution_net_buy_days}일 연속")
    if tech.macd_signal_cross == 1:
        reasons.append("MACD 골든크로스 (상승 전환)")
    if tech.golden_cross:
        reasons.append("50일/200일 EMA 골든크로스")
    if macro.regime == "risk_off":
        reasons.append("글로벌 리스크오프 -> 반등 기대")
    elif macro.regime == "risk_on":
        reasons.append("매크로 우호적 환경")
    if tech.mtf_aligned:
        reasons.append("주봉+일봉 상승 추세 일치")
    if not reasons:
        reasons.append("기술적 + 수급 종합 판단")
    return reasons[:5]


def generate_one_liner(tech, flow, macro) -> str:
    parts = []
    if flow.foreign_net_buy_days < 0:
        parts.append("외인 일시적 매도")
    if flow.institution_net_buy_days > 0:
        parts.append("기관 매집")
    if tech.rsi <= 35:
        parts.append("기술적 과매도")
    if macro.regime == "risk_on":
        parts.append("매크로 우호적")
    if len(parts) >= 2:
        return f'"{parts[0]} + {parts[1]} = 전형적 반등 패턴"'
    if parts:
        return f'"{parts[0]} 구간, 매수 기회"'
    return '"종합 점수 기반 매수 추천"'


# ---------------------------------------------------------------------------
# Public format functions
# ---------------------------------------------------------------------------

def format_welcome() -> str:
    return (
        f"🚀 {USER_NAME}, {APP_NAME}\n\n"
        "AI 투자 매니저 시스템\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👨‍💼 4명의 전담 매니저가 종목을 관리합니다\n"
        "  ⚡ 리버모어 (단타 1~3일)\n"
        "  🔥 오닐 (스윙 1~4주)\n"
        "  📊 린치 (포지션 1~6개월)\n"
        "  💎 버핏 (장기 6개월+)\n\n"
        "📋 매일 아침 7:30 자동 브리핑\n"
        "  → 보유종목 분석 + 매수 추천 + 오늘의 할 일\n\n"
        "🔰 빠른 시작 가이드\n"
        "  📊 분석 — 종목명 입력 → AI 즉시 분석\n"
        "  💰 잔고 — 보유종목 + 매니저 의견\n"
        "  ⭐ 즐겨찾기 — 75종목 매니저별 관리\n"
        "  📈 시황 — 🟢🟡🔴 신호등 시장 판단\n"
        "  💬 AI비서 — 투자 + 범용 질문\n\n"
        "💡 처음이라면 '온보딩'을 입력해보세요!\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "아래 메뉴 버튼을 눌러 시작하세요 👇"
    )


def format_buy_alert(
    name: str, ticker: str, score, tech, info, flow, macro,
    rank_pct: float = 5.0, strategy_type: str = "A",
    price_target=None, pattern_report=None,
) -> str:
    """종목 매수 알림 양식. price_target/pattern_report는 v9.4 엔진 결과."""
    price = info.current_price
    entry_2 = round(price * 0.97, 0)

    reasons = generate_buy_reasons(tech, info, flow, macro)
    reason_lines = "\n".join(f"\u2022 {r}" for r in reasons)
    stars, label = _confidence_stars(score.composite)
    tag = _strategy_tag(strategy_type)

    lines = [
        "\u2550" * 22,
        f"{USER_NAME}, 지금 {name} 사세요 \U0001f4b0 {tag}",
        "\u2550" * 22,
        "",
        reason_lines,
        "",
        f"{_won(price)}에 총 자금의 10% 매수",
        f"추가 하락 시 {_won(entry_2)}에 10% 더 매수",
    ]

    # 가격 목표: PriceTarget 있으면 동적, 없으면 기존 +3%/+7%
    if price_target:
        pt = price_target
        lines.append("")
        lines.append("\U0001f4c8 가격 목표 (실시간 계산)")
        if pt.resistance_1 > 0:
            r1_pct = (pt.resistance_1 - price) / price * 100
            lines.append(f"\u2022 1차 저항: {_won(pt.resistance_1)} ({r1_pct:+.1f}%) \u2192 절반 매도")
        if pt.resistance_2 > 0:
            r2_pct = (pt.resistance_2 - price) / price * 100
            lines.append(f"\u2022 2차 저항: {_won(pt.resistance_2)} ({r2_pct:+.1f}%) \u2192 30% 매도")
        if getattr(pt, "fair_value_per", 0) > 0:
            lines.append(f"\u2022 PER 적정가: {_won(pt.fair_value_per)}")
        lines.append(f"\u2022 나머지: 고점-3% 트레일링")

        lines.append("")
        lines.append("\U0001f6e1 리스크")
        if pt.support_1 > 0:
            s1_pct = (pt.support_1 - price) / price * 100
            lines.append(f"\u2022 1차 지지: {_won(pt.support_1)} ({s1_pct:+.1f}%)")
        stop_price = pt.support_2 if pt.support_2 > 0 else round(price * 0.95, 0)
        stop_pct = (stop_price - price) / price * 100
        lines.append(f"\u2022 손절: {_won(stop_price)} ({stop_pct:+.1f}%) \u2192 전량 매도")
        if pt.risk_reward_ratio > 0:
            lines.append(f"\u2022 위험보상비: {pt.risk_reward_ratio:.1f}배")
    else:
        target_1 = round(price * 1.03, 0)
        target_2 = round(price * 1.07, 0)
        stop = round(price * 0.95, 0)
        lines.append("")
        lines.append("\U0001f4c8 수익 목표")
        lines.append(f"\u2022 +3% {_won(target_1)} \u2192 절반 매도")
        lines.append(f"\u2022 +7% {_won(target_2)} \u2192 30% 매도")
        lines.append(f"\u2022 나머지: 고점-3% 트레일링")
        lines.append("")
        lines.append(f"\U0001f6d1 손절: {_won(stop)} (-5%) 오면 전량 매도")

    # 패턴 매칭 결과
    if pattern_report and getattr(pattern_report, "match_count", 0) > 0:
        pr = pattern_report
        lines.append("")
        lines.append("\U0001f4ca 과거 패턴")
        lines.append(
            f"유사패턴 {pr.match_count}건: "
            f"20일후 평균 {pr.avg_return_20d:+.1f}% "
            f"(상승 {pr.positive_pct:.0f}%)"
        )

    lines.append("")
    lines.append(f"확신도  {stars} ({label})")

    return "\n".join(lines)


def format_momentum_alert(
    name: str, ticker: str, tech, info,
    rs_rank: int = 0, rs_total: int = 1,
) -> str:
    price = info.current_price
    pullback = round(price * 0.975, 0)
    rs_pct = rs_rank / rs_total * 100 if rs_total > 0 else 50

    return (
        f"\u2550" * 22 + "\n"
        f"\U0001f680 모멘텀 매수!  {name}\n"
        f"\u2550" * 22 + "\n\n"
        f"골든크로스 발생 (50일 EMA > 200일 EMA)\n"
        f"상대강도 상위 {rs_pct:.0f}% ({rs_total}종목 중 {rs_rank}위)\n"
        f"거래량 평균 대비 {tech.volume_ratio:.1f}배\n\n"
        f"\U0001f4b0 매수\n"
        f"현재가  {_won(price)}\n"
        f"1차 매수  지금 (70%)\n"
        f"2차 매수  풀백 시 {_won(pullback)} (30%)\n\n"
        f"\U0001f4c8 청산 조건\n"
        f"50일 EMA 하향 이탈 시 전량 매도\n"
        f"트레일링 스탑  고점 -5%"
    )


def format_breakout_alert(name: str, ticker: str, tech, info) -> str:
    price = info.current_price
    stop = round(price * 0.98, 0)

    breakout_type = ""
    if tech.high_52w > 0 and price >= tech.high_52w * 0.98:
        breakout_type = f"52주 신고가 돌파 ({_won(tech.high_52w)})"
    elif tech.high_20d > 0 and price >= tech.high_20d * 0.98:
        breakout_type = f"20일 고점 돌파 ({_won(tech.high_20d)})"

    squeeze = ""
    if tech.bb_squeeze:
        squeeze = "\nBB 스퀴즈 후 돌파 -> 신뢰도 UP"

    return (
        f"\u2550" * 22 + "\n"
        f"\U0001f4a5 돌파 매수!  {name}\n"
        f"\u2550" * 22 + "\n\n"
        f"{breakout_type}\n"
        f"거래량 평균 대비 {tech.volume_ratio:.1f}배{squeeze}\n\n"
        f"\U0001f4b0 매수\n"
        f"현재가  {_won(price)}\n"
        f"돌파 확인 후 즉시 매수 (50%)\n"
        f"리테스트 시 추가 매수 (50%)\n\n"
        f"\U0001f6d1 손절: {_won(stop)} (돌파가 -2%)"
    )


def format_watch_alert(
    name: str, ticker: str, score, tech, info,
    strategy_type: str = "A", price_target=None,
) -> str:
    """관심 종목 알림 양식. price_target 있으면 지지선 기반 매수 조건 추가."""
    price = info.current_price
    tag = _strategy_tag(strategy_type)

    conditions = []
    if tech.rsi > 30:
        conditions.append(f"RSI 30 이하 진입 시 (현재: {tech.rsi:.1f})")

    # 지지선 기반 매수 조건 (PriceTarget 있으면 활용)
    if price_target and price_target.support_1 > 0:
        conditions.append(f"{_won(price_target.support_1)} 지지선 근접 시")
    else:
        target_price = round(price * 0.97, 0)
        conditions.append(f"{_won(target_price)} 이하 도달 시")
    conditions.append("외인 매도 전환(순매수) 시")
    cond_lines = "\n".join(f"\u2022 {c}" for c in conditions)

    reasons = []
    if tech.rsi <= 40:
        reasons.append(f"RSI {tech.rsi:.1f} (과매도 근접, 아직 미진입)")
    if tech.bb_pctb <= 0.35:
        reasons.append(f"볼린저밴드 하단 근접 (%B: {tech.bb_pctb:.2f})")
    if not reasons:
        reasons.append("매수 조건 근접 중")
    reason_lines = "\n".join(f"\u2022 {r}" for r in reasons)

    lines = [
        "\u2550" * 22,
        f"\U0001f7e1 {USER_NAME}, {name} 주시하세요 {tag}",
        "\u2550" * 22,
        f"\U0001f4ca 점수: {score.composite:.1f}/100",
        "",
        "아직 매수 타이밍은 아닙니다",
        reason_lines,
    ]

    # 가격 목표 요약 (있으면)
    if price_target:
        pt = price_target
        target_parts = []
        if pt.resistance_1 > 0:
            r1_pct = (pt.resistance_1 - price) / price * 100
            target_parts.append(f"저항 {_won(pt.resistance_1)}({r1_pct:+.1f}%)")
        if pt.support_1 > 0:
            s1_pct = (pt.support_1 - price) / price * 100
            target_parts.append(f"지지 {_won(pt.support_1)}({s1_pct:+.1f}%)")
        if target_parts:
            lines.append("")
            lines.append(f"\U0001f4c8 {' | '.join(target_parts)}")

    lines.append("")
    lines.append("\U0001f3af 이 조건 되면 매수 알림 보내드릴게요:")
    lines.append(cond_lines)

    return "\n".join(lines)


def format_sell_alert_profit(
    name: str, holding: dict, current_price: float,
    reliability_grade: str = "", reliability_emoji: str = "",
    reliability_score: float = 0,
) -> str:
    buy_price = holding["buy_price"]
    pnl_pct = (current_price - buy_price) / buy_price * 100

    badge = _reliability_badge(reliability_grade, reliability_emoji, reliability_score)
    badge_line = f"\n{badge}" if badge else ""

    return (
        f"\u2550" * 22 + "\n"
        f"{USER_NAME}, 지금 {name} 파세요 \U0001f4c8\n"
        f"\u2550" * 22 + "\n\n"
        f"현재 {pnl_pct:+.1f}% 수익 중\n"
        f"{_won(buy_price)} -> {_won(current_price)}\n"
        f"1차 익절 구간 도달\n\n"
        f"절반(50%) 매도하세요\n"
        f"나머지는 트레일링 스탑 작동 중{badge_line}"
    )


def format_sell_alert_stop(
    name: str, holding: dict, current_price: float,
    reliability_grade: str = "", reliability_emoji: str = "",
    reliability_score: float = 0, holding_suppressed: bool = False,
) -> str:
    buy_price = holding["buy_price"]
    pnl_pct = (current_price - buy_price) / buy_price * 100

    # v6.5: 장기보유 보호 — 매도 억제 시 보호 메시지
    if holding_suppressed:
        holding_type = holding.get("holding_type", "")
        horizon_kr = {
            "scalp": "초단타", "swing": "스윙",
            "position": "포지션", "long_term": "장기",
            "tenbagger": "텐배거",
        }.get(holding_type, holding_type)
        return (
            f"\u2550" * 22 + "\n"
            f"🛡 {USER_NAME}, {name} 보호 중\n"
            f"\u2550" * 22 + "\n\n"
            f"{pnl_pct:+.1f}% ({_won(buy_price)} -> {_won(current_price)})\n"
            f"{horizon_kr} 보유 — 단기 매도 신호 억제됨\n"
            f"펀더멘탈 기반으로 보유 유지하세요"
        )

    badge = _reliability_badge(reliability_grade, reliability_emoji, reliability_score)
    badge_line = f"\n{badge}" if badge else ""

    return (
        f"\u2550" * 22 + "\n"
        f"{USER_NAME}, {name} 손절하세요 \U0001f6d1\n"
        f"\u2550" * 22 + "\n\n"
        f"{pnl_pct:+.1f}% 도달 ({_won(buy_price)} -> {_won(current_price)})\n"
        f"더 빠질 수 있습니다\n"
        f"지금 전량 매도하세요{badge_line}"
    )


def format_recommendations(results: list) -> str:
    now = _now_kst()
    lines = [f"\U0001f4ca 오늘의 매수 후보 ({now})\n"]

    buy_items = []
    watch_items = []
    hold_items = []
    medals = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}

    for item in results:
        # v6.5: 8번째 항목으로 reliability_grade, v9.4: 9번째 debate_summary
        # v9.6.0: 10번째 atr_pct 지원
        reliability_grade = ""
        debate_summary = ""
        atr_pct = 0.0
        if len(item) >= 10:
            rank, name, ticker, score_val, signal, strat, price, reliability_grade, debate_summary, atr_pct = item[:10]
        elif len(item) >= 9:
            rank, name, ticker, score_val, signal, strat, price, reliability_grade, debate_summary = item[:9]
        elif len(item) >= 8:
            rank, name, ticker, score_val, signal, strat, price, reliability_grade = item[:8]
        elif len(item) >= 7:
            rank, name, ticker, score_val, signal, strat, price = item[:7]
        elif len(item) >= 6:
            rank, name, ticker, score_val, signal, strat = item[:6]
            price = 0
        else:
            rank, name, ticker, score_val, signal = item[:5]
            strat = "A"
            price = 0
        medal = medals.get(rank, f"{rank}.")
        tag = _strategy_tag(strat)
        price_str = f" {_won(price)}" if price and price > 0 else ""
        # v6.5: 신뢰도 등급 배지 (있으면 표시)
        grade_badge = ""
        if reliability_grade:
            grade_emoji = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🔴"}.get(reliability_grade, "")
            grade_badge = f" {grade_emoji}{reliability_grade}"
        # v9.4: AI 토론 결과 뱃지
        debate_badge = ""
        if debate_summary:
            debate_badge = f" \U0001f399{debate_summary}"
        line = f"{medal} {name}{price_str} {score_val:.1f}점 {tag}{grade_badge}{debate_badge}"
        # v9.6.0: 매수 추천 종목에 ATR 손절/목표 한 줄 표시
        if signal == "BUY" and atr_pct and atr_pct > 0 and price and price > 0:
            try:
                from kstock.core.position_sizer import compute_atr_stops
                stops = compute_atr_stops(float(atr_pct), "swing", float(price))
                line += (
                    f"\n   🔴{_won(stops['stop_price'])}"
                    f" → 🟡{_won(stops['target_1_price'])}"
                    f" → 🟠{_won(stops['target_2_price'])}"
                )
            except Exception:
                pass
        if signal == "BUY":
            buy_items.append(line)
        elif signal == "WATCH":
            watch_items.append(line)
        else:
            hold_items.append(line)

    if buy_items:
        lines.append("\U0001f7e2 매수 추천")
        lines.extend(buy_items)
        lines.append("")
    if watch_items:
        lines.append("\U0001f7e1 관심 (조건 근접)")
        lines.extend(watch_items)
        lines.append("")
    if hold_items:
        lines.append("\u26aa 대기")
        lines.extend(hold_items)

    return "\n".join(lines)


def format_stock_detail(
    name, ticker, score, tech, info, flow, macro,
    rank_pct=5.0, strategy_type="A",
    confidence_stars="", confidence_label="",
    price_target=None, pattern_report=None, debate=None,
):
    """종목 상세보기 — 가격목표, 패턴, AI 토론, 이동평균 정보 포함."""
    price = info.current_price
    stars, label = _confidence_stars(score.composite)
    tag = _strategy_tag(strategy_type)

    reasons = generate_buy_reasons(tech, info, flow, macro)
    reason_lines = "\n".join(f"\u2022 {r}" for r in reasons)

    lines = [
        "\u2550" * 22,
        f"\U0001f4cb {name} 종목 상세 {tag}",
        "\u2550" * 22,
        f"현재가: {_won(price)} | 점수: {score.composite:.1f}/100",
        "",
        reason_lines,
    ]

    # v9.6.0: ATR 기반 동적 손절/익절 안내
    _atr = getattr(tech, "atr_pct", 0.0) if tech else 0.0
    if _atr > 0 and price and price > 0:
        try:
            from kstock.core.position_sizer import compute_atr_stops
            stops = compute_atr_stops(_atr, "swing", price)
            lines.append("")
            lines.append(f"📊 ATR {_atr:.1f}% 기준 가이드")
            lines.append(
                f"  🔴 손절: {_won(stops['stop_price'])} ({stops['stop_pct']*100:+.1f}%)"
            )
            lines.append(
                f"  🟡 1차 목표: {_won(stops['target_1_price'])} ({stops['target_1_pct']*100:+.1f}%)"
            )
            lines.append(
                f"  🟠 2차 목표: {_won(stops['target_2_price'])} ({stops['target_2_pct']*100:+.1f}%)"
            )
        except Exception:
            pass

    # 이동평균 위치
    sma_parts = []
    if hasattr(tech, "ema_20") and tech.ema_20 > 0:
        pos = "위" if price > tech.ema_20 else "아래"
        sma_parts.append(f"20일선 {pos}")
    if hasattr(tech, "ema_50") and tech.ema_50 > 0:
        pos = "위" if price > tech.ema_50 else "아래"
        sma_parts.append(f"50일선 {pos}")
    if sma_parts:
        lines.append("")
        lines.append(f"\U0001f4c9 이평선: {' / '.join(sma_parts)}")

    # 가격 목표
    if price_target:
        pt = price_target
        lines.append("")
        lines.append("\U0001f4c8 가격 목표")
        if pt.resistance_1 > 0:
            r1_pct = (pt.resistance_1 - price) / price * 100
            lines.append(f"\u2022 1차 저항: {_won(pt.resistance_1)} ({r1_pct:+.1f}%)")
        if pt.resistance_2 > 0:
            r2_pct = (pt.resistance_2 - price) / price * 100
            lines.append(f"\u2022 2차 저항: {_won(pt.resistance_2)} ({r2_pct:+.1f}%)")
        if pt.support_1 > 0:
            s1_pct = (pt.support_1 - price) / price * 100
            lines.append(f"\u2022 1차 지지: {_won(pt.support_1)} ({s1_pct:+.1f}%)")
        if getattr(pt, "fair_value_per", 0) > 0:
            lines.append(f"\u2022 PER 적정가: {_won(pt.fair_value_per)}")
        if pt.risk_reward_ratio > 0:
            lines.append(f"\u2022 위험보상비: {pt.risk_reward_ratio:.1f}배")

    # 패턴 매칭
    if pattern_report and getattr(pattern_report, "match_count", 0) > 0:
        pr = pattern_report
        lines.append("")
        lines.append("\U0001f4ca 과거 패턴")
        lines.append(
            f"유사패턴 {pr.match_count}건: "
            f"5일후 {pr.avg_return_5d:+.1f}% / "
            f"20일후 {pr.avg_return_20d:+.1f}% "
            f"(상승 {pr.positive_pct:.0f}%)"
        )

    # AI 토론 verdict
    if debate:
        _v_emoji = {
            "STRONG_BUY": "\U0001f7e2\U0001f7e2", "BUY": "\U0001f7e2",
            "HOLD": "\U0001f7e1", "SELL": "\U0001f534",
            "STRONG_SELL": "\U0001f534\U0001f534",
        }
        v = debate.get("verdict", "")
        conf = debate.get("confidence", 0)
        cons = debate.get("consensus_level", "")
        ve = _v_emoji.get(v, "\u2753")
        diss = debate.get("dissenting_view", "")
        lines.append("")
        lines.append(f"\U0001f399 AI 토론: {ve}{v} ({cons} {conf:.0f}%)")
        if diss:
            lines.append(f"  반대의견: {diss[:60]}")

    lines.append("")
    lines.append(f"확신도  {stars} ({label})")

    return "\n".join(lines)


def _trend_arrow(change_pct: float) -> str:
    """Return trend arrow based on change percentage."""
    if change_pct > 1.0:
        return "\u2b06\ufe0f"  # ⬆️
    elif change_pct > 0.1:
        return "\u2197\ufe0f"  # ↗️
    elif change_pct < -1.0:
        return "\u2b07\ufe0f"  # ⬇️
    elif change_pct < -0.1:
        return "\u2198\ufe0f"  # ↘️
    return "\u27a1\ufe0f"  # ➡️


def _fear_greed_bar(score: float) -> str:
    """Visual bar for Fear & Greed score (0-100)."""
    filled = int(score / 10)
    bar = "\u2588" * filled + "\u2591" * (10 - filled)
    return f"[{bar}]"


def format_market_status(
    macro, regime_mode: dict | None = None,
    sector_text: str = "", fx_message: str = "",
    alert_mode: str = "normal",
) -> str:
    regime_map = {
        "risk_on": "\U0001f7e2 적극 공격",
        "neutral": "\U0001f7e1 보수적 공격",
        "risk_off": "\U0001f534 방어적",
    }
    regime_text = regime_map.get(macro.regime, "\u26aa 판단 중")

    try:
        from kstock.core.risk_config import get_risk_thresholds
        vix_status = get_risk_thresholds().vix.status_label(macro.vix)
    except Exception:
        vix_status = "안정" if macro.vix < 20 else "주의" if macro.vix < 25 else "공포"
    krw_status = "강세" if macro.usdkrw_change_pct < -0.3 else "보합" if abs(macro.usdkrw_change_pct) <= 0.3 else "약세"
    btc_status = "강세" if macro.btc_change_pct > 1 else "보합" if abs(macro.btc_change_pct) <= 1 else "약세"

    gold_status = ""
    if macro.gold_price > 0:
        gold_status = "강세" if macro.gold_change_pct > 0.5 else "약세" if macro.gold_change_pct < -0.5 else "보합"

    inst_text = _억(macro.institution_total) if macro.institution_total else "데이터 없음"
    foreign_text = _억(macro.foreign_total) if macro.foreign_total else "데이터 없음"

    # v3.5: Header with timestamp
    fetched_at = getattr(macro, "fetched_at", None)
    is_cached = getattr(macro, "is_cached", False)

    if fetched_at:
        kst_time = fetched_at + timedelta(hours=9)
        time_str = kst_time.strftime("%m/%d %H:%M")
        cache_tag = " (캐시)" if is_cached else " (실시간)"
    else:
        time_str = "알 수 없음"
        cache_tag = ""

    # Trend arrows
    spx_arrow = _trend_arrow(macro.spx_change_pct)
    ndx_arrow = _trend_arrow(macro.nasdaq_change_pct)
    vix_arrow = _trend_arrow(macro.vix_change_pct)
    krw_arrow = _trend_arrow(macro.usdkrw_change_pct)
    btc_arrow = _trend_arrow(macro.btc_change_pct)

    lines = [
        f"\U0001f30d 시장 현황: {regime_text}",
        f"\U0001f552 {time_str}{cache_tag}",
    ]
    if alert_mode == "wartime":
        lines.append("\U0001f534 전시 경계 모드 \u2014 손절 강화, 신규 매수 자제")
    elif alert_mode == "elevated":
        lines.append("\U0001f7e0 경계 모드 \u2014 변동성 확대, 분할 매수 권장")
    lines.extend([
        "\u2500" * 25,
        "",
        f"{spx_arrow} S&P500: {macro.spx_change_pct:+.2f}%",
        f"{ndx_arrow} 나스닥: {macro.nasdaq_change_pct:+.2f}%",
    ])

    # v9.0: 선물지수 (장 마감 후에도 방향성 파악)
    es = getattr(macro, "es_futures", 0)
    nq = getattr(macro, "nq_futures", 0)
    if es > 0:
        es_chg = getattr(macro, "es_futures_change_pct", 0)
        lines.append(f"{_trend_arrow(es_chg)} S&P선물: {es:,.0f} ({es_chg:+.2f}%)")
    if nq > 0:
        nq_chg = getattr(macro, "nq_futures_change_pct", 0)
        lines.append(f"{_trend_arrow(nq_chg)} NQ선물: {nq:,.0f} ({nq_chg:+.2f}%)")

    lines.append(
        f"{vix_arrow} VIX: {macro.vix:.1f} ({macro.vix_change_pct:+.1f}%) {vix_status}",
    )

    # v3.5: US10Y / DXY
    us10y_change = getattr(macro, "us10y_change_pct", 0)
    dxy_change = getattr(macro, "dxy_change_pct", 0)
    us10y_arrow = _trend_arrow(us10y_change)
    dxy_arrow = _trend_arrow(dxy_change)
    lines.append(f"{us10y_arrow} 미국10년물: {macro.us10y:.2f}% ({us10y_change:+.1f}%)")
    lines.append(f"{dxy_arrow} 달러인덱스: {macro.dxy:.1f} ({dxy_change:+.1f}%)")

    lines.extend([
        "",
        f"{krw_arrow} \U0001f4b1 환율: {macro.usdkrw:,.0f}원 ({macro.usdkrw_change_pct:+.1f}%) {krw_status}",
        f"{btc_arrow} \U0001fa99 BTC: ${macro.btc_price:,.0f} ({macro.btc_change_pct:+.1f}%) {btc_status}",
    ])

    if macro.gold_price > 0:
        gold_arrow = _trend_arrow(macro.gold_change_pct)
        lines.append(f"{gold_arrow} \U0001f947 금: ${macro.gold_price:,.0f} ({macro.gold_change_pct:+.1f}%) {gold_status}")

    # v3.5: Fear & Greed
    fg_score = getattr(macro, "fear_greed_score", 50)
    fg_label = getattr(macro, "fear_greed_label", "중립")
    fg_bar = _fear_greed_bar(fg_score)
    fg_emoji = "\U0001f631" if fg_score < 30 else "\U0001f60e" if fg_score > 70 else "\U0001f610"
    lines.extend([
        "",
        f"{fg_emoji} 탐욕/공포: {fg_score:.0f}점 ({fg_label})",
        f"   {fg_bar}",
        "   공포 0 ──── 50 ──── 100 탐욕",
    ])

    lines.extend([
        "",
        f"\U0001f3e2 기관: {inst_text}",
        f"\U0001f464 외인: {foreign_text}",
    ])

    # v9.0: 선물만기 경고
    from kstock.bot.context_builder import get_futures_expiry_warning
    expiry_warn = get_futures_expiry_warning()
    if expiry_warn:
        lines.extend(["", expiry_warn])

    # v9.0: 변동성 레짐
    kr_vol = getattr(macro, "korean_vol", 0)
    vol_regime = getattr(macro, "vol_regime", "")
    if vol_regime:
        try:
            from kstock.signal.volatility_regime import (
                classify_volatility_regime,
                format_volatility_regime,
            )
            vr = classify_volatility_regime(macro.vix, kr_vol)
            lines.extend(["", format_volatility_regime(vr)])
        except Exception:
            pass

    # FX strategy
    if fx_message:
        lines.extend(["", fx_message])

    # Sector strength
    if sector_text:
        lines.extend(["", sector_text])

    # Regime mode
    if regime_mode:
        mode_emoji = regime_mode.get("emoji", "")
        mode_label = regime_mode.get("label", "")
        mode_msg = regime_mode.get("message", "")
        alloc = regime_mode.get("allocations", {})
        lines.extend([
            "",
            "\u2500" * 25,
            f"현재 모드: {mode_emoji} {mode_label}",
            f"\u2192 {mode_msg}",
        ])
        if alloc:
            label_map = {
                "A": "반등", "B": "ETF", "C": "장기",
                "D": "섹터", "E": "글로벌", "F": "모멘텀",
                "G": "돌파", "cash": "현금",
            }
            alloc_parts = [f"{label_map.get(k, k)}({v}%)" for k, v in alloc.items() if v > 0]
            lines.append(f"\u2192 추천: {' + '.join(alloc_parts)}")

    return "\n".join(lines)


def format_portfolio(holdings: list, db=None) -> str:
    """포트폴리오 표시. db가 있으면 AI 토론 verdict를 함께 표시."""
    if not holdings:
        return (
            f"\U0001f4bc {USER_NAME}의 포트폴리오\n\n"
            "보유 종목이 없습니다.\n"
            "\U0001f4ca 오늘의 추천종목에서 매수해보세요!"
        )

    lines = [f"\U0001f4bc {USER_NAME}의 포트폴리오\n"]
    total_pnl = 0.0
    count = 0

    _verdict_emoji = {
        "STRONG_BUY": "\U0001f7e2\U0001f7e2",
        "BUY": "\U0001f7e2",
        "HOLD": "\U0001f7e1",
        "SELL": "\U0001f534",
        "STRONG_SELL": "\U0001f534\U0001f534",
    }

    for h in holdings:
        buy_price = h["buy_price"]
        current = h.get("current_price") or buy_price
        pnl_pct = (current - buy_price) / buy_price * 100 if buy_price > 0 else 0
        total_pnl += pnl_pct
        count += 1

        emoji = "\U0001f7e2" if pnl_pct > 0 else "\U0001f534" if pnl_pct < -3 else "\U0001f7e1"
        stop = h.get("stop_price") or buy_price * 0.95
        stop_pct = (stop - current) / current * 100 if current > 0 else 0

        lines.append(
            f"{h['name']}: {pnl_pct:+.1f}% {emoji} "
            f"{_won(buy_price)}\u2192{_won(current)}"
        )

        # AI 토론 verdict 표시
        debate = None
        if db:
            try:
                debate = db.get_latest_debate(h.get("ticker", ""))
            except Exception:
                pass
        if debate:
            v = debate.get("verdict", "")
            conf = debate.get("confidence", 0)
            cons = debate.get("consensus_level", "")
            v_emoji = _verdict_emoji.get(v, "\u2753")
            pt = debate.get("price_target", 0)
            line = f"  \U0001f399{v_emoji}{v}({cons} {conf:.0f}%)"
            if pt and current > 0:
                upside = (pt - current) / current * 100
                line += f" | 목표 {_won(pt)} ({upside:+.1f}%)"
            lines.append(line)

        if pnl_pct >= 3:
            lines.append("  \u2192 1차 익절 구간 (50% 매도 권장)")
        elif pnl_pct < 0:
            lines.append(f"  \u2192 보유 유지 (손절까지 {stop_pct:+.1f}% 여유)")
        else:
            lines.append("  \u2192 보유 유지")
        lines.append("")

    avg_pnl = total_pnl / count if count > 0 else 0
    lines.append(f"총 평균 수익: {avg_pnl:+.1f}%")
    return "\n".join(lines)


def format_reco_performance(active: list, completed: list, watch: list, stats: dict) -> str:
    lines = [
        f"\U0001f4c8 {USER_NAME}의 추천 성과",
        "\u2500" * 25,
        "",
    ]

    lines.append(f"\U0001f7e2 진행 중 ({stats.get('active', 0)}건)")
    if active:
        for r in active:
            pnl = r.get("pnl_pct", 0)
            emoji = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < -3 else "\U0001f7e1"
            strat = r.get("strategy_type", "A")
            tag = _strategy_tag(strat)
            rec_date = (r.get("rec_date") or "")[:5]
            lines.append(
                f"  {r['name']} {tag} {rec_date} "
                f"{_won(r['rec_price'])}->{_won(r.get('current_price') or r['rec_price'])} "
                f"({pnl:+.1f}%) {emoji}"
            )
    else:
        lines.append("  진행 중인 추천이 없습니다.")
    lines.append("")

    lines.append(f"\U0001f7e1 관심 종목 ({stats.get('watch', 0)}건)")
    if watch:
        for r in watch:
            tag = _strategy_tag(r.get("strategy_type", "A"))
            lines.append(f"  {r['name']} {tag} 목표: {_won(r['rec_price'])}")
    else:
        lines.append("  관심 종목이 없습니다.")
    lines.append("")

    lines.append(f"\u2705 완료 ({stats.get('profit', 0) + stats.get('stop', 0)}건)")
    if completed:
        for r in completed[:5]:
            status_emoji = "\U0001f7e2" if r.get("status") == "profit" else "\U0001f534"
            status_text = "익절" if r.get("status") == "profit" else "손절"
            tag = _strategy_tag(r.get("strategy_type", "A"))
            lines.append(
                f"  {status_emoji} {r['name']} {tag} "
                f"{r.get('pnl_pct', 0):+.1f}% ({status_text})"
            )
        if len(completed) > 5:
            lines.append(f"  ... 외 {len(completed) - 5}건")
    else:
        lines.append("  완료된 추천이 없습니다.")
    lines.append("")

    lines.append("\u2500" * 25)
    lines.append("\U0001f4ca 전체 성과")
    total = stats.get("total", 0)
    profit_cnt = stats.get("profit", 0)
    stop_cnt = stats.get("stop", 0)
    closed = profit_cnt + stop_cnt
    win_rate = (profit_cnt / closed * 100) if closed > 0 else 0
    avg_closed = stats.get("avg_closed_pnl", 0)
    avg_active = stats.get("avg_active_pnl", 0)
    lines.append(f"  총 추천: {total}건")
    lines.append(f"  승률: {win_rate:.0f}% ({profit_cnt}승 {stop_cnt}패)")
    lines.append(f"  완료 평균: {avg_closed:+.1f}%")
    lines.append(f"  진행중 평균: {avg_active:+.1f}%")

    # ML accuracy section (v3.0)
    ml_acc = stats.get("ml_accuracy")
    if ml_acc is not None:
        lines.append("")
        lines.append("\u2500" * 25)
        lines.append("\U0001f916 ML 예측 성과")
        lines.append(f"  방향 적중률: {ml_acc:.0f}%")
        ml_top = stats.get("ml_top5_accuracy")
        if ml_top is not None:
            lines.append(f"  상위5 적중률: {ml_top:.0f}%")

    # Sentiment section (v3.0)
    sent_acc = stats.get("sentiment_accuracy")
    if sent_acc is not None:
        lines.append("")
        lines.append("\u2500" * 25)
        lines.append("\U0001f4f0 감성분석 효과")
        lines.append(f"  감성 반영 정확도: {sent_acc:.0f}%")
        sent_boost = stats.get("sentiment_pnl_boost")
        if sent_boost is not None:
            lines.append(f"  수익 기여: {sent_boost:+.1f}%p")

    lines.append(f"\n\U0001f551 {_now_kst()}")

    return "\n".join(lines)


def format_strategy_list(strategy_type: str, recs: list) -> str:
    meta = {
        "A": ("\U0001f525", "단기 반등", "3~10일", "+3~7%"),
        "B": ("\u26a1", "ETF 레버리지", "1~3일", "+2~5%"),
        "C": ("\U0001f3e6", "장기 우량주", "6개월~1년", "배당+시세"),
        "D": ("\U0001f504", "섹터 로테이션", "1~3개월", "시장 초과"),
        "E": ("\U0001f30e", "글로벌 분산", "장기", "분산+시세"),
        "F": ("\U0001f680", "모멘텀", "2~8주", "+5~10%"),
        "G": ("\U0001f4a5", "돌파", "3~10일", "+3~7%"),
    }
    emoji, name, period, target = meta.get(strategy_type, ("", strategy_type, "", ""))

    lines = [
        f"{emoji} 전략 {strategy_type}: {name}",
        f"기간: {period} | 목표: {target}",
        "\u2500" * 25,
        "",
    ]

    if not recs:
        lines.append("현재 해당 전략의 추천 종목이 없습니다.")
    else:
        for r in recs:
            pnl = r.get("pnl_pct", 0)
            emoji_pnl = "\U0001f7e2" if pnl > 0 else "\U0001f534" if pnl < -3 else "\U0001f7e1"
            status_text = {
                "active": "진행중", "watch": "관심",
                "profit": "익절", "stop": "손절",
            }.get(r.get("status", ""), r.get("status", ""))
            lines.append(f"{emoji_pnl} {r['name']} ({status_text})")
            lines.append(
                f"  {_won(r['rec_price'])} -> "
                f"{_won(r.get('current_price') or r['rec_price'])} "
                f"({pnl:+.1f}%)"
            )
            lines.append("")

    lines.append(f"\U0001f551 {_now_kst()}")
    return "\n".join(lines)


def format_long_term_detail(name: str, lt_score, info_dict: dict) -> str:
    lines = [
        f"\U0001f3e6 장기 투자 추천",
        f"{name} {lt_score.total:.0f}점 (Grade: {lt_score.grade})",
        "\u2500" * 25,
        "",
        f"\U0001f4b0 배당수익률: {info_dict.get('dividend_yield', 0):.1f}% ({lt_score.dividend:.0f}점)",
        f"\U0001f4d6 PBR: {info_dict.get('pbr', 0):.2f} ({lt_score.pbr:.0f}점)",
        f"\U0001f4c8 ROE: {info_dict.get('roe', 0):.1f}% ({lt_score.roe:.0f}점)",
        f"\U0001f3e6 부채비율: {info_dict.get('debt_ratio', 0):.0f}% ({lt_score.debt:.0f}점)",
        f"\U0001f4b5 FCF: ({lt_score.fcf:.0f}점)",
        f"\U0001f3ed 업종: ({lt_score.sector:.0f}점)",
        "",
        f"\u2192 {lt_score.monthly_recommendation}",
    ]
    return "\n".join(lines)


def format_claude_briefing(briefing_text: str) -> str:
    return (
        f"\u2600\ufe0f {USER_NAME}, 오늘의 AI 시장 브리핑\n"
        "\u2500" * 25 + "\n\n"
        f"{briefing_text}\n\n"
        "\u2500" * 25 + "\n"
        f"\U0001f551 {_now_kst()}\n"
        "\U0001f916 Powered by Claude"
    )


def format_system_status(last_runs: list, job_infos: list = None) -> str:
    lines = ["\u2699\ufe0f 시스템 상태\n", "\u2500" * 25]
    if job_infos:
        for j in job_infos:
            lines.append(f"\U0001f4cc {j.get('name', 'N/A')}")
            lines.append(f"   다음 실행: {j.get('next_run', 'N/A')}")
        lines.append("")
    if last_runs:
        lines.append("최근 실행:")
        for run in last_runs:
            icon = "\u2705" if run.get("status") == "success" else "\u274c"
            ended = run.get("ended_at", "N/A")
            if len(ended) > 16:
                ended = ended[:16]
            lines.append(f"  {icon} {run.get('job_name', 'N/A')} @ {ended}")
    else:
        lines.append("아직 실행된 작업이 없습니다.")
    lines.extend(["", "\u2500" * 25, f"\U0001f551 {_now_kst()}", APP_NAME])
    return "\n".join(lines)


def format_help() -> str:
    return (
        f"❓ {USER_NAME}, {APP_NAME} 도움말\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 분석 — 종목명 입력 → AI 즉시 분석\n"
        "📈 시황 — 🟢🟡🔴 신호등 시장 판단\n"
        "💰 잔고 — 보유종목 + 매니저 의견\n"
        "⭐ 즐겨찾기 — 75종목 매니저별 관리\n"
        "💬 AI비서 — 뭐든 물어보세요\n"
        "📋 리포트 — 증권사 리포트 + 컨센서스\n"
        "🤖 에이전트 — 4매니저 동시 분석\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 자동 일과표\n"
        "  06:30 📺 YouTube 학습 (1/5)\n"
        "  07:00 🇺🇸 미국 프리마켓 브리핑\n"
        "  07:10 🛢 유가 분석 + 🌍 크로스마켓\n"
        "  07:30 ☀️ 모닝 브리핑\n"
        "  08:00 📺 YouTube (2/5) + 📰 칼럼 수집\n"
        "  08:20 📋 증권사 리포트\n"
        "  09:00~ 장중 실시간 모니터링\n"
        "  12:00 📺 YouTube (3/5)\n"
        "  16:00 📊 장마감 PDF + 수급/공매도 수집\n"
        "  17:00 📺 YouTube (4/5) + 📰 칼럼\n"
        "  21:00 📺 YouTube (5/5) + 자가진단\n"
        "  21:30 📊 일일 학습 합성\n"
        "  22:00 🔬 Tier2 심화 + ML 점진학습\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👨‍💼 4인 투자 매니저\n"
        "  ⚡ 리버모어 — 단타 (손절-3%, 익절+5%)\n"
        "  🔥 오닐 — 스윙 (손절-7%, 익절+10%)\n"
        "  📊 린치 — 포지션 (손절-12%, 익절+20%)\n"
        "  💎 버핏 — 장기 (손절-20%, 익절+30%)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 AI 학습 ({DISPLAY_VERSION})\n"
        "  📺 YouTube 24채널 × 5회/일\n"
        "  ⚡ Tier1: Gemini Flash 벌크 스크리닝\n"
        "  🔬 Tier2: Claude Haiku 심화 분석\n"
        "  👤 19명 애널리스트 자동 추적\n"
        "  📰 네이버 칼럼/투자전략 수집\n"
        "  🧠 ML 앙상블 (LGB+XGB+LSTM, 58피처)\n"
        "  /learning — 학습 현황 대시보드\n\n"
        "💡 '온보딩' → 기능 체험 투어\n"
        "⚙️ 더보기 → 고급 기능\n\n"
        f"{SYSTEM_NAME}"
    )


def format_alerts_summary(alerts: list) -> str:
    if not alerts:
        return (
            f"\U0001f514 {USER_NAME}의 알림\n\n"
            "최근 알림이 없습니다.\n"
            "조건이 충족되면 자동으로 알림이 옵니다! \U0001f514"
        )

    type_emoji = {
        "buy": "\U0001f7e2", "watch": "\U0001f7e1",
        "sell": "\U0001f534", "stop": "\U0001f534",
        "momentum": "\U0001f680", "breakout": "\U0001f4a5",
    }
    lines = [f"\U0001f514 {USER_NAME}의 최근 알림\n"]

    for alert in alerts[:10]:
        emoji = type_emoji.get(alert.get("alert_type", ""), "\u26aa")
        msg = alert.get("message", "")[:60]
        ts = alert.get("created_at", "")[:16]
        lines.append(f"{emoji} {msg}")
        lines.append(f"   {ts}")
        lines.append("")

    return "\n".join(lines)


def format_trade_record(
    name: str, action: str, price: float, pnl_pct: float = 0,
    atr_pct: float = 0.0, holding_type: str = "swing",
    composite_score: float = 0.0,
) -> str:
    """Format trade record confirmation message.

    v9.6.0: ATR 기반 손절/익절 + 확신도 + 분할매수 안내 추가.
    """
    if action == "buy":
        base = f"{name} {_won(price)} 매수 기록했습니다 \U0001f44d"
        extras = []
        # v9.6.0: ATR 기반 동적 손절/익절
        if atr_pct > 0:
            try:
                from kstock.core.position_sizer import compute_atr_stops, plan_split_entry
                stops = compute_atr_stops(atr_pct, holding_type, price)
                extras.append(
                    f"\n📊 ATR {atr_pct:.1f}% 기반 자동 설정\n"
                    f"   🔴 손절: {_won(stops['stop_price'])} ({stops['stop_pct']*100:+.1f}%)\n"
                    f"   🟡 1차 목표: {_won(stops['target_1_price'])} ({stops['target_1_pct']*100:+.1f}%)\n"
                    f"   🟠 2차 목표: {_won(stops['target_2_price'])} ({stops['target_2_pct']*100:+.1f}%)"
                )
                # 분할매수 안내 (스캘핑 제외)
                if holding_type not in ("scalp",):
                    split = plan_split_entry(
                        ticker="", name=name, current_price=price,
                        atr_pct=atr_pct, total_shares=100,
                        holding_type=holding_type,
                    )
                    if len(split.entries) > 1:
                        extras.append(
                            f"\n📐 분할매수 추천\n"
                            f"   1차: 50% 즉시 ({_won(price)})\n"
                            f"   2차: 30% 눌림 ({_won(split.entries[1]['price'])})\n"
                            f"   3차: 20% 돌파확인 ({_won(split.entries[2]['price'])})"
                        )
            except Exception:
                pass
        # 확신도 표시
        if composite_score >= 130:
            extras.append("\n🔥 확신도: ★★★★★ 매우 높음")
        elif composite_score >= 110:
            extras.append("\n⭐ 확신도: ★★★★ 높음")
        elif composite_score >= 90:
            extras.append("\n✨ 확신도: ★★★ 보통")

        if extras:
            return base + "".join(extras)
        return base + " 익절/손절 알림 켜졌어요"
    elif action == "sell":
        return f"{name} {_won(price)} 매도 기록 ({pnl_pct:+.1f}%) \U0001f3af"
    elif action == "skip":
        return "패스 기록했습니다. 이후 가격 변동은 추적해드릴게요"
    elif action == "hold":
        return "계속 보유합니다. 트레일링 스탑 작동 중입니다"
    elif action == "stop_loss":
        return f"{name} {_won(price)} 손절 기록 ({pnl_pct:+.1f}%) 다음 기회를 잡겠습니다"
    elif action == "hold_through_stop":
        return "⚠️ 손절선을 넘었지만 보유를 선택했습니다. 후속 조치를 확인하세요."
    return "기록 완료"


def format_strategy_performance(strategy_stats: dict) -> str:
    """Format strategy performance report."""
    lines = [
        f"\U0001f4c8 {USER_NAME}의 K-Quant 성과 리포트\n",
        "전략별 성과",
    ]

    for strat_key, data in strategy_stats.items():
        if strat_key == "summary":
            continue
        emoji = STRATEGY_LABELS.get(strat_key, strat_key)
        win_rate = data.get("win_rate", 0)
        avg_pnl = data.get("avg_pnl", 0)
        count = data.get("total", 0)
        if count > 0:
            lines.append(f"{emoji}  승률 {win_rate:.0f}%  평균 {avg_pnl:+.1f}%  {count}건")

    summary = strategy_stats.get("summary", {})
    if summary:
        lines.extend([
            "",
            f"{USER_NAME} 매매 습관",
            f"매수 실행률  {summary.get('execution_rate', 0):.0f}%",
            f"평균 보유일  {summary.get('avg_hold_days', 0):.1f}일",
            f"손절 준수율  {summary.get('stop_compliance', 0):.0f}%",
        ])

    return "\n".join(lines)


def format_weekly_learning_report(learning_data: dict) -> str:
    """Format weekly learning report."""
    lines = [
        f"\U0001f4ca 주간 학습 리포트\n",
        f"이번 주 K-Quant가 배운 것:\n",
    ]

    insights = learning_data.get("insights", [])
    for i, ins in enumerate(insights[:5], 1):
        lines.append(f"{i}. {ins}")

    adjustments = learning_data.get("adjustments", {})
    if adjustments:
        lines.append("\n다음 주 추천 비중 조정:")
        for strat, change in adjustments.items():
            label = STRATEGY_LABELS.get(strat, strat)
            lines.append(f"{label} {change}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# v3.0 format functions
# ---------------------------------------------------------------------------

def format_auto_trade_alert(
    name: str, ticker: str, score_val: float, signal: str,
    price: float, strategy_type: str = "A",
) -> str:
    """Format auto-trade alert with KIS buy/sell buttons."""
    tag = _strategy_tag(strategy_type)
    target = round(price * 1.03, 0)
    stop = round(price * 0.95, 0)

    signal_emoji = {
        "STRONG_BUY": "\U0001f525\U0001f525",
        "BUY": "\U0001f7e2",
        "MILD_BUY": "\U0001f7e1",
        "WATCH": "\U0001f7e1",
    }.get(signal, "\u26aa")

    return (
        f"\u2550" * 22 + "\n"
        f"{signal_emoji} {USER_NAME}, {name} 매매 신호! {tag}\n"
        f"\u2550" * 22 + "\n\n"
        f"종합 점수: {score_val:.1f}/160\n"
        f"신호: {signal}\n"
        f"현재가: {_won(price)}\n\n"
        f"\U0001f4c8 목표: {_won(target)} (+3%)\n"
        f"\U0001f6d1 손절: {_won(stop)} (-5%)\n\n"
        f"아래 버튼으로 바로 주문하세요!"
    )


def format_kis_status_msg(connected: bool, mode: str = "virtual") -> str:
    """Format KIS connection status."""
    if not connected:
        return (
            f"\U0001f4e1 {USER_NAME}, KIS API 미연결\n\n"
            "/setup_kis 로 설정하세요."
        )
    mode_kr = "모의투자" if mode == "virtual" else "실전"
    return (
        f"\U0001f4e1 {USER_NAME}, KIS API 연결됨\n"
        f"모드: {mode_kr}\n\n"
        f"\u2705 자동매매 준비 완료"
    )


def format_regime_status(regime_result) -> str:
    """Format market regime for v3.0 (supports bubble attack)."""
    lines = [
        f"현재 모드: {regime_result.emoji} {regime_result.label}",
        f"\u2192 {regime_result.message}",
    ]

    alloc = regime_result.allocations
    if alloc:
        label_map = {
            "A": "반등", "B": "ETF", "C": "장기",
            "D": "섹터", "E": "글로벌", "F": "모멘텀",
            "G": "돌파", "cash": "현금",
        }
        alloc_parts = [
            f"{label_map.get(k, k)}({v}%)"
            for k, v in alloc.items()
            if isinstance(v, (int, float)) and v > 0 and k != "trailing_mode"
        ]
        if alloc_parts:
            lines.append(f"\u2192 추천: {' + '.join(alloc_parts)}")

    if regime_result.mode == "bubble_attack":
        lines.append(
            f"\U0001f4c8 목표: +{regime_result.profit_target_pct:.0f}% | "
            f"트레일링: {regime_result.trailing_stop_pct:.0f}%"
        )

    return "\n".join(lines)


def format_v3_score_signal(score_val: float, signal: str) -> str:
    """Format v3.0 score with new thresholds (max 160)."""
    signal_map = {
        "STRONG_BUY": "\U0001f525 강력 매수",
        "BUY": "\U0001f7e2 매수",
        "WATCH": "\U0001f7e1 관심",
        "MILD_BUY": "\U0001f7e1 약한 매수",
        "HOLD": "\u26aa 대기",
    }
    label = signal_map.get(signal, signal)
    bar_len = min(10, int(score_val / 16))
    bar = "\u2588" * bar_len + "\u2591" * (10 - bar_len)
    return f"[{bar}] {score_val:.1f}/160  {label}"


# ---------------------------------------------------------------------------
# v3.0+ format functions (sections 32-46)
# ---------------------------------------------------------------------------

def format_goal_dashboard(
    progress: dict,
    holdings: list[dict] | None = None,
    tenbagger_count: int = 0,
    swing_count: int = 0,
) -> str:
    """Format 30억 goal dashboard for /goal command."""
    start = progress.get("start_asset", 175_000_000)
    current = progress.get("current_asset", start)
    target = progress.get("target_asset", 3_000_000_000)
    progress_pct = progress.get("progress_pct", current / target * 100)

    bar_len = min(10, int(progress_pct / 10))
    bar = "\u2588" * bar_len + "\u2591" * (10 - bar_len)

    milestone = progress.get("current_milestone", "")
    milestone_pct = progress.get("milestone_progress_pct", 0)
    m_bar_len = min(10, int(milestone_pct / 10))
    m_bar = "\u2588" * m_bar_len + "\u2591" * (10 - m_bar_len)

    monthly_ret = progress.get("monthly_return_pct", 0)
    needed_monthly = progress.get("needed_monthly_pct", 10)

    lines = [
        "\U0001f3af 30\uc5b5 \ubaa9\ud45c \ub300\uc2dc\ubcf4\ub4dc",
        "",
        f"\uc2dc\uc791: {_eok(start)}",
        f"\ud604\uc7ac: {_eok(current)}",
        f"\ubaa9\ud45c: {_eok(target)}",
        "",
        f"\uc9c4\ud589\ub960: [{bar}] {progress_pct:.1f}%",
    ]

    if milestone:
        lines.extend([
            "",
            f"\uc62c\ud574 \ubaa9\ud45c: {milestone}",
            f"\uc62c\ud574 \uc9c4\ud589: [{m_bar}] {milestone_pct:.0f}%",
        ])

    lines.extend([
        "",
        f"\uc774\ubc88 \ub2ec \uc218\uc775: {monthly_ret:+.1f}%",
        f"\ud544\uc694 \uc6d4\uc218\uc775: +{needed_monthly:.0f}%",
    ])

    if holdings:
        lines.append("")
        lines.append("\ud3ec\uc9c0\uc158 \ud604\ud669:")
        for h in holdings[:5]:
            name = h.get("name", "")
            qty = h.get("quantity", h.get("qty", 0)) or 0
            eval_amt = h.get("eval_amount", h.get("eval", 0)) or 0
            # v5.8: 보유수량/평가금액 0인 종목 필터
            if qty <= 0 and eval_amt <= 0:
                continue
            pnl = h.get("profit_pct", h.get("pnl_pct", 0))
            lines.append(f"  {name} {pnl:+.1f}%")

    if tenbagger_count > 0:
        lines.append(f"\n\ud150\ubc30\uac70 \ud6c4\ubcf4: {tenbagger_count}\uc885\ubaa9 \ubaa8\ub2c8\ud130\ub9c1 \uc911")
    if swing_count > 0:
        lines.append(f"\uc2a4\uc719 \uac70\ub798: {swing_count}\uac74 \ud65c\uc131")

    return "\n".join(lines)


def _eok(amount: float) -> str:
    """Format amount in 억 units."""
    eok = amount / 100_000_000
    if eok >= 100:
        return f"{eok:,.0f}\uc5b5"
    return f"{eok:,.2f}\uc5b5"


def format_tenbagger_alert(candidate: dict) -> str:
    """Format tenbagger candidate alert for Telegram."""
    name = candidate.get("name", "")
    ticker = candidate.get("ticker", "")
    market = candidate.get("market", "")
    price = candidate.get("current_price", 0)
    drop = candidate.get("drop_from_high_pct", 0)
    met = candidate.get("conditions_met", 0)
    total = candidate.get("conditions_total", 5)
    details = candidate.get("conditions_detail", [])
    ml_prob = candidate.get("ml_prob", 0.5)
    sentiment = candidate.get("sentiment_pct", 50)

    lines = [
        "\U0001f525 \ud150\ubc30\uac70 \ud6c4\ubcf4 \ubc1c\uacac!",
        "",
        f"\uc885\ubaa9: {name} ({market})",
        f"\ud604\uc7ac\uac00: {_won(price)}  52\uc8fc \uace0\uc810 \ub300\ube44 {drop:+.0f}%",
        "",
        f"\ucda9\uc871 \uc870\uac74 {met}/{total}:",
    ]
    for d in details:
        lines.append(f"  \u2705 {d}")

    lines.extend([
        "",
        f"ML \uc0c1\uc2b9 \ud655\ub960: {ml_prob * 100:.0f}%",
        f"\ub274\uc2a4 \uc13c\ud2f0\uba3c\ud2b8: \uae0d\uc815 {sentiment:.0f}%",
        "",
        f"{USER_NAME}, \uc774\uac70 \uac15\ud558\uac8c \uc8fc\ubaa9\ud558\uc138\uc694!",
    ])
    return "\n".join(lines)


def format_swing_alert(signal: dict) -> str:
    """Format swing trade alert for Telegram."""
    name = signal.get("name", "")
    entry = signal.get("entry_price", 0)
    target = signal.get("target_price", 0)
    stop = signal.get("stop_price", 0)
    target_pct = signal.get("target_pct", 10)
    stop_pct = signal.get("stop_pct", -5)
    hold_days = signal.get("hold_days", 5)
    confidence = signal.get("confidence", 0)
    ml_prob = signal.get("ml_prob", 0.5)

    return (
        f"\u26a1 \uc2a4\uc719 \ub9e4\uc218 \ucd94\ucc9c\n\n"
        f"\uc885\ubaa9: {name}  \ubaa9\ud45c \ubcf4\uc720: {hold_days}\uc77c\n"
        f"\uc9c4\uc785: {_won(entry)}\n"
        f"\ubaa9\ud45c: {_won(target)} ({target_pct:+.0f}%)\n"
        f"\uc190\uc808: {_won(stop)} ({stop_pct:+.0f}%)\n\n"
        f"\ud655\uc2e0\ub3c4 {confidence:.0f}\uc810  ML {ml_prob * 100:.0f}%"
    )


def format_concentration_warning(report: dict) -> str:
    """Format portfolio concentration warning."""
    alerts = report.get("alerts", [])
    if not alerts:
        return ""

    lines = ["\u26a0\ufe0f \ud3b8\uc911 \uacbd\uace0"]
    for a in alerts:
        severity_emoji = "\U0001f534" if a.get("severity") == "danger" else "\U0001f7e1"
        lines.append(f"{severity_emoji} {a.get('message', '')}")
        suggestion = a.get("suggestion", "")
        if suggestion:
            lines.append(f"  \u2192 {suggestion}")

    lines.append(f"\n\ud3ec\ud2b8\ud3f4\ub9ac\uc624 \ubd84\uc0b0 \uc810\uc218: {report.get('score', 0)}/100")
    return "\n".join(lines)


def format_fear_greed(fear_greed: dict) -> str:
    """Format fear/greed index for Telegram."""
    total = fear_greed.get("total", 50)
    label = fear_greed.get("label", "\uc911\ub9bd")

    label_emoji = {
        "\uadf9\ub2e8\uacf5\ud3ec": "\U0001f630",
        "\uacf5\ud3ec": "\U0001f628",
        "\uc911\ub9bd": "\U0001f610",
        "\ud0d0\uc695": "\U0001f911",
        "\uadf9\ub2e8\ud0d0\uc695": "\U0001f525",
    }
    emoji = label_emoji.get(label, "\U0001f610")

    bar_len = min(10, int(total / 10))
    bar = "\u2588" * bar_len + "\u2591" * (10 - bar_len)

    return f"\uc2dc\uc7a5 \uc2ec\ub9ac: {emoji} [{bar}] {total:.0f}/100 ({label})"


def format_aggressive_score_signal(score_val: float, signal: str) -> str:
    """Format score with 30억 mode aggressive messaging."""
    if score_val >= 130:
        return f"\U0001f525\U0001f525 {USER_NAME}, \uc774\uac70 \uc5d0\ucf54\ud504\ub85c\uae09\uc785\ub2c8\ub2e4! \uac15\ud558\uac8c \uac00\uc138\uc694! ({score_val:.0f}\uc810)"
    if score_val >= 120:
        return f"\U0001f525 {USER_NAME}, \ud655\uc2e0 \ub192\uc2b5\ub2c8\ub2e4. \uc9d1\uc911 \ub9e4\uc218! ({score_val:.0f}\uc810)"
    if score_val >= 110:
        return f"\U0001f7e2 {USER_NAME}, \uad1c\ucc2e\uc740 \uae30\ud68c\uc785\ub2c8\ub2e4. \uc2a4\uc719 \ub9e4\uc218! ({score_val:.0f}\uc810)"
    return format_v3_score_signal(score_val, signal)


# ── v8.5: 온보딩 + 가이드 + 오늘의 할 일 ────────────────────────

ONBOARDING_STEPS = [
    {
        "id": 1, "title": "종목 분석", "emoji": "📊",
        "desc": (
            "종목명만 입력하면 AI가 즉시 분석합니다.\n"
            "삼성전자, SK하이닉스 등 어떤 종목이든 OK!\n\n"
            "v9.2: 10종 차트도 바로 볼 수 있어요\n"
            "기본/확장(MACD+수급)/주봉/MTF/밸류에이션"
        ),
        "try_label": "📊 삼성전자 분석 체험",
        "try_cb": "onboard:try:analysis",
    },
    {
        "id": 2, "title": "잔고 확인", "emoji": "💰",
        "desc": (
            "보유종목 수익률과 매도/보유 가이드를 확인합니다.\n"
            "전담 매니저가 종목별 코멘트를 달아줍니다.\n\n"
            "v9.1: 매니저 성과 피드백 + 교훈 자동 반영\n"
            "승률이 낮으면 더 보수적으로, 높으면 자신감 유지"
        ),
        "try_label": "💰 잔고 보기",
        "try_cb": "onboard:try:balance",
    },
    {
        "id": 3, "title": "즐겨찾기 + 차트", "emoji": "⭐",
        "desc": (
            "75종목을 카테고리별로 관리하는 대시보드입니다.\n\n"
            "v9.2: 종목 상세 > 차트에서 5가지 모드 선택\n"
            "  기본: 캔들+BB+RSI\n"
            "  확장: +MACD+수급+공매도+시그널선\n"
            "  주봉: 주봉캔들+매집점수(0-100)\n"
            "  일봉+주봉: 멀티타임프레임 비교\n"
            "  밸류에이션: PER밴드+적정가"
        ),
        "try_label": "⭐ 즐겨찾기 열기",
        "try_cb": "onboard:try:favorites",
    },
    {
        "id": 4, "title": "시장 현황", "emoji": "📈",
        "desc": (
            "미국/한국 시장 + VIX + 환율 + 공포탐욕 지수를\n"
            "한 화면에서 확인합니다.\n\n"
            "v9.0: 한국형 리스크 게이지 (0-100점)\n"
            "신용잔고/ETF/환율/공매도/선물만기 7요인 종합"
        ),
        "try_label": "📈 시황 보기",
        "try_cb": "onboard:try:market",
    },
    {
        "id": 5, "title": "AI 질문", "emoji": "💬",
        "desc": (
            "투자 관련 아무 질문이나 하면 AI가 답변합니다.\n"
            "'반도체 전망?', '현금 비중 늘려야 해?' 등\n\n"
            "보유종목/시장 데이터가 자동 연동되어\n"
            "내 상황에 맞는 맞춤형 답변을 받습니다."
        ),
        "try_label": "💬 AI에게 질문하기",
        "try_cb": "onboard:try:ai",
    },
    {
        "id": 6, "title": "4매니저 시스템", "emoji": "🤖",
        "desc": (
            "4명의 전설적 투자자가 보유종목을 분석합니다.\n\n"
            "⚡ 리버모어 (단타 1~3일)\n"
            "🔥 오닐 (스윙 1~2주)\n"
            "📊 린치 (포지션 1~3개월)\n"
            "💎 버핏 (장기 3개월+)\n\n"
            "v9.1: 매니저 토론 (4명이 같은 종목 동시 분석)\n"
            "매니저별 능동 발굴 + 주간 반성 + 포트폴리오 밸런스"
        ),
        "try_label": "🤖 매니저 보기",
        "try_cb": "onboard:try:manager",
    },
    {
        "id": 7, "title": "레짐별 가중치", "emoji": "⚖️",
        "desc": (
            "VIX에 따라 매니저별 영향력이 자동 조절됩니다.\n\n"
            "VIX < 18 (calm): 단타 1.2x, 장기 0.8x\n"
            "VIX 18~25 (normal): 모두 1.0x\n"
            "VIX 25~30 (fear): 단타 0.6x, 장기 1.3x\n"
            "VIX 30+ (panic): 단타 0.3x 보류, 장기 1.5x\n\n"
            "공포 구간에서 단타는 자동 축소, 장기 투자 확대!"
        ),
        "try_label": "📈 시황에서 VIX 확인",
        "try_cb": "onboard:try:market",
    },
    {
        "id": 8, "title": "자동 알림 시스템", "emoji": "🔔",
        "desc": (
            "45개 스케줄 잡이 하루 종일 돌아갑니다.\n\n"
            "07:30 모닝브리핑 (AI 분석 + 매니저 코멘트)\n"
            "09:00~15:30 장중 모니터링 (60초 주기)\n"
            "  매니저별 실시간 알림 (손절/익절/차트조건)\n"
            "13:00 매니저 능동 발굴 (신규 종목 탐색)\n"
            "14:30 단타 청산 리마인더\n"
            "16:00 일일보고서 (PDF)\n"
            "19:00(일) 매니저 주간 반성 + 임계값 조정 제안\n"
            "21:00 일일 자가진단"
        ),
        "try_label": "🔔 알림 설정 확인",
        "try_cb": "onboard:try:alerts",
    },
    {
        "id": 9, "title": "kbot CLI + 원격", "emoji": "🖥️",
        "desc": (
            "터미널에서 봇을 완전 제어할 수 있습니다.\n\n"
            "./kbot status   상태 확인\n"
            "./kbot restart  재시작\n"
            "./kbot logs 20  로그\n"
            "./kbot jobs     스케줄 작업 목록\n"
            "./kbot trigger morning_briefing  즉시 실행\n"
            "./kbot alert wartime  경계모드 변경\n\n"
            "v9.2: 맥북에서 원격 관리 (SSH + Tailscale)"
        ),
        "try_label": "📖 도움말 보기",
        "try_cb": "onboard:try:alerts",
    },
]

MENU_TIPS: dict[str, str] = {
    "analysis": "💡 종목명을 입력하면 AI가 즉시 매수/매도 판단을 해드립니다",
    "market": "💡 신호등(🟢🟡🔴)으로 오늘 공격/방어 전략을 확인하세요",
    "balance": "💡 보유종목별 전담 매니저가 매도/보유 의견을 드립니다",
    "favorites": "💡 카테고리 탭으로 75종목을 한눈에 관리하세요",
    "ai_chat": "💡 투자 질문을 자유롭게 입력하세요. AI가 답변합니다",
    "reports": "💡 최신 증권사 리포트와 섹터별 전망을 확인하세요",
    "more": "💡 급등주/스윙/매집탐지 등 고급 기능을 사용해보세요",
}


def format_onboarding_step(step: dict, current: int, total: int) -> str:
    """온보딩 스텝 1개를 진행바와 함께 포맷."""
    filled = current
    empty = total - current
    bar = "\u2588" * filled + "\u2591" * empty
    return (
        f"\U0001f4d6 {USER_NAME}, K-Quant 둘러보기\n"
        f"[{bar}] {current}/{total}\n"
        f"{'━' * 22}\n\n"
        f"{step['emoji']} Step {step['id']}: {step['title']}\n\n"
        f"{step['desc']}\n"
    )


def format_onboarding_complete() -> str:
    """온보딩 완료 메시지."""
    return (
        f"\U0001f389 {USER_NAME}, 온보딩 완료!\n"
        f"{'━' * 22}\n\n"
        f"{APP_NAME}의 핵심 기능을 모두 둘러보셨습니다.\n\n"
        "\U0001f4cc 매일 07:30 모닝 브리핑 + 4매니저 분석\n"
        "\U0001f4cc 장중 실시간 알림 (급등/매도타겟/경고)\n"
        "\U0001f4cc YouTube 24채널 자동 학습 (5회/일)\n"
        "\U0001f4cc ML 앙상블 예측 (58피처, LGB+XGB+LSTM)\n"
        "\U0001f4cc 19명 애널리스트 추적 + 일일 합성\n"
        "\U0001f4cc /learning → 학습 현황 대시보드\n\n"
        "이제 메인 메뉴에서 자유롭게 사용해보세요! \U0001f447"
    )


def format_daily_actions(actions: list[dict], alert_mode: str = "normal") -> str:
    """오늘의 할 일 포맷."""
    mode_label = {
        "normal": "\U0001f7e2 일상",
        "elevated": "\U0001f7e1 경계",
        "wartime": "\U0001f534 전시",
    }.get(alert_mode, "")

    if not actions:
        return (
            f"\U0001f4cb {USER_NAME}, 오늘의 할 일\n"
            f"{'━' * 22}\n"
            f"경계 모드: {mode_label}\n\n"
            "오늘은 특별한 조치가 필요 없습니다.\n"
            "시장 상황을 지켜보세요! \U0001f440"
        )

    priority_labels = {
        "urgent": "\U0001f534 긴급 조치",
        "caution": "\U0001f7e1 주의 필요",
        "opportunity": "\U0001f7e2 매수 기회",
        "check": "\u26aa 확인 사항",
    }

    lines = [
        f"\U0001f4cb {USER_NAME}, 오늘의 할 일",
        "━" * 22,
        f"경계 모드: {mode_label}",
        "",
    ]

    for p in ("urgent", "caution", "opportunity", "check"):
        items = [a for a in actions if a.get("priority") == p]
        if not items:
            continue
        lines.append(priority_labels[p])
        for item in items:
            lines.append(f"  {item['name']}: {item['action']}")
            manager_label = str(item.get("manager_label", "") or "").strip()
            reason = str(item.get("reason", "") or "").strip()
            if manager_label:
                lines.append(f"    \u2514 {manager_label} | {reason}")
            else:
                lines.append(f"    \u2514 {reason}")
        lines.append("")

    cnt = {p: sum(1 for a in actions if a.get("priority") == p)
           for p in ("urgent", "caution", "opportunity", "check")}
    lines.append("━" * 22)
    lines.append(
        f"총 {len(actions)}건 | "
        f"\U0001f534{cnt['urgent']} \U0001f7e1{cnt['caution']} "
        f"\U0001f7e2{cnt['opportunity']} \u26aa{cnt['check']}"
    )
    return "\n".join(lines)
