"""v9.0: 주봉 매집/세력 패턴 탐지.

OBV 다이버전스, 거래량 프로파일, 주봉 캔들 패턴, 수급 패턴을
종합하여 매집 점수(0-100)를 산출.

70+ = 매집 확인 (매수 적극 검토)
40-69 = 매집 가능성 (관심 유지)
<40 = 매집 미확인
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AccumulationScore:
    """매집 종합 점수."""

    total: int = 0  # 0-100
    obv_score: int = 0  # max 30
    volume_score: int = 0  # max 25
    candle_score: int = 0  # max 20
    flow_score: int = 0  # max 25
    signals: list[str] = field(default_factory=list)
    pattern: str = ""  # 세력 패턴명


@dataclass
class ForcePattern:
    """세력 패턴."""

    name: str
    description: str
    action: str  # 대응 전략


def analyze_weekly_accumulation(
    daily_df: pd.DataFrame,
    supply_data: list[dict] | None = None,
) -> AccumulationScore:
    """주봉 기반 매집/세력 탐지.

    Args:
        daily_df: 일봉 OHLCV DataFrame (최소 60일, 권장 120일+).
                  columns: open, high, low, close, volume
        supply_data: 수급 데이터 (supply_demand 테이블, 최신순).

    Returns:
        AccumulationScore with 0-100 total score.
    """
    result = AccumulationScore()

    if daily_df is None or len(daily_df) < 20:
        return result

    # 주봉 리샘플링
    weekly = _resample_weekly(daily_df)
    if len(weekly) < 4:
        return result

    # 1. OBV 다이버전스 (max 30점)
    result.obv_score = _score_obv_divergence(weekly)
    if result.obv_score >= 20:
        result.signals.append("OBV 상승 다이버전스 (가격↓ + OBV↑ = 매집)")

    # 2. 거래량 프로파일 (max 25점)
    result.volume_score = _score_volume_profile(weekly)
    if result.volume_score >= 15:
        result.signals.append("매집형 거래량 (하락시 저거래 + 상승시 고거래)")

    # 3. 주봉 캔들 패턴 (max 20점)
    result.candle_score = _score_candle_patterns(weekly)
    if result.candle_score >= 12:
        result.signals.append("매집형 캔들 패턴 감지")

    # 4. 기관/외인 수급 (max 25점)
    if supply_data and len(supply_data) >= 5:
        result.flow_score = _score_institutional_flow(supply_data)
        if result.flow_score >= 15:
            result.signals.append("기관/외인 지속 순매수")

    result.total = min(
        100,
        result.obv_score + result.volume_score + result.candle_score + result.flow_score,
    )

    # 세력 패턴 분류
    result.pattern = _classify_force_pattern(weekly, result)

    return result


def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 주봉 리샘플링."""
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        else:
            df.index = pd.to_datetime(df.index)

    weekly = df.resample("W-FRI").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    return weekly


def _score_obv_divergence(weekly: pd.DataFrame) -> int:
    """OBV 다이버전스 점수 (max 30)."""
    score = 0
    closes = weekly["close"].values
    volumes = weekly["volume"].values

    if len(closes) < 8:
        return 0

    # OBV 계산
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])

    # 최근 8주 구간 분석
    recent_close = closes[-8:]
    recent_obv = obv[-8:]

    # 가격 추세 (선형 회귀 간이)
    price_trend = (recent_close[-1] - recent_close[0]) / max(recent_close[0], 1)
    obv_trend = (recent_obv[-1] - recent_obv[0]) / max(abs(recent_obv[0]), 1)

    # 가격 횡보/하락 + OBV 상승 = 매집
    if price_trend <= 0.02 and obv_trend > 0.05:
        score += 25  # 강한 다이버전스
    elif price_trend <= 0.05 and obv_trend > 0.03:
        score += 15  # 약한 다이버전스

    # OBV 20주 추세선 돌파 체크 (20주 데이터 있으면)
    if len(obv) >= 20:
        obv_20_avg = sum(obv[-20:]) / 20
        if obv[-1] > obv_20_avg and obv[-2] <= obv_20_avg:
            score += 5  # OBV 추세선 돌파

    return min(30, score)


def _score_volume_profile(weekly: pd.DataFrame) -> int:
    """거래량 프로파일 점수 (max 25)."""
    score = 0
    if len(weekly) < 8:
        return 0

    recent = weekly.tail(8)
    avg_vol = weekly["volume"].mean()

    # 하락 시 저거래량 + 상승 시 고거래량 = 매집
    up_vols = []
    down_vols = []
    for i in range(len(recent)):
        row = recent.iloc[i]
        if row["close"] >= row["open"]:
            up_vols.append(row["volume"])
        else:
            down_vols.append(row["volume"])

    if up_vols and down_vols:
        avg_up = sum(up_vols) / len(up_vols)
        avg_down = sum(down_vols) / len(down_vols)
        if avg_up > avg_down * 1.5:
            score += 15  # 상승 고거래 + 하락 저거래
        elif avg_up > avg_down * 1.2:
            score += 8

    # 3주 연속 거래량 평균 대비 50%+ 증가 = 세력 진입
    last3_vols = recent["volume"].tail(3).values
    if all(v > avg_vol * 1.5 for v in last3_vols):
        score += 10
    elif all(v > avg_vol * 1.2 for v in last3_vols):
        score += 5

    return min(25, score)


