"""v9.5.4 비주얼 브리핑 — matplotlib 기반 차트 이미지 생성.

포트폴리오 수익 차트, 섹터 배분, 매니저 성적 비교 등
텔레그램에 이미지로 전송하여 한눈에 파악 가능.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# ── 한글 폰트 설정 ───────────────────────────────────────────────

_FONT_CANDIDATES = [
    "Apple SD Gothic Neo",
    "Nanum Gothic",
    "NanumGothic",
    "Malgun Gothic",
    "NanumBarunGothic",
]


def _setup_korean_font():
    """한글 폰트 자동 감지 및 설정."""
    import matplotlib.font_manager as fm
    available = {f.name for f in fm.fontManager.ttflist}
    for font in _FONT_CANDIDATES:
        if font in available:
            plt.rcParams["font.family"] = font
            plt.rcParams["axes.unicode_minus"] = False
            logger.debug("Korean font set: %s", font)
            return
    # fallback
    plt.rcParams["axes.unicode_minus"] = False
    logger.warning("No Korean font found, charts may show boxes")

_setup_korean_font()

# ── 공통 스타일 ──────────────────────────────────────────────────

COLORS = {
    "primary": "#2196F3",
    "success": "#4CAF50",
    "danger": "#F44336",
    "warning": "#FF9800",
    "purple": "#9C27B0",
    "teal": "#009688",
    "bg": "#1a1a2e",
    "bg_light": "#16213e",
    "text": "#e0e0e0",
    "grid": "#333366",
    "accent1": "#00BCD4",
    "accent2": "#FF5722",
}

MANAGER_COLORS = {
    "scalp": "#FF5722",
    "swing": "#FF9800",
    "position": "#4CAF50",
    "long_term": "#2196F3",
}

MANAGER_LABELS = {
    "scalp": "리버모어\n(단타)",
    "swing": "오닐\n(스윙)",
    "position": "린치\n(중기)",
    "long_term": "버핏\n(장기)",
}


def _apply_dark_theme(fig, ax):
    """다크 테마 적용."""
    fig.patch.set_facecolor(COLORS["bg"])
    ax.set_facecolor(COLORS["bg_light"])
    ax.tick_params(colors=COLORS["text"])
    ax.xaxis.label.set_color(COLORS["text"])
    ax.yaxis.label.set_color(COLORS["text"])
    ax.title.set_color(COLORS["text"])
    for spine in ax.spines.values():
        spine.set_color(COLORS["grid"])
    ax.grid(True, alpha=0.15, color=COLORS["grid"])


def _fig_to_bytes(fig) -> bytes:
    """matplotlib Figure를 PNG 바이트로 변환."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ── 차트 1: 포트폴리오 수익 현황 ─────────────────────────────────


