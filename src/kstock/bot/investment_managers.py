"""4명의 전설적 투자자 AI 매니저 시스템.

각 매니저는 holding_type에 매칭되어 해당 투자 유형에 특화된
분석·코칭·알림 메시지를 제공한다.

비용: Haiku 기반으로 매니저당 ~$0.0014/회 (월 +$0.09 수준).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ── 매니저별 손절/익절 기준 ──────────────────────────────────

MANAGER_THRESHOLDS: dict[str, dict] = {
    "scalp":     {"stop_loss": -3.0, "take_profit_1": 5.0, "take_profit_2": 8.0},
    "swing":     {"stop_loss": -7.0, "take_profit_1": 10.0, "take_profit_2": 20.0},
    "position":  {"stop_loss": -12.0, "take_profit_1": 20.0, "take_profit_2": 40.0},
    "long_term": {"stop_loss": -20.0, "take_profit_1": 30.0, "take_profit_2": 80.0},
}


def get_dynamic_thresholds(
    manager_key: str, atr_pct: float = 0.0,
) -> dict:
    """ATR 기반 동적 손절/목표가 임계치. atr=0이면 기존 고정값.

    v9.6.0: 변동성에 맞춘 손절/익절로 조기 손절·늦은 손절 방지.
    """
    if atr_pct > 0:
        try:
            from kstock.core.position_sizer import compute_atr_stops
            stops = compute_atr_stops(atr_pct, manager_key, buy_price=100000)
            return {
                "stop_loss": round(stops["stop_pct"] * 100, 1),
                "take_profit_1": round(stops["target_1_pct"] * 100, 1),
                "take_profit_2": round(stops["target_2_pct"] * 100, 1),
            }
        except Exception:
            pass
    return MANAGER_THRESHOLDS.get(manager_key, MANAGER_THRESHOLDS["swing"])


# ── 매니저 정의 ─────────────────────────────────────────────

MANAGERS: dict[str, dict] = {
    "scalp": {
        "name": "제시 리버모어",
        "emoji": "⚡",
        "title": "단타 매니저",
        "persona": (
            "너는 제시 리버모어(Jesse Livermore)의 투자 철학을 따르는 단타 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 추세를 따르라. 시장과 싸우지 마라\n"
            "- 손절은 빠르게(-3%), 수익은 달리게 하라\n"
            "- 거래량이 진실을 말한다 (전일 대비 200% 이상 거래량 급증 = 진입 신호)\n"
            "- 감정을 배제하고 가격만 본다\n"
            "- 피벗 포인트(돌파/이탈)에서만 진입\n\n"
            "[정량 스크리닝 기준 — 리버모어 피벗 시스템]\n"
            "진입 조건 (3개 이상 충족 시 매수):\n"
            "1. 거래량: 20일 평균 대비 200% 이상 급증\n"
            "2. 가격: 20일 이동평균선 돌파 (종가 기준)\n"
            "3. RSI: 40~65 구간 (과매수 직전 모멘텀 구간)\n"
            "4. 일봉 캔들: 양봉 + 시가 대비 +2% 이상 상승\n"
            "5. 전일 대비 갭: 갭업 2% 이상 시 강한 매수세 확인\n\n"
            "청산 조건:\n"
            "- 목표 수익: +5~8% (1~3일 내)\n"
            "- 손절: -3% (당일 또는 익일 즉시)\n"
            "- 거래량 급감 시 (전일 대비 50% 이하) → 관망 전환\n\n"
            "분석 시 반드시: 거래량 비율, RSI, 20일선 대비 위치, 당일 등락률 포함.\n\n"
            "[한국 시장 특수 — 단타]\n"
            "- 외국인/기관 동시 순매수 종목은 추세 신뢰도 높음\n"
            "- 개인 순매수 급증 + 외인/기관 매도 = '개미 털기' 패턴 경계\n"
            "- 선물 만기일(매월 둘째 목요일) 전후 변동성 극대화 → 손절 -2%로 강화\n"
            "- 프로그램 매매 순매도 -3000억 이상: 추세 반전 위험\n"
            "- 신용잔고 급증 종목: 단기 천장 가능성 → 진입 회피\n"
            "말투: 단호하고 간결. '~해야 합니다', '시장이 말하고 있습니다'.\n"
        ),
        "holding_type": "scalp",
        "greeting": (
            "⚡ 제시 리버모어입니다.\n"
            "이 종목의 추세를 추적하겠습니다.\n"
            "핵심은 타이밍. 시장이 말할 때 움직이세요."
        ),
    },
    "swing": {
        "name": "윌리엄 오닐",
        "emoji": "🔥",
        "title": "스윙 매니저",
        "persona": (
            "너는 윌리엄 오닐(William O'Neil)의 CAN SLIM을 따르는 스윙 전문 매니저다.\n"
            "핵심 원칙 (CAN SLIM 정량 기준):\n"
            "- C: 최근 분기 EPS 성장률 ≥ 25% (전년 동기 대비)\n"
            "- A: 연간 EPS 성장률 ≥ 25% (최근 3년)\n"
            "- N: 신제품/신사업/52주 신고가 돌파\n"
            "- S: 발행주식수 적정, 거래량 50일 평균 대비 150% 이상 급증\n"
            "- L: 업종 RS(상대강도) 상위 20% 이내\n"
            "- I: 기관 보유 비중 증가 추세 (최근 3개월 순매수)\n"
            "- M: 코스피/나스닥 200일선 위에서 거래 (상승장)\n\n"
            "[정량 스크리닝 기준 — CAN SLIM 체크리스트]\n"
            "진입 조건 (5개 이상 충족 시 매수):\n"
            "1. EPS 분기 성장 ≥ 25%\n"
            "2. EPS 연간 성장 ≥ 25% (3년 평균)\n"
            "3. ROE ≥ 17%\n"
            "4. 52주 신고가 대비 -15% 이내 (베이스 형성)\n"
            "5. 거래량: 50일 평균 대비 150% 이상 돌파 시점\n"
            "6. 기관 순매수 3개월 연속 양(+)\n"
            "7. 시장 방향: 주요 지수 200일선 위\n\n"
            "차트 패턴 (오닐 핵심):\n"
            "- 컵앤핸들: 7~65주 베이스, 핸들 하락 -8~12%\n"
            "- 더블바텀: 두번째 바닥이 첫번째보다 높거나 같을 것\n"
            "- 플랫베이스: 5주 이상 횡보, 변동폭 15% 이내\n\n"
            "청산 기준:\n"
            "- 매수가 대비 -7~8% 하락 시 무조건 손절 (오닐의 철칙)\n"
            "- 목표: +20~25% (1~4주)\n"
            "- 20일선 이탈 3일 연속 시 청산 검토\n\n"
            "분석 시 반드시: EPS 성장률, RS순위, 차트 패턴명, 기관 수급 포함.\n\n"
            "[한국 시장 특수 — 스윙]\n"
            "- 외국인 5일 연속 순매수 = 편입 신호 → 동조 매수 유효\n"
            "- 기관 연기금 매수 패턴: 저점 분할매수 → 바닥 확인 신호\n"
            "- 산업 밸류체인 분석: 업스트림(소재) 선행 → 다운스트림(완성품) 후행\n"
            "- 12월 대주주 양도세 매도 시즌: 11월 말~12월 중순 수급 악화\n"
            "- 분기말 윈도우드레싱: 대형 우량주 일시적 매수세\n"
            "말투: 데이터 중심, 체계적. '통계적으로', '과거 패턴에 따르면'.\n"
        ),
        "holding_type": "swing",
        "greeting": (
            "🔥 윌리엄 오닐입니다.\n"
            "CAN SLIM 기준으로 이 종목을 관리하겠습니다.\n"
            "차트 패턴과 수급이 핵심입니다."
        ),
    },
    "position": {
        "name": "피터 린치",
        "emoji": "📊",
        "title": "포지션 매니저",
        "persona": (
            "너는 피터 린치(Peter Lynch)의 투자 철학을 따르는 포지션 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 아는 것에 투자하라 (생활 속 투자 기회)\n"
            "- PEG 비율이 1 미만인 성장주를 찾아라\n"
            "- 10배 주식(Tenbagger)의 가능성을 항상 염두\n"
            "- 과매도된 우량주는 기회\n"
            "- 분산 투자하되, 확신 있는 곳에 집중\n\n"
            "[정량 스크리닝 기준 — 린치 6분류 시스템]\n"
            "종목 분류 (린치가 실제 사용한 6가지 카테고리):\n"
            "1. 저성장주(Slow Growers): EPS 성장 2~4%, 배당수익률 ≥ 3%\n"
            "2. 우량주(Stalwarts): EPS 성장 10~12%, PER 10~15배\n"
            "3. 고성장주(Fast Growers): EPS 성장 ≥ 20%, PEG < 1.0\n"
            "4. 경기순환주(Cyclicals): PER 최저 구간 + 업황 반등 시그널\n"
            "5. 회생주(Turnarounds): 영업이익 흑자 전환, 부채비율 하락 추세\n"
            "6. 자산주(Asset Plays): PBR < 0.7, 숨겨진 자산 (토지/IP/자회사)\n\n"
            "필수 체크 지표:\n"
            "- PEG 비율: (PER / EPS 성장률) < 1.0이면 저평가\n"
            "- PER: 동종 업계 평균 대비 할인율\n"
            "- ROE: ≥ 15% (자본 효율)\n"
            "- 매출 성장률: ≥ 10% (최근 3년)\n"
            "- 영업이익률: ≥ 10% (수익성)\n"
            "- 부채비율: ≤ 100% (재무 안정성)\n"
            "- 현금흐름: 영업현금흐름 양(+), FCF 양(+)\n\n"
            "청산 기준:\n"
            "- PEG > 2.0 도달 시 (과대평가)\n"
            "- 린치의 2분 스토리가 무너졌을 때 (투자 근거 소멸)\n"
            "- 목표: +30~50% (1~6개월)\n\n"
            "분석 시 반드시: PEG, 린치 6분류 중 어디 해당, 매출/이익 성장률, 투자 스토리 포함.\n\n"
            "[한국 시장 특수 — 포지션]\n"
            "- 한국 시장 PER 밴드: KOSPI 역사적 저평가 8~10배, 적정 12~14배\n"
            "- 수출주(반도체/자동차/조선): 환율 1,300원+ 환차익 효과\n"
            "- 2차전지/바이오: PEG 기준 적용 시 성장 프리미엄 할인 필요\n"
            "- 지배구조 할인: 한국 기업의 구조적 할인 요인 → PBR 0.8 이하면 자산주 후보\n"
            "- 한국 특수 경기순환: 반도체 사이클(3~4년), 조선 사이클(7~10년)\n"
            "말투: 친근하고 스토리텔링. '이 회사는 ~한 이유로', '일상에서 볼 수 있듯이'.\n"
        ),
        "holding_type": "position",
        "greeting": (
            "📊 피터 린치입니다.\n"
            "이 종목의 성장 스토리를 함께 지켜보겠습니다.\n"
            "PEG와 펀더멘털이 핵심입니다."
        ),
    },
    "long_term": {
        "name": "워렌 버핏",
        "emoji": "💎",
        "title": "장기 매니저",
        "persona": (
            "너는 워렌 버핏(Warren Buffett)의 가치투자 철학을 따르는 장기 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 경제적 해자(Moat)가 있는 기업만\n"
            "- 내재가치 대비 안전마진 30% 이상\n"
            "- 10년 보유할 수 없으면 10분도 보유하지 마라\n"
            "- 시장의 두려움이 기회\n"
            "- 복리의 마법을 믿어라\n\n"
            "[정량 스크리닝 기준 — 버핏 가치투자 체크리스트]\n"
            "경제적 해자 판별 (5가지 해자 유형):\n"
            "1. 브랜드 파워: 소비자 프리미엄 가격 수용 (영업이익률 ≥ 15%)\n"
            "2. 전환 비용: 고객 이탈률 < 5% (반복 매출 비중 ≥ 70%)\n"
            "3. 네트워크 효과: 사용자 증가 → 가치 증가 (MAU/매출 상관관계)\n"
            "4. 비용 우위: 동종 대비 영업이익률 상위 25%\n"
            "5. 규제 장벽: 인허가/특허/독점적 지위\n\n"
            "필수 재무 기준 (버핏이 실제 사용한 지표):\n"
            "- ROE: ≥ 15% (최근 5년 평균, 일관성 중요)\n"
            "- 순이익률: ≥ 10% (가격 결정력 증거)\n"
            "- 부채비율: ≤ 80% (장기 부채 / 순이익 < 4년)\n"
            "- 배당 성장: 최근 5년 연속 배당 증가 또는 자사주 매입\n"
            "- 이익 잉여금: 매년 증가 (내부 유보 → 재투자)\n"
            "- 자본적 지출: 순이익 대비 CAPEX ≤ 50% (가벼운 자산 구조)\n"
            "- FCF: 5년 연속 양(+)\n\n"
            "내재가치 평가:\n"
            "- DCF: 향후 10년 FCF를 할인율 10%로 현재가치\n"
            "- PER 밴드: 최근 10년 PER 평균 대비 현재 위치\n"
            "- 안전마진: 내재가치 대비 30% 이상 할인된 가격에서만 매수\n\n"
            "청산 기준:\n"
            "- 경제적 해자 훼손 (구조적 경쟁력 상실)\n"
            "- ROE 15% 미만 3년 연속\n"
            "- 경영진 신뢰 상실 (회계 의혹, 지배구조 문제)\n"
            "- 내재가치 대비 과대평가 50% 이상\n\n"
            "분석 시 반드시: 해자 유형, ROE 5년 추세, FCF, 배당성장, 안전마진 수준 포함.\n\n"
            "[한국 시장 특수 — 장기투자]\n"
            "- 삼성전자/SK하이닉스: 글로벌 반도체 해자 보유, DRAM/HBM 과점\n"
            "- 현대차/기아: 글로벌 3위 자동차 그룹, EV 전환기 해자 구축 중\n"
            "- 코리아 디스카운트: 지배구조, 지정학, 배당률 문제 → 장기 개선 추세\n"
            "- 환율 급등(1,400원+) 시에도 수출주는 장기 관점 유지\n"
            "- 전시/위기 시 장기투자 종목은 절대 매도하지 마라 → 조정은 추가매수 기회\n"
            "- 한국 배당주: 배당수익률 4%+ 종목(은행/통신/유틸리티) → 장기 복리 효과\n"
            "말투: 지혜롭고 장기적. '장기적으로 보면', '이 기업의 본질적 가치는'.\n"
        ),
        "holding_type": "long_term",
        "greeting": (
            "💎 워렌 버핏입니다.\n"
            "이 기업의 내재가치를 함께 분석하겠습니다.\n"
            "좋은 기업을 적정 가격에 사는 것이 핵심이죠."
        ),
    },
}

# ── 매니저 이름 조회 헬퍼 ──────────────────────────────────

def get_manager(holding_type: str) -> dict | None:
    """holding_type으로 매니저 조회. 없으면 None."""
    return MANAGERS.get(holding_type)


def get_manager_label(holding_type: str) -> str:
    """매니저 이름 라벨 (예: '⚡ 제시 리버모어')."""
    mgr = MANAGERS.get(holding_type)
    if mgr:
        return f"{mgr['emoji']} {mgr['name']}"
    return "📌 자동"


# ── 차트 요약 (scalp/swing 전용) ──────────────────────────

def build_chart_summary(
    tech, current_price: float, supply: list[dict] | None = None,
    ohlcv=None, ticker: str = "", name: str = "",
) -> str:
    """TechnicalIndicators → 매니저용 차트 요약 텍스트 (scalp/swing 전용).

    ohlcv를 전달하면 추가 시그널(변동성 돌파, 갭, 스윙, 멀티타임프레임)도 분석.
    """
    if tech is None:
        return ""

    lines = []

    # RSI
    rsi_label = "과매도" if tech.rsi < 35 else ("과매수" if tech.rsi > 65 else "중립")
    lines.append(f"RSI {tech.rsi:.0f} ({rsi_label})")

    # 거래량
    if tech.volume_ratio > 0:
        lines.append(f"거래량 20일평균 대비 {tech.volume_ratio * 100:.0f}%")

    # 이평선 대비 위치
    if current_price > 0:
        ma_parts = []
        for label, ma_val in [("5일", tech.ma5), ("20일", tech.ma20),
                               ("60일", tech.ma60), ("120일", tech.ma120)]:
            if ma_val and ma_val > 0:
                vs = (current_price - ma_val) / ma_val * 100
                ma_parts.append(f"{label}선 {'위' if vs > 0 else '아래'}{abs(vs):.1f}%")
        if ma_parts:
            lines.append("이평선: " + ", ".join(ma_parts))

    # MA 배열
    if tech.ma5 > 0 and tech.ma20 > 0 and tech.ma60 > 0:
        if tech.ma5 > tech.ma20 > tech.ma60:
            arr = "정배열"
            if tech.ma120 > 0 and tech.ma60 > tech.ma120:
                arr = "완전정배열 (5>20>60>120)"
            else:
                arr = "정배열 (5>20>60)"
            lines.append(f"MA배열: {arr}")
        elif tech.ma5 < tech.ma20 < tech.ma60:
            arr = "역배열"
            if tech.ma120 > 0 and tech.ma60 < tech.ma120:
                arr = "완전역배열 (5<20<60<120)"
            else:
                arr = "역배열 (5<20<60)"
            lines.append(f"MA배열: {arr}")
        else:
            lines.append("MA배열: 혼조")

    # MACD
    if tech.macd_signal_cross == 1:
        lines.append("MACD: 골든크로스 (매수)")
    elif tech.macd_signal_cross == -1:
        lines.append("MACD: 데드크로스 (매도)")
    elif tech.macd_histogram > 0:
        lines.append("MACD: 양전환 유지")
    else:
        lines.append("MACD: 음전환 유지")

    # BB 위치
    if 0 <= tech.bb_pctb <= 1:
        bb_label = "하단근접" if tech.bb_pctb < 0.2 else ("상단근접" if tech.bb_pctb > 0.8 else "중간")
        lines.append(f"BB위치: {tech.bb_pctb:.2f} ({bb_label})")

    # 저항선
    if tech.high_20d > 0 and current_price > 0 and tech.high_20d > current_price:
        gap = (tech.high_20d - current_price) / current_price * 100
        lines.append(f"20일고점 대비 -{gap:.1f}% (단기저항)")
    if tech.high_52w > 0 and current_price > 0 and tech.high_52w > current_price:
        gap = (tech.high_52w - current_price) / current_price * 100
        lines.append(f"52주고점 대비 -{gap:.1f}%")

    # 다이버전스
    if getattr(tech, "rsi_divergence", 0) == 1:
        lines.append("RSI 강세 다이버전스 (반등 신호)")
    elif getattr(tech, "rsi_divergence", 0) == -1:
        lines.append("RSI 약세 다이버전스 (하락 신호)")

    # BB 스퀴즈
    if getattr(tech, "bb_squeeze", False):
        lines.append("BB스퀴즈: 변동성 폭발 임박")

    # ATR (변동성)
    atr_pct = getattr(tech, "atr_pct", 0) or 0
    if atr_pct > 0:
        atr_label = "고변동" if atr_pct > 4 else ("저변동" if atr_pct < 1.5 else "보통")
        lines.append(f"ATR {atr_pct:.1f}% ({atr_label})")

    # 골든/데드크로스 (EMA50 vs EMA200)
    gc = getattr(tech, "golden_cross", 0) or 0
    if gc == 1:
        lines.append("EMA 골든크로스 (장기 매수)")
    elif gc == -1:
        lines.append("EMA 데드크로스 (장기 매도)")

    # ── 추가 시그널 (ohlcv 기반) ──
    if ohlcv is not None and len(ohlcv) >= 5:
        _append_ohlcv_signals(lines, ohlcv, tech, current_price, ticker, name)

    # 수급 (CAN SLIM의 I: 기관 보유 증가)
    if supply and len(supply) >= 2:
        foreign_days = sum(1 for d in supply[:5] if (d.get("foreign_net", 0) or 0) > 0)
        inst_days = sum(1 for d in supply[:5] if (d.get("institution_net", 0) or 0) > 0)
        if foreign_days >= 3:
            lines.append(f"외국인 5일중 {foreign_days}일 순매수")
        if inst_days >= 3:
            lines.append(f"기관 5일중 {inst_days}일 순매수")
        if foreign_days <= 1 and inst_days <= 1:
            lines.append("외인+기관 수급 부진")

        # 스텔스 매집 감지
        _append_stealth_accumulation(lines, supply, ticker, name)

    return "\n".join(lines)


def _append_ohlcv_signals(
    lines: list, ohlcv, tech, current_price: float,
    ticker: str, name: str,
) -> None:
    """OHLCV 기반 추가 시그널을 lines에 추가."""
    import pandas as pd

    try:
        df = ohlcv
        if df is None or len(df) < 5:
            return

        # 최근 2일 데이터
        last = df.iloc[-1]
        prev = df.iloc[-2]

        open_col = "open" if "open" in df.columns else "Open"
        high_col = "high" if "high" in df.columns else "High"
        low_col = "low" if "low" in df.columns else "Low"
        close_col = "close" if "close" in df.columns else "Close"
        vol_col = "volume" if "volume" in df.columns else "Volume"

        today_open = float(last.get(open_col, 0) or 0)
        prev_high = float(prev.get(high_col, 0) or 0)
        prev_low = float(prev.get(low_col, 0) or 0)
        prev_close = float(prev.get(close_col, 0) or 0)
        vol_ratio = tech.volume_ratio if tech.volume_ratio > 0 else 1.0

        # 1) 당일 등락 (시가 대비)
        if today_open > 0 and current_price > 0:
            day_from_open = (current_price - today_open) / today_open * 100
            lines.append(f"당일시가 대비 {day_from_open:+.1f}%")

        # 2) 변동성 돌파 (래리 윌리엄스)
        if today_open > 0 and prev_high > 0 and prev_low > 0:
            try:
                from kstock.signal.volatility_breakout import evaluate_breakout
                bo = evaluate_breakout(
                    ticker=ticker, name=name,
                    open_price=today_open, current_price=current_price,
                    prev_high=prev_high, prev_low=prev_low,
                    volume_ratio=vol_ratio,
                )
                if bo:
                    lines.append(
                        f"변동성돌파: 돌파가 {bo.breakout_price:,.0f}원 "
                        f"({'거래량확인' if bo.volume_confirmed else '거래량미달'})"
                    )
                    lines.append(
                        f"  목표 {bo.target_price:,.0f}원 / 손절 {bo.stop_price:,.0f}원"
                    )
            except Exception:
                pass

        # 3) 갭 시그널
        if prev_close > 0 and today_open > 0:
            try:
                from kstock.signal.gap_trader import detect_gap
                gap = detect_gap(
                    ticker=ticker, name=name,
                    prev_close=prev_close, open_price=today_open,
                    current_price=current_price, volume_ratio=vol_ratio,
                )
                if gap:
                    lines.append(f"갭: {gap.gap_type} {gap.gap_pct:+.1f}% → {gap.action}")
            except Exception:
                pass

        # 4) 스윙 시그널 평가
        try:
            from kstock.signal.swing_trader import evaluate_swing
            swing = evaluate_swing(
                ticker=ticker, name=name,
                current_price=current_price,
                rsi=tech.rsi, bb_pctb=tech.bb_pctb,
                volume_ratio_20d=vol_ratio,
                macd_signal_cross=tech.macd_signal_cross,
                confidence_score=getattr(tech, "rsi", 50),
            )
            if swing:
                lines.append(
                    f"스윙진입: 목표 +{swing.target_pct:.0f}% "
                    f"손절 {swing.stop_pct:.0f}% ({swing.hold_days}일)"
                )
                if swing.reasons:
                    lines.append(f"  사유: {', '.join(swing.reasons[:3])}")
        except Exception:
            pass

        # 5) 멀티타임프레임 정렬
        if len(df) >= 60:
            try:
                from kstock.features.timeframe import (
                    build_timeframe_data, analyze_mtf_alignment,
                )
                tf_data = build_timeframe_data(ticker, df)
                mtf = analyze_mtf_alignment(ticker, tf_data)
                alignment_label = {
                    "all_up": "전체상승 (일/주/월 일치)",
                    "all_down": "전체하락 (일/주/월 일치)",
                    "mixed_bullish": "혼조상승",
                    "mixed_bearish": "혼조하락",
                }.get(mtf.alignment, "중립")
                lines.append(f"멀티타임프레임: {alignment_label}")
                if mtf.confirmation:
                    lines.append("  주봉이 일봉 추세 확인")
            except Exception:
                pass

    except Exception:
        pass


def _append_stealth_accumulation(
    lines: list, supply: list[dict], ticker: str, name: str,
) -> None:
    """수급 데이터에서 스텔스 매집 패턴 감지."""
    try:
        if not supply or len(supply) < 5:
            return

        from kstock.signal.stealth_accumulation import (
            detect_institutional_streak,
            detect_foreign_streak,
        )

        # supply → daily amounts 변환
        inst_amounts = [float(d.get("institution_net", 0) or 0) for d in supply]
        foreign_amounts = [float(d.get("foreign_net", 0) or 0) for d in supply]

        inst_result = detect_institutional_streak(inst_amounts)
        if inst_result and inst_result.streak_days >= 3:
            lines.append(
                f"기관매집: {inst_result.streak_days}일 연속 "
                f"총 {inst_result.total_amount / 1e8:.0f}억원"
            )

        foreign_result = detect_foreign_streak(foreign_amounts)
        if foreign_result and foreign_result.streak_days >= 3:
            lines.append(
                f"외인매집: {foreign_result.streak_days}일 연속 "
                f"총 {foreign_result.total_amount / 1e8:.0f}억원"
            )
    except Exception:
        pass


# ── 재무 요약 (position/long_term 전용) ──────────────────

def build_fundamental_summary(
    info: dict | None = None,
    financials: dict | None = None,
    consensus: dict | None = None,
    supply: list[dict] | None = None,
    current_price: float = 0,
    ticker: str = "",
    name: str = "",
) -> str:
    """재무/수급 데이터 → 매니저용 펀더멘털 요약 (position/long_term 전용)."""
    lines = []

    # 밸류에이션 (info or financials)
    per = 0.0
    pbr = 0.0
    roe = 0.0
    if info:
        per = info.get("per", 0) or 0
        pbr = info.get("pbr", 0) or 0
        roe = info.get("roe", 0) or 0
    if financials:
        per = financials.get("per", 0) or per
        pbr = financials.get("pbr", 0) or pbr
        roe = financials.get("roe", 0) or roe

    if per > 0:
        per_label = "저평가" if per < 10 else ("고평가" if per > 30 else "적정")
        lines.append(f"PER {per:.1f}배 ({per_label})")
    if pbr > 0:
        pbr_label = "자산가치↑" if pbr < 1.0 else ("고평가" if pbr > 3.0 else "적정")
        lines.append(f"PBR {pbr:.2f}배 ({pbr_label})")
    if roe > 0:
        roe_label = "우수" if roe >= 15 else ("양호" if roe >= 10 else "부진")
        lines.append(f"ROE {roe:.1f}% ({roe_label})")

    # 재무 상세 (financials 테이블)
    eps = 0
    if financials:
        debt = financials.get("debt_ratio", 0) or 0
        if debt > 0:
            d_label = "안정" if debt < 100 else ("주의" if debt < 200 else "위험")
            lines.append(f"부채비율 {debt:.0f}% ({d_label})")
        op_margin = financials.get("op_margin", 0) or 0
        if op_margin > 0:
            lines.append(f"영업이익률 {op_margin:.1f}%")
        fcf = financials.get("fcf", 0) or 0
        if fcf != 0:
            lines.append(f"FCF {fcf:,.0f}억원 ({'양호' if fcf > 0 else '음(-)'})")
        eps = financials.get("eps", 0) or 0
        if eps > 0:
            lines.append(f"EPS {eps:,.0f}원")

    # info에서 추가 (dividend, foreign)
    if info:
        div_yield = info.get("dividend_yield", 0) or 0
        if div_yield > 0:
            lines.append(f"배당수익률 {div_yield:.2f}%")
        foreign = info.get("foreign_ratio", 0) or 0
        if foreign > 0:
            lines.append(f"외국인 비율 {foreign:.1f}%")

    # 컨센서스
    if consensus:
        target = consensus.get("avg_target_price", 0) or 0
        upside = consensus.get("upside_pct", 0) or 0
        buy_cnt = consensus.get("buy_count", 0) or 0
        if target > 0:
            lines.append(f"컨센서스 목표가 {target:,.0f}원 (상승여력 {upside:+.1f}%)")
        if buy_cnt > 0:
            hold_cnt = consensus.get("hold_count", 0) or 0
            sell_cnt = consensus.get("sell_count", 0) or 0
            lines.append(f"증권사 의견: 매수{buy_cnt} 보유{hold_cnt} 매도{sell_cnt}")

    # 버블/밸류에이션 분석
    if per > 0 and eps > 0 and current_price > 0:
        try:
            from kstock.signal.bubble_detector import analyze_bubble
            growth_rate = 0.0
            if financials:
                growth_rate = financials.get("earnings_growth", 0) or 0
            bubble = analyze_bubble(
                ticker=ticker, name=name,
                current_price=current_price,
                trailing_per=per, forward_per=per * 0.9,
                eps=eps,
                revenue_yoy=financials.get("revenue_yoy", 0) or 0 if financials else 0,
                op_profit_yoy=financials.get("op_profit_yoy", 0) or 0 if financials else 0,
                earnings_cagr_2y=growth_rate,
                prev_growth=growth_rate * 0.8,
            )
            lines.append(f"밸류에이션: {bubble.valuation}")
            if bubble.peg_ratio < 900:
                lines.append(f"PEG {bubble.peg_ratio:.2f} ({bubble.peg_zone})")
            if bubble.bubble_probability > 30:
                lines.append(f"거품확률 {bubble.bubble_probability:.0f}%")
            if bubble.fair_price_sector > 0:
                dev = bubble.deviation_sector_pct
                lines.append(f"섹터적정가 대비 {dev:+.0f}%")
        except Exception:
            pass

    # 수급 (최근 5일)
    if supply and len(supply) >= 2:
        foreign_days = 0
        inst_days = 0
        for d in supply[:5]:
            fn = d.get("foreign_net", 0) or 0
            inst_n = d.get("institution_net", 0) or 0
            if fn > 0:
                foreign_days += 1
            if inst_n > 0:
                inst_days += 1
        if foreign_days >= 3:
            lines.append(f"외국인 최근5일 {foreign_days}일 순매수")
        elif foreign_days == 0:
            lines.append("외국인 최근5일 연속 순매도")
        if inst_days >= 3:
            lines.append(f"기관 최근5일 {inst_days}일 순매수")

    if not lines:
        return ""
    return "\n".join(lines)


# ── 매니저별 AI 분석 ───────────────────────────────────────

def _build_shared_context_prompt(shared_context: dict | None) -> str:
    """공유 컨텍스트를 매니저 프롬프트 텍스트로 변환."""
    if not shared_context:
        return ""
    sections = []

    crisis = shared_context.get("crisis_context", "")
    if crisis and "없음" not in crisis:
        sections.append(f"[현재 위기 상황]\n{crisis[:400]}")

    post_war = shared_context.get("post_war_rotation", "")
    if post_war:
        sections.append(f"\n{post_war[:300]}")

    news = shared_context.get("global_news", "")
    if news and "없음" not in news:
        sections.append(f"[글로벌 뉴스 — 최신]\n{news[:300]}")

    policies = shared_context.get("policies", "")
    if policies and "없음" not in policies:
        sections.append(f"[활성 정책]\n{policies[:200]}")

    lessons = shared_context.get("trade_lessons", "")
    if lessons and "없음" not in lessons:
        sections.append(f"[매매 교훈 — 반복 실수 방지]\n{lessons[:200]}")

    style = shared_context.get("investor_style", "")
    if style and "없음" not in style:
        sections.append(f"[투자자 성향] {style[:150]}")

    portfolio = shared_context.get("portfolio_summary", "")
    if portfolio:
        sections.append(f"[전체 포트폴리오 현황]\n{portfolio[:300]}")

    # v9.5: YouTube 방송 인사이트
    yt_intel = shared_context.get("youtube_intelligence", "")
    if yt_intel:
        sections.append(f"{yt_intel[:300]}")

    return "\n\n".join(sections)


# ── 매니저별 데이터→액션 해석 규칙 ────────────────────────

_INTERPRETATION_RULES: dict[str, str] = {
    "scalp": (
        "\n[차트 데이터 해석 규칙 — 반드시 적용]\n"
        "매수 신호 (3개 이상 충족 시 매수 추천):\n"
        "- RSI 35~65 + 거래량 200%+ + 양봉 → 모멘텀 진입\n"
        "- 변동성돌파 시그널 + 거래량확인 → 즉시 매수\n"
        "- RSI <35 + BB하단(0.2이하) + 거래량급증 → 과매도 반등 매수\n"
        "- 갭업 3%+ + 거래량 150%+ → 추세추종 매수\n"
        "- 정배열 + MACD골든크로스 → 추세 매수\n\n"
        "매도/손절 신호:\n"
        "- RSI >70 + 거래량감소 → 차익실현\n"
        "- 역배열 + 데드크로스 → 즉시 손절\n"
        "- 갭채우기 발생 → 매도\n"
        "- 5일선 이탈 + 거래량감소 → 청산\n"
        "- 스윙 손절가 도달 → 무조건 손절\n\n"
        "관망 신호:\n"
        "- BB스퀴즈 → 방향 확인까지 대기\n"
        "- 혼조배열 + 거래량 보통 → 관망\n"
        "- 멀티타임프레임 불일치 → 관망\n\n"
        "제공된 시그널 데이터(변동성돌파, 갭, 스윙진입, 멀티타임프레임, 매집)를 반드시 해석하여 근거로 사용하라.\n"
    ),
    "swing": (
        "\n[차트 데이터 해석 규칙 — 반드시 적용]\n"
        "매수 신호 (CAN SLIM + 차트):\n"
        "- 정배열 + 기관/외인 3일+ 순매수 + RSI 40~60 → 매수\n"
        "- 스윙진입 시그널 발생 → 진입 근거와 리스크 명확히 제시\n"
        "- 멀티타임프레임 전체상승 + 거래량 150%+ → 강한 매수\n"
        "- BB하단 + RSI<35 + MACD골든크로스 → 반등 매수\n"
        "- 기관/외인 매집 감지 → 스마트머니 추종 매수\n"
        "- 52주고점 대비 -15%이내 + 거래량급증 → 돌파 매수\n\n"
        "매도/손절 신호:\n"
        "- 20일선 이탈 3일 연속 → 청산\n"
        "- 매수가 대비 -7% → 무조건 손절 (오닐 철칙)\n"
        "- 역배열 전환 + 기관 순매도 → 청산\n"
        "- RSI약세다이버전스 → 매도 준비\n"
        "- 멀티타임프레임 전체하락 → 즉시 청산\n\n"
        "관망:\n"
        "- 혼조배열 + 수급 부진 → 관망\n"
        "- 외인+기관 수급 부진 → 진입 보류\n\n"
        "스텔스매집 감지 시 반드시 언급. 스윙진입 시그널 강도와 근거를 평가하라.\n"
    ),
    "position": (
        "\n[펀더멘털 데이터 해석 규칙 — 반드시 적용]\n"
        "매수 신호:\n"
        "- PEG <1 + ROE >15% + 매출성장 → 고성장 저평가 매수\n"
        "- PER <10 + FCF 양(+) + 부채비율 <100% → 가치주 매수\n"
        "- 밸류에이션 '저평가' + 컨센서스 상승여력 20%+ → 매수\n"
        "- PBR <1.0 + ROE >10% → 자산주 매수\n"
        "- 외인/기관 5일중 4일+ 순매수 → 수급 확인 매수\n\n"
        "매도 신호:\n"
        "- PEG >2.0 → 과대평가, 매도 검토\n"
        "- 거품확률 50%+ → 차익실현 권고\n"
        "- ROE <10% + 부채비율 >200% → 위험, 축소\n"
        "- 컨센서스 하향 + 외인 순매도 → 축소 검토\n"
        "- 섹터적정가 대비 +50%이상 → 차익실현\n\n"
        "밸류에이션/버블 분석 결과가 있으면 반드시 해석하여 근거로 사용하라.\n"
        "린치 6분류 중 해당 카테고리를 명시하라.\n"
    ),
    "long_term": (
        "\n[펀더멘털 데이터 해석 규칙 — 반드시 적용]\n"
        "매수 신호 (버핏 가치투자):\n"
        "- ROE >15% + 부채비율 <80% + FCF 양(+) → 해자 기업 매수\n"
        "- PBR <1.0 + 배당수익률 >3% + ROE >10% → 자산가치 매수\n"
        "- 밸류에이션 '저평가' + PEG <1 → 안전마진 확보 매수\n"
        "- 영업이익률 >15% + 외인비율 >20% → 우량주 매수\n"
        "- 섹터적정가 대비 -30%이상 할인 → 안전마진 매수\n\n"
        "매도 신호:\n"
        "- ROE <15% 3년 연속 → 해자 훼손, 매도 검토\n"
        "- 거품확률 60%+ → 과대평가, 비중 축소\n"
        "- 부채비율 >150% + FCF 음(-) → 재무위험, 축소\n"
        "- 밸류에이션 '과열' + 컨센서스 하향 → 차익실현\n\n"
        "보유 유지:\n"
        "- ROE >15% + FCF 양(+) + 배당 유지 → 장기 보유 계속\n"
        "- 단기 주가 하락이지만 펀더멘털 건전 → 추가매수 기회\n\n"
        "밸류에이션/버블 분석과 해자 유형을 반드시 언급하라.\n"
    ),
}


async def get_manager_analysis(
    manager_key: str,
    holdings: list[dict],
    market_context: str = "",
    question: str = "",
    shared_context: dict | None = None,
    alert_mode: str = "normal",
    performance: dict | None = None,
    manager_lessons: list[dict] | None = None,
) -> str:
    """매니저 페르소나로 보유종목 분석 (Haiku 기반, 저비용).

    v7.0: shared_context를 통해 위기/뉴스/교훈/포트폴리오 등
    전체 시스템 컨텍스트를 공유받아 일관된 분석 수행.
    v8.1: alert_mode에 따른 전시/경계 맞춤 분석.
    v9.0: performance/manager_lessons로 성과 피드백 + 매니저별 교훈 주입.
    """
    manager = MANAGERS.get(manager_key)
    if not manager:
        return f"알 수 없는 매니저 유형: {manager_key}"

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return f"{manager['emoji']} {manager['name']}: API 키 없음"

    try:
        import httpx

        # 공유 컨텍스트에서 위기/뉴스/교훈 등 조합
        shared_prompt = _build_shared_context_prompt(shared_context)

        # 공유 컨텍스트 없으면 기본 위기 로드 (하위 호환)
        if not shared_prompt:
            try:
                import yaml
                crisis_path = os.path.join(
                    os.path.dirname(__file__), "..", "..", "config", "crisis_events.yaml"
                )
                if os.path.exists(crisis_path):
                    with open(crisis_path, encoding="utf-8") as f:
                        cdata = yaml.safe_load(f) or {}
                    active = cdata.get("active_crises", [])
                    if active:
                        shared_prompt = (
                            "\n[현재 위기 상황]\n"
                            f"{active[0].get('description', '')}\n"
                            f"수혜 섹터: {', '.join(active[0].get('beneficiary_sectors', [])[:3])}\n"
                            f"피해 섹터: {', '.join(active[0].get('damaged_sectors', [])[:3])}\n"
                        )
            except Exception:
                pass

        # 시장 상황별 매매 지침
        situation_directive = ""
        if alert_mode == "wartime":
            situation_directive = (
                "\n[🔴 전시 경계 모드 — 필수 적용]\n"
                "현재 국내 증시 전반이 전시/폭락 상황이다.\n"
                "- 모든 분석에 '전시 상황'을 반드시 반영하라\n"
                "- 경기민감 섹터(반도체/2차전지/자동차 등): 비중 축소 또는 손절 권고\n"
                "- 방어 섹터(의료/필수소비재/유틸리티): 보유 유지 권고\n"
                "- 신규 매수: 매우 높은 확신 종목만 (신뢰도 80%↑)\n"
                "- 손절 기준 -5%로 강화 (평시 -7%)\n"
                "- 현금 비중 40% 이상 유지 권고\n"
                "- 종목별로 '보유/축소/손절/분할매수' 중 명확한 액션 1개를 제시하라\n"
            )
        elif alert_mode == "elevated":
            situation_directive = (
                "\n[🟠 경계 모드 — 필수 적용]\n"
                "현재 변동성 확대 구간이다.\n"
                "- 손절 기준 -6%로 강화\n"
                "- 분할 매수 권장, 한번에 풀 매수 금지\n"
                "- 종목별 '보유/관망/분할매수' 액션을 제시하라\n"
            )

        # 매니저별 데이터→액션 해석 규칙
        interpretation = _INTERPRETATION_RULES.get(manager_key, "")

        # 성과 피드백 주입
        perf_prompt = ""
        if performance and performance.get("total_trades", 0) > 0:
            perf_text = format_manager_performance(performance)
            perf_prompt = f"\n[내 최근 매매 성과 — 반성하고 개선하라]\n{perf_text}\n"
            wr = performance.get("win_rate", 50)
            if wr < 40:
                perf_prompt += "⚠️ 승률 저조. 더 보수적으로 판단하라.\n"
            elif wr > 70:
                perf_prompt += "성과 양호. 자신감 유지하되 과신 금지.\n"

        # 매니저별 교훈 주입
        lesson_prompt = ""
        if manager_lessons:
            lesson_prompt = "\n[과거 교훈 — 같은 실수 반복 금지]\n"
            for lsn in manager_lessons[:5]:
                lesson_prompt += f"- {lsn.get('name', '')}: {lsn.get('lesson', '')}\n"

        system_prompt = (
            f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
            f"{manager['persona']}\n"
            f"{interpretation}\n"
            f"{perf_prompt}"
            f"{lesson_prompt}"
            f"\n{shared_prompt}\n"
            f"{situation_directive}\n"
            f"\n[필수 규칙]\n"
            f"호칭: 주호님\n"
            f"볼드(**) 사용 금지. 이모지로 구분.\n"
            f"장기투자 종목은 전시/단기 변동으로 매도 권유 절대 금지.\n"
            f"위기 상황에서는 수혜/피해 섹터를 명확히 구분하여 분석.\n"
            f"전쟁 후 주도주 전환 가능성도 항상 염두.\n"
            f"[가격 데이터 필수 규칙]\n"
            f"- 제공된 '현재가' 데이터만 사용. 학습 데이터의 과거 가격 사용 절대 금지.\n"
            f"- '현재가 미확인' 종목은 가격/수익률 언급하지 마라. '실시간 확인 필요'로 표기.\n"
            f"- 목업/예전 가격으로 분석하면 주호님에게 큰 손해. 절대 위반 금지.\n"
            f"종목마다 반드시 구체적 액션(매수/매도/보유/축소/관망) 1개를 명시하라.\n"
            f"간결하게. 종목당 3줄 이내.\n"
        )

        # v9.0: 산업 생태계 컨텍스트 주입
        try:
            from kstock.signal.industry_ecosystem import get_industry_context
        except ImportError:
            get_industry_context = None

        holdings_text = ""
        for h in holdings:
            cp = h.get('current_price', 0)
            price_tag = f"{cp:,.0f}원" if cp > 0 else "미확인"
            holdings_text += (
                f"- {h.get('name', '')}: 매수가 {h.get('buy_price', 0):,.0f}원, "
                f"현재가 {price_tag}, "
                f"수익률 {h.get('pnl_pct', 0):+.1f}%, "
                f"보유일 {h.get('holding_days', 0)}일\n"
            )
            for key in ("chart_summary", "fundamental_summary"):
                summary = h.get(key, "")
                if summary:
                    for line in summary.splitlines():
                        holdings_text += f"  {line}\n"
            # v9.0: 산업 생태계 정보
            if get_industry_context:
                ticker = h.get("ticker", "")
                ind_ctx = get_industry_context(ticker)
                if ind_ctx:
                    for line in ind_ctx.splitlines():
                        holdings_text += f"  {line}\n"

        user_prompt = ""
        if market_context:
            user_prompt += f"[시장 상황]\n{market_context}\n\n"
        user_prompt += (
            f"[{manager['emoji']} {manager['title']} 담당 종목]\n{holdings_text}\n"
        )
        if question:
            user_prompt += f"\n[사용자 질문] {question}\n"
        user_prompt += (
            f"\n{manager['name']}의 관점에서 각 종목을 분석하고 "
            f"구체적 행동 제안을 해주세요."
        )

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 900,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis = data["content"][0]["text"].strip().replace("**", "")
                header = f"{manager['emoji']} {manager['name']} ({manager['title']})\n{'━' * 20}\n\n"
                return header + analysis
            else:
                logger.warning("Manager API %s: %d", manager_key, resp.status_code)
                return f"{manager['emoji']} {manager['name']}: 분석 실패"

    except Exception as e:
        logger.error("Manager analysis error %s: %s", manager_key, e)
        return f"{manager['emoji']} {manager['name']}: 분석 오류"


async def recommend_investment_type(
    ticker: str, name: str, price: float = 0, market_cap: str = "",
) -> str:
    """AI가 종목 특성 분석 → scalp/swing/position/long_term 중 추천.

    Returns:
        추천 투자유형 키 (scalp, swing, position, long_term) 또는 "" (실패 시)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    try:
        import httpx

        prompt = (
            f"한국 주식 종목 '{name}'({ticker})의 적합한 투자유형을 하나만 선택해줘.\n"
            f"현재가: {price:,.0f}원\n" if price > 0 else ""
        ) + (
            f"시가총액: {market_cap}\n" if market_cap else ""
        ) + (
            "\n선택지:\n"
            "- scalp: 초단기 1~3일 (변동성 큰 테마주, 소형주)\n"
            "- swing: 스윙 1~4주 (기술적 반등, 이벤트 드리븐)\n"
            "- position: 포지션 1~6개월 (실적 턴어라운드, 섹터 성장)\n"
            "- long_term: 장기 6개월+ (대형 우량주, 배당주, ETF)\n\n"
            "반드시 scalp, swing, position, long_term 중 하나만 답해. 다른 말 하지마."
        )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 20,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if resp.status_code == 200:
                answer = resp.json()["content"][0]["text"].strip().lower()
                for key in ["long_term", "position", "swing", "scalp"]:
                    if key in answer:
                        return key
        return ""
    except Exception as e:
        logger.debug("recommend_investment_type error: %s", e)
        return ""


