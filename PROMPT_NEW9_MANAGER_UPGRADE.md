# 프롬프트 9: AI 매니저 시스템 + 관리자 모드 개선 + 거품 판별 + PDF 수정

## 4가지 개선 사항

1. **관리자 클로드 모드** — 이미지 포함 대화 지속, 종료 전까지 유지
2. **4명의 전설적 투자자 매니저** — 단타/스윙/포지션/장기 전담 AI 에이전트
3. **거품 판별 엔진** — PER/PEG/성장률/적정주가 기반 종합 판단
4. **PDF 리포트** — 표 글씨 겹침 수정 + 스마트폰 최적화
5. **PDF 가격 데이터 신뢰성** — 실시간 가격 갱신 + AI 환각 방지 (치명적 버그 수정)

---

## Part A: 관리자 클로드 모드 개선

### 현재 문제

`remote_claude.py`의 대화 모드 (`/claude`)는 **텍스트만** 처리.
이미지를 보내면 주식 분석 모드로 빠지거나, 관리자 모드가 끊김.

### 목표

- 이미지를 보내도 관리자 모드 유지
- 이미지 + 텍스트 조합 대화 가능 (예: 차트 이미지 + "이거 분석해줘")
- **종료 버튼을 누르기 전까지 절대 모드 해제 안 됨**

### 수정: core_handlers.py

현재 메시지 라우팅 로직에서 `claude_mode == True`일 때 **모든 입력**(텍스트, 이미지)을 관리자 모드로 보내도록 수정.

```python
# core_handlers.py — handle_menu_text() 최상단에:
if context.user_data.get("claude_mode"):
    # 관리자 모드 활성화 상태 → 모든 입력을 Claude에 전달
    # 이미지가 있으면 vision 분석 + 텍스트 함께 전달
    await self._handle_claude_conversation(update, context)
    return
```

### 수정: remote_claude.py — 이미지 처리 추가

```python
async def _handle_claude_conversation(self, update, context):
    """관리자 모드 대화 처리. 텍스트 + 이미지 모두 지원."""
    text = update.message.text or update.message.caption or ""

    # 이미지가 있으면 Claude Vision으로 분석
    image_analysis = ""
    if update.message.photo:
        photo = update.message.photo[-1]  # 가장 큰 해상도
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()

        # Claude Vision API로 이미지 분석
        import base64
        img_b64 = base64.b64encode(img_bytes).decode()

        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        vision_resp = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": text or "이 이미지를 분석해줘. 주식 차트나 데이터가 있으면 투자 관점에서 분석해줘."},
                ],
            }],
        )
        image_analysis = vision_resp.content[0].text

    # 기존 텍스트 대화 + 이미지 분석 결합
    if image_analysis:
        combined = f"[이미지 분석 결과]\n{image_analysis}\n\n[사용자 메시지] {text}"
    else:
        combined = text

    # 기존 Claude 대화 흐름으로 전달
    # ... (기존 _handle_claude_prompt 로직)
```

### 핵심: 모드 유지 조건

```python
# 관리자 모드 해제는 오직:
# 1. "🔙 대화 종료" 버튼 클릭
# 2. "종료", "끝", "exit" 텍스트 입력
# 그 외 모든 입력(텍스트, 이미지, 파일)은 관리자 모드 내에서 처리

# handle_photo()에도 관리자 모드 체크 추가:
async def handle_photo(self, update, context):
    if context.user_data.get("claude_mode"):
        await self._handle_claude_conversation(update, context)
        return
    # ... 기존 스크린샷 분석 로직
```

---

## Part B: 4명의 전설적 투자자 매니저 시스템

### 컨셉

주식 투자 유형별로 **역사적 전설의 투자자** 페르소나를 가진 AI 매니저를 배정:

| 유형 | 매니저 | 실제 투자자 모델 | 투자 철학 |
|------|--------|----------------|---------|
| ⚡ 단타 | **제시 리버모어** | Jesse Livermore | 추세 추종, 시장 타이밍, 테이프 리딩 |
| 🔥 스윙 | **윌리엄 오닐** | William O'Neil | CAN SLIM, 모멘텀, 차트 패턴 |
| 📊 포지션 | **피터 린치** | Peter Lynch | 10배 주식, 성장주, 일상 관찰 |
| 💎 장기 | **워렌 버핏** | Warren Buffett | 가치투자, 경제적 해자, 복리 |

