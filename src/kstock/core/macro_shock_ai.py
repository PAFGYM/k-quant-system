"""v10.3 AI-Powered Macro Shock Analysis Pipeline.

3-Step AI 파이프라인 + 통합(Combined) 프롬프트 두 가지 모드 지원.
- 3-step: Haiku(충격감지) → Haiku(한국영향) → Sonnet(최종지침)
- combined: Sonnet 단일호출로 3단계 일괄 수행

출력:
- MacroAIResult: AI 분석 JSON + 텔레그램 브리핑 텍스트
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from kstock.core.tz import KST

logger = logging.getLogger(__name__)


# ── 데이터 클래스 ──────────────────────────────────────────

@dataclass
class MacroAIResult:
    """AI 매크로 분석 결과."""
    step1_json: dict = field(default_factory=dict)  # 글로벌 충격 감지
    step2_json: dict = field(default_factory=dict)  # 한국 시장 영향
    step3_json: dict = field(default_factory=dict)  # 운영 지침
    telegram_briefing: str = ""                      # Part B 브리핑 텍스트
    raw_response: str = ""
    timestamp: datetime | None = None
    mode: str = "combined"  # "3step" or "combined"


# ── Step 1: 글로벌 충격 감지 (Haiku) ─────────────────────

STEP1_SYSTEM = (
    "당신은 글로벌 금융 시장의 이상 신호를 감지하는 전문 센서입니다.\n"
    "감정 없이, 숫자만 보고, 충격 여부와 등급만 판정합니다.\n"
    "반드시 지정된 JSON 형식으로만 응답하십시오. 설명 문장은 일절 금지입니다."
)

STEP1_USER_TEMPLATE = """\
오늘 날짜: {date}

아래는 전일 종가 기준 글로벌 주요 지표의 등락률입니다.

=== 에너지 ===
WTI 원유: {wti_change}%
브렌트유: {brent_change}%

=== 미국 시장 ===
나스닥 선물: {nq_futures_change}%
S&P500 선물: {es_futures_change}%
VIX 지수: {vix_value} (전일比 {vix_change}%)

=== 아시아 시장 ===
닛케이225: {nikkei_change}%
항셍지수: {hsi_change}%

=== 달러/환율 ===
DXY 달러인덱스: {dxy_change}%
USD/KRW: {usdkrw_change}%

=== 한국 관련 해외 ETF ===
EWY (iShares MSCI Korea): {ewy_change}%
KORU (3x 한국 레버리지): {koru_change}%

---
각 항목을 아래 기준으로 평가하고 JSON으로만 응답하십시오.

충격 등급 기준:
- NONE: 정상 범위
- WATCH: 경계 수준 (WTI ±2% / 나스닥선물 ±1% / VIX +15% / EWY ±1.5%)
- ALERT: 주의 수준 (WTI ±3% / 나스닥선물 ±1.5% / VIX +25% / EWY ±2.5%)
- SHOCK: 충격 수준 (WTI ±5% / 나스닥선물 ±2.5% / VIX +40% / EWY ±4%)
- CRISIS: 복합 충격 (SHOCK 등급 2개 이상 동시)

응답 형식 (이 JSON 외 어떤 텍스트도 출력 금지):
{{
  "date": "{date}",
  "signals": {{
    "oil": {{"grade": "NONE|WATCH|ALERT|SHOCK", "value": {wti_change}, "reason": "한 줄 근거"}},
    "us_futures": {{"grade": "NONE|WATCH|ALERT|SHOCK", "value": {nq_futures_change}, "reason": "한 줄 근거"}},
    "vix": {{"grade": "NONE|WATCH|ALERT|SHOCK", "value": {vix_value}, "reason": "한 줄 근거"}},
    "asia": {{"grade": "NONE|WATCH|ALERT|SHOCK", "value": "{nikkei_change}/{hsi_change}", "reason": "한 줄 근거"}},
    "dollar": {{"grade": "NONE|WATCH|ALERT|SHOCK", "value": {dxy_change}, "reason": "한 줄 근거"}},
    "korea_etf": {{"grade": "NONE|WATCH|ALERT|SHOCK", "value": "{ewy_change}/{koru_change}", "reason": "한 줄 근거"}}
  }},
  "overall_grade": "NONE|WATCH|ALERT|SHOCK|CRISIS",
  "shock_count": 0,
  "dominant_shock": "가장 심각한 충격 유형 한 단어 (oil/us_market/asia/dollar/korea_etf/none)"
}}"""


# ── Step 2: 한국 시장 영향 분석 (Haiku) ──────────────────

STEP2_SYSTEM = (
    "당신은 한국 주식 시장 전문 분석가입니다.\n"
    "글로벌 충격 신호를 받아 한국 시장의 섹터별/종목 유형별 예상 영향을 분석합니다.\n"
    "20년간의 한국 시장 데이터와 글로벌-한국 상관관계 패턴을 기반으로 판단합니다.\n"
    "반드시 지정된 JSON 형식으로만 응답하십시오."
)

STEP2_USER_TEMPLATE = """\
오늘 날짜: {date}

