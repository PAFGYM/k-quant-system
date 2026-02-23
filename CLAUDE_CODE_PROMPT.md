# K-Quant v3.5 Phase 10 개선 프롬프트

프로젝트 경로: /Users/juhodang/k-quant-system
PYTHONPATH=src 으로 설정 후 작업

---

## 작업 1: 자연어 종목 인식 (슬래시 명령 제거)

현재 문제:
- /finance 005930, /multi 삼성전자, /consensus 에코프로 등 슬래시+종목코드 입력이 필요함
- 사용자 불편. 그냥 "삼성전자 분석해줘", "에코프로 어떻게 보여?" 같은 자연어가 되어야 함

수정 파일: src/kstock/bot/bot.py

현재 흐름 (handle_menu_text 메서드, 약 line 780):
1. 메뉴 버튼 텍스트 매칭 → 해당 핸들러 실행
2. 매칭 안 되면 → _handle_ai_question()으로 전달

변경할 흐름:
1. 메뉴 버튼 텍스트 매칭 → 해당 핸들러 실행
2. 매칭 안 되면 → 종목명/종목코드 감지 시도
3. 종목 감지 성공 → 자동으로 해당 종목 분석 실행 (AI 질문에 시장 데이터 + 종목 데이터 포함)
4. 종목 감지 실패 → 일반 AI 질문으로 처리

종목 감지 방법:
- self.all_tickers (bot.py line 324)에 모든 종목 코드/이름 리스트가 있음
- 사용자 텍스트에서 종목명이 포함되어 있는지 확인 (예: "삼성전자 분석해줘" → "삼성전자" 매칭)
- 6자리 숫자 패턴도 감지 (예: "005930 어때?" → 005930 매칭)

종목 감지 시 행동:
- 감지된 종목의 실시간 가격을 yfinance_kr_client에서 가져오기
- "처리 중..." 메시지 표시
- AI에게 종목 데이터(현재가, 기술적 지표 등) + 시장 컨텍스트 + 사용자 질문을 함께 전달
- src/kstock/ingest/yfinance_kr_client.py의 get_stock_info()로 RSI, MACD, 이동평균선 데이터 확보

구현 위치: bot.py의 handle_menu_text에서 else 블록(현재 line 828) 수정

```python
# 현재: await self._handle_ai_question(update, context, text)
# 변경:
detected = self._detect_stock_in_text(text)
if detected:
    await self._handle_stock_question(update, context, text, detected)
else:
    await self._handle_ai_question(update, context, text)
```

_detect_stock_in_text 메서드 구현:
- self.all_tickers 순회하며 텍스트에 종목명 또는 종목코드가 포함되어 있는지 확인
- 매칭 시 {"ticker": "005930", "name": "삼성전자"} 반환
- 긴 이름부터 매칭 (예: "삼성전자우" 가 "삼성전자"보다 먼저)

_handle_stock_question 메서드 구현:
- 실시간 가격 + 기술 지표 조회
- 조회된 데이터를 AI 질문 컨텍스트에 추가
- AI에게 질문 전달

중요: 슬래시 명령은 하위 호환성을 위해 유지하되, 자연어로도 동작하게 하는 것이 목표.

---

## 작업 2: 모바일 최적화 메시지 포맷팅

현재 문제:
- AI 응답이 텔레그램 모바일에서 읽기 어려움
- 긴 문단, 줄바꿈 부족, 시각적 구분 없음

수정 파일: src/kstock/bot/chat_handler.py, src/kstock/bot/context_builder.py

시스템 프롬프트 (context_builder.py)의 [응답 형식] 섹션을 다음으로 변경:

```
[응답 형식 - 모바일 텔레그램 최적화]
- 볼드(별표 두개) 절대 사용 금지
- 한국어로 답변
- 한 문장은 최대 25자. 긴 문장은 줄바꿈으로 끊어라.
- 각 섹션 사이에 빈 줄 하나 넣어라.
- 구분선: ── (20개)
- 숫자/가격에는 콤마 사용: 75,000원
- 핵심 내용은 이모지로 시작: 📈 📉 💰 ⚠️ 🎯 💡
- 목록은 이모지 bullet으로: ✅ 🔸 →
- 관심/매수/매도 포인트를 명확히 구분:
  🟡 관심: 아직 매수 타이밍 아님, 지켜보기
  🟢 매수: 지금 사도 되는 구간
  🔴 매도: 이익 실현 또는 손절 필요
- 500~800자 범위로 답변 (너무 길지 않게)
- 항상 "{user_name}"으로 호칭
```

chat_handler.py도 수정:
- handle_ai_question 함수에서 AI 응답 후처리 추가
- max_tokens를 1500 → 1200으로 (간결하게)
- 응답 끝에 자동으로 줄바꿈 + 포인트 태그 확인

---

## 작업 3: 실시간 주가 데이터 AI 주입

현재 문제:
- AI가 종목 추천 시 주가를 "추정"으로 답함 (예: "75,000원대 가정")
- 실제 yfinance에서 가져올 수 있는데 AI 컨텍스트에 안 넣고 있음

수정 파일: src/kstock/bot/context_builder.py