### 새 파일: `src/kstock/bot/investment_managers.py`

```python
"""4명의 전설적 투자자 AI 매니저 시스템.

각 매니저는 해당 투자 유형에 특화된 분석과 코칭을 제공.
보유종목과 추천종목을 개별 관리.
"""

MANAGERS = {
    "scalp": {
        "name": "제시 리버모어",
        "emoji": "⚡",
        "title": "단타 매니저",
        "persona": (
            "너는 제시 리버모어(Jesse Livermore)의 투자 철학을 따르는 단타 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- 추세를 따르라. 시장과 싸우지 마라\n"
            "- 손절은 빠르게, 수익은 달리게 하라\n"
            "- 거래량이 진실을 말한다\n"
            "- 감정을 배제하고 가격만 본다\n"
            "- 피벗 포인트(돌파/이탈)에서만 진입\n"
            "분석 시 반드시: 수급(매수/매도잔량), 거래량 변화, 분봉 패턴, 호가창 분석 포함.\n"
            "말투: 단호하고 간결. '~해야 합니다', '시장이 말하고 있습니다'.\n"
        ),
        "holding_type": "scalp",
        "check_interval": "실시간 ~ 15초",
        "strategies": {"B", "G"},
    },
    "swing": {
        "name": "윌리엄 오닐",
        "emoji": "🔥",
        "title": "스윙 매니저",
        "persona": (
            "너는 윌리엄 오닐(William O'Neil)의 CAN SLIM을 따르는 스윙 전문 매니저다.\n"
            "핵심 원칙:\n"
            "- C: Current quarterly earnings (최근 분기 이익 증가)\n"
            "- A: Annual earnings growth (연간 이익 성장)\n"
            "- N: New products/management (신제품, 신경영)\n"
            "- S: Supply/demand (수급)\n"
            "- L: Leader or laggard (업종 리더)\n"
            "- I: Institutional sponsorship (기관 매수)\n"
            "- M: Market direction (시장 방향)\n"
            "분석 시 반드시: 컵앤핸들, 더블바텀 등 차트 패턴 + 기관/외인 수급 포함.\n"
            "말투: 데이터 중심, 체계적. '통계적으로', '과거 패턴에 따르면'.\n"
        ),
        "holding_type": "swing",
        "check_interval": "매일",
        "strategies": {"A", "G", "F"},
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
            "- 분산 투자하되, 확신 있는 곳에 집중\n"
            "분석 시 반드시: PER/PEG/ROE/매출 성장률/영업이익률 포함.\n"
            "산업 동향, 경쟁 우위, 경영진 역량 분석.\n"
            "말투: 친근하고 스토리텔링. '이 회사는 ~한 이유로', '일상에서 볼 수 있듯이'.\n"
        ),
        "holding_type": "position",
        "check_interval": "주 1회",
        "strategies": {"D", "F"},
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
            "- 복리의 마법을 믿어라\n"
            "분석 시 반드시: DCF 관점의 내재가치, ROE 장기 추세, 배당 성장, 자사주 매입, 부채비율.\n"
            "말투: 지혜롭고 장기적. '장기적으로 보면', '이 기업의 본질적 가치는'.\n"
        ),
        "holding_type": "long_term",
        "check_interval": "월 1회",
        "strategies": {"C", "E"},
    },
}
```

### 매니저별 AI 분석 함수