async def _analyze_picks_for_manager(
    manager_key: str,
    picks: list[dict],
    market_context: str = "",
    shared_context: dict | None = None,
    alert_mode: str = "normal",
    regime_weight: float = 1.0,
) -> str:
    """단일 매니저가 자기 horizon 종목을 분석 (Haiku 기반).

    v7.0: shared_context를 통해 위기/포트폴리오 중복/뉴스 반영.
    v8.1: alert_mode에 따른 전시/경계 맞춤 추천.
    """
    manager = MANAGERS.get(manager_key)
    if not manager or not picks:
        if manager:
            return f"{manager['emoji']} {manager['name']}: 추천 종목 없음"
        return ""

    # #5 레짐 가중치: 0.3 이하면 해당 매니저 추천 보류
    if regime_weight <= 0.3:
        return (
            f"{manager['emoji']} {manager['name']}: "
            f"현재 고변동성 구간 — 추천 보류 (가중치 {regime_weight:.1f})"
        )

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        # 폴백: AI 없이 기본 포맷
        lines = [f"{manager['emoji']} {manager['name']} ({manager['title']})"]
        lines.append(f"{'━' * 20}")
        for i, p in enumerate(picks[:3], 1):
            lines.append(
                f"\n{i}. {p.get('name', '')} ({p.get('ticker', '')})\n"
                f"   현재가: {p.get('price', 0):,.0f}원 | 점수: {p.get('score', 0):.0f}점"
            )
        return "\n".join(lines)

    try:
        import httpx

        # 공유 컨텍스트 텍스트 생성
        shared_prompt = _build_shared_context_prompt(shared_context)

        picks_text = ""
        for i, p in enumerate(picks[:3], 1):
            picks_text += (
                f"{i}. {p.get('name', '')} ({p.get('ticker', '')})\n"
                f"   현재가: {p.get('price', 0):,.0f}원\n"
                f"   점수: {p.get('score', 0):.0f} | RSI: {p.get('rsi', 0):.0f}\n"
                f"   ATR: {p.get('atr_pct', 0):.1f}% | E[R]: {p.get('expected_return', 0):+.1f}%\n"
                f"   목표: +{p.get('target_pct', 0):.0f}% | 손절: {p.get('stop_pct', 0):.0f}%\n"
            )
            for key in ("chart_summary", "fundamental_summary"):
                summary = p.get(key, "")
                if summary:
                    for line in summary.splitlines():
                        picks_text += f"   {line}\n"

        # 시장 상황별 지침
        alert_directive = ""
        if alert_mode == "wartime":
            alert_directive = (
                "\n[🔴 전시 경계 모드]\n"
                "국내 증시 전반 하락/폭락 상황. 추천 시 반드시 반영:\n"
                "- 방어 섹터(의료/필수소비재/유틸리티) 종목 우선 추천\n"
                "- 경기민감 섹터 종목은 '관망' 또는 '진입 보류' 권고\n"
                "- 매수 추천 시 반드시 분할 진입 강조\n"
                "- '추천하지 않음'도 가능 — 무리한 추천 금지\n"
            )
        elif alert_mode == "elevated":
            alert_directive = (
                "\n[🟠 경계 모드]\n"
                "변동성 확대 구간. 분할 매수 권장, 풀 매수 금지.\n"
            )

        # 매니저별 데이터→액션 해석 규칙
        interpretation = _INTERPRETATION_RULES.get(manager_key, "")

        system_prompt = (
            f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
            f"{manager['persona']}\n"
            f"{interpretation}\n"
        )
        if shared_prompt:
            system_prompt += f"\n{shared_prompt}\n"
        system_prompt += alert_directive
        system_prompt += (
            f"\n[필수 규칙]\n"
            f"호칭: 주호님\n"
            f"볼드(**) 사용 금지. 이모지로 구분.\n"
            f"제공된 데이터만 사용. 학습 데이터의 과거 가격 사용 절대 금지.\n"
            f"위기 상황에서는 수혜/피해 섹터를 명확히 구분.\n"
            f"이미 보유 중인 종목과 중복 추천 시 '추가매수' vs '신규 진입' 구분.\n"
            f"종목당 2~3줄. 핵심만. 이유+액션.\n"
        )

        user_prompt = ""
        if market_context:
            user_prompt += f"[시장 상황]\n{market_context}\n\n"
        # #5 레짐 가중치 컨텍스트
        if regime_weight < 1.0:
            user_prompt += (
                f"[레짐 가중치: {regime_weight:.1f}]\n"
                f"현재 시장 변동성으로 인해 당신의 영향력이 축소됨.\n"
                f"더 보수적으로 추천하고, 확신 높은 종목만 선별.\n\n"
            )
        elif regime_weight > 1.0:
            user_prompt += (
                f"[레짐 가중치: {regime_weight:.1f}]\n"
                f"현재 시장이 당신의 전략에 유리한 환경.\n"
                f"적극적으로 추천 가능.\n\n"
            )
        user_prompt += (
            f"[{manager['emoji']} 후보 종목]\n{picks_text}\n"
            f"위 종목 중 가장 추천하는 1~2개를 선정하고,\n"
            f"{manager['name']}의 관점에서 간결하게 분석해주세요.\n"
            f"현재 위기/시장 상황을 반드시 반영하세요.\n"
            f"형식: 종목명 — 한줄 핵심 이유 + 액션\n"
        )

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis = data["content"][0]["text"].strip().replace("**", "")
                header = f"{manager['emoji']} {manager['name']} ({manager['title']})\n{'━' * 20}\n"
                return header + analysis
            else:
                logger.warning("Manager picks API %s: %d", manager_key, resp.status_code)

    except Exception as e:
        logger.error("Manager picks error %s: %s", manager_key, e)

    # 폴백
    lines = [f"{manager['emoji']} {manager['name']} ({manager['title']})", f"{'━' * 20}"]
    for i, p in enumerate(picks[:3], 1):
        lines.append(
            f"{i}. {p.get('name', '')} — {p.get('price', 0):,.0f}원 "
            f"(점수 {p.get('score', 0):.0f})"
        )
    return "\n".join(lines)


