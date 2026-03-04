#!/usr/bin/env python3
"""클로드코드 → 텔레그램 실시간 컨트롤 유틸리티.

터미널에서 직접 텔레그램 봇을 제어할 수 있습니다.

사용법:
    python3 scripts/tg.py "메시지 내용"          # 텍스트 전송
    python3 scripts/tg.py --status               # 봇 상태 확인
    python3 scripts/tg.py --balance              # 잔고 요약
    python3 scripts/tg.py --market               # 시장 현황
    python3 scripts/tg.py --restart              # 봇 재시작
    python3 scripts/tg.py --macro                # 매크로 스냅샷
    python3 scripts/tg.py --holdings             # 보유종목 목록
    python3 scripts/tg.py --ai "질문"            # AI에게 질문

Claude Code에서:
    python3 scripts/tg.py "SK하이닉스 지금 어때?"

봇 관리는 ./kbot 스크립트를 사용하세요.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

# 프로젝트 루트를 sys.path에 추가
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_DIR, ".env"), override=True)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def send_message(text: str) -> bool:
    """텔레그램 메시지 전송."""
    import requests

    r = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": text[:4096]},
        timeout=10,
    )
    data = r.json()
    if data.get("ok"):
        print(f"✅ 전송 완료 ({len(text)}자)")
        return True
    print(f"❌ 전송 실패: {data.get('description', 'unknown')}")
    return False


def bot_status() -> str:
    """봇 프로세스 상태 확인."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "kstock.app"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p for p in result.stdout.strip().split("\n") if p]
        if pids:
            pid = pids[0]
            # 가동 시간 확인
            ps = subprocess.run(
                ["ps", "-p", pid, "-o", "etime="],
                capture_output=True, text=True, timeout=5,
            )
            uptime = ps.stdout.strip()
            return f"🟢 봇 실행 중 (PID {pid}, 가동: {uptime})"
        return "🔴 봇이 실행되고 있지 않습니다"
    except Exception as e:
        return f"⚠️ 상태 확인 실패: {e}"


def get_macro_snapshot() -> str:
    """매크로 스냅샷 조회 (SQLite 캐시)."""
    try:
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore()
        row = db.get_macro_cache()
        if not row or not row.get("snapshot_json"):
            return "매크로 캐시 없음"
        data = json.loads(row["snapshot_json"])
        lines = ["📊 매크로 스냅샷"]
        if data.get("kospi"):
            lines.append(f"코스피: {data['kospi']:,.2f} ({data.get('kospi_change_pct', 0):+.2f}%)")
        if data.get("kosdaq"):
            lines.append(f"코스닥: {data['kosdaq']:,.2f} ({data.get('kosdaq_change_pct', 0):+.2f}%)")
        lines.append(f"S&P500: {data.get('spx_change_pct', 0):+.2f}%")
        lines.append(f"나스닥: {data.get('nasdaq_change_pct', 0):+.2f}%")
        lines.append(f"VIX: {data.get('vix', 0):.1f}")
        lines.append(f"원/달러: {data.get('usdkrw', 0):,.0f}원")
        lines.append(f"BTC: ${data.get('btc_price', 0):,.0f}")
        lines.append(f"공포탐욕: {data.get('fear_greed_score', 50):.0f}점 ({data.get('fear_greed_label', '중립')})")
        fetched = data.get("fetched_at", "?")
        lines.append(f"업데이트: {fetched}")
        return "\n".join(lines)
    except Exception as e:
        return f"매크로 조회 실패: {e}"


def get_holdings() -> str:
    """보유종목 목록 조회."""
    try:
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore()
        holdings = db.get_active_holdings()
        if not holdings:
            return "📦 보유종목 없음"
        lines = [f"💰 보유종목 ({len(holdings)}개)"]
        for h in holdings:
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            bp = h.get("buy_price", 0)
            qty = h.get("quantity", 0)
            ht = h.get("holding_type", "auto")
            emoji = {"scalp": "⚡", "swing": "🔥", "position": "📊", "long_term": "💎"}.get(ht, "📌")
            lines.append(f"{emoji} {name}({ticker}) {bp:,.0f}원 x {qty}주")
        return "\n".join(lines)
    except Exception as e:
        return f"보유종목 조회 실패: {e}"