```python
async def get_manager_analysis(
    manager_key: str,        # "scalp" | "swing" | "position" | "long_term"
    holdings: list[dict],    # 해당 유형 보유종목
    market_context: str,     # 시장 상황
    question: str = "",      # 사용자 질문 (옵션)
) -> str:
    """매니저 페르소나로 보유종목 분석."""
    manager = MANAGERS[manager_key]

    system_prompt = (
        f"너는 {manager['name']}의 투자 철학을 따르는 '{manager['title']}'이다.\n"
        f"{manager['persona']}\n"
        f"호칭: 주호님\n"
        f"볼드(**) 사용 금지. 이모지로 구분.\n"
        f"제공된 데이터만 사용. 학습 데이터의 과거 가격 사용 절대 금지.\n"
    )

    holdings_text = ""
    for h in holdings:
        holdings_text += (
            f"- {h.get('name', '')}: 매수가 {h.get('buy_price', 0):,.0f}원, "
            f"현재가 {h.get('current_price', 0):,.0f}원, "
            f"수익률 {h.get('pnl_pct', 0):+.1f}%, "
            f"보유 {h.get('holding_days', 0)}일\n"
        )

    user_prompt = (
        f"[시장 상황]\n{market_context}\n\n"
        f"[{manager['emoji']} {manager['title']} 담당 종목]\n{holdings_text}\n\n"
    )
    if question:
        user_prompt += f"[사용자 질문] {question}\n\n"
    user_prompt += (
        f"{manager['name']}의 관점에서 각 종목을 분석하고, "
        f"구체적인 행동 제안을 해주세요.\n"
        f"체크 주기: {manager['check_interval']}"
    )

    # Claude API 호출
    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1500,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )

    header = f"{manager['emoji']} {manager['name']} ({manager['title']})\n{'━' * 20}\n\n"
    return header + response.content[0].text.strip().replace("**", "")
```

### 아침 브리핑에 매니저별 리포트 통합

기존 `job_morning_briefing`에서 보유종목을 holding_type별로 그룹핑 → 각 매니저가 담당 종목 분석:

```python
# scheduler.py — job_morning_briefing에서
holdings = self.db.get_active_holdings()

# holding_type별 그룹핑
from collections import defaultdict
by_type = defaultdict(list)
for h in holdings:
    ht = h.get("holding_type", "swing")
    by_type[ht].append(h)

# 각 매니저가 담당 종목 분석 (보유종목이 있는 매니저만)
from kstock.bot.investment_managers import get_manager_analysis, MANAGERS
manager_reports = []
for mtype, mholdings in by_type.items():
    if mholdings and mtype in MANAGERS:
        report = await get_manager_analysis(mtype, mholdings, market_text)
        manager_reports.append(report)
```

### 매니저별 추천 종목

기존 장바구니에서 기간별 종목을 볼 때, 해당 매니저가 추천 코멘트 제공:

```python
# trading.py — _show_horizon_picks에서
# 종목 리스트 표시 후 매니저 한줄 코멘트 추가:
manager = MANAGERS.get(horizon)
if manager:
    header = f"{manager['emoji']} {manager['name']}: "
    # 간단한 한줄 코멘트 (AI 호출 없이 규칙 기반)
```

### 텔레그램 메뉴에 매니저 접근

```python
# 새 콜백: mgr:scalp, mgr:swing, mgr:position, mgr:long_term
# 기존 메뉴에 추가:
[⚡ 리버모어] [🔥 오닐] [📊 린치] [💎 버핏]
```

누르면 해당 매니저가 담당 종목 상태 + 코칭 메시지 제공.

---

## Part C: 거품 판별 엔진

### 참고 프레임워크 (ai_frontier 스레드)

7단계 분석:
1. 코스피 평균 PER 리서치 (Trailing/Forward)
2. 섹터 평균 PER 리서치
3. 네이버 증권 컨센서스 데이터 (연도별 영업이익)
4. 이익 성장 속도 계산 (YoY, CAGR, 둔화 여부)
5. PER vs 성장률 비교 → PEG 비율
6. 적정주가 역산 (코스피PER/섹터PER/PEG1 기준 3가지)
7. 종합 판별 (과열/적정/저평가 + 거품 확률 + 6개월 조정 확률)

### 새 파일: `src/kstock/signal/bubble_detector.py`