# 매니저별 horizon 매핑 (스캔 엔진 호라이즌 → 매니저)
MANAGER_HORIZON_MAP = {
    "scalp": "scalp",
    "swing": "short",
    "position": "mid",
    "long_term": "long",
}


async def get_all_managers_picks(
    picks_by_horizon: dict[str, list[dict]],
    market_context: str = "",
    shared_context: dict | None = None,
    alert_mode: str = "normal",
    vix: float = 20.0,
) -> dict[str, str]:
    """4매니저 동시 분석 (asyncio.gather). 각 매니저가 자기 horizon 종목 분석.

    v7.0: shared_context를 통해 위기/뉴스/교훈/포트폴리오 등
    전체 시스템 컨텍스트를 공유받아 일관된 추천 수행.
    v8.1: alert_mode에 따른 전시/경계 맞춤 추천.
    v9.1: vix 기반 레짐 가중치 적용 (고변동성 시 단타 축소, 장기 확대).

    Args:
        picks_by_horizon: {"scalp": [picks], "short": [picks], "mid": [...], "long": [...]}
        market_context: 시장 상황 텍스트
        shared_context: 공유 컨텍스트 (위기/뉴스/교훈/포트폴리오 등)
        alert_mode: 시장 경계 수준 (normal/elevated/wartime)
        vix: VIX 지수 (레짐 가중치 결정)

    Returns:
        {manager_key: analysis_text} — scalp/swing/position/long_term 키
    """
    import asyncio

    tasks = {}
    for mgr_key, horizon in MANAGER_HORIZON_MAP.items():
        picks = picks_by_horizon.get(horizon, [])
        weight = get_regime_weight(mgr_key, vix)
        tasks[mgr_key] = _analyze_picks_for_manager(
            mgr_key, picks, market_context, shared_context, alert_mode,
            regime_weight=weight,
        )

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    output = {}
    for (mgr_key, _), result in zip(tasks.items(), results):
        if isinstance(result, Exception):
            manager = MANAGERS.get(mgr_key, {})
            emoji = manager.get("emoji", "📌")
            name = manager.get("name", mgr_key)
            output[mgr_key] = f"{emoji} {name}: 분석 오류"
            logger.error("Manager %s gather error: %s", mgr_key, result)
        else:
            output[mgr_key] = result

    return output


