# K-Quant v3.5 프로젝트 규칙 (CLAUDE.md)

## 프로젝트 개요
한국 주식 퀀트 트레이딩 텔레그램 봇 (주호님 전용)
- 경로: /Users/juhodang/k-quant-system
- 언어: Python 3.9
- PYTHONPATH=src 필수

## 핵심 원칙 (보리스 체르니 워크플로우)

### 1. Plan First
- 3단계 이상의 작업은 반드시 계획 먼저
- 잘못된 방향이면 즉시 STOP → 재계획
- "일단 해보자" 금지

### 2. Verification Before Done
- 코드 수정 후 반드시 테스트 실행: `PYTHONPATH=src python3 -m pytest tests/ -x -q`
- 2048 tests 전체 통과 확인 필수
- 봇 재시작 후 로그 확인

### 3. Self-Improvement (교훈 기록)
- 사용자 피드백 받으면 이 파일의 [교훈] 섹션에 기록
- 같은 실수 반복 금지

### 4. Minimal Impact
- 버그 수정 시 관련 코드만 수정
- 수정하다가 리팩터링까지 하지 말 것
- 별도 작업으로 제안

## 필수 규칙

### .env 로딩
- 반드시 `load_dotenv(override=True)` 사용
- 이유: 시스템에 빈 ANTHROPIC_API_KEY 환경변수 존재 (Claude Code 설치로 인해)
- `load_dotenv()` (기본값 override=False) 사용 금지

### 텔레그램 메시지 형식
- parse_mode 없음 (plain text + emoji)
- HTML/Markdown 모드 사용 금지
- 볼드(**) 사용 금지
- 모바일 가독성 최우선
- 한 문장 최대 25자, 줄바꿈 적극 사용
- 이모지로 시각적 구분

### AI 모델
- 대화/분석: claude-sonnet-4-5-20250929
- 요약/빠른응답: claude-haiku-4-5-20251001
- 시스템 프롬프트에서 항상 "주호님"으로 호칭

### 종목 코드
- KOSPI: 6자리 (예: 005930)
- KOSDAQ: 6자리
- yfinance: KOSPI는 .KS, KOSDAQ은 .KQ 접미사

### 봇 운영
- 봇 PID 확인: `pgrep -f "kstock.app"`
- 봇 재시작: `kill $(pgrep -f "kstock.app") && sleep 2 && PYTHONPATH=src nohup python3 -m kstock.app > bot.log 2>&1 &`
- 로그 확인: `tail -30 bot.log`
- 텔레그램 CHAT_ID: 6247622742

### 테스트
- 모든 변경 후 반드시 테스트 실행
- 현재 2048개 테스트 전체 통과 필수
- 테스트 파일: tests/ 디렉토리
- 시스템 프롬프트 변경 시 tests/test_chat_handler.py 확인

## 아키텍처

### 주요 파일
- bot.py: 텔레그램 봇 메인 (3800+ 줄)
- chat_handler.py: AI 대화 처리
- context_builder.py: AI 시스템 프롬프트 + 컨텍스트 조합
- macro_client.py: 3-tier 캐시 (Memory → SQLite → yfinance)
- investor_profile.py: 투자자 학습 + 보유기간별 솔루션
- sqlite.py: DB 스토어 (65+ 테이블)
- market_pulse.py: 실시간 시장 분위기 감지

### 데이터 흐름
1. MacroClient → 3분마다 시장 데이터 갱신
2. MarketPulse → 5분마다 시장 상태 체크
3. AI 질문 → build_full_context_with_macro() → Claude API
4. 스케줄러 → 10개 정기 작업 (모닝브리핑, 인트라데이 등)

### DB 주요 테이블
- holdings: 보유종목
- investor_profile: 투자자 성향 (Phase 9)
- holding_analysis: 보유종목 AI 분석
- trade_lessons: 매매 교훈
- macro_cache: 시장 데이터 캐시
- chat_history: AI 대화 이력

## 교훈 (Lessons Learned)

### 2026-02-23
- .env 파일은 정상인데 load_dotenv()로 API 키가 안 읽힘
  → 원인: Claude Code가 빈 ANTHROPIC_API_KEY를 시스템 환경변수에 설정
  → 해결: load_dotenv(override=True) 사용 필수

- AI 응답에 시장 데이터가 "없음"으로 나옴
  → 원인: build_full_context_async()에 macro_snapshot을 전달하지 않았음
  → 해결: build_full_context_with_macro(db, macro_client) 사용

- 삼성전자 주가를 AI가 추정으로 답함
  → 원인: AI 컨텍스트에 실시간 주가 데이터가 없음
  → 해결: yfinance에서 실시간 가격 조회 후 컨텍스트에 주입 필요

- 슬래시 명령이 불편함
  → "삼성전자 분석해줘" 같은 자연어로 동작해야 함
  → 자연어 종목 인식 기능 구현 필요

- 메시지가 모바일에서 읽기 어려움
  → 짧은 문장, 줄바꿈, 이모지 구분 필요
  → 관심/매수/매도 포인트 명확 구분 (🟡🟢🔴)

- 보유종목 등록이 /register로만 가능해서 불편함
  → 해결: 자연어 매수 감지 ("삼성전자 50주 76000원") + 확인 버튼
  → 해결: 스크린샷에서 인식된 종목 → "포트폴리오에 추가해드릴까요?" 제안

- 스크린샷 보유종목이 포트폴리오에 자동 추가 안 됨
  → 해결: handle_screenshot에서 신규 종목 감지 → InlineKeyboard로 추가 확인
  → DB active_holdings에 없는 종목만 제안

- get_latest_screenshot vs get_last_screenshot 불일치
  → 해결: get_latest_screenshot = get_last_screenshot 별칭 추가

- daily_self_report에서 get_job_runs(date) 메서드 필요
  → 해결: sqlite.py에 get_job_runs(run_date) 메서드 추가

## Phase 10: 자연어 입력 + 포트폴리오 자동 관리
- handle_menu_text: 자연어 매수 감지 → _detect_trade_input → _propose_trade_addition
- handle_screenshot: 신규 종목 → "포트폴리오에 추가해드릴까요?" 제안
- add_ss 콜백: 스크린샷에서 전체/개별 종목 포트폴리오 추가
- add_txt 콜백: 자연어 입력 종목 확인 후 포트폴리오 추가
- job_daily_self_report: 매일 21:00 봇 자가진단 보고서

## 금지 사항
- var 사용 금지 (const/let만)
- 하드코딩된 API 키 금지
- parse_mode=HTML 또는 Markdown 사용 금지
- git push --force 금지
- 테스트 미통과 상태에서 봇 재시작 금지
- load_dotenv() 기본 호출 금지 (override=True 필수)
