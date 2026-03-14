#!/bin/bash
# 1시간마다 봇 상태 체크 + 문제 시 자동 복구
cd /Users/juhodang/k-quant-system
LOG="data/watchdog.log"
STDOUT_LOG="data/logs/kquant_stdout.log"

mkdir -p data/logs

while true; do
    sleep 3600
    NOW=$(date '+%Y-%m-%d %H:%M:%S')
    
    # 1. 프로세스 확인
    BOT_PID=$(pgrep -f "kstock.app")
    if [ -z "$BOT_PID" ]; then
        echo "[$NOW] ALERT: Bot dead. Restarting..." >> "$LOG"
        PYTHONPATH=src nohup python3 -m kstock.app >> "$STDOUT_LOG" 2>&1 &
        sleep 10
        echo "[$NOW] Restarted PID: $(pgrep -f 'kstock.app')" >> "$LOG"
        continue
    fi
    
    # 2. 중복 프로세스
    PROC_COUNT=$(pgrep -f "kstock.app" | wc -l | tr -d ' ')
    if [ "$PROC_COUNT" -gt 1 ]; then
        echo "[$NOW] ALERT: $PROC_COUNT processes. Cleaning..." >> "$LOG"
        kill -9 $(pgrep -f "kstock.app") 2>/dev/null
        sleep 5
        TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
        curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook?drop_pending_updates=true" > /dev/null 2>&1
        sleep 3
        PYTHONPATH=src nohup python3 -m kstock.app >> "$STDOUT_LOG" 2>&1 &
        sleep 10
        echo "[$NOW] Cleaned. New PID: $(pgrep -f 'kstock.app')" >> "$LOG"
        continue
    fi
    
    # 3. 클로드 메뉴 재전송 (1시간마다)
    TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    CHAT_ID=$(cat data/.chat_id 2>/dev/null)
    if [ -n "$CHAT_ID" ]; then
        LAST_MENU=$(grep "sendMessage.*200 OK" "$STDOUT_LOG" | tail -1 | awk '{print $1 " " $2}')
        echo "[$NOW] OK: PID=$BOT_PID, procs=$PROC_COUNT" >> "$LOG"
    fi
done