async def get_manager_greeting(holding_type: str, name: str, ticker: str) -> str:
    """종목 등록 시 매니저 인사 + 간단 첫 분석."""
    manager = MANAGERS.get(holding_type)
    if not manager:
        return f"✅ {name} 등록 완료"

    greeting = manager["greeting"]
    return (
        f"{manager['emoji']} {name} ({ticker}) 등록 완료\n\n"
        f"{greeting}\n\n"
        f"📌 이 종목은 {manager['name']}이 관리합니다."
    )


# ── 회복 탄력성 점수 ───────────────────────────────────────

def compute_recovery_score(tech, day_change: float = 0) -> int:
    """기술적 지표 기반 회복 탄력성 점수 (0~100).

    과매도 반등 가능성이 높을수록 높은 점수.
    tech: TechnicalIndicators 또는 유사 객체 (rsi, bb_pctb, macd_signal_cross 등).
    """
    score = 0
    # RSI 과매도 (30↓: +30, 40↓: +15)
    if tech.rsi <= 30:
        score += 30
    elif tech.rsi <= 40:
        score += 15
    # BB 하단 근접 (%B 0.2↓: +20, 0.3↓: +10)
    if tech.bb_pctb <= 0.2:
        score += 20
    elif tech.bb_pctb <= 0.3:
        score += 10
    # MACD 골든크로스 (+15)
    if tech.macd_signal_cross > 0:
        score += 15
    # 거래량 급증 (200%+: +20, 150%+: +10)
    if tech.volume_ratio >= 2.0:
        score += 20
    elif tech.volume_ratio >= 1.5:
        score += 10
    # RSI 상승 다이버전스 (+15) — 가격↓ but RSI↑
    if getattr(tech, "rsi_divergence", 0) > 0:
        score += 15
    return min(score, 100)


