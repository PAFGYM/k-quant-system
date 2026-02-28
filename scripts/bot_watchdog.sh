#!/bin/bash
# K-Quant Bot Watchdog
# 봇 프로세스 상태 + 409 Conflict 감지 + 자동 복구
# 사용: crontab에 등록하거나 수동 실행

cd /Users/juhodang/k-quant-system

LOG_FILE="bot.log"
WATCHDOG_LOG="data/watchdog.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$WATCHDOG_LOG"
    echo "$1"
}

# 1. 봇 프로세스 확인
BOT_PID=$(pgrep -f "kstock.app")
if [ -z "$BOT_PID" ]; then
    log "ALERT: Bot not running! Restarting..."
    PYTHONPATH=src nohup python3 -m kstock.app > bot.log 2>&1 &
    sleep 10
    NEW_PID=$(pgrep -f "kstock.app")
    if [ -n "$NEW_PID" ]; then
        log "OK: Bot restarted (PID: $NEW_PID)"
    else
        log "FAIL: Bot failed to restart"
    fi
    exit 0
fi

# 2. 중복 프로세스 확인
PROC_COUNT=$(pgrep -f "kstock.app" | wc -l | tr -d ' ')
if [ "$PROC_COUNT" -gt 1 ]; then
    log "ALERT: Multiple bot processes ($PROC_COUNT). Killing all and restarting..."
    kill -9 $(pgrep -f "kstock.app") 2>/dev/null
    sleep 5
    TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook?drop_pending_updates=true" > /dev/null 2>&1
    sleep 3
    PYTHONPATH=src nohup python3 -m kstock.app > bot.log 2>&1 &
    sleep 10
    log "OK: Bot restarted after duplicate cleanup (PID: $(pgrep -f 'kstock.app'))"
    exit 0
fi

# 3. 연속 409 Conflict 감지 (최근 5분)
CONFLICT_COUNT=$(tail -100 "$LOG_FILE" 2>/dev/null | grep -c "409 Conflict")
if [ "$CONFLICT_COUNT" -gt 5 ]; then
    log "ALERT: Too many 409 Conflicts ($CONFLICT_COUNT in recent logs). Cleaning up..."
    TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook?drop_pending_updates=true" > /dev/null 2>&1
    # getUpdates 세션 리셋
    curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates?offset=-1&timeout=0" > /dev/null 2>&1
    log "OK: Webhook deleted + session reset"
    exit 0
fi

# 4. 최근 getUpdates 200 OK 확인 (5분 내)
LAST_OK=$(grep "getUpdates.*200 OK" "$LOG_FILE" 2>/dev/null | tail -1)
if [ -z "$LAST_OK" ]; then
    log "WARNING: No recent getUpdates 200 OK found"
else
    log "OK: Bot running (PID: $BOT_PID, procs: $PROC_COUNT, conflicts: $CONFLICT_COUNT)"
fi
