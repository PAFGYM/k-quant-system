"""세력/개미떼 탐지 모듈 (Herd Detector).

한국 시장 특유의 리딩방/종목토론방 패턴을 감지:
  1. 개미떼 유입: 거래량 급증 + 기관/외인 미동참 → 위험
  2. 세력 매집 초기: 거래량↑ + 기관 연속매수 + 가격 횡보 → 기회
  3. 리딩방 급락: 급등 후 급락 = 세력 물량 떠넘기기 → 경고
  4. 진성 세력: 외인+기관 동시매수 + 거래량↑ → 강력 기회
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern configuration
# ---------------------------------------------------------------------------

HERD_PATTERNS: dict[str, dict] = {
    "retail_herd": {
        "name": "개미떼 유입",
        "emoji": "🔴",
        "danger_level": "위험",
        "score_adj": -15,
        "volume_min": 3.0,          # 20일 평균 대비 3배
        "inst_foreign_max": 0.3,    # 기관+외인 비중 30% 미만
    },
    "stealth_force": {
        "name": "세력 매집 초기",
        "emoji": "🟢",
        "danger_level": "안전",
        "score_adj": 25,
        "volume_min": 2.0,
        "inst_consecutive_min": 3,  # 기관 연속매수 3일+
        "price_range": 3.0,         # 가격 횡보 ±3%
    },
    "pump_dump": {
        "name": "리딩방 급락",
        "emoji": "⛔",
        "danger_level": "위험",
        "score_adj": -30,
        "surge_pct": 15.0,          # 5일내 +15%
        "dump_pct": -5.0,           # 당일 -5%
    },
    "genuine_force": {
        "name": "진성 세력",
        "emoji": "🟢",
        "danger_level": "안전",
        "score_adj": 35,
        "volume_min": 1.5,
        "dual_buy_days": 3,         # 외인+기관 동시매수 3일+
    },
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class HerdSignal:
    """세력/개미떼 탐지 결과."""

    ticker: str
    name: str
    pattern: str            # "개미떼 유입" | "세력 매집 초기" | "리딩방 급락" | "진성 세력"
    danger_level: str       # "안전" | "주의" | "위험"
    volume_ratio: float     # 20일 평균 대비 거래량 비율
    inst_flow: float        # 기관 순매수 추정 (억)
    foreign_flow: float     # 외인 순매수 추정 (억)
    score_adj: int          # 점수 보정값
    reasons: list[str] = field(default_factory=list)
    message: str = ""


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_herd_pattern(
    ticker: str,
    name: str,
    daily_volumes: list[float],
    daily_closes: list[float],
    daily_inst: list[float],
    daily_foreign: list[float],
) -> HerdSignal | None:
    """단일 종목 세력/개미떼 패턴 탐지.

    Args:
        ticker: 종목코드.
        name: 종목명.
        daily_volumes: 최근 20일 거래량 리스트.
        daily_closes: 최근 20일 종가 리스트.
        daily_inst: 최근 20일 기관 순매수 추정액 리스트.
        daily_foreign: 최근 20일 외인 순매수 추정액 리스트.

    Returns:
        HerdSignal if pattern detected, None otherwise.
    """
    if len(daily_volumes) < 20 or len(daily_closes) < 20:
        return None

    avg_vol = sum(daily_volumes[:15]) / 15 if sum(daily_volumes[:15]) > 0 else 1
    recent_vol = daily_volumes[-1]
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 0

    # 최근 5일 기관/외인 순매수 합산
    inst_5d = sum(daily_inst[-5:])
    foreign_5d = sum(daily_foreign[-5:])
    inst_flow_억 = inst_5d / 1e8
    foreign_flow_억 = foreign_5d / 1e8

    # 가격 변동
    price_now = daily_closes[-1]
    price_5d = daily_closes[-6] if len(daily_closes) >= 6 else daily_closes[0]
    price_20d = daily_closes[0]
    chg_5d_pct = ((price_now - price_5d) / price_5d * 100) if price_5d > 0 else 0
    chg_20d_pct = ((price_now - price_20d) / price_20d * 100) if price_20d > 0 else 0

    # 기관 연속매수 일수 (최근부터 역순)
    inst_streak = 0
    for v in reversed(daily_inst[-10:]):
        if v > 0:
            inst_streak += 1
        else:
            break

    # 외인+기관 동시매수 일수
    dual_buy_days = 0
    for i in range(-1, -min(11, len(daily_inst) + 1), -1):
        if daily_inst[i] > 0 and daily_foreign[i] > 0:
            dual_buy_days += 1
        else:
            break

    # 당일 등락률
    prev_close = daily_closes[-2] if len(daily_closes) >= 2 else price_now
    day_chg_pct = ((price_now - prev_close) / prev_close * 100) if prev_close > 0 else 0

    # 기관+외인 거래 비중 추정 (순매수 절대값 / 거래대금)
    total_flow = abs(inst_5d) + abs(foreign_5d)
    total_volume_val = sum(daily_volumes[-5:]) * price_now if price_now > 0 else 1
    inst_foreign_ratio = total_flow / total_volume_val if total_volume_val > 0 else 0

    # --- 패턴 1: 진성 세력 (가장 먼저 체크 — 강력 기회) ---
    cfg = HERD_PATTERNS["genuine_force"]
    if vol_ratio >= cfg["volume_min"] and dual_buy_days >= cfg["dual_buy_days"]:
        reasons = [
            f"외인+기관 동시매수 {dual_buy_days}일 연속",
            f"거래량 {vol_ratio:.1f}배",
            f"기관 {inst_flow_억:+.0f}억 / 외인 {foreign_flow_억:+.0f}억",
        ]
        signal = HerdSignal(
            ticker=ticker, name=name,
            pattern=cfg["name"], danger_level=cfg["danger_level"],
            volume_ratio=vol_ratio,
            inst_flow=inst_flow_억, foreign_flow=foreign_flow_억,
            score_adj=cfg["score_adj"], reasons=reasons,
        )
        signal.message = format_herd_alert(signal)
        logger.info("Herd %s(%s): %s score=%+d", name, ticker, cfg["name"], cfg["score_adj"])
        return signal

    # --- 패턴 2: 세력 매집 초기 ---
    cfg = HERD_PATTERNS["stealth_force"]
    if (vol_ratio >= cfg["volume_min"]
            and inst_streak >= cfg["inst_consecutive_min"]
            and abs(chg_20d_pct) <= cfg["price_range"]):
        reasons = [
            f"기관 연속매수 {inst_streak}일",
            f"거래량 {vol_ratio:.1f}배",
            f"가격 횡보 ({chg_20d_pct:+.1f}%)",
            f"기관 {inst_flow_억:+.0f}억",
        ]
        signal = HerdSignal(
            ticker=ticker, name=name,
            pattern=cfg["name"], danger_level=cfg["danger_level"],
            volume_ratio=vol_ratio,
            inst_flow=inst_flow_억, foreign_flow=foreign_flow_억,
            score_adj=cfg["score_adj"], reasons=reasons,
        )
        signal.message = format_herd_alert(signal)
        logger.info("Herd %s(%s): %s score=%+d", name, ticker, cfg["name"], cfg["score_adj"])
        return signal

    # --- 패턴 3: 리딩방 급락 (5일 +15% 후 당일 -5%) ---
    cfg = HERD_PATTERNS["pump_dump"]
    if chg_5d_pct >= cfg["surge_pct"] and day_chg_pct <= cfg["dump_pct"]:
        reasons = [
            f"5일 급등 {chg_5d_pct:+.1f}% → 당일 급락 {day_chg_pct:+.1f}%",
            "세력 물량 떠넘기기 의심",
        ]
        signal = HerdSignal(
            ticker=ticker, name=name,
            pattern=cfg["name"], danger_level=cfg["danger_level"],
            volume_ratio=vol_ratio,
            inst_flow=inst_flow_억, foreign_flow=foreign_flow_억,
            score_adj=cfg["score_adj"], reasons=reasons,
        )
        signal.message = format_herd_alert(signal)
        logger.info("Herd %s(%s): %s score=%+d", name, ticker, cfg["name"], cfg["score_adj"])
        return signal

    # --- 패턴 4: 개미떼 유입 ---
    cfg = HERD_PATTERNS["retail_herd"]
    if vol_ratio >= cfg["volume_min"] and inst_foreign_ratio < cfg["inst_foreign_max"]:
        reasons = [
            f"거래량 {vol_ratio:.1f}배 급증",
            f"기관+외인 비중 {inst_foreign_ratio:.0%} (미동참)",
            "리딩방/토론방 유입 의심",
        ]
        signal = HerdSignal(
            ticker=ticker, name=name,
            pattern=cfg["name"], danger_level=cfg["danger_level"],
            volume_ratio=vol_ratio,
            inst_flow=inst_flow_억, foreign_flow=foreign_flow_억,
            score_adj=cfg["score_adj"], reasons=reasons,
        )
        signal.message = format_herd_alert(signal)
        logger.info("Herd %s(%s): %s score=%+d", name, ticker, cfg["name"], cfg["score_adj"])
        return signal

    return None


def scan_herd_all(
    stocks_data: list[dict],
) -> list[HerdSignal]:
    """배치 스캔 — 전체 종목 세력/개미떼 패턴 탐지.

    Args:
        stocks_data: list of dicts with keys:
            ticker, name, daily_volumes, daily_closes, daily_inst, daily_foreign

    Returns:
        Detected signals sorted by |score_adj| descending.
    """
    results: list[HerdSignal] = []
    for d in stocks_data:
        try:
            sig = detect_herd_pattern(
                ticker=d["ticker"],
                name=d["name"],
                daily_volumes=d["daily_volumes"],
                daily_closes=d["daily_closes"],
                daily_inst=d["daily_inst"],
                daily_foreign=d["daily_foreign"],
            )
            if sig is not None:
                results.append(sig)
        except Exception:
            logger.debug("Herd scan failed for %s", d.get("ticker"), exc_info=True)
    results.sort(key=lambda s: abs(s.score_adj), reverse=True)
    return results


def integrate_herd_score(base_score: int, signal: HerdSignal | None) -> int:
    """기존 점수에 세력 탐지 보정 적용.

    Args:
        base_score: 기존 종합 점수.
        signal: HerdSignal (None이면 보정 없음).

    Returns:
        보정된 점수 (max 250).
    """
    if signal is None:
        return base_score
    adjusted = base_score + signal.score_adj
    return max(0, min(250, adjusted))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_herd_alert(signal: HerdSignal) -> str:
    """HerdSignal을 텔레그램 알림 메시지로 포맷."""
    emoji_map = {
        "안전": "🟢",
        "주의": "🟡",
        "위험": "🔴",
    }
    emoji = emoji_map.get(signal.danger_level, "⚪")

    lines = [
        f"{emoji} {signal.pattern} 감지",
        f"종목: {signal.name} ({signal.ticker})",
        f"등급: {signal.danger_level} ({signal.score_adj:+d}점)",
        f"거래량: {signal.volume_ratio:.1f}배",
        f"기관: {signal.inst_flow:+.0f}억 / 외인: {signal.foreign_flow:+.0f}억",
    ]
    if signal.reasons:
        lines.append(f"근거: {', '.join(signal.reasons)}")
    return "\n".join(lines)