```python
"""거품 판별 엔진.

PER/PEG/성장률/적정주가 기반 종합 밸류에이션 분석.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BubbleAnalysis:
    """거품 판별 결과."""
    ticker: str
    name: str
    current_price: float

    # PER 분석
    trailing_per: float          # 현재 Trailing PER
    forward_per: float           # Forward PER (예상 실적 기준)
    sector_avg_per: float        # 섹터 평균 PER
    kospi_avg_per: float         # 코스피 평균 PER (약 12~13)

    # 성장률
    revenue_yoy: float           # 매출 YoY 성장률 (%)
    op_profit_yoy: float         # 영업이익 YoY 성장률 (%)
    earnings_cagr_2y: float      # 향후 2년 이익 CAGR (%)
    growth_decelerating: bool    # 이익 성장 둔화 여부

    # PEG 비율
    peg_ratio: float             # PER / 이익성장률
    peg_zone: str                # "저평가" (<1) | "적정" (1~1.5) | "고평가" (>1.5)

    # 적정주가 3가지 기준
    fair_price_kospi: float      # 코스피 평균 PER 기준 적정주가
    fair_price_sector: float     # 섹터 평균 PER 기준 적정주가
    fair_price_peg1: float       # PEG=1 기준 적정주가
    deviation_kospi_pct: float   # 코스피 기준 괴리율
    deviation_sector_pct: float  # 섹터 기준 괴리율
    deviation_peg1_pct: float    # PEG1 기준 괴리율

    # 종합 판단
    valuation: str               # "과열" | "적정" | "저평가"
    bubble_probability: float    # 거품 확률 (0~100%)
    correction_6m_prob: float    # 6개월 내 조정 확률 (0~100%)
    summary: str                 # 한줄 요약


def calculate_peg(per: float, growth_rate: float) -> float:
    """PEG 비율 계산. growth_rate가 0 이하면 999 반환."""
    if growth_rate <= 0:
        return 999.0
    return round(per / growth_rate, 2)


def classify_peg(peg: float) -> str:
    if peg < 1.0:
        return "저평가"
    elif peg <= 1.5:
        return "적정"
    else:
        return "고평가"


def calculate_fair_prices(
    eps: float,               # 주당순이익
    kospi_per: float = 12.5,  # 코스피 평균 PER
    sector_per: float = 15.0, # 섹터 평균 PER
    growth_rate: float = 10.0, # 이익 성장률 (%)
) -> dict:
    """3가지 기준 적정주가 계산."""
    return {
        "kospi": round(eps * kospi_per, 0),
        "sector": round(eps * sector_per, 0),
        "peg1": round(eps * growth_rate, 0),  # PEG=1이면 PER=성장률
    }


def analyze_bubble(
    ticker: str,
    name: str,
    current_price: float,
    trailing_per: float,
    forward_per: float,
    eps: float,
    sector_avg_per: float = 15.0,
    kospi_avg_per: float = 12.5,
    revenue_yoy: float = 0.0,
    op_profit_yoy: float = 0.0,
    earnings_cagr_2y: float = 0.0,
    prev_growth: float = 0.0,   # 이전 기간 성장률 (둔화 판단용)
) -> BubbleAnalysis:
    """종합 거품 판별."""

    # 1. 성장 둔화 판단
    growth_decelerating = (
        earnings_cagr_2y > 0 and
        prev_growth > 0 and
        earnings_cagr_2y < prev_growth * 0.7  # 성장률 30% 이상 둔화
    )

    # 2. PEG 계산
    growth_for_peg = max(earnings_cagr_2y, 1.0)
    peg = calculate_peg(forward_per, growth_for_peg)
    peg_zone = classify_peg(peg)

    # 3. 적정주가 3가지
    fair = calculate_fair_prices(eps, kospi_avg_per, sector_avg_per, growth_for_peg)
    dev_kospi = (current_price - fair["kospi"]) / fair["kospi"] * 100 if fair["kospi"] > 0 else 0
    dev_sector = (current_price - fair["sector"]) / fair["sector"] * 100 if fair["sector"] > 0 else 0
    dev_peg1 = (current_price - fair["peg1"]) / fair["peg1"] * 100 if fair["peg1"] > 0 else 0

    # 4. 종합 판단
    bubble_score = 0

    # PEG 기반 (40%)
    if peg > 2.0: bubble_score += 40
    elif peg > 1.5: bubble_score += 25
    elif peg > 1.0: bubble_score += 10

    # 섹터 PER 대비 (25%)
    if forward_per > sector_avg_per * 1.5: bubble_score += 25
    elif forward_per > sector_avg_per * 1.2: bubble_score += 15
    elif forward_per > sector_avg_per: bubble_score += 5

    # 성장 둔화 (20%)
    if growth_decelerating: bubble_score += 20
    elif earnings_cagr_2y < 5: bubble_score += 10

    # 적정주가 괴리 (15%)
    avg_deviation = (dev_kospi + dev_sector + dev_peg1) / 3
    if avg_deviation > 50: bubble_score += 15
    elif avg_deviation > 30: bubble_score += 10
    elif avg_deviation > 15: bubble_score += 5

    # 밸류에이션 등급
    if bubble_score >= 60:
        valuation = "과열"
    elif bubble_score >= 30:
        valuation = "적정"
    else:
        valuation = "저평가"

    # 6개월 조정 확률 (거품 확률 기반)
    correction_prob = min(bubble_score * 1.2, 95)

    summary = (
        f"{name}: {valuation} (거품 {bubble_score}%) | "
        f"PEG {peg:.1f} ({peg_zone}) | "
        f"적정가 {fair['sector']:,.0f}원 (괴리 {dev_sector:+.1f}%)"
    )

    return BubbleAnalysis(
        ticker=ticker, name=name, current_price=current_price,
        trailing_per=trailing_per, forward_per=forward_per,
        sector_avg_per=sector_avg_per, kospi_avg_per=kospi_avg_per,
        revenue_yoy=revenue_yoy, op_profit_yoy=op_profit_yoy,
        earnings_cagr_2y=earnings_cagr_2y,
        growth_decelerating=growth_decelerating,
        peg_ratio=peg, peg_zone=peg_zone,
        fair_price_kospi=fair["kospi"],
        fair_price_sector=fair["sector"],
        fair_price_peg1=fair["peg1"],
        deviation_kospi_pct=round(dev_kospi, 1),
        deviation_sector_pct=round(dev_sector, 1),
        deviation_peg1_pct=round(dev_peg1, 1),
        valuation=valuation,
        bubble_probability=bubble_score,
        correction_6m_prob=round(correction_prob, 1),
        summary=summary,
    )
```

