#!/bin/bash
# K-Quant Bot 실행 + 관리 스크립트
# Usage: ./ralph.sh [start|stop|restart|logs|admin|watch]

cd "$(dirname "$0")"

case "${1:-start}" in
  start)
    echo "🚀 K-Quant v3.5.1 봇 시작..."
    PYTHONPATH=src python3 -m kstock.bot.bot &
    echo $! > .bot.pid
    echo "PID: $(cat .bot.pid)"
    ;;
  stop)
    if [ -f .bot.pid ]; then
      kill $(cat .bot.pid) 2>/dev/null
      rm .bot.pid
      echo "🛑 봇 종료됨"
    else
      pkill -f "kstock.bot.bot" 2>/dev/null
      echo "🛑 봇 프로세스 종료 시도"
    fi
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  logs)
    tail -50 bot.log 2>/dev/null | grep -E "ERROR|WARNING" | tail -20
    ;;
  admin)
    # 관리자 리포트 확인
    echo "📋 최근 관리자 리포트:"
    tail -20 data/admin_reports.jsonl 2>/dev/null || echo "아직 리포트 없음"
    ;;
  watch)
    # Claude Code 연동: 실시간 모니터링
    echo "👀 관리자 리포트 실시간 감시 중... (Ctrl+C로 종료)"
    tail -f data/admin_reports.jsonl 2>/dev/null || (
      mkdir -p data && touch data/admin_reports.jsonl
      echo "파일 생성 완료. 다시 실행해주세요."
    )
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|logs|admin|watch}"
    ;;
esac