# ── 매니저 관심종목 매수 스캔 ──────────────────────────────

async def scan_manager_domain(
    manager_key: str,
    watchlist_stocks: list[dict],
    market_context: str = "",
    alert_mode: str = "normal",
) -> str:
    """매니저가 관심종목에서 매수 타이밍 종목을 스캔.

    Args:
        manager_key: scalp/swing/position/long_term
        watchlist_stocks: 관심종목 리스트 (ticker, name, price, day_change 등)
        market_context: 시장 상황 텍스트
        alert_mode: normal/elevated/wartime

    Returns:
        매수 추천 텍스트 또는 빈 문자열
    """
    manager = MANAGERS.get(manager_key)
    if not manager or not watchlist_stocks:
        return ""

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    # 종목 데이터 정리 — 기술적 지표 포함
    stocks_text = ""
    # 전시모드: 회복 탄력성 순으로 정렬 (RSI 낮고 거래량 높은 종목 우선)
    sorted_stocks = list(watchlist_stocks[:20])
    if alert_mode in ("wartime", "elevated"):
        sorted_stocks.sort(key=lambda w: (
            w.get("rsi", 50),  # RSI 낮은 순 (과매도)
            -(w.get("vol_ratio", 0) or 0),  # 거래량 높은 순
        ))

    for w in sorted_stocks[:15]:
        price = w.get("price", 0)
        change = w.get("day_change", 0)
        rsi = w.get("rsi", 0)
        vol_ratio = w.get("vol_ratio", 0)
        bb_pctb = w.get("bb_pctb", -1)
        macd_cross = w.get("macd_cross", 0)
        drop_from_high = w.get("drop_from_high", 0)
        recovery_score = w.get("recovery_score", 0)

        line = f"- {w.get('name', '')} ({w.get('ticker', '')})"
        if price > 0:
            line += f": {price:,.0f}원"
        if change != 0:
            line += f", 등락 {change:+.1f}%"
        if rsi > 0:
            line += f", RSI {rsi:.0f}"
        if vol_ratio > 0:
            line += f", 거래량비 {vol_ratio:.0f}%"
        if 0 <= bb_pctb <= 1:
            line += f", BB {bb_pctb:.2f}"
        if macd_cross != 0:
            line += ", MACD골든크로스" if macd_cross > 0 else ""
        if drop_from_high < -10:
            line += f", 고점대비 {drop_from_high:.0f}%"
        if recovery_score > 0:
            line += f", 회복점수 {recovery_score:.0f}"
        stocks_text += line + "\n"

    if not stocks_text.strip():
        return ""

    situation = ""
    if alert_mode == "wartime":
        situation = (
            "\n[🔴 전시 모드 — 하락장 기회 탐색]\n"
            "국내 증시가 크게 하락한 상황이다.\n"
            "핵심 분석 포인트:\n"
            "1. 회복 탄력성: RSI 30↓ + 거래량 급증 = 과매도 반등 후보\n"
            "2. 바닥 신호: BB 하단(0.2↓) + MACD 골든크로스 = 반전 신호\n"
            "3. 방어력: 고점 대비 하락폭이 시장보다 작은 종목 = 강한 종목\n"
            "4. 수급 전환: 외인/기관 매도 → 매수 전환 포착\n\n"
            "추천 기준: 확신 80%↑, 분할매수 필수, 방어섹터 우선.\n"
            "추천 시 '단타 적합' 또는 '스윙 적합' 명시.\n"
            "추천 없으면 '현재 관망 — 바닥 확인 후 진입'으로.\n"
        )
    elif alert_mode == "elevated":
        situation = (
            "\n[🟠 경계 모드]\n"
            "분할 매수만 권장. 한번에 풀 매수 금지.\n"
            "RSI 과매도 + 거래량 증가 종목 우선 검토.\n"
        )

    system_prompt = (
        f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
        f"{manager['persona']}\n"
        f"{situation}\n"
        f"[필수 규칙]\n"
        f"호칭: 주호님. 볼드(**) 사용 금지. 이모지로 구분.\n"
        f"제공된 가격 데이터만 사용. 학습 데이터의 과거 가격 절대 금지.\n"
        f"관심종목 중에서 지금 매수 타이밍인 종목을 골라 추천.\n"
        f"추천 종목이 없으면 '현재 매수 타이밍 종목 없음'이라고 답해.\n"
        f"추천 시: 종목명, 매수 이유(2줄), 단타/스윙 적합 여부 제시.\n"
        f"가격은 제공된 데이터만 사용. 지지선/목표가/손절가 추측 금지.\n"
    )

    user_prompt = (
        f"[시장 상황]\n{market_context}\n\n"
        f"[{manager['emoji']} 관심 종목 — 기술적 데이터 포함]\n{stocks_text}\n"
    )
    if alert_mode == "wartime":
        user_prompt += (
            "위 종목 중:\n"
            "1. 회복 탄력성이 좋은 종목 (과매도 반등 후보)\n"
            "2. 단타/스윙으로 적합한 종목\n"
            "3. 매수하면 안 되는 종목 (추가 하락 위험)\n"
            "을 구분해서 분석해줘."
        )
    else:
        user_prompt += "위 종목 중 매수 추천 종목이 있으면 1~3개 선정하고 이유를 설명해줘."

    try:
        import httpx
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 600,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                analysis = data["content"][0]["text"].strip().replace("**", "")
                header = f"{manager['emoji']} {manager['name']} 매수 스캔\n{'━' * 20}\n\n"
                return header + analysis
            else:
                logger.warning("scan_manager_domain %s: %d", manager_key, resp.status_code)
    except Exception as e:
        logger.error("scan_manager_domain error %s: %s", manager_key, e)
    return ""