def generate_portfolio_chart(db) -> bytes | None:
    """보유종목별 수익률 + 전체 수익 바 차트."""
    try:
        holdings = db.get_active_holdings()
        if not holdings:
            return None

        names = []
        pnl_pcts = []
        values = []

        for h in holdings[:15]:
            name = h.get("name", "")[:6]
            pnl = h.get("pnl_pct", 0) or 0
            eval_amt = h.get("eval_amount", 0) or (
                h.get("current_price", 0) * h.get("quantity", 1)
            )
            names.append(name)
            pnl_pcts.append(float(pnl))
            values.append(float(eval_amt))

        if not names:
            return None

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        _apply_dark_theme(fig, ax1)
        _apply_dark_theme(fig, ax2)

        # 좌: 종목별 수익률 바 차트
        bar_colors = [COLORS["success"] if p >= 0 else COLORS["danger"] for p in pnl_pcts]
        bars = ax1.barh(names, pnl_pcts, color=bar_colors, edgecolor="none", height=0.6)
        ax1.set_xlabel("수익률 (%)")
        ax1.set_title("📊 종목별 수익률", fontsize=13, fontweight="bold")
        ax1.axvline(x=0, color=COLORS["text"], linewidth=0.8, alpha=0.5)

        # 수치 표시
        for bar, val in zip(bars, pnl_pcts):
            x_pos = bar.get_width()
            offset = 0.5 if x_pos >= 0 else -0.5
            ha = "left" if x_pos >= 0 else "right"
            ax1.text(x_pos + offset, bar.get_y() + bar.get_height() / 2,
                     f"{val:+.1f}%", va="center", ha=ha,
                     color=COLORS["text"], fontsize=9)

        # 우: 평가금액 비중 파이 차트
        if sum(values) > 0:
            pie_colors = [
                COLORS["primary"], COLORS["success"], COLORS["warning"],
                COLORS["purple"], COLORS["teal"], COLORS["accent1"],
                COLORS["accent2"], COLORS["danger"],
            ]
            # 작은 비중은 기타로 합치기
            if len(names) > 6:
                top_idx = sorted(range(len(values)), key=lambda i: values[i], reverse=True)[:5]
                pie_names = [names[i] for i in top_idx]
                pie_vals = [values[i] for i in top_idx]
                etc = sum(values) - sum(pie_vals)
                if etc > 0:
                    pie_names.append("기타")
                    pie_vals.append(etc)
            else:
                pie_names = names
                pie_vals = values

            wedges, texts, autotexts = ax2.pie(
                pie_vals, labels=pie_names, autopct="%1.1f%%",
                colors=pie_colors[:len(pie_vals)],
                textprops={"color": COLORS["text"], "fontsize": 9},
                pctdistance=0.75,
            )
            for at in autotexts:
                at.set_fontsize(8)
            ax2.set_title("💼 포트폴리오 비중", fontsize=13, fontweight="bold")

        # 전체 수익률 표시
        total_buy = sum(
            (h.get("buy_price", 0) or 0) * (h.get("quantity", 0) or 1)
            for h in holdings[:15]
        )
        total_eval = sum(values)
        if total_buy > 0:
            total_pnl = (total_eval - total_buy) / total_buy * 100
            color = COLORS["success"] if total_pnl >= 0 else COLORS["danger"]
            fig.suptitle(
                f"K-Quant 포트폴리오  |  전체 수익률: {total_pnl:+.1f}%  |  "
                f"{datetime.now(KST).strftime('%Y-%m-%d %H:%M')}",
                fontsize=11, color=color, fontweight="bold", y=0.98,
            )

        fig.tight_layout(rect=[0, 0, 1, 0.95])
        return _fig_to_bytes(fig)

    except Exception as e:
        logger.error("generate_portfolio_chart error: %s", e, exc_info=True)
        return None


# ── 차트 2: 매니저 성적 비교 ─────────────────────────────────────


def generate_manager_scorecard_chart(db) -> bytes | None:
    """4매니저 적중률 + 수익률 비교 레이더/바 차트."""
    try:
        with db._connect() as conn:
            rows = conn.execute(
                """SELECT manager_key, hit_rate, avg_return_5d,
                          avg_return_10d, avg_return_20d, weight_adj,
                          total_recs, evaluated_recs, hits
                   FROM manager_scorecard
                   ORDER BY calculated_at DESC LIMIT 8""",
            ).fetchall()

        if not rows:
            return None

        # 매니저별 최신 데이터만
        mgr_data = {}
        for r in rows:
            key = r["manager_key"]
            if key not in mgr_data:
                mgr_data[key] = dict(r)

        if len(mgr_data) < 2:
            return None

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        _apply_dark_theme(fig, ax1)
        _apply_dark_theme(fig, ax2)

        managers = list(mgr_data.keys())
        labels = [MANAGER_LABELS.get(m, m) for m in managers]
        colors = [MANAGER_COLORS.get(m, COLORS["primary"]) for m in managers]

        # 좌: 적중률 + 추천수 바 차트
        hit_rates = [mgr_data[m].get("hit_rate", 0) or 0 for m in managers]
        total_recs = [mgr_data[m].get("total_recs", 0) or 0 for m in managers]

        x = range(len(managers))
        bars1 = ax1.bar(x, hit_rates, color=colors, edgecolor="none", width=0.6)
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, fontsize=9)
        ax1.set_ylabel("적중률 (%)")
        ax1.set_title("🎯 매니저 적중률", fontsize=13, fontweight="bold")
        ax1.set_ylim(0, 100)
        ax1.axhline(y=50, color=COLORS["warning"], linewidth=1, alpha=0.5, linestyle="--")

        for bar, rate, recs in zip(bars1, hit_rates, total_recs):
            ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 2,
                     f"{rate:.0f}%\n({recs}건)",
                     ha="center", va="bottom", color=COLORS["text"], fontsize=9)

        # 우: 평균 수익률 비교 (5일/10일/20일)
        returns_5d = [mgr_data[m].get("avg_return_5d", 0) or 0 for m in managers]
        returns_10d = [mgr_data[m].get("avg_return_10d", 0) or 0 for m in managers]
        returns_20d = [mgr_data[m].get("avg_return_20d", 0) or 0 for m in managers]

        bar_width = 0.22
        x_arr = list(range(len(managers)))
        x1 = [xi - bar_width for xi in x_arr]
        x2 = x_arr
        x3 = [xi + bar_width for xi in x_arr]

        ax2.bar(x1, returns_5d, width=bar_width, label="5일 수익",
                color=COLORS["accent1"], edgecolor="none")
        ax2.bar(x2, returns_10d, width=bar_width, label="10일 수익",
                color=COLORS["primary"], edgecolor="none")
        ax2.bar(x3, returns_20d, width=bar_width, label="20일 수익",
                color=COLORS["purple"], edgecolor="none")

        ax2.set_xticks(x_arr)
        ax2.set_xticklabels(labels, fontsize=9)
        ax2.set_ylabel("평균 수익률 (%)")
        ax2.set_title("📈 매니저별 평균 수익률", fontsize=13, fontweight="bold")
        ax2.axhline(y=0, color=COLORS["text"], linewidth=0.8, alpha=0.5)
        ax2.legend(fontsize=8, facecolor=COLORS["bg_light"],
                   edgecolor=COLORS["grid"], labelcolor=COLORS["text"])

        fig.suptitle(
            f"K-Quant 매니저 성적표  |  "
            f"{datetime.now(KST).strftime('%Y-%m-%d')}",
            fontsize=11, color=COLORS["text"], fontweight="bold", y=0.98,
        )
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        return _fig_to_bytes(fig)

    except Exception as e:
        logger.error("generate_manager_scorecard_chart error: %s", e, exc_info=True)
        return None