def restart_bot() -> str:
    """봇 재시작."""
    try:
        # 기존 프로세스 종료
        subprocess.run(["pkill", "-9", "-f", "kstock.app"], timeout=5)
        time.sleep(5)
        # 새 프로세스 시작
        subprocess.Popen(
            [sys.executable, "-c", "from kstock.app import main; main()"],
            cwd=PROJECT_DIR,
            env={**os.environ, "PYTHONPATH": "src"},
            stdout=open("/tmp/kstock_bot.log", "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(3)
        return bot_status()
    except Exception as e:
        return f"재시작 실패: {e}"


def get_balance() -> str:
    """잔고 요약 조회."""
    try:
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore()
        holdings = db.get_active_holdings()
        if not holdings:
            return "📦 보유종목 없음 (잔고 데이터 없음)"
        total_buy = 0
        total_eval = 0
        lines = [f"💰 잔고 요약 ({len(holdings)}종목)"]
        for h in holdings:
            name = h.get("name", "")[:8]
            bp = float(h.get("buy_price", 0) or 0)
            qty = int(h.get("quantity", 0) or 0)
            cur = float(h.get("current_price", 0) or 0)
            buy_amt = bp * qty
            eval_amt = cur * qty if cur > 0 else buy_amt
            total_buy += buy_amt
            total_eval += eval_amt
            pnl = ((cur - bp) / bp * 100) if bp > 0 and cur > 0 else 0
            emoji = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")
            lines.append(f"{emoji} {name}: {pnl:+.1f}% ({eval_amt:,.0f}원)")
        total_pnl = ((total_eval - total_buy) / total_buy * 100) if total_buy > 0 else 0
        lines.append(f"\n총 매입: {total_buy:,.0f}원")
        lines.append(f"총 평가: {total_eval:,.0f}원")
        lines.append(f"총 손익: {total_pnl:+.1f}%")
        return "\n".join(lines)
    except Exception as e:
        return f"잔고 조회 실패: {e}"


def get_market() -> str:
    """시장 현황 조회."""
    try:
        snapshot = get_macro_snapshot()
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore()
        alert_mode = db.get_meta("alert_mode") or "normal"
        mode_label = {"normal": "🟢 일상", "elevated": "🟡 긴장", "wartime": "🔴 전시"}.get(alert_mode, alert_mode)
        return f"📈 시장 현황\n경계: {mode_label}\n\n{snapshot}"
    except Exception as e:
        return f"시장 현황 조회 실패: {e}"


def ask_ai(question: str) -> str:
    """AI에게 질문 (동기 호출)."""
    try:
        import asyncio
        from kstock.store.sqlite import SQLiteStore
        from kstock.bot.chat_handler import handle_ai_question
        from kstock.bot.context_builder import build_full_context_with_macro
        from kstock.ingest.macro_client import MacroClient
        from kstock.bot.chat_memory import ChatMemory

        db = SQLiteStore()
        macro = MacroClient(db=db)
        memory = ChatMemory()

        async def _ask():
            context = await build_full_context_with_macro(db, macro_client=macro)
            return await handle_ai_question(question, context, db, memory)

        return asyncio.run(_ask())
    except Exception as e:
        return f"AI 질문 실패: {e}"


def main():
    parser = argparse.ArgumentParser(description="K-Quant 텔레그램 컨트롤러")
    parser.add_argument("message", nargs="?", help="전송할 메시지")
    parser.add_argument("--status", action="store_true", help="봇 상태")
    parser.add_argument("--balance", action="store_true", help="잔고 요약")
    parser.add_argument("--market", action="store_true", help="시장 현황")
    parser.add_argument("--restart", action="store_true", help="봇 재시작")
    parser.add_argument("--macro", action="store_true", help="매크로 스냅샷")
    parser.add_argument("--holdings", action="store_true", help="보유종목")
    parser.add_argument("--ai", type=str, help="AI에게 질문")
    parser.add_argument("--send", action="store_true", default=True, help="텔레그램으로 전송")
    parser.add_argument("--no-send", action="store_true", help="터미널에만 출력")
    args = parser.parse_args()

    if not TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 .env에 없습니다")
        sys.exit(1)

    result = None

    if args.status:
        result = bot_status()
    elif args.balance:
        result = get_balance()
    elif args.market:
        result = get_market()
    elif args.macro:
        result = get_macro_snapshot()
    elif args.holdings:
        result = get_holdings()
    elif args.restart:
        result = restart_bot()
    elif args.ai:
        print(f"🤖 AI에게 질문 중: {args.ai}")
        result = ask_ai(args.ai)
    elif args.message:
        send_message(args.message)
        return

    if result:
        print(result)
        if not args.no_send:
            send_message(result)


if __name__ == "__main__":
    main()