# ── #1 매니저 성과 요약 텍스트 ──────────────────────────────

def format_manager_performance(perf: dict) -> str:
    """매니저 성과 dict → 프롬프트 주입용 텍스트."""
    if not perf or perf.get("total_trades", 0) == 0:
        return ""
    lines = [
        f"최근 {perf['total_trades']}건: "
        f"승률 {perf['win_rate']:.0f}% "
        f"({perf['wins']}승 {perf['losses']}패)",
        f"평균수익 {perf['avg_pnl']:+.1f}%",
    ]
    if perf["avg_win"]:
        lines.append(f"승리 평균 +{perf['avg_win']:.1f}% / 패배 평균 {perf['avg_loss']:.1f}%")
    if perf.get("avg_hold_days"):
        lines.append(f"평균 보유일 {perf['avg_hold_days']}일")
    return "\n".join(lines)


# ── #2 크로스매니저 컨센서스 ─────────────────────────────────

def detect_consensus(picks_by_manager: dict[str, list[dict]]) -> list[dict]:
    """4매니저 추천 결과에서 2명 이상 겹치는 종목 추출."""
    ticker_managers: dict[str, list[str]] = {}
    ticker_info: dict[str, dict] = {}

    for mgr_key, picks in picks_by_manager.items():
        for p in picks[:3]:
            tk = p.get("ticker", "")
            if not tk:
                continue
            ticker_managers.setdefault(tk, []).append(mgr_key)
            ticker_info[tk] = p

    consensus = []
    for tk, managers in ticker_managers.items():
        if len(managers) >= 2:
            info = ticker_info[tk]
            mgr_names = [MANAGERS[m]["name"] for m in managers if m in MANAGERS]
            consensus.append({
                "ticker": tk,
                "name": info.get("name", ""),
                "price": info.get("price", 0),
                "managers": managers,
                "manager_names": mgr_names,
                "confidence": len(managers),
            })

    consensus.sort(key=lambda x: -x["confidence"])
    return consensus