=== Step 1 충격 감지 결과 ===
{step1_json}

=== 현재 보유 포트폴리오 요약 ===
총 보유 종목 수: {total_positions}개
섹터 구성: {sector_summary}
매니저별 보유:
- 리버모어 (스캘프): {livermore_positions}
- 오닐 (스윙): {oneil_positions}
- 린치 (포지션): {lynch_positions}
- 버핏 (장기): {buffett_positions}

---
아래 분석을 JSON으로 응답하십시오.

한국 시장 민감도 기준:
HIGH_SENSITIVITY 섹터: 반도체, 2차전지, IT, 디스플레이, 인터넷플랫폼
  -> 원유충격 직접 피해 없으나 외인 이탈 + 나스닥 연동 강함
MEDIUM_SENSITIVITY 섹터: 자동차, 화학, 철강, 금융, 조선
  -> 원유/환율 직접 영향 + 경기 민감
LOW_SENSITIVITY 섹터: 통신, 유틸리티, 음식료, 의료, 내수 소비
  -> 글로벌 충격 방어적, 외인 이탈에도 상대적 안정

원유 충격 시 특이 패턴 (반드시 반영):
- WTI ALERT 이상: 정유/화학주 단기 수혜 / 항공/운송주 피해
- WTI SHOCK 이상: 외인 전방위 이탈 시작 -> HIGH 섹터 선제 매도
- 달러 ALERT 이상: 수출주 환차익 vs 원자재 비용 상승 혼재
- KORU SHOCK: 외인이 이미 한국 포지션 청산 중 -> 전 섹터 압박

응답 형식:
{{
  "date": "{date}",
  "overall_grade": "(Step1 overall_grade 그대로)",
  "korea_market_outlook": {{
    "open_direction": "GAP_DOWN_STRONG|GAP_DOWN|FLAT|GAP_UP",
    "estimated_kospi_impact": "-X% ~ -Y% 예상",
    "foreign_selloff_risk": "HIGH|MEDIUM|LOW",
    "recovery_timeline": "당일회복|2~3일|1주일이상|불명확"
  }},
  "sector_impact": {{
    "semiconductor": {{"impact": "NEGATIVE_HIGH|NEGATIVE|NEUTRAL|POSITIVE", "reason": "한 줄"}},
    "battery": {{"impact": "...", "reason": "..."}},
    "auto": {{"impact": "...", "reason": "..."}},
    "chemical_refinery": {{"impact": "...", "reason": "..."}},
    "finance": {{"impact": "...", "reason": "..."}},
    "bio_health": {{"impact": "...", "reason": "..."}},
    "utilities_telecom": {{"impact": "...", "reason": "..."}}
  }},
  "portfolio_risk": {{
    "high_risk_positions": ["위험 종목/섹터 리스트"],
    "safe_positions": ["안전한 종목/섹터"],
    "immediate_review_needed": true
  }},
  "special_patterns": ["해당 특이 패턴 리스트"],
  "one_line_summary": "오늘 한국 시장 한 줄 요약 (30자 이내)"
}}"""


# ── Step 3: 최종 운영 지침 (Sonnet) ──────────────────────

STEP3_SYSTEM = (
    "당신은 K-Quant 퀀트 트레이딩 시스템의 수석 리스크 매니저입니다.\n"
    "16년 경력의 한국 주식 시장 전문가로서, 글로벌 매크로 충격이 한국 시장에 미치는\n"
    "영향을 누구보다 정확하게 판단합니다.\n\n"
    "판단 원칙:\n"
    "1. 한국 시장은 펀더멘탈이 좋아도 글로벌 충격에는 예외가 없다\n"
    "2. 외인이 이미 움직였다면 (EWY/KORU 이미 하락) 국내 대응은 늦다\n"
    "3. SHOCK 이상 등급에서는 신규 매수보다 기존 포지션 보호가 우선이다\n"
    "4. 지나친 보수성도 금물 — WATCH/ALERT 수준에서 멀쩡한 종목까지 손절하지 않는다\n"
    "5. 매니저별 특성을 유지한다 (리버모어는 손절 빠름 / 버핏은 장기 보유 유지)"
)

STEP3_USER_TEMPLATE = """\
오늘 날짜: {date}
현재 시각: {current_time}

