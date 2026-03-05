# K-Quant v9 프로젝트 규칙 (CLAUDE.md)

## 프로젝트 개요
한국 주식 퀀트 트레이딩 텔레그램 봇 (주호님 전용)
- 경로: /Users/botddol/k-quant-system
- 언어: Python 3.9
- PYTHONPATH=src 필수

## 4대 원칙 (Karpathy-Inspired)

### 1. Think Before Coding
- 가정하지 마라. 불확실하면 물어라
- 여러 해석이 가능하면 선택지를 제시하라
- 더 단순한 방법이 있으면 제안하라
- 혼란스러우면 멈추고 무엇이 불분명한지 말하라

### 2. Simplicity First
- 요청받은 것만 구현. 추측성 기능 추가 금지
- 1회용 코드에 추상화 금지
- "유연성", "확장성"이 요청되지 않았으면 넣지 마라
- 불가능한 시나리오에 대한 에러 처리 금지
- 200줄이 50줄로 가능하면 50줄로 써라

### 3. Surgical Changes
- 요청과 무관한 코드, 주석, 포맷 수정 금지
- 안 깨진 것을 리팩터하지 마라
- 기존 스타일을 따라라 (내 방식이 더 나아도)
- 관련 없는 죽은 코드 발견 시: 삭제하지 말고 언급만
- 내 변경으로 생긴 미사용 import/변수만 정리

### 4. Goal-Driven Execution
- 성공 기준을 먼저 정의하고, 충족될 때까지 반복
- "기능 추가" → 테스트 작성 → 통과시키기
- "버그 수정" → 재현 테스트 → 통과시키기
- 여러 단계면 각 단계별 검증 기준 명시

## 필수 규칙

### .env
- `load_dotenv(override=True)` 필수 (시스템에 빈 ANTHROPIC_API_KEY 존재)

### 텔레그램 메시지
- parse_mode 없음 (plain text + emoji)
- HTML/Markdown/**볼드** 사용 금지
- 한 문장 최대 25자, 줄바꿈 적극, 이모지로 구분

### AI 모델
- 대화/분석: claude-sonnet-4-5-20250929
- 요약/빠른응답: claude-haiku-4-5-20251001
- 시스템 프롬프트에서 "주호님" 호칭

### 종목 코드
- KOSPI/KOSDAQ: 6자리 (예: 005930)
- yfinance: KOSPI→.KS, KOSDAQ→.KQ

### 봇 운영
- 재시작: `./kbot restart`
- 로그: `tail -30 /tmp/kstock_bot.log`
- CHAT_ID: 6247622742

### 문법 검사
- 수정 후: `python3 -c "import py_compile; py_compile.compile('파일경로', doraise=True)"`

## 금지 사항
- 하드코딩된 API 키 금지
- parse_mode=HTML 또는 Markdown 사용 금지
- git push --force 금지
- load_dotenv() 기본 호출 금지 (override=True 필수)
- 요청 외 코드 정리/리팩터링 금지
- docstring/주석/타입힌트를 변경하지 않은 코드에 추가 금지