### 텔레그램 포맷

```python
def format_bubble_analysis(b: BubbleAnalysis) -> str:
    """거품 판별 결과 텔레그램 표시."""
    icon = "🔴" if b.valuation == "과열" else "🟢" if b.valuation == "저평가" else "🟡"

    return (
        f"{icon} {b.name} 밸류에이션 분석\n"
        f"{'━' * 22}\n\n"
        f"현재가: {b.current_price:,.0f}원\n"
        f"Trailing PER: {b.trailing_per:.1f} | Forward PER: {b.forward_per:.1f}\n"
        f"섹터 평균 PER: {b.sector_avg_per:.1f}\n\n"
        f"📈 성장률\n"
        f"  매출 YoY: {b.revenue_yoy:+.1f}%\n"
        f"  영업이익 YoY: {b.op_profit_yoy:+.1f}%\n"
        f"  2년 CAGR: {b.earnings_cagr_2y:.1f}%\n"
        f"  {'⚠️ 성장 둔화 감지' if b.growth_decelerating else '✅ 성장 지속'}\n\n"
        f"📊 PEG: {b.peg_ratio:.2f} → {b.peg_zone}\n\n"
        f"💰 적정주가 (3가지 기준)\n"
        f"  코스피 PER 기준: {b.fair_price_kospi:,.0f}원 ({b.deviation_kospi_pct:+.1f}%)\n"
        f"  섹터 PER 기준: {b.fair_price_sector:,.0f}원 ({b.deviation_sector_pct:+.1f}%)\n"
        f"  PEG=1 기준: {b.fair_price_peg1:,.0f}원 ({b.deviation_peg1_pct:+.1f}%)\n\n"
        f"{'━' * 22}\n"
        f"{icon} 판정: {b.valuation}\n"
        f"🎯 거품 확률: {b.bubble_probability:.0f}%\n"
        f"📉 6개월 조정 확률: {b.correction_6m_prob:.0f}%\n"
    )
```

### 메뉴 연결