=== AI #1 충격 감지 결과 ===
{step1_json}

=== AI #2 한국 시장 영향 분석 ===
{step2_json}

=== 추가 컨텍스트 ===
현재 포트폴리오 총 평가금액: {total_portfolio_value}원
오늘 가용 현금 비율: {cash_ratio}%
어제 포트폴리오 수익률: {yesterday_return}%
최근 5일 연속 등락 패턴: {recent_5day_pattern}
현재 ML 레짐 (어제 기준): {current_regime}

---
아래 두 파트를 순서대로 작성하십시오.

[PART A] 시스템 운영 파라미터 (JSON):
{{
  "regime": "RISK_ON|RISK_OFF|PANIC|NEUTRAL",
  "regime_reason": "레짐 판단 근거 2문장",
  "new_buy_allowed": true,
  "buy_restriction_sectors": [],
  "position_action": {{
    "livermore": "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL",
    "oneil": "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL",
    "lynch": "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL",
    "buffett": "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL"
  }},
  "atr_multiplier_adjustment": {{
    "scalp": 1.0, "swing": 1.0, "position": 1.0, "long": 1.0
  }},
  "ml_blend_override": {{
    "active": false,
    "traditional_weight": 0.6,
    "ml_weight": 0.4,
    "reason": null
  }},
  "alert_level": "GREEN|YELLOW|ORANGE|RED",
  "recheck_time": "HH:MM 또는 null"
}}

[PART B] 텔레그램 브리핑 삽입 텍스트 (300자 이내, 마크다운 금지):
---BRIEFING_START---
(여기에 브리핑 텍스트)
---BRIEFING_END---"""


# ── Combined 통합 프롬프트 (Sonnet 단일 호출) ────────────

COMBINED_SYSTEM = (
    "당신은 K-Quant 퀀트 트레이딩 시스템의 글로벌 매크로 리스크 매니저입니다.\n"
    "16년 경력의 한국 주식 시장 전문가로서 다음 3단계를 순서대로 수행합니다.\n\n"
    "STEP 1. 글로벌 충격 감지 — 숫자를 보고 충격 등급 판정\n"
    "STEP 2. 한국 시장 영향 분석 — 충격이 한국 섹터/포지션에 미치는 영향\n"
    "STEP 3. 최종 운영 지침 확정 — 오늘 하루 K-Quant 운영 레짐과 행동 결정\n\n"
    "판단 원칙:\n"
    "1. 한국 시장은 펀더멘탈이 좋아도 글로벌 충격에는 예외가 없다\n"
    "2. EWY/KORU가 이미 하락했다면 외인은 이미 움직인 것이다 — 대응이 늦다\n"
    "3. SHOCK 이상에서는 신규 매수보다 기존 포지션 보호가 우선이다\n"
    "4. WATCH/ALERT 수준에서 멀쩡한 종목까지 손절하지 않는다\n"
    "5. 매니저 특성을 유지한다 (리버모어 손절 빠름 / 버핏 장기 보유 유지)\n\n"
    "반드시 STEP 순서대로 작성하고, 각 STEP의 출력 형식을 정확히 따르십시오."
)

COMBINED_USER_TEMPLATE = """\
오늘 날짜: {date}
현재 시각: {current_time}

=== 글로벌 시장 데이터 (전일 종가 기준 등락률) ===

[에너지]
WTI 원유: {wti_change}%
브렌트유: {brent_change}%

[미국 시장]
나스닥 선물: {nq_futures_change}%
S&P500 선물: {es_futures_change}%
VIX 지수: {vix_value} (전일比 {vix_change}%)