def format_consensus(consensus_list: list[dict]) -> str:
    """컨센서스 종목 → 텔레그램 메시지."""
    if not consensus_list:
        return ""
    lines = ["🤝 매니저 합의 종목", "━" * 20]
    for c in consensus_list:
        mgr_text = " + ".join(c["manager_names"])
        lines.append(
            f"\n📌 {c['name']} ({c['ticker']})"
            f"\n   {mgr_text} 동시 추천"
            f"\n   신뢰도: {'⭐' * c['confidence']}"
        )
    return "\n".join(lines)


# ── #5 레짐별 매니저 가중치 ──────────────────────────────────

REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "calm": {"scalp": 1.2, "swing": 1.1, "position": 1.0, "long_term": 0.8},
    "normal": {"scalp": 1.0, "swing": 1.0, "position": 1.0, "long_term": 1.0},
    "fear": {"scalp": 0.6, "swing": 0.8, "position": 1.1, "long_term": 1.3},
    "panic": {"scalp": 0.3, "swing": 0.5, "position": 1.0, "long_term": 1.5},
}


def get_regime_weight(manager_key: str, vix: float = 20.0) -> float:
    """VIX 기반 레짐에서 매니저의 가중치 반환."""
    if vix >= 30:
        regime = "panic"
    elif vix >= 25:
        regime = "fear"
    elif vix >= 18:
        regime = "normal"
    else:
        regime = "calm"
    return REGIME_WEIGHTS.get(regime, {}).get(manager_key, 1.0)


# ── #8 포트폴리오 밸런스 조언 ────────────────────────────────

def analyze_portfolio_balance(holdings_by_type: dict[str, list]) -> str:
    """매니저별 보유종목 비중 분석 + 리밸런싱 조언."""
    total = sum(len(v) for v in holdings_by_type.values())
    if total == 0:
        return ""

    lines = ["📊 포트폴리오 밸런스 분석", "━" * 20]
    ideal = {"scalp": (10, 20), "swing": (20, 35), "position": (25, 40), "long_term": (20, 35)}
    labels = {"scalp": "단타", "swing": "스윙", "position": "포지션", "long_term": "장기"}
    warnings = []

    for mtype in ("scalp", "swing", "position", "long_term"):
        count = len(holdings_by_type.get(mtype, []))
        pct = count / total * 100 if total > 0 else 0
        lo, hi = ideal[mtype]
        status = "적정" if lo <= pct <= hi else ("과다" if pct > hi else "부족")
        lines.append(f"  {labels[mtype]}: {count}종목 ({pct:.0f}%) [{status}]")
        if pct > hi + 15:
            warnings.append(f"{labels[mtype]} 비중 과다 → 축소 검토")
        elif pct < lo - 10 and total >= 5:
            warnings.append(f"{labels[mtype]} 비중 부족 → 확대 검토")

    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"⚠️ {w}")
    else:
        lines.append("\n✅ 밸런스 양호")

    return "\n".join(lines)


# ── #9 매니저 토론 (3라운드 구조화 토론, v9.4) ──────────────

async def manager_debate(
    ticker: str,
    name: str,
    stock_data: str = "",
    market_context: str = "",
    pattern_summary: str = "",
    price_target_data: str = "",
) -> str:
    """4매니저 3라운드 구조화 토론.

    v9.4: DebateEngine을 사용한 3라운드 토론.
    - Round 1: 독립 분석
    - Round 2: 상호 반론
    - Round 3: Sonnet 종합 판결

    Returns:
        토론 결과 포맷팅된 텍스트
    """
    try:
        from kstock.bot.debate_engine import DebateEngine, format_debate_telegram

        engine = DebateEngine()
        result = await engine.run_debate(
            ticker=ticker,
            name=name,
            stock_data=stock_data,
            market_context=market_context,
            pattern_summary=pattern_summary,
            price_target_data=price_target_data,
        )
        return format_debate_telegram(result)

    except Exception as e:
        logger.error("manager_debate error: %s", e, exc_info=True)
        return f"토론 오류: {e}"


async def manager_debate_full(
    ticker: str,
    name: str,
    stock_data: str = "",
    market_context: str = "",
    pattern_summary: str = "",
    price_target_data: str = "",
):
    """3라운드 토론 실행 + DebateResult 반환 (DB 저장용).

    Returns:
        DebateResult 객체 (format_debate_telegram으로 텍스트 변환 가능)
    """
    try:
        from kstock.bot.debate_engine import DebateEngine

        engine = DebateEngine()
        return await engine.run_debate(
            ticker=ticker,
            name=name,
            stock_data=stock_data,
            market_context=market_context,
            pattern_summary=pattern_summary,
            price_target_data=price_target_data,
        )
    except Exception as e:
        logger.error("manager_debate_full error: %s", e, exc_info=True)
        return None


# ── #6 매니저 자기반성 보고서 ─────────────────────────────────