```python
# 기존 분석 메뉴에 추가:
[🫧 거품 판별]

# 콜백: bubble:{ticker}
# 보유종목 선택 → 거품 판별 실행
# 또는 AI 질문에서 "삼성전자 거품 판별해줘" → 자동 실행
```

### 아침 브리핑에 거품 경고 통합

보유종목 중 PEG > 2.0 또는 bubble_probability > 60%인 종목이 있으면:

```
⚠️ 밸류에이션 경고
에코프로 (🔥스윙): PEG 3.2 (과열) — 거품 확률 72%
→ 윌리엄 오닐: "이익 성장 둔화 시 차트 패턴 붕괴 가능성 높음"
```

---

## Part D: PDF 리포트 수정

### 문제 1: 표 글씨 겹침

`daily_pdf_report.py`의 테이블에서 **컬럼 너비가 좁아서** 한글이 겹침.

**수정:**

```python
# _table_style() — 폰트 크기 축소 + 패딩 증가
def _table_style(font_name: str = "Korean"):
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, 0), 7),     # 헤더: 8→7
        ("FONTSIZE", (0, 1), (-1, -1), 7),     # 본문: 8→7
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),    # 첫 컬럼은 좌측 정렬
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8f9fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),   # 3→4
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), # 3→4
        ("LEFTPADDING", (0, 0), (-1, -1), 3),   # 추가
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),  # 추가
        ("WORDWRAP", (0, 0), (-1, -1), True),    # 줄바꿈 허용 (선택)
    ])
```

### 문제 2: 글로벌 시장 테이블 컬럼 너비

```python
# 기존 (겹치는 원인):
colWidths=[25 * mm, 30 * mm, 22 * mm, 22 * mm]
# → 총 99mm. A4 가용 180mm 중 절반도 안 씀

# 수정 — 가용 폭을 충분히 활용:
colWidths=[35 * mm, 35 * mm, 30 * mm, 30 * mm]
# → 총 130mm. 여유 있음
```

### 문제 3: 보유종목 테이블 컬럼 너비

```python
# 기존:
colWidths=[25 * mm, 22 * mm, 22 * mm, 18 * mm, 15 * mm, 18 * mm]
# → 종목명 25mm로 한글 8자가 안 들어감

# 수정:
colWidths=[30 * mm, 25 * mm, 25 * mm, 20 * mm, 18 * mm, 22 * mm]
```

### 문제 4: 스마트폰 최적화 — 글자 크기 키우기

```python
# _create_styles() 수정:
custom_styles["body"] = ParagraphStyle(
    name="ReportBody",
    fontName=font_name,
    fontSize=10,          # 9→10 (스마트폰 가독성)
    leading=15,           # 14→15 (줄간격)
    textColor=colors.HexColor("#333333"),
)
custom_styles["small"] = ParagraphStyle(
    name="SmallBody",
    fontName=font_name,
    fontSize=9,           # 8→9
    leading=13,           # 11→13
    textColor=colors.HexColor("#555555"),
)
custom_styles["section"] = ParagraphStyle(
    name="SectionHeader",
    fontName=font_name,
    fontSize=13,          # 12→13
    spaceBefore=5 * mm,   # 4→5
    spaceAfter=3 * mm,    # 2→3
    textColor=colors.HexColor("#16213e"),
)
```

### 문제 5: 매도계획 테이블도 수정

```python
# 기존:
colWidths=[25 * mm, 15 * mm, 22 * mm, 22 * mm, 40 * mm]
# → "전략" 컬럼 40mm에 30자 → 겹침

# 수정 — Paragraph로 셀 내 줄바꿈 허용:
# 전략 컬럼은 Paragraph 객체로 감싸기
for plan in sell_plans[:10]:
    strategy_text = Paragraph(plan.strategy[:60], styles["small"])
    sp_rows.append([
        plan.name[:8],
        plan.horizon[:4],
        str(plan.target),
        str(plan.stoploss),
        strategy_text,  # 문자열 대신 Paragraph
    ])

colWidths=[28 * mm, 18 * mm, 25 * mm, 25 * mm, 50 * mm]
```

---

## Part E: PDF 가격 데이터 신뢰성 (치명적 버그 수정)

### 문제