[아시아 시장]
닛케이225: {nikkei_change}%
항셍지수: {hsi_change}%

[달러/환율]
DXY 달러인덱스: {dxy_change}%
USD/KRW: {usdkrw_change}%

[한국 관련 해외 ETF]
EWY (MSCI Korea): {ewy_change}%
KORU (한국 3배 레버리지): {koru_change}%

=== 현재 포트폴리오 ===
총 평가금액: {total_portfolio_value}원
현금 비율: {cash_ratio}%
어제 수익률: {yesterday_return}%
최근 5일 패턴: {recent_5day_pattern}
현재 ML 레짐: {current_regime}
섹터 구성: {sector_summary}
매니저별 보유:
- 리버모어(스캘프): {livermore_positions}
- 오닐(스윙): {oneil_positions}
- 린치(포지션): {lynch_positions}
- 버핏(장기): {buffett_positions}

---
아래 3개 STEP을 순서대로 수행하십시오.

━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1. 글로벌 충격 감지
━━━━━━━━━━━━━━━━━━━━━━━━━━━

충격 등급 기준:
- NONE   : 정상 범위
- WATCH  : WTI ±2% / 나스닥선물 ±1% / VIX +15% / EWY ±1.5%
- ALERT  : WTI ±3% / 나스닥선물 ±1.5% / VIX +25% / EWY ±2.5%
- SHOCK  : WTI ±5% / 나스닥선물 ±2.5% / VIX +40% / EWY ±4%
- CRISIS : SHOCK 등급 2개 이상 동시 발생

출력 형식 (JSON):
```json
{{
  "step1_shock_detection": {{
    "signals": {{
      "oil":       {{"grade": "등급", "value": 수치, "reason": "한 줄 근거"}},
      "us_futures":{{"grade": "등급", "value": 수치, "reason": "한 줄 근거"}},
      "vix":       {{"grade": "등급", "value": 수치, "reason": "한 줄 근거"}},
      "asia":      {{"grade": "등급", "value": "닛케이/항셍", "reason": "한 줄 근거"}},
      "dollar":    {{"grade": "등급", "value": 수치, "reason": "한 줄 근거"}},
      "korea_etf": {{"grade": "등급", "value": "EWY/KORU", "reason": "한 줄 근거"}}
    }},
    "overall_grade": "NONE|WATCH|ALERT|SHOCK|CRISIS",
    "shock_count": 0,
    "dominant_shock": "oil/us_market/asia/dollar/korea_etf/none"
  }}
}}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2. 한국 시장 영향 분석
━━━━━━━━━━━━━━━━━━━━━━━━━━━

섹터 민감도 기준:
- HIGH   : 반도체, 2차전지, IT, 디스플레이, 인터넷플랫폼 (외인 이탈 + 나스닥 연동)
- MEDIUM : 자동차, 화학, 철강, 금융, 조선 (원유/환율 직접 영향)
- LOW    : 통신, 유틸리티, 음식료, 의료, 내수 (글로벌 충격 방어적)

원유 충격 시 특이 패턴:
- WTI ALERT 이상 -> 정유/화학 단기 수혜 / 항공/운송 피해
- WTI SHOCK 이상 -> 외인 전방위 이탈 -> HIGH 섹터 선제 매도 압력
- KORU SHOCK     -> 외인이 이미 한국 포지션 청산 중 -> 전 섹터 압박
- 달러 ALERT 이상 -> 수출주 환차익 vs 원자재 비용 상승 혼재

출력 형식 (JSON):
```json
{{
  "step2_korea_impact": {{
    "market_outlook": {{
      "open_direction": "GAP_DOWN_STRONG|GAP_DOWN|FLAT|GAP_UP",
      "estimated_kospi_impact": "-X% ~ -Y%",
      "foreign_selloff_risk": "HIGH|MEDIUM|LOW",
      "recovery_timeline": "당일회복|2~3일|1주일이상|불명확"
    }},
    "sector_impact": {{
      "semiconductor": {{"impact": "NEGATIVE_HIGH|NEGATIVE|NEUTRAL|POSITIVE", "reason": "한 줄"}},
      "battery":       {{"impact": "...", "reason": "..."}},
      "auto":          {{"impact": "...", "reason": "..."}},
      "chemical":      {{"impact": "...", "reason": "..."}},
      "finance":       {{"impact": "...", "reason": "..."}},
      "bio_health":    {{"impact": "...", "reason": "..."}},
      "utilities":     {{"impact": "...", "reason": "..."}}
    }},
    "portfolio_risk": {{
      "high_risk_positions": ["위험 종목/섹터"],
      "safe_positions": ["안전 종목/섹터"],
      "immediate_review_needed": true
    }},
    "special_patterns_triggered": ["해당 특이 패턴"],
    "one_line_summary": "한 줄 요약 30자 이내"
  }}
}}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3. 최종 운영 지침 확정
━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1~2 결과를 종합하여 오늘 하루 K-Quant 운영 지침을 확정합니다.

