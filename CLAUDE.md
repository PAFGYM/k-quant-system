# K-Quant v3.8 프로젝트 규칙 (CLAUDE.md)

## 프로젝트 개요
한국 주식 퀀트 트레이딩 텔레그램 봇 (주호님 전용)
- 경로: /Users/juhodang/k-quant-system
- 언어: Python 3.9
- PYTHONPATH=src 필수
- 아키텍처 상세: .claude/docs/ARCHITECTURE.md
- 교훈/히스토리: .claude/docs/LESSONS.md

## 핵심 원칙 (보리스 체르니 워크플로우)
1. **Plan First** — 3단계 이상 작업은 계획 먼저. "일단 해보자" 금지
2. **Verify** — 수정 후 반드시: `PYTHONPATH=src python3 -m pytest tests/ -x -q` (2149 tests 전체 통과)
3. **Self-Improve** — 피드백 → .claude/docs/LESSONS.md에 기록. 같은 실수 반복 금지
4. **Minimal Impact** — 버그 수정 시 관련 코드만. 리팩터링은 별도 작업으로 제안

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
- PID: `pgrep -f "kstock.app"`
- 재시작: `kill $(pgrep -f "kstock.app") && sleep 2 && PYTHONPATH=src nohup python3 -m kstock.app > bot.log 2>&1 &`
- 로그: `tail -30 bot.log`
- CHAT_ID: 6247622742

### 테스트
- 모든 변경 후 반드시 실행, 2149 tests 전체 통과
- 시스템 프롬프트 변경 시 tests/test_chat_handler.py 확인

## 금지 사항
- 하드코딩된 API 키 금지
- parse_mode=HTML 또는 Markdown 사용 금지
- git push --force 금지
- 테스트 미통과 상태에서 봇 재시작 금지
- load_dotenv() 기본 호출 금지 (override=True 필수)