# ── 차트 3: 섹터 모멘텀 대시보드 ─────────────────────────────────


def generate_sector_momentum_chart(db) -> bytes | None:
    """섹터별 모멘텀 + 포트폴리오 섹터 비중 차트."""
    try:
        snapshots = db.get_sector_snapshots(limit=1)
        if not snapshots:
            return None

        latest = snapshots[0]
        sectors_data = json.loads(latest.get("sectors_json", "[]"))
        portfolio_data = json.loads(latest.get("portfolio_json", "{}"))

        if not sectors_data:
            return None

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        _apply_dark_theme(fig, ax1)
        _apply_dark_theme(fig, ax2)

        # 좌: 섹터 모멘텀 히트맵 스타일 바 차트
        sector_names = []
        ret_1w = []
        ret_1m = []
        for s in sectors_data:
            if isinstance(s, dict):
                sector_names.append(s.get("sector", "")[:6])
                ret_1w.append(s.get("return_1w_pct", 0))
                ret_1m.append(s.get("return_1m_pct", 0))

        if sector_names:
            y_pos = range(len(sector_names))
            bar_width = 0.35

            y1 = [y - bar_width / 2 for y in y_pos]
            y2 = [y + bar_width / 2 for y in y_pos]

            colors_1w = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in ret_1w]
            colors_1m = [COLORS["accent1"] if v >= 0 else COLORS["accent2"] for v in ret_1m]

            ax1.barh(y1, ret_1w, height=bar_width, color=colors_1w,
                     label="1주", edgecolor="none")
            ax1.barh(y2, ret_1m, height=bar_width, color=colors_1m,
                     label="1개월", edgecolor="none", alpha=0.8)

            ax1.set_yticks(list(y_pos))
            ax1.set_yticklabels(sector_names, fontsize=9)
            ax1.set_xlabel("수익률 (%)")
            ax1.set_title("🔥 섹터 모멘텀", fontsize=13, fontweight="bold")
            ax1.axvline(x=0, color=COLORS["text"], linewidth=0.8, alpha=0.5)
            ax1.legend(fontsize=8, facecolor=COLORS["bg_light"],
                       edgecolor=COLORS["grid"], labelcolor=COLORS["text"])

        # 우: 포트폴리오 섹터 비중
        if portfolio_data and isinstance(portfolio_data, dict):
            p_sectors = list(portfolio_data.keys())
            p_weights = [portfolio_data[s] for s in p_sectors]
            p_labels = [f"{s[:6]}" for s in p_sectors]

            pie_colors = [
                COLORS["primary"], COLORS["success"], COLORS["warning"],
                COLORS["purple"], COLORS["teal"], COLORS["accent1"],
                COLORS["accent2"], COLORS["danger"],
            ]

            wedges, texts, autotexts = ax2.pie(
                p_weights, labels=p_labels, autopct="%1.0f%%",
                colors=pie_colors[:len(p_sectors)],
                textprops={"color": COLORS["text"], "fontsize": 9},
                startangle=90,
            )
            for at in autotexts:
                at.set_fontsize(8)
            ax2.set_title("💼 내 섹터 비중", fontsize=13, fontweight="bold")
        else:
            ax2.text(0.5, 0.5, "포트폴리오 섹터 데이터 없음",
                     ha="center", va="center", color=COLORS["text"], fontsize=12,
                     transform=ax2.transAxes)
            ax2.set_title("💼 내 섹터 비중", fontsize=13, fontweight="bold")

        fig.suptitle(
            f"K-Quant 섹터 분석  |  "
            f"{datetime.now(KST).strftime('%Y-%m-%d %H:%M')}",
            fontsize=11, color=COLORS["text"], fontweight="bold", y=0.98,
        )
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        return _fig_to_bytes(fig)

    except Exception as e:
        logger.error("generate_sector_momentum_chart error: %s", e, exc_info=True)
        return None