[PART A] 시스템 운영 파라미터 (JSON):
```json
{{
  "step3_operation": {{
    "regime": "RISK_ON|RISK_OFF|PANIC|NEUTRAL",
    "regime_reason": "레짐 판단 근거 2문장",
    "new_buy_allowed": true,
    "buy_restriction_sectors": [],
    "position_action": {{
      "livermore": "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL",
      "oneil":     "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL",
      "lynch":     "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL",
      "buffett":   "HOLD_ALL|TIGHTEN_STOP|REDUCE_30|CLOSE_ALL"
    }},
    "atr_adjustment": {{
      "scalp": 1.0, "swing": 1.0, "position": 1.0, "long": 1.0
    }},
    "ml_blend_override": {{
      "active": false,
      "traditional_weight": 0.6,
      "ml_weight": 0.4,
      "reason": null
    }},
    "alert_level": "GREEN|YELLOW|ORANGE|RED",
    "recheck_time": "HH:MM 또는 null"
  }}
}}
```

[PART B] 텔레그램 브리핑 삽입 텍스트 (300자 이내, 마크다운 금지):
---BRIEFING_START---
(여기에 브리핑 텍스트를 작성하십시오)
---BRIEFING_END---"""


# ── 프롬프트 빌더 ─────────────────────────────────────────

def build_macro_context(macro, portfolio_ctx: dict) -> dict:
    """MacroSnapshot + 포트폴리오 정보로 프롬프트 변수 딕셔너리 생성."""
    now = datetime.now(KST)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "current_time": now.strftime("%H:%M"),
        # 에너지
        "wti_change": f"{getattr(macro, 'wti_change_pct', 0.0):+.2f}",
        "brent_change": f"{getattr(macro, 'brent_change_pct', 0.0):+.2f}",
        # 미국 시장
        "nq_futures_change": f"{getattr(macro, 'nq_futures_change_pct', 0.0):+.2f}",
        "es_futures_change": f"{getattr(macro, 'es_futures_change_pct', 0.0):+.2f}",
        "vix_value": f"{getattr(macro, 'vix', 0.0):.1f}",
        "vix_change": f"{getattr(macro, 'vix_change_pct', 0.0):+.1f}",
        # 아시아
        "nikkei_change": f"{getattr(macro, 'nikkei_change_pct', 0.0):+.2f}",
        "hsi_change": f"{getattr(macro, 'hsi_change_pct', 0.0):+.2f}",
        # 달러/환율
        "dxy_change": f"{getattr(macro, 'dxy_change_pct', 0.0):+.2f}",
        "usdkrw_change": f"{getattr(macro, 'usdkrw_change_pct', 0.0):+.2f}",
        # 한국 ETF
        "ewy_change": f"{getattr(macro, 'ewy_change_pct', 0.0):+.2f}",
        "koru_change": f"{getattr(macro, 'koru_change_pct', 0.0):+.2f}",
        # 포트폴리오
        "total_portfolio_value": portfolio_ctx.get("total_value", "N/A"),
        "cash_ratio": portfolio_ctx.get("cash_ratio", "N/A"),
        "yesterday_return": portfolio_ctx.get("yesterday_return", "N/A"),
        "recent_5day_pattern": portfolio_ctx.get("recent_5day_pattern", "N/A"),
        "current_regime": getattr(macro, "regime", "neutral"),
        "sector_summary": portfolio_ctx.get("sector_summary", "N/A"),
        "total_positions": portfolio_ctx.get("total_positions", 0),
        "livermore_positions": portfolio_ctx.get("livermore_positions", "없음"),
        "oneil_positions": portfolio_ctx.get("oneil_positions", "없음"),
        "lynch_positions": portfolio_ctx.get("lynch_positions", "없음"),
        "buffett_positions": portfolio_ctx.get("buffett_positions", "없음"),
    }


def build_step1_prompt(ctx: dict) -> str:
    """Step 1 프롬프트 생성."""
    return STEP1_USER_TEMPLATE.format(**ctx)


def build_step2_prompt(ctx: dict, step1_json: str) -> str:
    """Step 2 프롬프트 생성."""
    ctx_copy = dict(ctx)
    ctx_copy["step1_json"] = step1_json
    return STEP2_USER_TEMPLATE.format(**ctx_copy)


def build_step3_prompt(ctx: dict, step1_json: str, step2_json: str) -> str:
    """Step 3 프롬프트 생성."""
    ctx_copy = dict(ctx)
    ctx_copy["step1_json"] = step1_json
    ctx_copy["step2_json"] = step2_json
    return STEP3_USER_TEMPLATE.format(**ctx_copy)


def build_combined_prompt(ctx: dict) -> str:
    """통합 프롬프트 생성."""
    return COMBINED_USER_TEMPLATE.format(**ctx)


# ── 응답 파서 ─────────────────────────────────────────────

def _extract_json_blocks(text: str) -> list[dict]:
    """텍스트에서 JSON 블록들을 추출."""
    blocks = []
    # ```json ... ``` 블록 추출
    for m in re.finditer(r'```json\s*\n?(.*?)\n?\s*```', text, re.DOTALL):
        try:
            blocks.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            logger.debug("Failed to parse JSON block: %s", m.group(1)[:100])
    # 블록 없으면 { ... } 패턴 시도
    if not blocks:
        for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
            try:
                parsed = json.loads(m.group(0))
                if len(parsed) > 1:  # 단순 키-값이 아닌 구조적 JSON만
                    blocks.append(parsed)
            except json.JSONDecodeError:
                continue
    return blocks


def _extract_briefing_text(text: str) -> str:
    """---BRIEFING_START--- ... ---BRIEFING_END--- 구간 추출."""
    m = re.search(
        r'---BRIEFING_START---\s*\n?(.*?)\n?\s*---BRIEFING_END---',
        text, re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    # 폴백: [PART B] 이후 텍스트
    m = re.search(r'\[PART\s*B\].*?\n(.*?)$', text, re.DOTALL)
    if m:
        briefing = m.group(1).strip()
        # JSON이 아닌 부분만 추출
        briefing = re.sub(r'```.*?```', '', briefing, flags=re.DOTALL).strip()
        # 마크다운 시작/끝 마커 제거
        briefing = re.sub(r'^---+$', '', briefing, flags=re.MULTILINE).strip()
        return briefing[:500] if briefing else ""
    return ""


def parse_combined_response(raw: str) -> MacroAIResult:
    """통합 프롬프트 응답 파싱."""
    result = MacroAIResult(
        raw_response=raw,
        timestamp=datetime.now(KST),
        mode="combined",
    )

    blocks = _extract_json_blocks(raw)

    for block in blocks:
        if "step1_shock_detection" in block:
            result.step1_json = block["step1_shock_detection"]
        elif "step2_korea_impact" in block:
            result.step2_json = block["step2_korea_impact"]
        elif "step3_operation" in block:
            result.step3_json = block["step3_operation"]
        else:
            # 키 추론
            if "signals" in block and "overall_grade" in block:
                result.step1_json = block
            elif "market_outlook" in block or "sector_impact" in block:
                result.step2_json = block
            elif "regime" in block and "position_action" in block:
                result.step3_json = block

    result.telegram_briefing = _extract_briefing_text(raw)

    return result


def parse_step_response(raw: str, step: int) -> dict:
    """개별 스텝 응답 파싱."""
    blocks = _extract_json_blocks(raw)
    if blocks:
        return blocks[0]
    # 전체를 JSON으로 시도
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        logger.warning("Step %d JSON parse failed", step)
        return {}


# ── 포트폴리오 컨텍스트 빌더 ─────────────────────────────

def build_portfolio_context(db) -> dict:
    """DB에서 포트폴리오 정보를 수집하여 컨텍스트 딕셔너리 생성."""
    ctx = {
        "total_value": "N/A",
        "cash_ratio": "N/A",
        "yesterday_return": "N/A",
        "recent_5day_pattern": "N/A",
        "sector_summary": "N/A",
        "total_positions": 0,
        "livermore_positions": "없음",
        "oneil_positions": "없음",
        "lynch_positions": "없음",
        "buffett_positions": "없음",
    }

    try:
        holdings = db.get_active_holdings()
        if not holdings:
            return ctx

        ctx["total_positions"] = len(holdings)

        # 매니저별 분류
        mgr_map = {
            "scalp": [], "swing": [], "position": [], "long_term": [],
        }
        sectors = {}

        for h in holdings:
            name = h.get("name", h.get("ticker", "?"))
            horizon = h.get("holding_type", "swing")
            sector = h.get("sector", "기타")
            buy_price = h.get("buy_price", 0)
            current = h.get("current_price", buy_price)
            pnl = ((current - buy_price) / buy_price * 100) if buy_price > 0 else 0

            info = f"{name}({pnl:+.1f}%)"
            if horizon in mgr_map:
                mgr_map[horizon].append(info)
            else:
                mgr_map["swing"].append(info)

            sectors[sector] = sectors.get(sector, 0) + 1

        # 매니저별 포지션 문자열
        mgr_names = {
            "scalp": "livermore_positions",
            "swing": "oneil_positions",
            "position": "lynch_positions",
            "long_term": "buffett_positions",
        }
        for key, ctx_key in mgr_names.items():
            if mgr_map[key]:
                ctx[ctx_key] = ", ".join(mgr_map[key])

        # 섹터 구성
        total = sum(sectors.values())
        if total > 0:
            sector_parts = []
            for s, cnt in sorted(sectors.items(), key=lambda x: -x[1]):
                pct = cnt / total * 100
                sector_parts.append(f"{s} {pct:.0f}%")
            ctx["sector_summary"] = " / ".join(sector_parts)

    except Exception as e:
        logger.debug("build_portfolio_context failed: %s", e)

    return ctx


# ── 텔레그램 포맷 (폴백) ─────────────────────────────────

def format_ai_shock_briefing(result: MacroAIResult) -> str:
    """AI 분석 결과를 텔레그램 메시지로 포맷 (Part B 텍스트 우선)."""
    if result.telegram_briefing:
        return result.telegram_briefing

    # AI 브리핑 없으면 JSON에서 요약 생성
    lines = ["🤖 AI 매크로 분석"]

    s1 = result.step1_json
    if s1:
        grade = s1.get("overall_grade", "N/A")
        dominant = s1.get("dominant_shock", "none")
        lines.append(f"충격 등급: {grade} (주요: {dominant})")

    s2 = result.step2_json
    if s2:
        outlook = s2.get("market_outlook", {})
        direction = outlook.get("open_direction", "N/A")
        impact = outlook.get("estimated_kospi_impact", "N/A")
        summary = s2.get("one_line_summary", "")
        lines.append(f"개장 전망: {direction} ({impact})")
        if summary:
            lines.append(summary)

    s3 = result.step3_json
    if s3:
        regime = s3.get("regime", "N/A")
        buy = "허용" if s3.get("new_buy_allowed", True) else "금지"
        lines.append(f"운영 레짐: {regime} / 신규매수: {buy}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 08:20 Pre-open Action Report
# ══════════════════════════════════════════════════════════

PREOPEN_ACTION_SYSTEM = (
    "당신은 K-Quant 시스템의 장전 실행 참모입니다.\n"
    "06:00 AI 매크로 분석 결과와 최신 데이터를 바탕으로,\n"
    "개장 전 마지막 행동 지침을 확정합니다.\n"
    "구체적이고 실행 가능한 지침만 제시하십시오.\n"
    "볼드(**) 금지. 이모지 사용. 300자 이내."
)

PREOPEN_ACTION_TEMPLATE = """\
오늘 날짜: {date} / 시각: {current_time}