PDF 리포트에서 **SK하이닉스 주가가 작년 데이터(195,000원)**로 표시됨. 실제 현재가는 ~203,500원.
이 문제가 발생하면 **리포트 전체의 신뢰성이 0**이 됨.

### 원인 분석

1. `generate_daily_pdf()`의 `holdings` 파라미터에 전달되는 `current_price`가 DB에 저장된 **오래된 값**
2. `_generate_ai_analysis()`에서 Claude에게 보유종목 데이터를 전달하지만, Claude가 **학습 데이터의 과거 가격**을 사용할 수 있음
3. holdings의 `current_price` 갱신 시점이 리포트 생성 시점보다 훨씬 이전일 수 있음

### 수정 1: `generate_daily_pdf()` 시작 시 실시간 가격 갱신

```python
# daily_pdf_report.py — generate_daily_pdf() 함수 시작 부분에 추가

async def generate_daily_pdf(
    macro_snapshot,
    holdings: list[dict],
    sell_plans: list | None = None,
    sector_data: list | None = None,
    pulse_history: list | None = None,
    date: datetime | None = None,
    yf_client=None,  # ← 새 파라미터 추가
) -> str | None:

    # 1. 보유종목 현재가 실시간 갱신
    if holdings and yf_client:
        import asyncio
        for h in holdings:
            ticker = h.get("ticker", "")
            if not ticker:
                continue
            try:
                # yfinance로 최신 가격 조회
                fresh_price = await yf_client.get_current_price(ticker)
                if fresh_price and fresh_price > 0:
                    old_price = h.get("current_price", 0)
                    h["current_price"] = fresh_price
                    # 수익률도 재계산
                    buy_price = h.get("buy_price", 0)
                    if buy_price > 0:
                        h["pnl_pct"] = (fresh_price - buy_price) / buy_price * 100
                    if old_price > 0 and abs(fresh_price - old_price) / old_price > 0.05:
                        logger.warning(
                            "가격 갭 감지: %s 기존=%s 갱신=%s (차이 %.1f%%)",
                            h.get("name", ticker), old_price, fresh_price,
                            (fresh_price - old_price) / old_price * 100
                        )
            except Exception as e:
                logger.debug("가격 갱신 실패 %s: %s", ticker, e)
                # 실패해도 기존 가격 유지, 리포트 생성은 계속

    # ... 이하 기존 로직
```

### 수정 2: `_generate_ai_analysis()` 프롬프트에 안티-환각 강화

```python
# daily_pdf_report.py — _generate_ai_analysis() 시스템 프롬프트 수정

system=(
    "당신은 Goldman Sachs Global Investment Research 팀의 "
    "수석 전략가입니다. 기관 투자자에게 제공하는 유료 데일리 리포트를 "
    "작성합니다. 모든 분석은 데이터에 기반하며, 구체적 수치와 "
    "논리적 근거를 반드시 포함합니다. 추상적 표현 대신 "
    "실행 가능한 투자 인사이트를 제공합니다. "
    "볼드(**), HTML 태그 사용 금지.\n\n"

    # ===== 아래 추가 (안티-환각 규칙) =====
    "⚠️ 절대 규칙 (위반 시 리포트 전체 신뢰 상실):\n"
    "1. 제공된 [보유종목] 데이터의 현재가만 사용하라.\n"
    "2. 학습 데이터에 있는 과거 주가를 절대 사용하지 마라.\n"
    "3. 가격 데이터가 제공되지 않은 종목은 '현재가 확인 필요'라고 표시하라.\n"
    "4. 종목의 구체적 주가를 언급할 때는 반드시 제공된 데이터에서 가져와라.\n"
    "5. 추정, 기억, 과거 학습된 가격 정보는 절대 사용 금지.\n"
),
```

### 수정 3: holdings 데이터에 갱신 시각 표시

```python
# _generate_ai_analysis()의 holdings_text 생성 부분 수정:

holdings_text = ""
if holdings:
    for h in holdings[:15]:
        current_price = h.get('current_price', 0)
        price_tag = f"{current_price:,.0f}원" if current_price > 0 else "현재가 없음"
        holdings_text += (
            f"  {h.get('name', '')}: 수익률 {h.get('pnl_pct', 0):+.1f}%, "
            f"매수가 {h.get('buy_price', 0):,.0f}원, "
            f"현재가 {price_tag} (실시간 갱신됨), "
            f"시계 {h.get('horizon', 'swing')}\n"
        )
```

