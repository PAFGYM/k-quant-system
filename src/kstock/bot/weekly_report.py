"""Weekly investment report generator with Google Docs integration.

Generates a comprehensive weekly report every Sunday at 19:00 KST,
saves it to Google Docs, and sends a summary with link via Telegram.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from kstock.core.tz import KST

logger = logging.getLogger(__name__)
USER_NAME = "주호님"

# Google API scopes
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]


# ---------------------------------------------------------------------------
# Google Docs client (lazy loaded)
# ---------------------------------------------------------------------------

def _get_google_credentials():
    """Load Google API credentials from file.

    Returns credentials object or None if not configured.
    """
    creds_path = os.getenv(
        "GOOGLE_CREDENTIALS_PATH", "config/google_credentials.json",
    )
    if not os.path.exists(creds_path):
        logger.info("Google credentials not found at %s", creds_path)
        return None

    try:
        from google.oauth2.service_account import Credentials
        return Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    except ImportError:
        logger.warning("google-auth not installed; Google Docs disabled")
        return None
    except Exception as e:
        logger.warning("Failed to load Google credentials: %s", e)
        return None


def _get_or_create_folder(drive_service, folder_name: str) -> str | None:
    """Get or create a Google Drive folder. Returns folder ID."""
    try:
        results = drive_service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces="drive",
            fields="files(id, name)",
        ).execute()
        files = results.get("files", [])
        if files:
            return files[0]["id"]

        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = drive_service.files().create(
            body=file_metadata, fields="id",
        ).execute()
        return folder.get("id")
    except Exception as e:
        logger.error("Failed to create/find Drive folder: %s", e)
        return None


def create_google_doc(
    title: str,
    content: str,
    folder_name: str = "",
) -> str | None:
    """Create a Google Doc with the given content and return the share URL.

    Returns None if Google API is not configured or fails.
    """
    creds = _get_google_credentials()
    if creds is None:
        return None

    if not folder_name:
        folder_name = os.getenv("GOOGLE_DRIVE_FOLDER_NAME", "K-Quant 주간보고서")

    try:
        from googleapiclient.discovery import build

        docs_service = build("docs", "v1", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        # Create document
        doc = docs_service.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]

        # Move to folder
        folder_id = _get_or_create_folder(drive_service, folder_name)
        if folder_id:
            drive_service.files().update(
                fileId=doc_id,
                addParents=folder_id,
                fields="id, parents",
            ).execute()

        # Insert content
        requests = [{
            "insertText": {
                "location": {"index": 1},
                "text": content,
            },
        }]
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests},
        ).execute()

        # Set sharing permissions (anyone with link can view)
        drive_service.permissions().create(
            fileId=doc_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

        return f"https://docs.google.com/document/d/{doc_id}/edit"

    except ImportError:
        logger.warning("google-api-python-client not installed")
        return None
    except Exception as e:
        logger.error("Failed to create Google Doc: %s", e)
        return None


# ---------------------------------------------------------------------------
# Report data collection
# ---------------------------------------------------------------------------

def _get_week_range(today: datetime | None = None) -> tuple[str, str, str]:
    """Get the week label and date range for the report.

    Returns (week_label, week_start, week_end).
    """
    if today is None:
        today = datetime.now(KST)
    # Find last Monday to Friday
    weekday = today.weekday()
    if weekday == 6:  # Sunday
        friday = today - timedelta(days=2)
    elif weekday == 5:  # Saturday
        friday = today - timedelta(days=1)
    else:
        friday = today
    monday = friday - timedelta(days=friday.weekday())

    month = monday.month
    week_of_month = (monday.day - 1) // 7 + 1
    week_label = f"{monday.year}년 {month}월 {week_of_month}주차"
    week_start = monday.strftime("%Y-%m-%d")
    week_end = friday.strftime("%Y-%m-%d")
    return week_label, week_start, week_end


def collect_weekly_data(db: Any) -> dict[str, Any]:
    """Collect all data needed for the weekly report from DB.

    Returns dict with keys: market, portfolio, holdings, reports,
    reco_performance, goal, events.
    """
    week_label, week_start, week_end = _get_week_range()

    data: dict[str, Any] = {
        "week_label": week_label,
        "week_start": week_start,
        "week_end": week_end,
        "generated_at": datetime.now(KST).strftime("%Y.%m.%d (%a) %H:%M"),
    }

    # Portfolio from last screenshot
    last_ss = db.get_last_screenshot()
    if last_ss:
        data["total_eval"] = last_ss.get("total_eval", 0)
        data["total_profit"] = last_ss.get("total_profit", 0)
        data["total_profit_pct"] = last_ss.get("total_profit_pct", 0)
        try:
            data["holdings"] = json.loads(last_ss.get("holdings_json", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            data["holdings"] = []
    else:
        data["total_eval"] = 0
        data["total_profit"] = 0
        data["total_profit_pct"] = 0
        data["holdings"] = []

    # Screenshot history for weekly change
    ss_history = db.get_screenshot_history(limit=5)
    if len(ss_history) >= 2:
        prev_eval = ss_history[1].get("total_eval", 0)
        current_eval = ss_history[0].get("total_eval", 0)
        data["weekly_change"] = current_eval - prev_eval
        data["weekly_change_pct"] = (
            round((current_eval - prev_eval) / prev_eval * 100, 1)
            if prev_eval > 0 else 0
        )
    else:
        data["weekly_change"] = 0
        data["weekly_change_pct"] = 0

    # Recent reports
    data["recent_reports"] = db.get_recent_reports(limit=10)

    # Recommendation performance
    active_recs = getattr(db, "get_active_recommendations", lambda: [])()
    data["active_recs"] = active_recs if isinstance(active_recs, list) else []

    completed_recs = getattr(db, "get_completed_recommendations", lambda l=20: [])()
    data["completed_recs"] = completed_recs if isinstance(completed_recs, list) else []

    # Goal progress
    try:
        from kstock.signal.aggressive_mode import compute_goal_progress
        current_asset = data.get("total_eval", 175_000_000) or 175_000_000
        progress = compute_goal_progress(current_asset)
        data["goal_progress"] = {
            "current_asset": progress.current_asset,
            "target_asset": progress.target_asset,
            "progress_pct": progress.progress_pct,
        }
    except Exception as e:
        logger.debug("collect_weekly_data goal_progress failed: %s", e)
        data["goal_progress"] = {
            "current_asset": data.get("total_eval", 0),
            "target_asset": 3_000_000_000,
            "progress_pct": 0,
        }

    # Portfolio horizons
    data["horizons"] = db.get_all_portfolio_horizons()

    # Short selling data for holdings
    short_data_map: dict[str, list] = {}
    margin_data_map: dict[str, list] = {}
    for h in data.get("holdings", []):
        ticker = h.get("ticker", "")
        if not ticker:
            continue
        try:
            short_data_map[ticker] = db.get_short_selling(ticker, days=7)
        except Exception as e:
            logger.debug("collect_weekly_data short_selling for %s: %s", ticker, e)
            short_data_map[ticker] = []
        try:
            margin_data_map[ticker] = db.get_margin_balance(ticker, days=7)
        except Exception as e:
            logger.debug("collect_weekly_data margin_balance for %s: %s", ticker, e)
            margin_data_map[ticker] = []
    data["short_data"] = short_data_map
    data["margin_data"] = margin_data_map

    # Overheated shorts
    try:
        data["overheated_shorts"] = db.get_overheated_shorts(min_ratio=20.0, days=7)
    except Exception as e:
        logger.debug("collect_weekly_data overheated_shorts: %s", e)
        data["overheated_shorts"] = []

    # Rebalance history
    try:
        data["rebalance_history"] = db.get_rebalance_history(limit=5)
    except Exception as e:
        logger.debug("collect_weekly_data rebalance_history: %s", e)
        data["rebalance_history"] = []

    # Future tech watchlist
    try:
        data["future_watchlist"] = db.get_future_watchlist()
    except Exception as e:
        logger.debug("collect_weekly_data future_watchlist: %s", e)
        data["future_watchlist"] = []

    # Future triggers
    future_triggers: dict[str, list] = {}
    for sk in ["autonomous_driving", "space_aerospace", "quantum_computing"]:
        try:
            future_triggers[sk] = db.get_future_triggers(sector=sk, days=7, limit=3)
        except Exception as e:
            logger.debug("collect_weekly_data future_triggers %s: %s", sk, e)
            future_triggers[sk] = []
    data["future_triggers"] = future_triggers

    # Seed positions
    try:
        data["seed_positions"] = db.get_seed_positions()
    except Exception as e:
        logger.debug("collect_weekly_data seed_positions: %s", e)
        data["seed_positions"] = []

    return data


# ---------------------------------------------------------------------------
# Report content generation
# ---------------------------------------------------------------------------

def generate_report_content(data: dict[str, Any]) -> str:
    """Generate the full weekly report text content."""
    week_label = data.get("week_label", "")
    week_start = data.get("week_start", "")
    week_end = data.get("week_end", "")
    generated_at = data.get("generated_at", "")

    lines: list[str] = []

    # Header
    lines.append("=" * 45)
    lines.append("K-Quant 주간 투자 보고서")
    lines.append(f"{week_label} ({week_start} ~ {week_end})")
    lines.append("=" * 45)
    lines.append("")
    lines.append(f"작성일: {generated_at}")
    lines.append("Powered by K-Quant v3.5 AI")
    lines.append("")

    # Section 1: Market Summary
    lines.append("-" * 40)
    lines.append("1. 주간 시장 요약")
    lines.append("-" * 40)
    lines.append("")
    lines.append("(시장 데이터는 실시간 연동 후 자동 채워집니다)")
    lines.append("")

    # Section 2: Portfolio Performance
    lines.append("-" * 40)
    lines.append(f"2. {USER_NAME} 포트폴리오 성과")
    lines.append("-" * 40)
    lines.append("")

    total_eval = data.get("total_eval", 0)
    total_profit = data.get("total_profit", 0)
    total_profit_pct = data.get("total_profit_pct", 0)
    weekly_change = data.get("weekly_change", 0)
    weekly_change_pct = data.get("weekly_change_pct", 0)

    lines.append(f"총 평가금액: {total_eval:,.0f}원")
    lines.append(f"주간 수익: {weekly_change:+,.0f}원 ({weekly_change_pct:+.1f}%)")
    lines.append(f"총 누적 수익: {total_profit:+,.0f}원 ({total_profit_pct:+.1f}%)")
    lines.append("")

    # Holdings
    holdings = data.get("holdings", [])
    horizons = {h["ticker"]: h.get("horizon", "") for h in data.get("horizons", [])}

    if holdings:
        lines.append("종목별 성과:")
        for h in holdings:
            name = h.get("name", "?")
            ticker = h.get("ticker", "")
            current_price = h.get("current_price", 0)
            profit_pct = h.get("profit_pct", 0)
            hz = horizons.get(ticker, "")
            hz_tag = f"  [{hz}]" if hz else ""
            lines.append(
                f"  {name}  {current_price:,.0f}원  {profit_pct:+.1f}%{hz_tag}"
            )
        lines.append("")

    # Goal progress
    goal = data.get("goal_progress", {})
    if goal:
        current = goal.get("current_asset", 0)
        progress_pct = goal.get("progress_pct", 0)
        bar_filled = int(progress_pct / 10)
        bar = "\u2588" * bar_filled + "\u2591" * (10 - bar_filled)
        lines.append(f"30억 목표 진행률: {bar} {progress_pct:.1f}% ({current / 100_000_000:.2f}억/30억)")
    lines.append("")

    # Section 3: Per-stock analysis
    lines.append("-" * 40)
    lines.append("3. 종목별 주간 분석")
    lines.append("-" * 40)
    lines.append("")

    for h in holdings:
        name = h.get("name", "?")
        ticker = h.get("ticker", "")
        profit_pct = h.get("profit_pct", 0)
        hz = horizons.get(ticker, "")
        hz_label = {"danta": "단타", "dangi": "단기", "junggi": "중기", "janggi": "장기"}.get(hz, "")
        lines.append(f"[{name}] {hz_label} 보유 | {profit_pct:+.1f}%")
        lines.append(f"  현재가: {h.get('current_price', 0):,.0f}원")
        lines.append("")

    # Section 4: Reports
    lines.append("-" * 40)
    lines.append("4. 증권사 리포트 요약 (이번 주 발행)")
    lines.append("-" * 40)
    lines.append("")

    reports = data.get("recent_reports", [])
    if reports:
        for r in reports[:5]:
            broker = r.get("broker", "")
            title = r.get("title", "")
            target = r.get("target_price", 0)
            opinion = r.get("opinion", "")
            date = r.get("date", "")
            target_str = f" 목표가 {target:,.0f}원" if target else ""
            lines.append(f"  {broker} - {title}")
            lines.append(f"    {opinion}{target_str} ({date})")
    else:
        lines.append("  이번 주 수집된 리포트가 없습니다.")
    lines.append("")

    # Section 5: Recommendation Performance
    lines.append("-" * 40)
    lines.append("5. K-Quant 추천 성과 (이번 주)")
    lines.append("-" * 40)
    lines.append("")

    completed = data.get("completed_recs", [])
    if completed:
        hits = sum(1 for r in completed if r.get("pnl_pct", 0) > 0)
        total = len(completed)
        hit_rate = round(hits / total * 100, 1) if total > 0 else 0
        avg_return = round(
            sum(r.get("pnl_pct", 0) for r in completed) / total, 1,
        ) if total > 0 else 0
        lines.append(f"  추천 적중률: {hit_rate}% ({hits}/{total})")
        lines.append(f"  평균 수익률: {avg_return:+.1f}%")
    else:
        lines.append("  이번 주 완료된 추천이 없습니다.")
    lines.append("")

    # Section 6: Next Week Outlook
    lines.append("-" * 40)
    lines.append("6. 다음 주 전망 + 전략")
    lines.append("-" * 40)
    lines.append("")
    lines.append("  (Claude AI 분석 결과가 채워집니다)")
    lines.append("")

    # Section 7: Goal Roadmap
    lines.append("-" * 40)
    lines.append("7. 30억 로드맵 진행 현황")
    lines.append("-" * 40)
    lines.append("")

    if goal:
        current_eok = goal.get("current_asset", 0) / 100_000_000
        lines.append(f"  현재 자산: {current_eok:.2f}억원")
        lines.append(f"  목표: 30억원")
        lines.append(f"  진행률: {goal.get('progress_pct', 0):.1f}%")
    lines.append("")

    # Section 8: Short selling trends
    lines.append("-" * 40)
    lines.append("8. 공매도 동향")
    lines.append("-" * 40)
    lines.append("")

    short_data = data.get("short_data", {})
    overheated = data.get("overheated_shorts", [])

    if overheated:
        seen_tickers: set[str] = set()
        lines.append("  공매도 과열 종목:")
        for oh in overheated[:5]:
            oh_ticker = oh.get("ticker", "")
            if oh_ticker in seen_tickers:
                continue
            seen_tickers.add(oh_ticker)
            lines.append(
                f"    {oh_ticker} - 비중 {oh.get('short_ratio', 0):.1f}% "
                f"잔고 {oh.get('short_balance', 0):,.0f}주"
            )
        lines.append("")

    if short_data:
        has_short = False
        for ticker, sd_list in short_data.items():
            if sd_list:
                has_short = True
                latest = sd_list[-1] if sd_list else {}
                h_name = ticker
                for h in holdings:
                    if h.get("ticker") == ticker:
                        h_name = h.get("name", ticker)
                        break
                ratio = latest.get("short_ratio", 0.0)
                balance = latest.get("short_balance", 0)
                emoji = "\U0001f534" if ratio >= 10 else "\u26aa"
                lines.append(
                    f"  {emoji} {h_name}: 비중 {ratio:.1f}% 잔고 {balance:,.0f}주"
                )
        if not has_short:
            lines.append("  공매도 데이터 수집 대기 중")
    else:
        lines.append("  공매도 데이터 수집 대기 중")
    lines.append("")

    # Section 9: Leverage trends
    lines.append("-" * 40)
    lines.append("9. 레버리지 동향")
    lines.append("-" * 40)
    lines.append("")

    margin_data = data.get("margin_data", {})
    if margin_data:
        has_margin = False
        for ticker, md_list in margin_data.items():
            if md_list:
                has_margin = True
                latest = md_list[-1] if md_list else {}
                h_name = ticker
                for h in holdings:
                    if h.get("ticker") == ticker:
                        h_name = h.get("name", ticker)
                        break
                credit_ratio = latest.get("credit_ratio", 0.0)
                credit_balance = latest.get("credit_balance", 0)
                emoji = "\U0001f534" if credit_ratio >= 5 else "\u26aa"
                lines.append(
                    f"  {emoji} {h_name}: 신용비율 {credit_ratio:.1f}% "
                    f"잔고 {credit_balance:,.0f}주"
                )
        if not has_margin:
            lines.append("  신용/레버리지 데이터 수집 대기 중")
    else:
        lines.append("  신용/레버리지 데이터 수집 대기 중")
    lines.append("")

    rebalance_hist = data.get("rebalance_history", [])
    if rebalance_hist:
        lines.append("  최근 리밸런싱 이벤트:")
        for rh in rebalance_hist[:3]:
            lines.append(
                f"    {rh.get('trigger_type', '')}: "
                f"{rh.get('description', '')}"
            )
        lines.append("")

    # Section 10: Future tech watchlist
    lines.append("-" * 40)
    lines.append("10. 미래기술 워치리스트 주간 동향")
    lines.append("-" * 40)
    lines.append("")

    sector_names = {
        "autonomous_driving": "자율주행",
        "space_aerospace": "우주항공",
        "quantum_computing": "양자컴퓨터",
    }
    future_watchlist = data.get("future_watchlist", [])
    future_triggers = data.get("future_triggers", {})
    seed_positions = data.get("seed_positions", [])

    for sk, sname in sector_names.items():
        lines.append(f"{sname}:")

        # Triggers
        triggers = future_triggers.get(sk, [])
        if triggers:
            lines.append(f"  주간 트리거: {triggers[0].get('title', '없음')}")
        else:
            lines.append("  주간 트리거: 없음")

        # Stocks in this sector
        sector_stocks = [fw for fw in future_watchlist if fw.get("sector") == sk]
        if sector_stocks:
            top = max(sector_stocks, key=lambda x: x.get("future_score", 0))
            lines.append(f"  주목 종목: {top.get('name', '?')} (스코어 {top.get('future_score', 0)}점)")
        else:
            lines.append("  주목 종목: 스코어 미산출")

        # Seed positions
        sector_seeds = [sp for sp in seed_positions if sp.get("sector") == sk]
        if sector_seeds:
            for sp in sector_seeds:
                lines.append(f"  씨앗 포지션: {sp.get('name', '?')}")
        else:
            lines.append("  씨앗 포지션: 없음")
        lines.append("")

    lines.append(f"미래기술 총 비중: - / 한도 15%")
    lines.append("")

    # Footer
    lines.append("=" * 45)
    lines.append("본 보고서는 K-Quant v3.5 AI가 자동 생성했습니다.")
    lines.append(f"투자의 최종 결정은 {USER_NAME}의 판단입니다.")
    lines.append("=" * 45)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram summary
# ---------------------------------------------------------------------------

def format_telegram_summary(data: dict[str, Any], doc_url: str | None = None) -> str:
    """Format the short Telegram message with report link."""
    week_start = data.get("week_start", "")
    week_end = data.get("week_end", "")
    weekly_change = data.get("weekly_change", 0)
    weekly_change_pct = data.get("weekly_change_pct", 0)
    total_profit = data.get("total_profit", 0)
    total_profit_pct = data.get("total_profit_pct", 0)
    goal = data.get("goal_progress", {})
    progress_pct = goal.get("progress_pct", 0)

    lines = [
        f"{USER_NAME}, 주간 투자 보고서가 준비되었습니다!",
        "",
        f"K-Quant 주간 보고서 ({week_start}~{week_end})",
        f"  주간 수익: {weekly_change:+,.0f}원 ({weekly_change_pct:+.1f}%)",
        f"  누적 수익: {total_profit:+,.0f}원 ({total_profit_pct:+.1f}%)",
        f"  30억 목표: {progress_pct:.1f}%",
    ]

    if doc_url:
        lines.append("")
        lines.append(f"구글 문서: {doc_url}")

    lines.append("")
    lines.append("좋은 한 주 보내세요!")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def generate_weekly_report(db: Any) -> tuple[str, str | None]:
    """Generate the weekly report, optionally save to Google Docs.

    Returns (telegram_message, doc_url or None).
    """
    data = collect_weekly_data(db)
    content = generate_report_content(data)

    week_label = data["week_label"]
    title = f"K-Quant 주간보고서 {week_label}"

    # Try Google Docs
    doc_url = create_google_doc(title, content)

    # Save to DB
    db.add_weekly_report(
        week_label=week_label,
        week_start=data["week_start"],
        week_end=data["week_end"],
        doc_url=doc_url or "",
        summary_json=json.dumps({
            "total_eval": data.get("total_eval", 0),
            "weekly_change": data.get("weekly_change", 0),
            "weekly_change_pct": data.get("weekly_change_pct", 0),
        }, ensure_ascii=False),
    )

    telegram_msg = format_telegram_summary(data, doc_url)
    return telegram_msg, doc_url