=== 06:00 AI 매크로 분석 요약 ===
충격 등급: {shock_grade}
운영 레짐: {regime}
신규매수: {new_buy_allowed}
주요 리스크: {dominant_shock}

=== 현재 매크로 최신값 ===
VIX: {vix_value} ({vix_change}%)
KOSPI200 선물: {kospi_futures}
환율: {usdkrw}원 ({usdkrw_change}%)

=== 보유종목 ===
{holdings_text}

---
아래 형식으로 장전 행동 지침을 작성하십시오:

1) 오늘 시장 한 줄 판단
2) 개장 직후 5분 행동
   - 매니저별 (리버모어/오닐/린치/버핏) 구체 지침
3) 종목별 주의사항 (보유종목 기반)
   - 위험종목: 즉시 행동 필요한 종목
   - 안전종목: 홀딩 유지 종목
4) 오늘 절대 하지 말 것 1가지
5) 09:05 개장 체크 시 확인할 핵심 지표 2개"""


# ══════════════════════════════════════════════════════════
# 09:05 Opening Reality Check
# ══════════════════════════════════════════════════════════

OPENING_REALITY_SYSTEM = (
    "당신은 K-Quant 시스템의 예측 검증 담당입니다.\n"
    "06:00 장전 예측과 실제 개장 결과를 대조하여,\n"
    "예측이 맞았는지 틀렸는지 즉시 판정하고 조정 지침을 내립니다.\n"
    "예측이 틀렸으면 솔직히 인정하고 즉시 수정 행동을 제시하십시오.\n"
    "볼드(**) 금지. 이모지 사용. 250자 이내."
)

OPENING_REALITY_TEMPLATE = """\
오늘 날짜: {date} / 시각: {current_time}

