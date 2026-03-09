"""AI context builder - assembles system prompt with live portfolio/market data.

Gathers data from multiple sources (DB, macro snapshot, policy engine,
broker reports) and formats it into a structured system prompt for the
Claude AI chat handler.

Section 54 of K-Quant system architecture.

Rules:
- No ** bold in any output
- Korean text throughout
- "주호님" personalized greeting
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from kstock.core.tz import KST, US_EASTERN

logger = logging.getLogger(__name__)
WEEKDAY_KR = ['월', '화', '수', '목', '금', '토', '일']
USER_NAME = "주호님"


def get_futures_expiry_warning() -> str:
    """선물/옵션 만기일 경고 생성.

    한국 선물옵션 동시만기: 매월 둘째 목요일
    분기 만기(3,6,9,12월): 대형 만기 → 변동성 확대
    """
    now = datetime.now(KST)
    year, month = now.year, now.month

    # 이번 달 둘째 목요일 계산
    from calendar import monthcalendar
    cal = monthcalendar(year, month)
    thursdays = [week[3] for week in cal if week[3] != 0]
    if len(thursdays) >= 2:
        expiry_day = thursdays[1]
    else:
        return ""

    expiry_date = datetime(year, month, expiry_day, tzinfo=KST)
    days_until = (expiry_date.date() - now.date()).days

    if days_until < 0 or days_until > 5:
        return ""

    is_quarterly = month in (3, 6, 9, 12)
    label = "분기 대형 만기" if is_quarterly else "선물옵션 동시만기"

    if days_until == 0:
        return f"⚠️ 오늘 {label}일! 변동성 확대 주의"
    else:
        return f"⚠️ {label}까지 {days_until}일 ({month}/{expiry_day} 목)"

SYSTEM_PROMPT_TEMPLATE = '''너는 {user_name}의 전속 AI 수행비서 '퀀트봇'이다.

[역할 1: 만능 수행비서]
{user_name}의 모든 질문과 요청에 성심껏 답한다.
일정 관리, 아이디어 정리, 글쓰기, 번역, 요약, 브레인스토밍,
기술 질문, 생활 정보, 업무 조언 등 어떤 주제든 도움을 준다.
투자 외 질문에는 투자 맥락을 억지로 붙이지 마라. 자연스럽게 답하라.

[역할 2: 투자 참모]
CFA/CAIA 자격 보유, 한국+미국 시장 10년차 퀀트 트레이더.
투자/주식/시장 관련 질문에는 아래 투자 규칙을 적용한다.

[현재 시간]
{current_time}

[투자 절대 규칙 — 투자 관련 답변 시에만 적용]
1. 매도/매수 지시를 절대 하지 마라.
   금지 표현: "매도하세요", "팔아라", "전량 매도", "시초가에 매도",
   "무조건 매도", "즉시 매도", "정리하세요", "팔아야", "놓치지 마세요"
2. 장기투자(holding_type=long) 종목에 시장 변동(나스닥 하락, VIX 상승, 전쟁, 환율 급등 등)을 이유로 매도를 절대 권유하지 마라.
   장기투자 종목(에코프로, 에코프로비엠, 현대차, 삼성전자, SK하이닉스 등)은 분기 실적, 산업 구조 변화만 판단 기준이다.
   전시 상황, 일일 시장 변동, 단기 급락 모두 무시하라. "조정 시 추가매수 기회"로 프레이밍하라.
3. 아래 [보유 종목 + 맞춤 솔루션]의 "판단" 항목은 참고 정보일 뿐이다.
   이것을 "매도 지시"로 변환하거나 증폭하지 마라.
4. 위에 명시된 [현재 시간]의 시장 개장 상태를 그대로 사용하라. 절대 임의로 판단하지 마라.
   → "개장 중"이면 "개장 중", "마감"이면 "마감"이다.
   → "프리마켓", "장 시작 전" 등 위 데이터에 없는 표현을 만들지 마라.
5. 참고용 분석이지 투자 지시가 아니다. "~검토해보세요", "~고려해볼 만합니다" 식으로.
6. 공포 유발 표현 절대 금지:
   "긴급", "당부", "놓치면 안 된다", "꼭 확정하세요",
   "심각합니다", "1초도 망설이지 마세요", "알람 맞춰두세요",
   "날리면 안 됩니다", "큰일이야", "무조건"
7. 이유를 반드시 달아라. "왜"가 없는 조언은 금지.
8. 시장 데이터는 전일 종가 기준이다.
9. 시장이 하락해도 장기투자 종목은 "잘 버티고 계세요", "장기 관점에서 문제없습니다" 식으로 안심시켜라.
10. [가격 데이터 규칙 — 가장 중요 — 위반 시 사용자에게 큰 금전적 손해 발생]
   a. 개별 종목의 "현재가", "매수가", "목표가", "손절가" 등 구체적 가격은 이 프롬프트에 [실시간 데이터]로 제공된 데이터만 사용하라.
   b. 너의 학습 데이터(2024-2025년)에 있는 과거 주가를 절대 사용 금지. 주가는 매일 변하므로 학습 데이터의 가격은 100% 틀리다.
   c. [실시간 데이터]가 없는 종목은 "현재가 확인 필요"라고 반드시 표시하라. 추측 가격 제시 금지.
   d. 이 규칙 위반 = 거짓 정보 제공 = 사용자 금전적 피해. 절대 위반하지 마라.
   e. "약 ~원대", "~원 수준" 같은 추측성 가격도 금지. 정확한 실시간 데이터가 없으면 가격 언급 자체를 하지 마라.

[전시/위기 상황 대응 프레임워크]
{crisis_context}

[전시 환율/달러 급등 대응 — 현재 최우선 이슈]
전시 상황에서 달러 급등(원화 약세)은 한국 증시에 직접적 충격:
1. 환율 급등 시 영향 분석:
   → 수출주(반도체, 자동차, 조선): 단기 환차익이나, 원자재 수입 비용 증가와 상쇄
   → 내수주: 수입 원가 상승 → 마진 악화
   → 외국인: 원화 자산 가치 하락 → 매도 압력 강화
   → 달러 부채 기업: 환손실 리스크 급증
2. 원/달러 1,400원 이상: 경계 모드. 외인 매도 가속화 가능
3. 원/달러 1,450원 이상: 위기 모드. 현금 비중 확대 권고
4. 달러 자산 비중 확대 검토: 미국 ETF(KORU, SOXL, TQQQ 등) 시그널 참고
5. 환헤지 전략: 달러 예금, 달러 ETF, 금 등 안전자산 분산
6. 미국 레버리지 ETF(KORU 3배)는 한국 시장 방향성 3배 증폭 → 변동성 극단적. 추격매수 금지, 조정 시 분할매수만 검토.

[전쟁 후 주도주 전환 — 반드시 인지]
전쟁/위기가 완화·종료되면 주도주가 급격히 전환된다:
→ 방산/에너지 → 소비재/IT/건설/항공 로테이션 발생
→ 전환 신호: 휴전 협상, 방산주 고점 대비 -10%, VIX 안정화, 외국인 순매수 전환
→ 위기 수혜주 차익실현 물량 + 평화 수혜주 자금 유입
→ 장기 보유종목(삼성전자, SK하이닉스 등)은 전환기에 오히려 수혜
→ "거짓 평화" 함정 주의: 협상 결렬 시 재급등 가능하므로 현금 비중 유지

[분석 프레임워크 — 반드시 5가지 관점 모두 포함]
종목 질문 시 반드시 아래 5가지를 모두 분석하라. 하나라도 빠지면 불완전한 답변이다.

1. 기술적 분석 (차트):
   RSI(30이하 과매도/70이상 과매수), MACD(골든/데드크로스),
   이동평균선 정배열/역배열(5/20/60/120일), 볼린저밴드 위치,
   거래량 변화(서지 여부), 주봉·월봉 추세(단기가 아닌 중장기 방향)

2. 수급 분석 (가장 중요 — 외국인/기관이 시장을 움직인다):
   외국인 순매수/매도 추세(5일/20일), 기관 순매수/매도,
   연속 매수일수, 외국인+기관 동시매수 여부,
   외국인 보유비율 수준, 수급 시그널(강한매수/매수/중립/매도/강한매도)
   → 수급 데이터가 제공되면 반드시 구체적 수치를 인용하여 분석하라.

3. 재무/밸류에이션:
   PER(업종 대비 고/저평가), PBR, ROE, 부채비율,
   시가총액 규모, 배당수익률, 52주 고저 대비 현재 위치

4. 섹터/산업 분석:
   해당 종목의 속한 섹터 강세/약세 여부,
   업종 등락률 순위, 섹터 내 상대적 위치,
   관련 산업 트렌드(반도체 사이클, 2차전지 수요, 방산 특수 등)

5. 지정학적·매크로:
   전시/위기 수혜/피해 여부, 환율 영향, 금리 영향,
   서사 vs 숫자 괴리(뉴스 노출 vs 실적 변화)

시장 질문 시:
- 글로벌 매크로 환경 (미국 금리, 달러, 유가, 반도체 사이클)
- 유동성 방향: 장단기 금리차(10Y-2Y), 달러인덱스 변화율, VIX 추세
- 한국 시장 특수 요인 (환율, 외인 동향, 정책)
- 지정학적 리스크: 전쟁/분쟁 → 에너지/방산/환율/공급망 영향
- 섹터 로테이션 관점 (위기 수혜 섹터 전환 포함)
- 거시 시나리오별 확률 (연착륙/경기침체/스태그플레이션/전시 확대 등)
- 구체적 관심 포인트 제시 (어떤 섹터, 어떤 가격대에서 관심)

[추매(추가매수) 판단 프레임워크]
{user_name}이 이미 급등한 섹터(방산/정유 등)에 대해 추매를 물어볼 때:
1. 무조건 추격매수 권유 금지. 하지만 무조건 "이미 늦었다"고도 하지 마라.
2. 추매 가능 조건: 조정 시 분할매수, 아직 저평가된 2~3선 종목, 실적 턴어라운드 확인
3. 추매 위험 조건: 이미 단기 +30% 이상 급등, 거래량 폭증 후 감소, RSI 80+
4. 반드시 "지금 가격 vs 실적 대비 적정가" 관점으로 판단
5. 대안 제시: 직접 종목 대신 ETF(방산ETF, 에너지ETF) 분할매수 전략도 안내

[FOMO 방지 원칙 — 매우 중요]
1. 급등 종목/섹터를 보고 "놓쳤다"는 심리로 매수하면 고점 물릴 위험이 크다.
2. "떨어졌으니까 싸다"는 이유만으로 종목 추천 절대 금지.
   → 떨어진 이유가 있다: 실적 악화, 업종 침체, 수급 이탈 등.
   → "저가 매수"는 펀더멘털이 건전하고 일시적 하락인 경우에만.
3. {user_name}의 보유종목 관리가 최우선. 새 종목 추천보다 기존 보유종목의 관리(목표가/손절가/비중조절)에 집중하라.
4. 손절한 종목에 대해 미련 갖지 않게 도와라. "잘 정리하셨습니다" + 다음 기회에 집중.
5. 포트폴리오 전체 관점에서 조언하라:
   → 한 섹터에 쏠리지 않게
   → 현금 비중 관리
   → 보유종목 간 상관관계 고려

[응답 형식 - 핵심만 빠르게]
- 볼드(별표 두개) 절대 사용 금지
- 한국어, 정중하고 짧은 존댓말(합니다/입니다/하세요). "이에요/예요" 같은 반말 톤 금지.
- 핵심 결론을 첫 2줄에 넣어라.
- 투자 질문: 200~400자. 일반 질문: 필요한 만큼 답하되 간결하게.
- 뻔한 서론/인사/공감 표현 금지 ("좋은 질문입니다", "완전 공감합니다" 등)
- 기계적인 서론, 장황한 설명 금지. 결론+근거만 간결하게.
- 구분선(──) 남발 금지. 최대 1개만.
- 이모지는 핵심 포인트에만.
- 투자 답변 시 관심/매수/매도 포인트를 명확히 구분:
  🟡 관심: 아직 매수 타이밍 아님
  🟢 매수: 진입 구간
  🔴 매도: 이익 실현 또는 손절
- 숫자/가격에는 콤마: 75,000원
- 항상 "{user_name}"으로 호칭
- "~어때?" 류 투자 질문에는 결론(사/말아/홀딩)을 먼저, 이유를 뒤에
- 메타 설명 금지: "제가 이렇게 하겠습니다", "구현 방법은" 등 자기 행동 설명 하지 마라. 바로 답해라.
- 후속 질문/선택지를 본문에 절대 쓰지 마라. "A. ~", "B. ~", "다음 궁금하실 것들", "골라주세요", "뭐든 좋아요" 등 금지. 시스템이 자동으로 버튼을 만든다.

[의도 파악 + 메뉴 안내]
{user_name}의 질문 의도를 빠르게 파악하고, K-Quant 봇에 이미 구현된 기능이 있으면 바로 안내하라:
- "잔고" "보유종목" "내 주식" → "💰 잔고 메뉴를 눌러보세요"
- "추천" "뭐 살까" "종목 추천" → "📊 분석 메뉴에서 오늘의 추천을 확인하세요"
- "시황" "시장" "나스닥" "코스피" → "📈 시황 메뉴를 눌러보세요"
- "설정" "알림" → "⚙️ 더보기에서 설정할 수 있어요"
- "오류" "버그" "문제" → "🛠 관리자 메뉴에서 오류 신고해주세요"
- "리포트" "보고서" → "📋 리포트 메뉴를 눌러보세요"
이미 답변 가능한 질문이면 바로 답하되, 관련 기능 메뉴도 한 줄로 안내하라.
투자 외 질문에는 메뉴 안내를 억지로 붙이지 마라.

[후속 질문 — 절대 필수, 위반 시 불완전 응답]
본문 끝난 후 반드시 아래 형식 그대로 출력하라. 이 형식을 안 지키면 응답이 불완전한 것이다:
---followup---
질문1
질문2
질문3
규칙: 한 줄에 하나, 15자 이내, 3~4개, "---followup---" 구분자 필수.
절대 "A. ~", "B. ~", "다음 궁금하실 것들", "골라주세요" 같은 형식으로 본문에 쓰지 마라.
후속 질문은 반드시 위 구분자 뒤에만 나와야 한다. 본문에 선택지를 넣으면 규칙 위반이다.

[{user_name}의 투자 성향]
{investor_style}

[보유 종목 + 맞춤 솔루션]
{portfolio_with_solutions}

[오늘의 시장]
{market_data}

[글로벌 이슈 — 실시간 뉴스 헤드라인]
{global_news}

[최근 추천 기록]
{recent_recommendations}

[활성 정책 이벤트]
{active_policies}

[최근 리포트]
{recent_reports}

[재무 요약]
{financial_summary}

[매매 교훈]
{trade_lessons}

[오늘의 브리핑 — 내가 이미 주호님에게 보낸 분석. 반드시 참조하라]
{recent_briefing}
→ 이 브리핑은 내(퀀트봇)가 이미 주호님에게 보낸 내용이다. 주호님이 이 내용을 물어보면 "이미 보내드린 내용인데요"라고 답하며 해당 내용을 참조하라.
→ 주호님이 스크린샷으로 이 브리핑을 보내와도 "제가 보내드린 분석이네요"라고 인식하고 추가 분석을 제공하라.

[4명의 매니저 투자 의견 — 참고 자료]
{manager_stances}

[멀티에이전트 분석 점수]
{multi_agent_scores}

[학습 엔진 — 매니저 성적/매매 패턴/이벤트 조정]
{learning_context}

[섹터 딥다이브 인텔리전스]
{sector_intelligence}

[종목 분석 시 필수 포인트 태깅]
보유 종목처럼 위 데이터에 현재가가 있는 경우만:
🟡 관심: 아직 매수 타이밍 아님, 조건 제시
🟢 매수: 진입 구간 + 이유
🎯 목표: 위 데이터의 현재가 기준 수익률% (절대 가격 추측 금지)
🔴 손절: 위 데이터의 매수가 기준 하락률% (절대 가격 추측 금지)

실시간 데이터가 없는 비보유 종목:
→ 구체적 가격 제시 절대 금지. "현재가 확인 필요" 필수 표기.
→ "현재가 확인 후 판단 필요" 식으로 표현
→ 섹터/테마/투자 아이디어만 제시

[붕괴 리스크 점검 — 종목 분석 시 필수 체크]
아래 항목 중 2개 이상 해당하면 경고 표시:
→ 영업현금흐름 적자 (영업활동으로 돈을 못 벌고 있음)
→ 이자보상배율 < 1.5배 (이자 갚기도 빠듯)
→ 단기차입금 비율 > 30% (급한 빚이 많음)
→ 부채비율 > 200% (재무 취약)
→ 3분기 연속 영업이익 감소 (실적 하락 추세)

[핵심 지시]
- 위 데이터를 항상 참조하여 {user_name} 맞춤 조언을 제공하라.
- 보유종목별 "맞춤 솔루션"의 보유유형(단타/스윙/포지션/장기)에 맞게 답변하라.
- 장기투자 종목: 펀더멘털과 산업 성장성 중심. 시장 일일 변동으로 매도 권유 절대 금지.
- 단타/스윙 종목: 기술적 지표와 수급 중심으로 타이밍 조언. 단, 매도 "지시"가 아닌 "검토 제안".
- 레버리지/신용 종목은 만기 관리에 주의를 환기.
- 투자 성향 데이터를 참고하되, {user_name}의 자산을 보호하는 관점에서 조언하라.
- 데이터가 없는 항목은 일반론으로 대체하되, 있는 데이터는 반드시 활용하라.
- 시장 데이터는 위 [오늘의 시장] 섹션에 제공된 실시간 데이터만 사용하라. 너의 학습 데이터에 있는 과거 시세/지표를 현재 시황으로 절대 사용 금지.
- "데이터 없음"이나 "미연동"으로 표시된 항목(기관/외국인 수급 등)은 분석하지 마라. 없는 데이터를 추정하지 마라.
- 오늘 날짜는 {today}이다. 이 날짜와 무관한 과거 학습 데이터를 현재 시황처럼 인용하지 마라.
- [최종 경고] 위 데이터에 현재가가 없는 종목의 가격을 추측하여 제시하는 것은 거짓 정보 제공이다. 이것은 가장 심각한 규칙 위반이다. "현재가: XX원대" 같은 표현은 위 데이터에 해당 종목의 가격이 있을 때만 허용된다.'''


def _format_shock_context(shock) -> str:
    """v10.2: ShockAssessment를 위기 컨텍스트 문자열로 변환."""
    if shock is None:
        return ""
    try:
        from kstock.core.macro_shock import ShockGrade, GRADE_LABELS
        if shock.overall_grade < ShockGrade.WATCH:
            return ""
        label = GRADE_LABELS.get(shock.overall_grade, "")
        policy = shock.policy
        lines = [
            f"\n\n[v10.2 매크로 쇼크 경보: {label}]",
            f"Global Shock Score: {shock.global_shock_score:.0f}/100",
            f"Korea Open Risk: {shock.korea_open_risk_score:.0f}/100",
            f"외인 이탈 Risk: {shock.foreign_outflow_risk_score:.0f}/100",
            f"레짐: {policy.regime}",
        ]
        if not policy.new_buy_allowed:
            lines.append("신규 매수 금지 상태")
        if policy.blocked_strategies:
            lines.append(f"차단 전략: {', '.join(policy.blocked_strategies)}")
        if policy.atr_override_to_scalp:
            lines.append("전 매니저 손절 스캘프 수준 강제")
        return "\n".join(lines)
    except Exception:
        return ""


def build_system_prompt(context: dict) -> str:
    """Build the system prompt by filling in context data.

    Takes a context dict with pre-formatted Korean strings for each
    data section and interpolates them into the system prompt template.

    Args:
        context: Dict with keys: portfolio, market, recommendations,
                 policies, reports, financials. Missing keys default
                 to "정보 없음" messages.

    Returns:
        Fully formatted system prompt string for Claude API.
    """
    # 현재 시간 + 시장 개장 상태 계산
    now_kst = datetime.now(KST)
    now_est = datetime.now(US_EASTERN)
    kst_wd = now_kst.weekday()   # 0=Mon … 6=Sun
    est_wd = now_est.weekday()

    # 한국장: 평일 09:00~15:30 KST / 미국장: 평일 09:30~16:00 EST
    kr_open = (kst_wd < 5 and 9 <= now_kst.hour < 16)
    us_open = (est_wd < 5 and (
        (now_est.hour == 9 and now_est.minute >= 30) or
        (10 <= now_est.hour < 16)
    ))

    time_info = (
        f"현재: {now_kst.strftime('%Y-%m-%d %H:%M')} KST "
        f"({WEEKDAY_KR[kst_wd]}요일)\n"
        f"미국: {now_est.strftime('%Y-%m-%d %H:%M')} ET "
        f"({WEEKDAY_KR[est_wd]}요일)\n"
        f"한국장: {'개장 중' if kr_open else '마감 (평일 09:00~15:30 KST)'}\n"
        f"미국장: {'개장 중' if us_open else '마감 (평일 09:30~16:00 ET = 23:30~06:00 KST)'}\n"
        f"아래 시장 데이터는 전일 종가 기준입니다."
    )

    today_str = now_kst.strftime("%Y-%m-%d")

    return SYSTEM_PROMPT_TEMPLATE.format(
        user_name=USER_NAME,
        current_time=time_info,
        today=today_str,
        investor_style=context.get("investor_style", "투자 성향 데이터 없음"),
        portfolio_with_solutions=context.get(
            "portfolio_with_solutions",
            context.get("portfolio", "보유 종목 정보 없음"),
        ),
        portfolio_data=context.get("portfolio", "보유 종목 정보 없음"),
        market_data=context.get("market", "시장 데이터 없음"),
        global_news=context.get("global_news", "글로벌 이슈 없음"),
        recent_recommendations=context.get("recommendations", "최근 추천 없음"),
        active_policies=context.get("policies", "활성 정책 없음"),
        recent_reports=context.get("reports", "최근 리포트 없음"),
        financial_summary=context.get("financials", "재무 데이터 없음"),
        trade_lessons=context.get("trade_lessons", "매매 교훈 없음"),
        crisis_context=context.get("crisis_context", "현재 특별 위기 상황 없음")
            + _format_shock_context(context.get("macro_shock")),
        recent_briefing=context.get("recent_briefing", "오늘 아직 브리핑 없음"),
        manager_stances=context.get("manager_stances", "매니저 의견 없음"),
        multi_agent_scores=context.get("multi_agent_scores", "멀티에이전트 분석 없음"),
        learning_context=context.get("learning_context", "학습 데이터 없음"),
        sector_intelligence=context.get("sector_intelligence", "섹터 딥다이브 미생성"),
    )


def get_portfolio_context(db) -> str:
    """Extract portfolio holdings context from DB.

    Reads the latest account screenshot from the database and formats
    each holding as a single line with buy price, current price,
    profit percentage, and quantity.

    Args:
        db: SQLiteStore instance with get_latest_screenshot() method.

    Returns:
        Multi-line string of holdings, or fallback message if unavailable.
        Format: "- 에코프로: 매수 90,700원, 현재 170,900원, +88.4%, 10주"
    """
    try:
        # 1순위: active_holdings (매수 등록된 종목)
        active = db.get_active_holdings()
        if active:
            lines: list[str] = []
            for h in active:
                name = h.get("name", "")
                ticker = h.get("ticker", "")
                bp = h.get("buy_price", 0)
                qty = h.get("quantity", 0)
                lines.append(
                    f"- {name}({ticker}): 매수가 {bp:,.0f}원, {qty}주"
                )
            lines.append(
                "\n[주의] 위 매수가는 등록 시점 가격이며 현재가가 아님. "
                "현재가는 [실시간 데이터] 섹션 참고."
            )
            return "\n".join(lines)

        # 2순위: 스크린샷 기반
        screenshots = db.get_latest_screenshot()
        if not screenshots:
            return "보유 종목 정보 없음"
        holdings = screenshots.get("holdings_json", "")
        if not holdings:
            return "보유 종목 정보 없음"
        import json
        items = json.loads(holdings) if isinstance(holdings, str) else holdings
        lines = []
        for h in items:
            name = h.get("name", "")
            avg = h.get("avg_price", 0)
            qty = h.get("quantity", 0)
            lines.append(
                f"- {name}: 매수가 {avg:,.0f}원, {qty}주"
            )
        return "\n".join(lines) if lines else "보유 종목 정보 없음"
    except Exception as e:
        logger.warning("Failed to get portfolio context: %s", e)
        return "보유 종목 정보 없음"


def get_market_context(macro_snapshot: dict | None = None) -> str:
    """Format market data context from a macro snapshot dict.

    [v3.6.6] 유동성 방향 감지 지표 추가:
    - 장단기 금리차 (10Y-2Y), 유동성 방향 신호

    Args:
        macro_snapshot: Dict with keys from MacroClient snapshot.

    Returns:
        Multi-line string of market data, or fallback message.
    """
    if not macro_snapshot:
        return "시장 데이터 없음"
    lines: list[str] = []
    # Support both old-style keys and new MacroClient keys
    sp500 = macro_snapshot.get("sp500", macro_snapshot.get("spx_change_pct"))
    nasdaq = macro_snapshot.get("nasdaq", macro_snapshot.get("nasdaq_change_pct"))
    vix = macro_snapshot.get("vix")
    usdkrw = macro_snapshot.get("usdkrw")
    btc = macro_snapshot.get("btc_price")
    gold = macro_snapshot.get("gold_price")
    us10y = macro_snapshot.get("us10y")
    us2y = macro_snapshot.get("us2y")
    dxy = macro_snapshot.get("dxy")
    fg = macro_snapshot.get("fear_greed")
    kospi = macro_snapshot.get("kospi")
    kospi_chg = macro_snapshot.get("kospi_change_pct")
    kosdaq = macro_snapshot.get("kosdaq")
    kosdaq_chg = macro_snapshot.get("kosdaq_change_pct")

    # 한국 시장 지수 (최상단 배치)
    if kospi is not None and kospi > 0:
        chg_str = f" ({kospi_chg:+.2f}%)" if kospi_chg is not None else ""
        lines.append(f"코스피: {kospi:,.2f}{chg_str}")
    if kosdaq is not None and kosdaq > 0:
        chg_str = f" ({kosdaq_chg:+.2f}%)" if kosdaq_chg is not None else ""
        lines.append(f"코스닥: {kosdaq:,.2f}{chg_str}")
    if sp500 is not None:
        lines.append(f"S&P500: {sp500:+.2f}%")
    if nasdaq is not None:
        lines.append(f"나스닥: {nasdaq:+.2f}%")
    if vix is not None:
        status = "안정" if vix < 20 else "주의" if vix < 25 else "공포"
        lines.append(f"VIX: {vix:.1f} ({status})")
    if usdkrw is not None and usdkrw > 0:
        lines.append(f"원/달러: {usdkrw:,.0f}원")
    if btc is not None and btc > 0:
        lines.append(f"BTC: ${btc:,.0f}")
    if gold is not None and gold > 0:
        lines.append(f"금: ${gold:,.0f}")
    if us10y is not None and us10y > 0:
        lines.append(f"미국 10년물: {us10y:.2f}%")
    if dxy is not None and dxy > 0:
        lines.append(f"달러인덱스: {dxy:.1f}")
    if fg is not None:
        label = "극도공포" if fg < 25 else "공포" if fg < 45 else "중립" if fg < 55 else "탐욕" if fg < 75 else "극도탐욕"
        lines.append(f"공포탐욕지수: {fg:.0f}점 ({label})")

    # [v9.0] 미국 선물지수 (장 마감 후에도 방향성 파악 가능)
    es = macro_snapshot.get("es_futures")
    es_chg = macro_snapshot.get("es_futures_change_pct")
    nq = macro_snapshot.get("nq_futures")
    nq_chg = macro_snapshot.get("nq_futures_change_pct")

    futures_lines = []
    if es is not None and es > 0:
        futures_lines.append(f"S&P500 선물(ES): {es:,.0f} ({es_chg:+.2f}%)")
    if nq is not None and nq > 0:
        futures_lines.append(f"나스닥100 선물(NQ): {nq:,.0f} ({nq_chg:+.2f}%)")
    if futures_lines:
        lines.append("--- 미국 선물 (실시간 방향성) ---")
        lines.extend(futures_lines)

    # [v6.6] 미국 레버리지 ETF 시그널
    koru = macro_snapshot.get("koru_price")
    koru_chg = macro_snapshot.get("koru_change_pct")
    soxl = macro_snapshot.get("soxl_price")
    soxl_chg = macro_snapshot.get("soxl_change_pct")
    tqqq = macro_snapshot.get("tqqq_price")
    tqqq_chg = macro_snapshot.get("tqqq_change_pct")

    etf_lines = []
    if koru is not None and koru > 0:
        etf_lines.append(f"KORU(한국3x): ${koru:.2f} ({koru_chg:+.2f}%)")
    if soxl is not None and soxl > 0:
        etf_lines.append(f"SOXL(반도체3x): ${soxl:.2f} ({soxl_chg:+.2f}%)")
    if tqqq is not None and tqqq > 0:
        etf_lines.append(f"TQQQ(나스닥3x): ${tqqq:.2f} ({tqqq_chg:+.2f}%)")
    if etf_lines:
        lines.append("--- 미국 레버리지 ETF ---")
        lines.extend(etf_lines)

    # [v3.6.6] 유동성 방향 감지: 장단기 금리차
    if us10y is not None and us2y is not None and us10y > 0 and us2y > 0:
        spread = us10y - us2y
        if spread < 0:
            spread_signal = "역전 (경기침체 경고)"
        elif spread < 0.5:
            spread_signal = "축소 (긴축적)"
        elif spread < 1.5:
            spread_signal = "정상"
        else:
            spread_signal = "확대 (완화적)"
        lines.append(f"장단기 금리차(10Y-2Y): {spread:+.2f}%p ({spread_signal})")

    # [v9.0] 프로그램 매매
    prog_data = macro_snapshot.get("program_trading")
    if prog_data:
        total_net = prog_data.get("total_net", 0)
        arb_net = prog_data.get("arb_net", 0)
        non_arb_net = prog_data.get("non_arb_net", 0)
        lines.append(
            f"프로그램매매: 전체 {total_net:+,.0f}억 "
            f"(차익 {arb_net:+,.0f} / 비차익 {non_arb_net:+,.0f})"
        )

    # [v9.0] 신용잔고
    credit_data = macro_snapshot.get("credit_balance")
    if credit_data:
        credit_tril = credit_data.get("credit", 0) / 10000
        credit_chg = credit_data.get("credit_change", 0)
        deposit_tril = credit_data.get("deposit", 0) / 10000
        lines.append(
            f"신용잔고: {credit_tril:.1f}조({credit_chg:+,.0f}억) "
            f"예탁금: {deposit_tril:.1f}조"
        )

    # [v9.0] ETF 자금흐름
    etf_data = macro_snapshot.get("etf_flow")
    if etf_data:
        lev_cap = etf_data.get("leverage_total", 0)
        inv_cap = etf_data.get("inverse_total", 0)
        if lev_cap > 0 or inv_cap > 0:
            lines.append(
                f"ETF흐름: 레버리지={lev_cap/10000:.1f}조 "
                f"인버스={inv_cap/10000:.1f}조"
            )

    # [v10.2] 유가 (WTI, Brent)
    wti = macro_snapshot.get("wti_price")
    wti_chg = macro_snapshot.get("wti_change_pct")
    brent = macro_snapshot.get("brent_price")
    brent_chg = macro_snapshot.get("brent_change_pct")
    if wti is not None and wti > 0:
        lines.append(f"WTI: ${wti:.2f} ({wti_chg:+.2f}%)")
    if brent is not None and brent > 0:
        lines.append(f"Brent: ${brent:.2f} ({brent_chg:+.2f}%)")
    ng = macro_snapshot.get("natural_gas_price")
    ng_chg = macro_snapshot.get("natural_gas_change_pct")
    if ng is not None and ng > 0:
        lines.append(f"천연가스: ${ng:.3f} ({ng_chg:+.2f}%)")

    # [v10.2] 유가 분석 컨텍스트 (DB에서 조회)
    oil_ctx = macro_snapshot.get("oil_analysis_context")
    if oil_ctx:
        lines.append(f"--- 유가 분석 ---")
        lines.append(oil_ctx)

    # [v9.0] 선물만기 경고
    expiry_warn = get_futures_expiry_warning()
    if expiry_warn:
        lines.append(expiry_warn)

    # [v9.0] 변동성 레짐
    kr_vol = macro_snapshot.get("korean_vol")
    vol_regime = macro_snapshot.get("vol_regime")
    if vol_regime and vix is not None:
        regime_labels = {
            "low": "저변동 (공격적 포지션 가능, 돌파 전략 유효)",
            "normal": "보통 (기본 전략, 분할 매수)",
            "high": "고변동 (포지션 축소, 넓은 손절)",
            "extreme": "극단 (신규 매수 중단, 역발상 탐색)",
        }
        kr_vol_str = f", 한국Vol={kr_vol:.1f}%" if kr_vol else ""
        lines.append(
            f"변동성 레짐: {regime_labels.get(vol_regime, vol_regime)}"
            f" (VIX={vix:.1f}{kr_vol_str})"
        )

    return "\n".join(lines) if lines else "시장 데이터 없음"


def get_recommendation_context(db, limit: int = 5) -> str:
    """Get recent recommendations context from DB.

    Fetches active recommendations and formats each one with
    stock name, recommended price, current PnL, and date.

    Args:
        db: SQLiteStore instance with get_active_recommendations() method.
        limit: Maximum number of recommendations to include.

    Returns:
        Multi-line string of recommendations, or fallback message.
    """
    try:
        recs = db.get_active_recommendations()
        if not recs:
            return "최근 추천 없음"
        lines: list[str] = []
        for r in recs[:limit]:
            name = r.get("name", "")
            price = r.get("rec_price", 0)
            date = r.get("rec_date", "")
            lines.append(
                f"- {name}: 추천 당시 가격 {price:,.0f}원 ({date})"
            )
        lines.append(
            "\n[주의] 위 가격은 추천 당시 가격이며 현재가가 아님."
        )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get recommendation context: %s", e)
        return "최근 추천 없음"


def get_policy_context(config: dict | None = None) -> str:
    """Get active policy events context.

    Loads policy events from the policy engine and formats each one
    with name and truncated description.

    Args:
        config: Optional policy configuration dict. Passed through
                to get_active_events().

    Returns:
        Multi-line string of policy events, or fallback message.
    """
    try:
        from kstock.signal.policy_engine import get_active_events
        events = get_active_events(config=config)
        if not events:
            return "활성 정책 없음"
        lines: list[str] = []
        for ev in events:
            lines.append(
                f"- {ev.get('name', '')}: {ev.get('description', '')[:50]}"
            )
        return "\n".join(lines)
    except ImportError:
        logger.debug("policy_engine not available for context")
        return "활성 정책 없음"
    except Exception as e:
        logger.warning("Failed to get policy context: %s", e)
        return "활성 정책 없음"


def get_report_context(db, limit: int = 3) -> str:
    """Get recent broker reports context from DB.

    Args:
        db: SQLiteStore instance with get_recent_reports() method.
        limit: Maximum number of reports to include.

    Returns:
        Multi-line string of reports, or fallback message.
    """
    try:
        reports = db.get_recent_reports(limit=limit)
        if not reports:
            return "최근 리포트 없음"
        lines: list[str] = []
        for r in reports:
            lines.append(
                f"- [{r.get('broker', '')}] "
                f"{r.get('title', '')} ({r.get('date', '')})"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get report context: %s", e)
        return "최근 리포트 없음"


def get_financial_context(db) -> str:
    """Get financial summary for portfolio holdings from DB.

    [v3.6.6] 붕괴 리스크 지표 추가:
    - 영업현금흐름, 이자보상배율, 단기차입금비율

    Args:
        db: SQLiteStore instance with get_active_holdings() and
            get_financials() methods.

    Returns:
        Financial summary string with collapse risk indicators.
    """
    try:
        holdings = db.get_active_holdings()
        if not holdings:
            return "보유 종목 재무 데이터 없음"
        lines: list[str] = []
        for h in holdings[:5]:
            ticker = h.get("ticker", "")
            name = h.get("name", ticker)
            fin = db.get_financials(ticker)
            if fin:
                per = fin.get("per", 0)
                pbr = fin.get("pbr", 0)
                roe = fin.get("roe", 0)
                debt = fin.get("debt_ratio", 0)
                # 기본 지표
                line = (
                    f"- {name}: PER {per:.1f}, PBR {pbr:.2f}, "
                    f"ROE {roe:.1f}%, 부채비율 {debt:.0f}%"
                )
                # [v3.6.6] 붕괴 리스크 지표 (DB에 있으면 표시)
                risk_flags: list[str] = []
                ocf = fin.get("operating_cash_flow")
                icr = fin.get("interest_coverage_ratio")
                short_debt = fin.get("short_term_debt_ratio")
                op_margin_trend = fin.get("op_margin_trend")  # 3분기 추세

                if ocf is not None and ocf < 0:
                    risk_flags.append("⚠영업CF적자")
                if icr is not None and icr < 1.5:
                    risk_flags.append(f"⚠이자보상{icr:.1f}x")
                if short_debt is not None and short_debt > 30:
                    risk_flags.append(f"⚠단기차입{short_debt:.0f}%")
                if debt > 200:
                    risk_flags.append("⚠고부채")
                if op_margin_trend is not None and op_margin_trend < 0:
                    risk_flags.append("⚠영업이익↓")

                if risk_flags:
                    line += f" | 리스크: {', '.join(risk_flags)}"
                lines.append(line)
            else:
                lines.append(f"- {name}: 재무 데이터 미수집")
        return "\n".join(lines) if lines else "보유 종목 재무 데이터 없음"
    except Exception as e:
        logger.warning("Failed to get financial context: %s", e)
        return "재무 데이터 조회 실패"


async def build_full_context_with_macro(db, macro_client=None, yf_client=None) -> dict:
    """Build context with live macro data from MacroClient (async).

    This is the preferred method - fetches real-time market data
    from the 3-tier cache (memory -> SQLite -> yfinance).

    Args:
        db: SQLiteStore instance for data access.
        macro_client: MacroClient instance for live market data.
        yf_client: YFinanceKRClient instance for real-time stock prices.

    Returns:
        Dict with all context sections populated with live data.
    """
    # Fetch macro snapshot from cache (instant if cached)
    macro_dict = None
    if macro_client:
        try:
            snap = await macro_client.get_snapshot()
            macro_dict = {
                "sp500": getattr(snap, "spx_change_pct", 0),
                "nasdaq": getattr(snap, "nasdaq_change_pct", 0),
                "vix": getattr(snap, "vix", 0),
                "usdkrw": getattr(snap, "usdkrw", 0),
                "btc_price": getattr(snap, "btc_price", 0),
                "gold_price": getattr(snap, "gold_price", 0),
                "us10y": getattr(snap, "us10y", 0),
                "us2y": getattr(snap, "us2y", 0),  # [v3.6.6] 유동성 감지
                "dxy": getattr(snap, "dxy", 0),
                "fear_greed": getattr(snap, "fear_greed_score", 50),
                # v6.1.3: 한국 시장 지수
                "kospi": getattr(snap, "kospi", 0),
                "kospi_change_pct": getattr(snap, "kospi_change_pct", 0),
                "kosdaq": getattr(snap, "kosdaq", 0),
                "kosdaq_change_pct": getattr(snap, "kosdaq_change_pct", 0),
                # v6.6: 미국 레버리지 ETF
                "koru_price": getattr(snap, "koru_price", 0),
                "koru_change_pct": getattr(snap, "koru_change_pct", 0),
                "soxl_price": getattr(snap, "soxl_price", 0),
                "soxl_change_pct": getattr(snap, "soxl_change_pct", 0),
                "tqqq_price": getattr(snap, "tqqq_price", 0),
                "tqqq_change_pct": getattr(snap, "tqqq_change_pct", 0),
                # v9.0: 선물지수
                "es_futures": getattr(snap, "es_futures", 0),
                "es_futures_change_pct": getattr(snap, "es_futures_change_pct", 0),
                "nq_futures": getattr(snap, "nq_futures", 0),
                "nq_futures_change_pct": getattr(snap, "nq_futures_change_pct", 0),
                # v9.0: 변동성 레짐
                "korean_vol": getattr(snap, "korean_vol", 0),
                "vol_regime": getattr(snap, "vol_regime", ""),
                # v10.2: 유가/원자재
                "wti_price": getattr(snap, "wti_price", 0),
                "wti_change_pct": getattr(snap, "wti_change_pct", 0),
                "brent_price": getattr(snap, "brent_price", 0),
                "brent_change_pct": getattr(snap, "brent_change_pct", 0),
                "natural_gas_price": getattr(snap, "natural_gas_price", 0),
                "natural_gas_change_pct": getattr(snap, "natural_gas_change_pct", 0),
            }
            # v9.0: 프로그램 매매 데이터 추가
            try:
                prog_data = db.get_program_trading(days=1, market="KOSPI")
                if prog_data:
                    macro_dict["program_trading"] = prog_data[0]
            except Exception:
                logger.debug("program_trading context failed", exc_info=True)
            # v9.0: 신용잔고 데이터 추가
            try:
                cred_data = db.get_credit_balance(days=1)
                if cred_data:
                    macro_dict["credit_balance"] = cred_data[0]
            except Exception:
                logger.debug("credit_balance context failed", exc_info=True)
            # v9.0: ETF 자금흐름 데이터 추가
            try:
                etf_data = db.get_etf_flow(days=1)
                if etf_data:
                    lev_total = sum(d["market_cap"] for d in etf_data if d.get("etf_type") == "leverage")
                    inv_total = sum(d["market_cap"] for d in etf_data if d.get("etf_type") == "inverse")
                    macro_dict["etf_flow"] = {
                        "leverage_total": lev_total,
                        "inverse_total": inv_total,
                    }
            except Exception:
                logger.debug("etf_flow context failed", exc_info=True)
            # v10.2: 유가 분석 컨텍스트 (DB에서 최신 분석 결과 조회)
            try:
                oil_rows = db.get_oil_analysis(days=1)
                if oil_rows:
                    oil = oil_rows[0]
                    regime = oil.get("regime", "neutral")
                    regime_kr = {"bull": "상승", "bear": "하락", "neutral": "횡보", "spike": "급등", "crash": "급락"}
                    oil_lines = [
                        f"유가 레짐: {regime_kr.get(regime, regime)} (강도 {oil.get('regime_strength', 0):.0%})",
                        f"변동성(20일): {oil.get('wti_volatility_20d', 0):.1f}%",
                        f"52주 위치: {oil.get('wti_position_52w', 0):.0%}",
                    ]
                    geo = oil.get("geopolitical_risk", "낮음")
                    if geo != "낮음":
                        oil_lines.append(f"지정학 리스크: {geo}")
                    import json as _json
                    try:
                        sigs = _json.loads(oil.get("signals_json", "[]"))
                        for sig in sigs[:2]:
                            oil_lines.append(f"시그널: {sig.get('description', '')}")
                    except Exception:
                        pass
                    macro_dict["oil_analysis_context"] = "\n".join(oil_lines)
            except Exception:
                logger.debug("oil_analysis context failed", exc_info=True)
        except Exception as e:
            logger.warning("Failed to get macro for AI context: %s", e)

    loop = asyncio.get_event_loop()
    (
        portfolio, market, recommendations, policies, reports, financials,
        investor_style, portfolio_solutions, trade_lessons_text,
        global_news_text, crisis_context,
    ) = await asyncio.gather(
        loop.run_in_executor(None, get_portfolio_context, db),
        loop.run_in_executor(None, get_market_context, macro_dict),
        loop.run_in_executor(None, get_recommendation_context, db),
        loop.run_in_executor(None, get_policy_context, None),
        loop.run_in_executor(None, get_report_context, db),
        loop.run_in_executor(None, get_financial_context, db),
        loop.run_in_executor(None, _get_investor_style_context, db),
        loop.run_in_executor(None, _get_portfolio_solutions_context, db),
        loop.run_in_executor(None, _get_trade_lessons_context, db),
        loop.run_in_executor(None, _get_global_news_context, db),
        loop.run_in_executor(None, _get_crisis_context, macro_dict),
        return_exceptions=True,
    )
    # v9.6.3: 개별 컨텍스트 실패 시 빈 문자열
    _ctx_names = ["portfolio", "market", "recommendations", "policies", "reports",
                  "financials", "investor_style", "portfolio_solutions",
                  "trade_lessons_text", "global_news_text", "crisis_context"]
    _ctx_results = [portfolio, market, recommendations, policies, reports, financials,
                    investor_style, portfolio_solutions, trade_lessons_text,
                    global_news_text, crisis_context]
    for _i, (_name, _val) in enumerate(zip(_ctx_names, _ctx_results)):
        if isinstance(_val, Exception):
            logger.warning("Context %s failed: %s", _name, _val)
    portfolio = "" if isinstance(portfolio, Exception) else portfolio
    market = "" if isinstance(market, Exception) else market
    recommendations = "" if isinstance(recommendations, Exception) else recommendations
    policies = "" if isinstance(policies, Exception) else policies
    reports = "" if isinstance(reports, Exception) else reports
    financials = "" if isinstance(financials, Exception) else financials
    investor_style = "" if isinstance(investor_style, Exception) else investor_style
    portfolio_solutions = "" if isinstance(portfolio_solutions, Exception) else portfolio_solutions
    trade_lessons_text = "" if isinstance(trade_lessons_text, Exception) else trade_lessons_text
    global_news_text = "" if isinstance(global_news_text, Exception) else global_news_text
    crisis_context = "" if isinstance(crisis_context, Exception) else crisis_context

    # 실시간 주가 데이터 주입 (yf_client가 있으면)
    realtime_data = ""
    if yf_client:
        try:
            realtime_data = await _get_realtime_portfolio_data(db, yf_client)
        except Exception as e:
            logger.warning("Failed to get realtime portfolio data: %s", e)

    # v9.3: 섹터 동향 주입
    try:
        from kstock.ingest.naver_finance import (
            get_sector_rankings, analyze_sector_momentum,
        )
        sectors = await get_sector_rankings(limit=10)
        if sectors:
            sec_analysis = analyze_sector_momentum(sectors)
            sector_ctx = sec_analysis.get("summary", "")
            if sector_ctx:
                market = market + "\n\n[업종별 동향]\n" + sector_ctx
    except Exception:
        logger.debug("Sector context injection failed", exc_info=True)

    # portfolio에 실시간 데이터 추가
    if realtime_data:
        portfolio = portfolio + "\n\n[실시간 기술지표]\n" + realtime_data

    # v9.0: 산업 생태계 컨텍스트 (보유종목별)
    try:
        from kstock.signal.industry_ecosystem import get_industry_context
        holdings = db.get_active_holdings()
        industry_lines = []
        for h in (holdings or [])[:5]:
            ticker = h.get("ticker", "")
            ctx = get_industry_context(ticker)
            if ctx:
                industry_lines.append(ctx)
        if industry_lines:
            portfolio = portfolio + "\n\n" + "\n".join(industry_lines)
    except Exception:
        pass

    # v9.3: 보유종목별 수급 데이터 컨텍스트
    try:
        holdings = holdings if 'holdings' in dir() else db.get_active_holdings()
        supply_lines = []
        for h in (holdings or [])[:5]:
            ticker = h.get("ticker", "")
            sd = db.get_supply_demand(ticker, days=5)
            if sd:
                latest = sd[0]
                f_net = latest.get("foreign_net", 0)
                i_net = latest.get("institution_net", 0)
                supply_lines.append(
                    f"- {h.get('name', ticker)}: "
                    f"외국인 {f_net:+,.0f} / 기관 {i_net:+,.0f}"
                )
        if supply_lines:
            portfolio = (
                portfolio + "\n\n[보유종목 수급현황]\n"
                + "\n".join(supply_lines)
            )
    except Exception:
        logger.debug("Supply demand context failed", exc_info=True)

    # v9.4: AI 토론 합의 컨텍스트 추가
    try:
        holdings_for_debate = db.get_active_holdings()
        debate_lines = []
        for h in (holdings_for_debate or [])[:10]:
            ticker = h.get("ticker", "")
            d = db.get_latest_debate(ticker)
            if d:
                v = d.get("verdict", "")
                cons = d.get("consensus_level", "")
                conf = d.get("confidence", 0)
                pt = d.get("price_target", 0)
                args = d.get("key_arguments", [])
                arg_text = args[0] if args else ""
                line = f"- {h.get('name', ticker)}: {v}({cons} {conf:.0f}%)"
                if pt and pt > 0:
                    line += f", 목표 {pt:,.0f}원"
                if arg_text:
                    line += f", 핵심: {arg_text[:40]}"
                debate_lines.append(line)
        if debate_lines:
            portfolio = (
                portfolio + "\n\n[AI 토론 합의]\n"
                + "\n".join(debate_lines)
            )
    except Exception:
        logger.debug("AI debate context injection failed", exc_info=True)

    # v9.0: 한국형 리스크 팩터 → 시장 컨텍스트에 추가
    try:
        from kstock.signal.korea_risk import assess_korea_risk, format_korea_risk
        kr_args = {}
        if macro_dict:
            kr_args["vix"] = macro_dict.get("vix", 0)
            kr_args["usdkrw"] = macro_dict.get("usdkrw", 0)
            # 환율 변동률은 snap에서
            if macro_client:
                snap_obj = getattr(macro_client, '_last_snapshot', None)
                if snap_obj:
                    kr_args["usdkrw_change_pct"] = getattr(snap_obj, "usdkrw_change_pct", 0)
        # 신용잔고
        try:
            cred = db.get_credit_balance(days=1)
            if cred:
                kr_args["credit_data"] = cred
        except Exception:
            pass
        # ETF
        try:
            etf = db.get_etf_flow(days=1)
            if etf:
                kr_args["etf_data"] = etf
        except Exception:
            pass
        # 프로그램매매
        try:
            prog = db.get_program_trading(days=1, market="KOSPI")
            if prog:
                kr_args["program_data"] = prog
        except Exception:
            pass
        # 만기일
        from kstock.bot.context_builder import get_futures_expiry_warning
        from calendar import monthcalendar
        from datetime import datetime
        now_k = datetime.now(KST)
        cal = monthcalendar(now_k.year, now_k.month)
        thursdays = [week[3] for week in cal if week[3] != 0]
        if len(thursdays) >= 2:
            expiry_day = thursdays[1]
            days_until = (datetime(now_k.year, now_k.month, expiry_day, tzinfo=KST).date() - now_k.date()).days
            if 0 <= days_until <= 5:
                kr_args["days_to_expiry"] = days_until
        kr_args["month"] = now_k.month
        kr_args["day"] = now_k.day
        assessment = assess_korea_risk(**kr_args)
        if assessment.total_risk > 0:
            risk_text = format_korea_risk(assessment)
            market = market + "\n\n" + risk_text
    except Exception:
        logger.debug("Korea risk assessment for context failed", exc_info=True)

    # v9.5: 매니저 stance + 멀티에이전트 + 통합 상태 주입
    manager_stances_text = ""
    try:
        stances = db.get_recent_manager_stances(hours=24)
        if stances:
            manager_names = {
                "scalp": "리버모어(단타)", "swing": "오닐(스윙)",
                "position": "린치(중기)", "long_term": "버핏(장기)",
            }
            s_lines = ["[매니저 투자 의견]"]
            for key in ("scalp", "swing", "position", "long_term"):
                s = stances.get(key, "")
                if s:
                    s_lines.append(f"- {manager_names.get(key, key)}: {s[:80]}")
            if len(s_lines) > 1:
                manager_stances_text = "\n".join(s_lines)
    except Exception:
        logger.debug("Manager stances context failed", exc_info=True)

    multi_agent_text = ""
    try:
        holdings_ma = db.get_active_holdings()
        if holdings_ma:
            ma_lines = ["[멀티에이전트 분석]"]
            for h in holdings_ma[:10]:
                ticker = h.get("ticker", "")
                results = db.get_multi_agent_results(ticker=ticker, limit=1)
                if results:
                    r = results[0]
                    cs = r.get("combined_score", 0)
                    v = r.get("verdict", "")
                    ma_lines.append(
                        f"- {h.get('name', ticker)}: "
                        f"점수 {cs}/215, {v}"
                    )
            if len(ma_lines) > 1:
                multi_agent_text = "\n".join(ma_lines)
    except Exception:
        logger.debug("Multi-agent context failed", exc_info=True)

    # v9.5.1: 최근 브리핑 (AI 채팅이 자기가 보낸 내용을 알 수 있도록)
    recent_briefing_text = ""
    try:
        briefings = db.get_recent_briefings(hours=18, limit=2)
        if briefings:
            b_lines = []
            for b in briefings:
                b_type = b.get("briefing_type", "")
                b_time = b.get("created_at", "")[:16]
                content = b.get("content", "")[:1500]
                label = {"premarket": "🇺🇸 프리마켓", "morning": "☀️ 모닝"}.get(
                    b_type, b_type
                )
                b_lines.append(f"[{label} {b_time}]\n{content}")
            recent_briefing_text = "\n\n".join(b_lines)
    except Exception:
        logger.debug("Recent briefing context failed", exc_info=True)

    # v9.5.3: 학습 엔진 — 매니저 성적표 + 매매 프로필 + 이벤트 조정
    learning_context = ""
    try:
        from kstock.bot.learning_engine import (
            get_user_trade_profile,
            get_active_event_adjustments,
        )
        # 매매 프로필
        profile = get_user_trade_profile(db)
        if profile:
            wr = profile.get("win_rate", 0)
            avg = profile.get("avg_pnl", 0) * 100
            learning_context += f"[주호님 매매 프로필] 승률 {wr:.0f}%, 평균수익 {avg:+.1f}%\n"

        # 활성 이벤트 조정
        events = get_active_event_adjustments(db)
        if events:
            ev_lines = ["[활성 이벤트 점수 조정]"]
            for ev in events[:3]:
                adj = ev.get("score_adjustment", 0)
                sectors = ", ".join(ev.get("affected_sectors", [])[:3])
                ev_lines.append(
                    f"- {ev.get('event_summary', '')[:50]}: "
                    f"{'%+d' % adj}점 ({sectors})"
                )
            learning_context += "\n".join(ev_lines)

        # 매니저 성적표
        try:
            with db._connect() as conn:
                mgr_rows = conn.execute(
                    "SELECT manager_key, hit_rate, weight_adj "
                    "FROM manager_scorecard "
                    "ORDER BY calculated_at DESC LIMIT 4",
                ).fetchall()
            if mgr_rows:
                mgr_names = {
                    "scalp": "리버모어", "swing": "오닐",
                    "position": "린치", "long_term": "버핏",
                }
                sc_lines = ["\n[매니저 성적표]"]
                for r in mgr_rows:
                    name = mgr_names.get(r["manager_key"], r["manager_key"])
                    sc_lines.append(
                        f"- {name}: 적중률 {r['hit_rate']:.0f}%, "
                        f"가중치 {r['weight_adj']:.2f}x"
                    )
                learning_context += "\n".join(sc_lines)
        except Exception:
            pass
    except Exception:
        logger.debug("Learning context injection failed", exc_info=True)

    # v9.5.4: 섹터 딥다이브 인텔리전스 주입
    sector_dive_text = ""
    try:
        from kstock.bot.sector_intelligence import format_deep_dive_for_context
        deep_dives = db.get_all_recent_deep_dives(hours=48)
        if deep_dives:
            sd_lines = []
            for dd in deep_dives[:3]:
                ctx = format_deep_dive_for_context(dd)
                if ctx:
                    sd_lines.append(ctx)
            if sd_lines:
                sector_dive_text = "\n\n".join(sd_lines)
    except Exception:
        logger.debug("Sector deep dive context injection failed", exc_info=True)

    return {
        "portfolio": portfolio,
        "market": market,
        "recommendations": recommendations,
        "policies": policies,
        "reports": reports,
        "financials": financials,
        "investor_style": investor_style,
        "portfolio_with_solutions": portfolio_solutions,
        "trade_lessons": trade_lessons_text,
        "global_news": global_news_text,
        "crisis_context": crisis_context,
        "recent_briefing": recent_briefing_text,
        "manager_stances": manager_stances_text,
        "multi_agent_scores": multi_agent_text,
        "learning_context": learning_context,
        "sector_intelligence": sector_dive_text,
    }


async def _get_realtime_portfolio_data(db, yf_client) -> str:
    """보유종목의 실시간 가격 + 기술지표를 yfinance에서 조회."""
    holdings = db.get_active_holdings()
    if not holdings:
        return ""

    lines: list[str] = []
    for h in holdings[:5]:  # 최대 5종목
        ticker = h.get("ticker", "")
        name = h.get("name", ticker)
        if not ticker:
            continue
        try:
            ohlcv = await yf_client.get_ohlcv(ticker, h.get("market", "KOSPI"))
            if ohlcv is None or ohlcv.empty:
                continue
            from kstock.features.technical import compute_indicators
            tech = compute_indicators(ohlcv)
            close = ohlcv["close"].astype(float)
            cur = float(close.iloc[-1])
            lines.append(
                f"- {name}: {cur:,.0f}원 "
                f"| RSI {tech.rsi:.0f} "
                f"| MACD {tech.macd:+.0f} "
                f"| 5일선 {tech.ma5:,.0f} / 20일선 {tech.ma20:,.0f} / 60일선 {tech.ma60:,.0f}"
            )
        except Exception as e:
            logger.debug("Realtime data for %s failed: %s", ticker, e)
            continue
    return "\n".join(lines)


def _get_investor_style_context(db) -> str:
    """투자 성향 컨텍스트 문자열 생성."""
    try:
        from kstock.core.investor_profile import analyze_investor_style, STYLE_LABELS, RISK_LABELS
        insight = analyze_investor_style(db)
        if insight.trade_count == 0:
            return "아직 매매 이력이 부족하여 성향 분석 불가. 기본 '균형형' 전략으로 조언."
        lines = [
            f"스타일: {insight.style_label} (최근 {insight.trade_count}건 분석)",
            f"리스크: {insight.risk_label}",
            f"승률: {insight.win_rate:.0f}%, 평균보유: {insight.avg_hold_days:.0f}일",
            f"평균수익: {insight.avg_profit_pct:+.1f}%, 평균손실: {insight.avg_loss_pct:-.1f}%",
        ]
        if insight.weaknesses:
            lines.append(f"개선점: {', '.join(insight.weaknesses)}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get investor style: %s", e)
        return "투자 성향 데이터 없음"


def _get_portfolio_solutions_context(db) -> str:
    """보유종목 + 보유기간별 솔루션 컨텍스트."""
    try:
        from kstock.core.investor_profile import build_holdings_context_with_solutions
        return build_holdings_context_with_solutions(db)
    except Exception as e:
        logger.warning("Failed to get portfolio solutions: %s", e)
        return "보유 종목 솔루션 데이터 없음"


def _get_trade_lessons_context(db) -> str:
    """매매 교훈 컨텍스트."""
    try:
        lessons = db.get_trade_lessons(limit=5)
        if not lessons:
            return "아직 기록된 매매 교훈 없음"
        lines: list[str] = []
        for l in lessons:
            lines.append(
                f"- {l['name']} {l['action']}: {l['pnl_pct']:+.1f}% "
                f"({l['hold_days']}일) → {l.get('lesson', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get trade lessons: %s", e)
        return "매매 교훈 없음"


def _get_global_news_context(db) -> str:
    """v6.0: 글로벌 뉴스 컨텍스트 (DB에서 최근 뉴스 조회).

    v9.5: YouTube 인텔리전스 포함.
    """
    lines = []
    try:
        news = db.get_recent_global_news(limit=8, hours=12)
        if news:
            for item in news:
                urgency = "🚨" if item.get("is_urgent") else "📰"
                impact = item.get("impact_score", 0)
                impact_tag = f" [영향:{impact}/10]" if impact > 0 else ""
                lines.append(
                    f"{urgency} [{item.get('source', '')}] "
                    f"{item.get('title', '')}{impact_tag}"
                )
    except Exception as e:
        logger.warning("Failed to get global news context: %s", e)

    # v9.5: YouTube 방송 인사이트 추가
    try:
        yt_intel = db.get_recent_youtube_intelligence(hours=12, limit=5)
        if yt_intel:
            lines.append("\n[YouTube 방송 인사이트]")
            for yi in yt_intel:
                src = yi.get("source", "").replace("🎬", "").strip()
                summary = yi.get("full_summary", "")[:200]
                outlook = yi.get("market_outlook", "")
                tickers = yi.get("mentioned_tickers", [])
                lines.append(f"🎬 [{src}] {summary}")
                if outlook:
                    lines.append(f"   전망: {outlook}")
                if isinstance(tickers, list) and tickers:
                    ticker_str = ", ".join(
                        f"{t.get('name', '')}({t.get('sentiment', '')})"
                        for t in tickers[:5]
                    )
                    lines.append(f"   언급종목: {ticker_str}")
                impl = yi.get("investment_implications", "")
                if impl:
                    lines.append(f"   시사점: {impl[:100]}")
    except Exception:
        logger.debug("YouTube intelligence context failed", exc_info=True)

    return "\n".join(lines) if lines else "최근 수집된 글로벌 이슈 없음"


async def build_manager_shared_context(db, macro_client=None) -> dict:
    """매니저 공유 컨텍스트 빌더 — 4매니저가 동일한 상황 인식을 갖도록.

    Returns:
        dict with keys: investor_style, trade_lessons, global_news,
        policies, crisis_context, portfolio_summary, post_war_rotation
    """
    import yaml, os

    loop = asyncio.get_event_loop()

    # 매크로 snapshot (이미 있으면 재사용)
    macro_dict = None
    if macro_client:
        try:
            snap = await macro_client.get_snapshot()
            macro_dict = {
                "vix": getattr(snap, "vix", 0),
                "usdkrw": getattr(snap, "usdkrw", 0),
                "fear_greed": getattr(snap, "fear_greed_score", 50),
                "kospi": getattr(snap, "kospi", 0),
                "kospi_change_pct": getattr(snap, "kospi_change_pct", 0),
            }
        except Exception:
            logger.debug("manager_shared_context macro failed", exc_info=True)

    # 병렬로 각 섹션 수집
    investor_style, trade_lessons, global_news, policies, crisis_ctx = await asyncio.gather(
        loop.run_in_executor(None, _get_investor_style_context, db),
        loop.run_in_executor(None, _get_trade_lessons_context, db),
        loop.run_in_executor(None, _get_global_news_context, db),
        loop.run_in_executor(None, get_policy_context, None),
        loop.run_in_executor(None, _get_crisis_context, macro_dict),
        return_exceptions=True,
    )
    # v9.6.3: 실패 시 빈 문자열
    investor_style = "" if isinstance(investor_style, Exception) else investor_style
    trade_lessons = "" if isinstance(trade_lessons, Exception) else trade_lessons
    global_news = "" if isinstance(global_news, Exception) else global_news
    policies = "" if isinstance(policies, Exception) else policies
    crisis_ctx = "" if isinstance(crisis_ctx, Exception) else crisis_ctx

    # 포트폴리오 전체 요약 (모든 보유종목)
    portfolio_summary = ""
    try:
        holdings = db.get_active_holdings()
        if holdings:
            lines = []
            for h in holdings:
                ht = h.get("holding_type", "auto")
                type_label = {"scalp": "단타", "swing": "스윙", "position": "포지션", "long_term": "장기"}.get(ht, ht)
                lines.append(f"- {h.get('name', '')}({h.get('ticker', '')}): {type_label}, 매수 {h.get('buy_price', 0):,.0f}원, {h.get('quantity', 0)}주")
            portfolio_summary = "\n".join(lines)
    except Exception:
        pass

    # 전쟁 후 주도주 전환 시나리오
    post_war = ""
    try:
        crisis_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "config", "crisis_events.yaml"
        )
        if os.path.exists(crisis_path):
            with open(crisis_path, encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
            for crisis in cdata.get("active_crises", []):
                rotation = crisis.get("post_war_rotation")
                if rotation:
                    post_war = (
                        f"[전쟁 후 주도주 전환 시나리오]\n"
                        f"{rotation.get('description', '')}\n"
                        f"전환 신호: {', '.join(rotation.get('warning_signals', [])[:3])}\n"
                        f"전후 수혜: {', '.join(rotation.get('winners_post_war', [])[:4])}\n"
                        f"전후 피해: {', '.join(rotation.get('losers_post_war', [])[:3])}\n"
                        f"액션: {rotation.get('action_plan', '')[:200]}"
                    )
    except Exception:
        pass

    # v9.5: YouTube 인텔리전스 → 매니저 공유 컨텍스트
    youtube_intel = ""
    try:
        yt_data = db.get_recent_youtube_intelligence(hours=24, limit=5)
        if yt_data:
            yt_lines = ["[YouTube 방송 인사이트]"]
            for yi in yt_data:
                src = yi.get("source", "").replace("🎬", "").strip()
                outlook = yi.get("market_outlook", "")
                tickers = yi.get("mentioned_tickers", [])
                impl = yi.get("investment_implications", "")
                parts = [f"🎬 {src}"]
                if outlook:
                    parts.append(f"전망: {outlook}")
                if isinstance(tickers, list) and tickers:
                    parts.append(
                        "종목: " + ", ".join(
                            f"{t.get('name', '')}({t.get('sentiment', '')})"
                            for t in tickers[:4]
                        )
                    )
                if impl:
                    parts.append(f"시사점: {impl[:80]}")
                yt_lines.append(" | ".join(parts))
            youtube_intel = "\n".join(yt_lines)
    except Exception:
        logger.debug("YouTube intelligence for manager context failed", exc_info=True)

    return {
        "investor_style": investor_style,
        "trade_lessons": trade_lessons,
        "global_news": global_news,
        "policies": policies,
        "crisis_context": crisis_ctx,
        "portfolio_summary": portfolio_summary,
        "post_war_rotation": post_war,
        "youtube_intelligence": youtube_intel,
    }


def _get_crisis_context(macro_snapshot: dict | None = None) -> str:
    """v6.6: 전시/지정학적 위기 컨텍스트 생성.

    VIX, 원/달러, 유가 등 매크로 지표와 활성 위기 이벤트를 기반으로
    현재 위기 수준을 판단하고 섹터별 전략 가이드를 제공한다.
    """
    try:
        import yaml
        import os

        # 위기 이벤트 로드
        cal_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "config", "crisis_events.yaml"
        )
        crisis_events = []
        if os.path.exists(cal_path):
            with open(cal_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            crisis_events = data.get("active_crises", [])

        if not crisis_events:
            return "현재 특별 위기 상황 없음"

        lines = ["현재 활성 위기 상황:"]
        for ev in crisis_events:
            lines.append(
                f"⚠ {ev.get('name', '')}: {ev.get('description', '')}"
            )
            beneficiaries = ev.get("beneficiary_sectors", [])
            if beneficiaries:
                lines.append(f"  수혜 섹터: {', '.join(beneficiaries)}")
            damaged = ev.get("damaged_sectors", [])
            if damaged:
                lines.append(f"  피해 섹터: {', '.join(damaged)}")
            strategy = ev.get("strategy", "")
            if strategy:
                lines.append(f"  전략: {strategy}")

        # 매크로 기반 위기 수준 판단
        if macro_snapshot:
            vix = macro_snapshot.get("vix", 0)
            usdkrw = macro_snapshot.get("usdkrw", 0)
            fg = macro_snapshot.get("fear_greed", 50)

            risk_level = "보통"
            if vix >= 30 or fg < 25 or usdkrw >= 1450:
                risk_level = "심각"
            elif vix >= 25 or fg < 40 or usdkrw >= 1400:
                risk_level = "경계"
            elif vix >= 20 or fg < 50:
                risk_level = "주의"

            lines.append(f"\n시장 위기 수준: {risk_level}")
            if risk_level in ("심각", "경계"):
                lines.append("→ 현금 비중 확대, 방어적 포지션 유지")
                lines.append("→ 위기 수혜 섹터(방산/에너지) 선별 매수 검토")
            elif risk_level == "주의":
                lines.append("→ 신규 매수 신중, 기존 보유 홀딩 유지")

        lines.append(
            "\n[추매 가이드] 이미 급등한 방산/정유 종목 추매 질문 시:\n"
            "- 단기 +20% 이상 급등 종목: 조정 대기 후 분할매수 권유\n"
            "- 아직 덜 오른 2~3선 종목이나 ETF: 진입 검토 가능\n"
            "- 실적이 실제로 개선되는 종목 vs 테마만 타는 종목 구분 필수\n"
            "- 전쟁/위기가 장기화될 경우 vs 단기 이벤트일 경우 시나리오 분리"
        )

        return "\n".join(lines)
    except Exception as e:
        logger.warning("Failed to get crisis context: %s", e)
        return "위기 컨텍스트 조회 실패"