build_full_context_with_macro()에서:
- 보유종목의 현재가를 yfinance에서 실시간 조회
- 종목별 기본 기술지표 (RSI, MACD, 이동평균선) 포함
- 이 데이터를 시스템 프롬프트에 주입

get_portfolio_context(db)를 개선:
- 현재는 스크린샷 기반으로만 데이터를 가져옴
- get_active_holdings()에서 보유종목 목록을 가져온 후
- 각 종목의 실시간 가격을 yfinance_kr_client.get_stock_info(ticker)로 조회
- current_price, RSI, MACD 등을 포맷에 포함

주의: yfinance 호출은 느릴 수 있으므로 asyncio.gather + 캐시 활용

---

## 작업 4: 매일 자동 자가진단 보고서

현재 문제: 봇이 매일 뭘 개선해야 하는지 자체 진단하지 않음

새 파일 생성: src/kstock/bot/daily_self_report.py

매일 오후 9시 (21:00 KST)에 실행되는 자가진단 보고서:

```python
async def generate_daily_self_report(db, macro_client) -> str:
    """봇 자가진단 + 개선 제안 보고서."""
```

보고서 내용:
1. 오늘 봇 성과 요약
   - AI 질문 응답 횟수 (chat_usage 테이블에서)
   - 추천 적중률 변화
   - 시장 분위기 변화 알림 횟수

2. 부족했던 점 분석
   - 답변 못한 질문 유형
   - 데이터 누락 항목 (재무 데이터 없는 종목 수 등)
   - API 에러 횟수

3. 개선 제안
   - "재무 데이터가 없는 보유종목 3개 → 데이터 수집 필요"
   - "응답 속도 평균 X초 → 캐시 추가 필요"
   - 주간 승률/수익률 트렌드

4. 시스템 상태
   - DB 크기, 캐시 적중률
   - 마지막 macro_refresh 시간
   - 스케줄러 잡 상태

5. 외부 도구/스킬 추천 (하드코딩 또는 AI 생성)
   - "GitHub에서 한국 주식 분석 도구 탐색 제안"
   - "새로운 데이터 소스 연동 가능성"
   - "pykis 패키지 설치하면 실시간 호가 가능"

봇 등록: bot.py의 schedule_jobs()에 추가:
```python
context.job_queue.run_daily(
    self.job_daily_self_report,
    time=time(hour=21, minute=0, tzinfo=KST),
    name="daily_self_report",
)
```

job_daily_self_report 메서드:
```python
async def job_daily_self_report(self, context):
    from kstock.bot.daily_self_report import generate_daily_self_report
    report = await generate_daily_self_report(self.db, self.macro_client)
    await context.bot.send_message(chat_id=self.chat_id, text=report)
```

---

## 작업 5: AI 응답에 관심/매수/매도 포인트 자동 태깅

현재 문제:
- AI가 종목 추천할 때 관심/매수/매도 구분 없이 한 덩어리로 답변
- 사용자가 즉시 행동할 수 없음

시스템 프롬프트 (context_builder.py)에 추가:

```
종목 분석 시 반드시 다음 포인트를 명시하라:
🟡 관심 포인트: 아직 매수 타이밍이 아니지만 주시할 가격대와 조건
🟢 매수 포인트: 진입하기 좋은 가격대와 그 이유
🔴 매도 포인트: 이익실현 또는 손절 가격대

예시 형식:
🟡 관심: 74,000원 이하로 내려오면 주목
🟢 매수: 73,000~74,500원 구간 (20일선 지지)
🎯 목표: 82,000원 (+11%)
🔴 손절: 70,000원 (-5%)
```

---

## 실행 순서

1. 작업 2 먼저 (메시지 포맷 → 즉시 효과)
2. 작업 1 (자연어 종목 인식 → 핵심 UX)
3. 작업 3 (실시간 주가 AI 주입)
4. 작업 5 (포인트 태깅)
5. 작업 4 (자가진단 → 마지막)

각 작업 후 PYTHONPATH=src python3 -m pytest tests/ -x -q 실행하여 2048 tests 통과 확인
각 작업 후 봇 재시작: kill $(pgrep -f "kstock.app") && PYTHONPATH=src nohup python3 -m kstock.app > bot.log 2>&1 &

---

## 주의사항

- .env 로드: 반드시 load_dotenv(override=True) 사용 (시스템에 빈 ANTHROPIC_API_KEY 환경변수 존재)
- 테스트: tests/test_chat_handler.py에 관련 테스트 존재. 시스템 프롬프트 변경 시 테스트도 업데이트
- 봇 PID: pgrep -f "kstock.app"으로 확인
- 텔레그램 메시지: parse_mode 없음 (plain text + emoji). HTML/Markdown 모드 사용하지 말 것
- 종목 코드: KOSPI는 6자리 (005930), KOSDAQ은 6자리
- yfinance 티커: KOSPI는 .KS, KOSDAQ은 .KQ 접미사
- AI 모델: 대화는 claude-sonnet-4-5-20250929, 요약은 claude-haiku-4-5-20251001
- 한국어로 모든 봇 메시지 작성
- 텔레그램 CHAT_ID: 6247622742
- 텔레그램 BOT_TOKEN: 환경변수에서 로드