async def generate_manager_reflection(
    manager_key: str,
    performance: dict,
    recent_lessons: list[dict] | None = None,
) -> str:
    """매니저가 자기 성과를 분석하고 전략 조정을 제안."""
    mgr = MANAGERS.get(manager_key)
    if not mgr:
        return ""
    if not performance or performance.get("total_trades", 0) == 0:
        return f"{mgr['emoji']} {mgr['name']}: 최근 매매 이력 없음"

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    perf_text = format_manager_performance(performance)
    lessons_text = ""
    if recent_lessons:
        for lsn in recent_lessons[:5]:
            lessons_text += f"- {lsn.get('name', '')}: {lsn.get('lesson', '')}\n"

    try:
        import httpx
        system = (
            f"너는 {mgr['name']}이다. 자신의 매매 성과를 냉정하게 돌아보라.\n"
            f"{mgr['persona']}\n"
            f"호칭: 주호님. 볼드(**) 금지.\n"
            f"분석할 것: 1)성과 요약 2)잘한 점 3)개선할 점 4)다음 주 전략 조정\n"
            f"간결하게 핵심만. 총 8줄 이내.\n"
        )
        user = f"[내 최근 성과]\n{perf_text}\n"
        if lessons_text:
            user += f"\n[과거 교훈]\n{lessons_text}\n"
        user += "\n위 성과를 돌아보고 반성 + 다음 주 전략 조정을 제안해주세요."

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 400,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            if resp.status_code == 200:
                text = resp.json()["content"][0]["text"].strip().replace("**", "")
                header = f"{mgr['emoji']} {mgr['name']} 주간 반성\n{'━' * 20}\n\n"
                return header + text
    except Exception as e:
        logger.error("manager_reflection error %s: %s", manager_key, e)
    return ""


# ── #10 매니저 임계값 자동 조정 ──────────────────────────────

def suggest_threshold_adjustment(
    manager_key: str,
    performance: dict,
) -> dict | None:
    """성과 기반 매니저 임계값 조정 제안.

    Returns:
        {"stop_loss": new_val, "reason": "..."} or None
    """
    if not performance or performance.get("total_trades", 0) < 5:
        return None

    current = MANAGER_THRESHOLDS.get(manager_key, {})
    win_rate = performance.get("win_rate", 50)
    avg_loss = performance.get("avg_loss", 0)
    avg_win = performance.get("avg_win", 0)

    suggestions = {}

    # 승률이 낮고 평균 손실이 손절 기준과 비슷 → 손절이 너무 타이트
    stop = current.get("stop_loss", -5)
    if win_rate < 40 and avg_loss and avg_loss > stop * 0.8:
        new_stop = round(stop * 1.3, 1)  # 30% 완화
        suggestions["stop_loss"] = new_stop
        suggestions["reason"] = (
            f"승률 {win_rate:.0f}%로 낮고 평균손실({avg_loss:.1f}%)이 "
            f"손절({stop}%)에 근접 → {new_stop}%로 완화 제안"
        )

    # 승률이 높지만 평균 수익이 목표 대비 작음 → 익절이 너무 빠름
    tp1 = current.get("take_profit_1", 10)
    if win_rate > 60 and avg_win and avg_win < tp1 * 0.6:
        new_tp = round(tp1 * 1.2, 1)
        suggestions["take_profit_1"] = new_tp
        suggestions["tp_reason"] = (
            f"승률 {win_rate:.0f}% 양호하나 평균수익({avg_win:.1f}%)이 "
            f"목표({tp1}%) 대비 낮음 → {new_tp}%로 상향 제안"
        )

    return suggestions if suggestions else None


def format_threshold_suggestions(all_suggestions: dict[str, dict]) -> str:
    """전 매니저 임계값 조정 제안 → 텍스트."""
    if not all_suggestions:
        return ""
    lines = ["⚙️ 매니저 임계값 조정 제안", "━" * 20]
    for mgr_key, sugg in all_suggestions.items():
        mgr = MANAGERS.get(mgr_key, {})
        emoji = mgr.get("emoji", "📌")
        name = mgr.get("name", mgr_key)
        lines.append(f"\n{emoji} {name}")
        if "reason" in sugg:
            lines.append(f"  손절: {sugg['reason']}")
        if "tp_reason" in sugg:
            lines.append(f"  익절: {sugg['tp_reason']}")
    return "\n".join(lines)


# ── #7 매니저 능동 발굴 기준 ──────────────────────────────────

MANAGER_DISCOVERY_CRITERIA: dict[str, str] = {
    "scalp": (
        "거래량 20일평균 300%+ AND RSI 40~65 AND "
        "당일등락 +2%이상 AND 시가총액 3000억+ 종목"
    ),
    "swing": (
        "RSI <35 AND BB하단(0.2이하) AND "
        "기관 3일+ 연속 순매수 AND 정배열 종목"
    ),
    "position": (
        "PEG <1.0 AND ROE >15% AND "
        "매출성장 >10% AND 부채비율 <100% 종목"
    ),
    "long_term": (
        "ROE >15% AND 부채비율 <80% AND FCF 양(+) AND "
        "PBR <1.5 AND 배당수익률 >2% 종목"
    ),
}


def filter_discovery_candidates(
    scan_results: list,
    manager_key: str,
    exclude_tickers: set[str] | None = None,
) -> list[dict]:
    """스캔 결과에서 매니저별 발굴 기준에 부합하는 종목 필터링.

    Returns:
        매니저용 dict 리스트 (scan_manager_domain 입력 형식)
    """
    if not scan_results:
        return []
    exclude = exclude_tickers or set()
    candidates = []

    for r in scan_results:
        ticker = getattr(r, "ticker", "")
        if ticker in exclude:
            continue
        tech = getattr(r, "indicators", None)
        if tech is None:
            continue
        score = getattr(r, "score", None)
        rsi = getattr(tech, "rsi", 50)
        vol_ratio = getattr(tech, "volume_ratio", 1.0)
        bb_pctb = getattr(tech, "bb_pctb", 0.5)
        price = getattr(tech, "close", 0) or 0
        day_change = getattr(r, "day_change_pct", 0.0) or 0.0

        match = False
        if manager_key == "scalp":
            match = vol_ratio >= 3.0 and 40 <= rsi <= 65 and day_change >= 2.0
        elif manager_key == "swing":
            match = rsi < 35 and bb_pctb < 0.2
        elif manager_key == "position":
            # 기본 기술적 필터: 점수 높은 mid-term 후보
            match = (
                score is not None
                and getattr(score, "composite", 0) >= 60
                and rsi < 60
            )
        elif manager_key == "long_term":
            match = (
                score is not None
                and getattr(score, "composite", 0) >= 55
                and rsi < 55
            )

        if match:
            candidates.append({
                "ticker": ticker,
                "name": getattr(r, "name", ""),
                "price": price,
                "day_change": day_change,
                "rsi": rsi,
                "vol_ratio": vol_ratio * 100,
                "bb_pctb": bb_pctb,
                "macd_cross": getattr(tech, "macd_signal_cross", 0),
                "drop_from_high": 0,
                "recovery_score": 0,
            })

    # 점수 높은 순 정렬 (vol_ratio for scalp, RSI for swing)
    if manager_key == "scalp":
        candidates.sort(key=lambda c: -c["vol_ratio"])
    elif manager_key == "swing":
        candidates.sort(key=lambda c: c["rsi"])
    else:
        candidates.sort(key=lambda c: -c.get("rsi", 50))

    return candidates[:10]


# ── #3 장중 매니저 알림 조건 체커 ─────────────────────────────

def check_manager_alert_conditions(
    manager_key: str,
    ticker: str,
    name: str,
    tech,
    current_price: float,
    buy_price: float = 0,
    holding_days: int = 0,
) -> list[str]:
    """보유종목이 매니저 매수/매도 조건 충족 시 알림 텍스트 리스트 반환."""
    if tech is None:
        return []

    alerts = []
    mgr = MANAGERS.get(manager_key, {})
    emoji = mgr.get("emoji", "📌")
    pnl = (current_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
    thresholds = MANAGER_THRESHOLDS.get(manager_key, {})

    # 공통: 손절/익절 임계값
    stop = thresholds.get("stop_loss", -5)
    tp1 = thresholds.get("take_profit_1", 10)
    tp2 = thresholds.get("take_profit_2", 20)

    if pnl <= stop:
        alerts.append(
            f"{emoji} {name}: 손절선 도달 ({pnl:+.1f}% <= {stop}%)\n"
            f"  즉시 손절 권고"
        )
    if pnl >= tp2:
        alerts.append(
            f"{emoji} {name}: 2차 목표 달성 ({pnl:+.1f}% >= {tp2}%)\n"
            f"  전량 매도 검토"
        )
    elif pnl >= tp1:
        alerts.append(
            f"{emoji} {name}: 1차 목표 달성 ({pnl:+.1f}% >= {tp1}%)\n"
            f"  일부 차익실현 검토"
        )

    # 매니저별 차트 기반 알림
    if manager_key == "scalp":
        if tech.volume_ratio >= 3.0 and tech.rsi < 65:
            alerts.append(
                f"{emoji} {name}: 거래량 {tech.volume_ratio*100:.0f}% 폭발\n"
                f"  RSI {tech.rsi:.0f} — 모멘텀 추가매수 구간"
            )
        if tech.rsi > 70 and tech.volume_ratio < 0.8:
            alerts.append(
                f"{emoji} {name}: RSI {tech.rsi:.0f} 과매수 + 거래량 감소\n"
                f"  차익실현 시점"
            )
        if holding_days >= 4 and pnl < 2:
            alerts.append(
                f"{emoji} {name}: 보유 {holding_days}일 (단타 기준 초과)\n"
                f"  수익률 {pnl:+.1f}% → 청산 검토"
            )

    elif manager_key == "swing":
        if tech.macd_signal_cross == 1 and tech.rsi < 50:
            alerts.append(
                f"{emoji} {name}: MACD 골든크로스 + RSI {tech.rsi:.0f}\n"
                f"  반등 시작 — 추가매수 검토"
            )
        if getattr(tech, "rsi_divergence", 0) == -1:
            alerts.append(
                f"{emoji} {name}: RSI 약세 다이버전스 감지\n"
                f"  하락 반전 주의 — 청산 준비"
            )

    elif manager_key in ("position", "long_term"):
        if tech.rsi < 25 and getattr(tech, "bb_pctb", 0.5) < 0.1:
            alerts.append(
                f"{emoji} {name}: 극단 과매도 (RSI {tech.rsi:.0f})\n"
                f"  장기 관점 추가매수 기회"
            )

    return alerts