# ── 차트 4: 수익 추이 (포트폴리오 스냅샷 기반) ───────────────────


def generate_pnl_trend_chart(db) -> bytes | None:
    """포트폴리오 수익 추이 라인 차트 (portfolio_snapshots 기반)."""
    try:
        with db._connect() as conn:
            rows = conn.execute(
                """SELECT snapshot_date, total_value, total_pnl_pct
                   FROM portfolio_snapshots
                   ORDER BY snapshot_date DESC LIMIT 60""",
            ).fetchall()

        if not rows or len(rows) < 3:
            return None

        rows = list(reversed(rows))
        dates = []
        values = []
        pnls = []

        for r in rows:
            try:
                d = datetime.strptime(r["snapshot_date"], "%Y-%m-%d")
                dates.append(d)
                values.append(float(r["total_value"] or 0))
                pnls.append(float(r["total_pnl_pct"] or 0))
            except Exception:
                continue

        if len(dates) < 3:
            return None

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
        _apply_dark_theme(fig, ax1)
        _apply_dark_theme(fig, ax2)

        # 상: 평가금액 추이
        ax1.plot(dates, values, color=COLORS["primary"], linewidth=2)
        ax1.fill_between(dates, values, alpha=0.1, color=COLORS["primary"])
        ax1.set_ylabel("평가금액 (원)")
        ax1.set_title("💰 포트폴리오 추이", fontsize=13, fontweight="bold")
        ax1.yaxis.set_major_formatter(ticker.FuncFormatter(
            lambda x, _: f"{x/1e6:.0f}M" if x >= 1e6 else f"{x:,.0f}"
        ))

        # 하: 수익률 추이
        fill_colors = [COLORS["success"] if p >= 0 else COLORS["danger"] for p in pnls]
        ax2.bar(dates, pnls, color=fill_colors, width=1.0, edgecolor="none")
        ax2.axhline(y=0, color=COLORS["text"], linewidth=0.8, alpha=0.5)
        ax2.set_ylabel("수익률 (%)")
        ax2.set_title("📈 일별 수익률", fontsize=13, fontweight="bold")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))

        fig.suptitle(
            f"K-Quant 수익 추이  |  최근 {len(dates)}일  |  "
            f"{datetime.now(KST).strftime('%Y-%m-%d')}",
            fontsize=11, color=COLORS["text"], fontweight="bold", y=0.98,
        )
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        return _fig_to_bytes(fig)

    except Exception as e:
        logger.error("generate_pnl_trend_chart error: %s", e, exc_info=True)
        return None


# ── 전체 비주얼 브리핑 생성 ──────────────────────────────────────


async def generate_visual_briefing(db) -> list[tuple[str, bytes]]:
    """모든 차트를 생성하여 (caption, png_bytes) 리스트로 반환."""
    charts = []

    # 1. 포트폴리오 현황
    portfolio_img = generate_portfolio_chart(db)
    if portfolio_img:
        charts.append(("📊 포트폴리오 현황", portfolio_img))

    # 2. 수익 추이
    pnl_img = generate_pnl_trend_chart(db)
    if pnl_img:
        charts.append(("📈 수익 추이", pnl_img))

    # 3. 섹터 모멘텀
    sector_img = generate_sector_momentum_chart(db)
    if sector_img:
        charts.append(("🔄 섹터 분석", sector_img))

    # 4. 매니저 성적
    mgr_img = generate_manager_scorecard_chart(db)
    if mgr_img:
        charts.append(("🎯 매니저 성적표", mgr_img))

    return charts