=== 06:00 장전 예측 ===
예측 개장 방향: {predicted_direction}
예측 KOSPI 영향: {predicted_impact}
외인 매도 리스크: {predicted_foreign_risk}

=== 실제 개장 결과 (09:05) ===
KOSPI: {kospi_open} ({kospi_change}%)
KOSDAQ: {kosdaq_open} ({kosdaq_change}%)
환율: {usdkrw_now}원
외국인 순매수: {foreign_net}억원

=== 보유종목 현재가 ===
{holdings_current}

---
아래 형식으로 검증 결과를 작성하십시오:

1) 예측 정확도: 적중|부분적중|빗나감
2) 예측 vs 실제 차이 원인 (1줄)
3) 즉시 수정 행동
   - 예측 적중 시: 기존 지침 유지 확인
   - 예측 빗나감 시: 구체적 수정 지침
4) 10:00까지 추가 모니터링 항목"""


# ══════════════════════════════════════════════════════════
# 15:50 Shock Attribution Report
# ══════════════════════════════════════════════════════════

SHOCK_ATTRIBUTION_SYSTEM = (
    "당신은 K-Quant 시스템의 장후 리스크 회고 담당입니다.\n"
    "오늘 하루 글로벌 매크로 충격이 한국 시장에 실제로 어떤 영향을 줬는지 분석합니다.\n"
    "06:00 예측과 실제 결과를 대조하여 시스템 개선점을 도출하십시오.\n"
    "볼드(**) 금지. 이모지 사용. 400자 이내."
)

SHOCK_ATTRIBUTION_TEMPLATE = """\
오늘 날짜: {date}