### 수정 4: 리포트 호출부에서 yf_client 전달

```python
# scheduler.py — job_daily_pdf에서 yf_client 전달
filepath = await generate_daily_pdf(
    macro_snapshot=snap,
    holdings=holdings,
    sell_plans=sell_plans,
    yf_client=self.yf_client,  # ← 추가
)
```

### 수정 5: 가격 갱신 실패 시 경고 표시

```python
# generate_daily_pdf() — holdings 테이블 생성 시:
for h in holdings[:15]:
    current_price = h.get("current_price", 0)
    buy_price = h.get("buy_price", 0)

    # 가격이 0이거나 매수가와 차이 50% 이상이면 경고
    if current_price <= 0:
        price_str = "미확인"
    else:
        price_str = f"{current_price:,.0f}"

    pnl = h.get("pnl_pct", 0)
    # ... 테이블 행 추가
```

---

## 검증

1. `PYTHONPATH=src python3 -m pytest tests/ -x -q` 전체 통과
2. 관리자 모드에서 이미지 전송 후에도 모드 유지 확인
3. 매니저별 분석이 페르소나에 맞는지 (리버모어=단호, 버핏=지혜)
4. 거품 판별에서 PEG < 1 = 저평가, PEG > 2 = 과열 확인
5. PDF 표에서 글씨 겹침 없는지 확인
6. 스마트폰에서 PDF 글자 읽을 수 있는지
7. **PDF 가격 신뢰성**: SK하이닉스 등 보유종목 현재가가 실시간 데이터인지 확인
8. **AI 환각 방지**: `_generate_ai_analysis()` 프롬프트에 안티-환각 규칙 포함 확인

## 테스트

```python
# tests/test_investment_managers.py
def test_all_managers_defined():
    """4명의 매니저가 모두 정의되어 있는지."""

def test_manager_persona_length():
    """페르소나가 충분히 상세한지 (최소 200자)."""

# tests/test_bubble_detector.py
def test_peg_below_1_is_undervalued():
def test_peg_above_2_is_overheated():
def test_fair_price_calculation():
def test_growth_deceleration_detection():

# tests/test_pdf_fix.py
def test_table_column_widths_fit_a4():
    """컬럼 너비 합이 A4 가용 폭(180mm) 이내인지."""

# tests/test_pdf_price_freshness.py
def test_generate_pdf_refreshes_prices():
    """generate_daily_pdf가 yf_client로 가격을 갱신하는지."""

def test_ai_analysis_prompt_has_anti_hallucination():
    """_generate_ai_analysis 프롬프트에 안티환각 규칙이 포함되는지."""

def test_holdings_stale_price_warning():
    """current_price가 0이면 '미확인'으로 표시되는지."""
```

## 주의사항

| 항목 | 주의 |
|------|------|
| 관리자 모드 | handle_photo()에서도 claude_mode 체크 추가 필수 |
| 매니저 API 비용 | 4명 동시 호출 시 약 $0.06. 아침 브리핑에서만 + 보유종목 있는 매니저만 |
| 거품 판별 데이터 | PER/EPS는 yfinance 또는 DB에서. 없으면 네이버 증권 크롤링 fallback |
| PDF 테스트 | reportlab 있는 환경에서만 PDF 테스트 가능 |
| 스마트폰 | 폰트 10pt + leading 15가 최소. 더 작으면 안 됨 |
| 매니저 말투 | 각 투자자의 실제 저서/인터뷰 기반. 캐릭터 일관성 유지 |
| PDF 가격 | generate_daily_pdf 호출 시 yf_client 반드시 전달. 안 하면 DB의 오래된 가격 사용됨 |
| AI 환각 | _generate_ai_analysis 시스템 프롬프트에 안티-환각 규칙 필수 포함 |
| 가격 갭 로그 | 갱신 전후 가격 차이 5% 이상이면 WARNING 로그 출력하여 이상 감지 |
| PYTHONPATH=src | 반드시 설정 |
