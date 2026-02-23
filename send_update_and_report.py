"""업데이트 알림 + 장마감 리포트(텍스트+PDF) 전송 스크립트."""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).parent / "src"))

KST = timezone(timedelta(hours=9))


async def main():
    import telegram

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    bot = telegram.Bot(token=bot_token)

    # ── 1. 업데이트 알림 전송 ──
    now = datetime.now(KST)
    update_msg = (
        "🚀 K-Quant v3.5 업데이트 완료!\n"
        f"{'━' * 24}\n\n"
        "📋 주요 변경사항:\n\n"
        "1️⃣ 가격 정확도 향상\n"
        "   • 데이터 수집 주기: 5분 → 1분\n"
        "   • KIS API 우선 가격 소스\n"
        "   • 가짜 가격(mock) 완전 제거\n\n"
        "2️⃣ 장마감 리포트 통합\n"
        "   • 텍스트 폭탄 → 간결한 요약 1건 + PDF 1건\n"
        "   • 결론 먼저 (매수/관망/방어)\n"
        "   • 내 포트폴리오 상황 반영\n\n"
        "3️⃣ 잔고 표시 개선\n"
        "   • 종목별 금액 손익 표시 (+200,000원)\n"
        "   • 전일 대비 등락 표시 (오늘 📈 +1.9%)\n\n"
        "4️⃣ 전략별 보기 활성화\n"
        "   • 7개 전략(반등~돌파) 추천 종목 자동 저장\n"
        "   • 전략 클릭 → 실제 추천 종목 확인\n\n"
        "5️⃣ ⭐ 즐겨찾기 기능\n"
        "   • 추천 종목에서 ⭐ 버튼으로 등록\n"
        "   • 즐겨찾기 메뉴에서 실시간 가격 확인\n\n"
        "6️⃣ 🤖 에이전트 대화\n"
        "   • 오류 신고, 기능 요청 버튼 1클릭\n"
        "   • 피드백 자동 수집 → 다음 업데이트 반영\n\n"
        "7️⃣ 자동매매 안전장치\n"
        "   • 모의투자 모드만 자동매매 허용\n"
        "   • 실전투자 시 자동매매 차단\n\n"
        "8️⃣ 버전 통일\n"
        "   • 전체 시스템 v3.5로 통일\n\n"
        f"🕐 {now.strftime('%Y-%m-%d %H:%M')} KST"
    )
    await bot.send_message(chat_id=chat_id, text=update_msg)
    print("✅ 업데이트 알림 전송 완료")

    # ── 2. 장마감 리포트 (텍스트) ──
    from kstock.ingest.macro_client import MacroClient
    from kstock.store.sqlite import SQLiteStore

    db = SQLiteStore()
    macro_client = MacroClient(db)
    macro = await macro_client.get_snapshot()

    holdings = db.get_active_holdings()

    # 시장 상태 결론
    regime_kr = {
        "risk_on": "🟢 공격",
        "neutral": "🟡 중립",
        "risk_off": "🔴 방어",
    }.get(macro.regime, "⚪ 중립")

    if macro.regime == "risk_on":
        verdict = "📈 매수 기회 탐색"
    elif macro.regime == "risk_off":
        verdict = "🛡️ 관망/방어 권고"
    else:
        verdict = "⏸️ 선별적 접근"

    # 보유종목
    if holdings:
        total_eval = sum(h.get("current_price", 0) * h.get("quantity", 0) for h in holdings)
        total_invested = sum(h.get("buy_price", 0) * h.get("quantity", 0) for h in holdings)
        total_pnl = total_eval - total_invested
        total_rate = (total_pnl / total_invested * 100) if total_invested > 0 else 0
        pnl_sign = "+" if total_pnl >= 0 else ""
        portfolio_line = f"💰 내 포트폴리오: {pnl_sign}{total_pnl:,.0f}원 ({pnl_sign}{total_rate:.1f}%)"
    else:
        portfolio_line = "💰 포트폴리오: 보유종목 없음"

    date_str = now.strftime("%m/%d")
    report_msg = (
        f"📊 장 마감 리포트 {date_str}\n"
        f"{'━' * 22}\n\n"
        f"🎯 결론: {verdict}\n"
        f"시장: {regime_kr} | S&P {macro.spx_change_pct:+.2f}%\n\n"
        f"{portfolio_line}\n\n"
        f"📎 상세 분석은 PDF 첨부 확인"
    )
    await bot.send_message(chat_id=chat_id, text=report_msg)
    print("✅ 장마감 리포트 텍스트 전송 완료")

    # ── 3. PDF 리포트 생성 + 전송 ──
    try:
        from kstock.report.daily_pdf_report import generate_daily_pdf
        filepath = await generate_daily_pdf(
            macro_snapshot=macro,
            holdings=holdings,
            sell_plans=[],
            pulse_history=[],
        )
        if filepath:
            with open(filepath, "rb") as f:
                await bot.send_document(chat_id=chat_id, document=f)
            print(f"✅ PDF 리포트 전송 완료: {filepath}")
        else:
            print("⚠️ PDF 생성 실패 (None)")
    except Exception as e:
        print(f"⚠️ PDF 생성/전송 실패: {e}")
        # PDF 실패해도 텍스트는 이미 전송됨

    print("🎉 모든 전송 완료!")


if __name__ == "__main__":
    asyncio.run(main())