=== 06:00 장전 예측 ===
충격 등급: {predicted_grade}
운영 레짐: {predicted_regime}
예측 방향: {predicted_direction}
차단 전략: {blocked_strategies}
신규매수: {new_buy_allowed}

=== 실제 장중 결과 ===
KOSPI: {kospi_close} ({kospi_day_change}%)
KOSDAQ: {kosdaq_close} ({kosdaq_day_change}%)
외국인 순매수: {foreign_net_total}억원
기관 순매수: {institution_net_total}억원

=== 보유종목 오늘 성과 ===
{holdings_performance}

=== 오늘 매크로 변동 ===
WTI: {wti_eod}%
환율: {usdkrw_eod}원 ({usdkrw_day_change}%)
VIX: {vix_eod} ({vix_day_change}%)

---
아래 형식으로 귀인 분석을 작성하십시오:

1) 오늘 예측 정확도: 적중|부분적중|빗나감 + 이유
2) 충격 전이 실제 경로
   - 어떤 글로벌 변수가 실제로 한국장에 영향을 줬는가
   - 예상치 못한 변수가 있었는가
3) 정책 엔진 효과 평가
   - 매수 차단이 손실을 막았는가
   - ATR 강화가 효과적이었는가
   - 전략 차단이 적절했는가
4) 시스템 개선 제안 (1~2개)
   - 오늘 경험에서 배운 것
   - 내일 적용할 조정사항
5) 내일 예비 전망 (1줄)"""