def _score_candle_patterns(weekly: pd.DataFrame) -> int:
    """주봉 캔들 패턴 점수 (max 20)."""
    score = 0
    if len(weekly) < 4:
        return 0

    recent = weekly.tail(4)

    for i in range(len(recent)):
        row = recent.iloc[i]
        body = abs(row["close"] - row["open"])
        total_range = row["high"] - row["low"]
        if total_range == 0:
            continue

        # 장대양봉 + 고거래량
        is_bullish = row["close"] > row["open"]
        body_ratio = body / total_range

        if is_bullish and body_ratio > 0.7:
            avg_vol = weekly["volume"].mean()
            if row["volume"] > avg_vol * 2:
                score += 8  # 장대양봉 + 거래량 2배
            elif row["volume"] > avg_vol * 1.5:
                score += 4

        # 긴 아래꼬리 (해머)
        lower_shadow = min(row["open"], row["close"]) - row["low"]
        upper_shadow = row["high"] - max(row["open"], row["close"])
        if total_range > 0 and lower_shadow > body * 2 and upper_shadow < body * 0.5:
            score += 5  # 해머 패턴

    # 3주 연속 양봉
    last3 = recent.tail(3)
    if all(last3.iloc[j]["close"] > last3.iloc[j]["open"] for j in range(3)):
        score += 5

    # 도지 3개 연속 (축적 단계)
    doji_count = 0
    for j in range(min(4, len(recent))):
        row = recent.iloc[j]
        body = abs(row["close"] - row["open"])
        total_range = row["high"] - row["low"]
        if total_range > 0 and body / total_range < 0.1:
            doji_count += 1

    if doji_count >= 3:
        score += 5

    return min(20, score)


def _score_institutional_flow(supply_data: list[dict]) -> int:
    """기관/외인 수급 점수 (max 25).

    4주(20일) 중 3주+ 순매수 = 편입 진행 중.
    """
    score = 0
    if len(supply_data) < 5:
        return 0

    # 최근 20일 데이터를 주 단위로 집계
    recent = supply_data[:min(20, len(supply_data))]

    # 외인 순매수 일수
    foreign_buy_days = sum(1 for d in recent if d.get("foreign_net", 0) > 0)
    inst_buy_days = sum(1 for d in recent if d.get("institution_net", 0) > 0)

    total_days = len(recent)
    foreign_ratio = foreign_buy_days / total_days if total_days > 0 else 0
    inst_ratio = inst_buy_days / total_days if total_days > 0 else 0

    # 외인 75%+ 매수일 = 적극 편입
    if foreign_ratio >= 0.75:
        score += 15
    elif foreign_ratio >= 0.6:
        score += 8

    # 기관 동조
    if inst_ratio >= 0.6:
        score += 10
    elif inst_ratio >= 0.5:
        score += 5

    # 금액 증가 추세 (최근 5일 vs 이전 5일)
    if len(recent) >= 10:
        recent_5 = sum(abs(d.get("foreign_net", 0)) for d in recent[:5])
        prev_5 = sum(abs(d.get("foreign_net", 0)) for d in recent[5:10])
        if prev_5 > 0 and recent_5 > prev_5 * 1.3:
            score += 5  # 금액 증가 추세

    return min(25, score)


def _classify_force_pattern(
    weekly: pd.DataFrame,
    acc: AccumulationScore,
) -> str:
    """세력 패턴 분류."""
    if len(weekly) < 4:
        return ""

    last = weekly.iloc[-1]
    prev = weekly.iloc[-2]
    avg_vol = weekly["volume"].mean()

    # 세력 물량 던지기: 고점 대거래량 음봉
    if (last["close"] < last["open"]
            and last["volume"] > avg_vol * 3
            and last["close"] < prev["close"]):
        return "세력 물량 던지기"

    # 작전주: 거래량 10배+ + 뉴스 없이 급등
    if last["volume"] > avg_vol * 10:
        return "작전주 의심"

    # 세력 시동: 장대양봉 + 거래량 3배+
    body = last["close"] - last["open"]
    total_range = last["high"] - last["low"]
    if (body > 0 and total_range > 0
            and body / total_range > 0.6
            and last["volume"] > avg_vol * 3):
        return "세력 시동"

    # 개미 털기: 급락 후 즉반등 (아래꼬리)
    lower_shadow = min(last["open"], last["close"]) - last["low"]
    if total_range > 0 and lower_shadow > body * 3:
        return "개미 털기 (반등 기회)"

    # 매집 확인
    if acc.total >= 70:
        return "세력 매집 확인"
    elif acc.total >= 40:
        return "매집 가능성"

    return ""


def format_accumulation_score(
    ticker: str,
    name: str,
    score: AccumulationScore,
) -> str:
    """매집 점수 텔레그램 포맷."""
    if score.total == 0:
        return ""

    if score.total >= 70:
        grade = "🟢 매집 확인"
    elif score.total >= 40:
        grade = "🟡 매집 가능성"
    else:
        grade = "⚪ 매집 미확인"

    lines = [
        f"📊 {name} 주봉 매집 분석: {score.total}점 {grade}",
        f"  OBV: {score.obv_score}/30 | 거래량: {score.volume_score}/25 | "
        f"캔들: {score.candle_score}/20 | 수급: {score.flow_score}/25",
    ]

    if score.pattern:
        lines.append(f"  패턴: {score.pattern}")

    for sig in score.signals[:3]:  # 최대 3개 신호
        lines.append(f"  → {sig}")

    return "\n".join(lines)
