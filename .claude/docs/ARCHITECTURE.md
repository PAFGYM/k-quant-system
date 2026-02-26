# K-Quant v3.8 아키텍처

## Mixin 구조
KQuantBot = CoreHandlers + MenusKis + Trading + Scheduler + Commands + AdminExtras + RemoteClaude
- src/kstock/bot/bot.py: 메인 클래스 (7개 mixin 합성)
- src/kstock/bot/mixins/core_handlers.py: 콜백 dispatch + schedule_jobs()
- src/kstock/bot/mixins/scheduler.py: 19개 스케줄 잡 + WebSocket 콜백
- src/kstock/bot/mixins/trading.py: 매수 플래너, 리스크 분석, 매도 가이드
- src/kstock/bot/mixins/menus_kis.py: 메뉴 + KIS 증권 연동
- src/kstock/bot/mixins/commands.py: 명령어 핸들러
- src/kstock/bot/mixins/admin_extras.py: 관리자 기능
- src/kstock/bot/mixins/remote_claude.py: 원격 Claude 연동

## 주요 모듈 (103 소스 파일, 10개 패키지)
- kstock/bot/: 텔레그램 봇 + AI 대화 (chat_handler, context_builder, morning_briefing...)
- kstock/core/: 리스크 엔진, 시나리오 분석, 투자자 프로필, 매도 플래너
- kstock/backtest/: 백테스트 엔진 (TradeCosts, run_backtest, run_portfolio_backtest)
- kstock/ml/: LSTM 예측, ML 예측, 감성 분석, 피처 스토어
- kstock/signal/: 37개 시그널 모듈 (스코어링, 스윙, 기관추적, 수급 등)
- kstock/ingest/: 데이터 수집 (macro_client 3-tier캐시, KIS WebSocket, yfinance)
- kstock/broker/: KIS 증권 주문 관리
- kstock/store/: SQLite (57 테이블) + Parquet
- kstock/report/: PDF 보고서 생성
- kstock/ops/: 스케줄러, 레이트 리밋

## 데이터 흐름
1. MacroClient → 1분마다 시장 데이터 갱신 (3-tier: Memory → SQLite → yfinance)
2. MarketPulse → 1분마다 시장 상태 체크
3. AI 질문 → build_full_context_with_macro() → Claude API
4. 스케줄러 → 19개 정기 작업 (모닝브리핑, 매수플래너, 인트라데이, 리포트 크롤링 등)
5. WebSocket → 실시간 급등 감지(+3%) + 보유종목 매도 타겟 모니터링

## 스케줄 잡 (19개)
| 시간 | 잡 | 요일 |
|------|-----|------|
| 03:00 | lstm_retrain | 일 |
| 07:00 | us_premarket_briefing | 매일 |
| 07:30 | morning_briefing | 매일 |
| 07:50 | premarket_buy_planner | 평일 |
| 08:00 | sentiment_analysis | 매일 |
| 08:00 | short_term_review | 평일 |
| 08:00 | screenshot_reminder | 월,금 |
| 08:20 | report_crawl | 평일 |
| 08:50 | ws_connect | 평일 |
| 09:00 | weekly_learning | 토 |
| 14:30 | scalp_close_reminder | 평일 |
| 15:35 | ws_disconnect | 평일 |
| 16:00 | daily_pdf_report | 매일 |
| 19:00 | weekly_report | 일 |
| 21:00 | daily_self_report | 매일 |
| 60초 | intraday_monitor | 반복 |
| 60초 | macro_refresh | 반복 |
| 60초 | market_pulse | 반복 |
| 5초 | ws_connect_startup | 1회 |

## DB 주요 테이블 (57개 중)
holdings, investor_profile, holding_analysis, trade_lessons,
macro_cache, chat_history, trade_registers, ml_predictions,
portfolio_snapshots, screenshots, orders, surge_stocks
