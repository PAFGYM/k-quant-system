"""Technical analysis chart image generator for Telegram.

v1.0: 기본 캔들스틱 (MA + BB + RSI + Volume)
v9.2: 10개 차트 모드 확장
  #1 MACD 서브패널
  #2 수급 오버레이 (외국인/기관)
  #3 공매도 비율
  #4 주봉 + 매집 점수
  #5 다이버전스 마커 (RSI/MACD)
  #6 매수/매도 시그널 수평선
  #7 멀티타임프레임 (일봉 + 주봉)
  #8 한국형 리스크 게이지
  #9 섹터 비교
  #10 버블 밸류에이션 밴드
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 다크 테마 상수 ──────────────────────────────────────────
BG_COLOR = "#1a1a2e"
PANEL_COLOR = "#16213e"
TEXT_COLOR = "#e0e0e0"
GRID_COLOR = "#2a2a4a"
UP_COLOR = "#ff4757"      # 한국 시장: 빨강 = 상승
DOWN_COLOR = "#1e90ff"    # 파랑 = 하락
ACCENT_COLOR = "#ffa502"
GREEN_COLOR = "#7bed9f"
PURPLE_COLOR = "#a29bfe"
YELLOW_COLOR = "#ffd32a"
PINK_COLOR = "#ff6b81"


def _apply_dark_theme():
    """Apply dark theme to matplotlib rcParams."""
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": BG_COLOR,
        "axes.facecolor": PANEL_COLOR,
        "axes.edgecolor": GRID_COLOR,
        "axes.labelcolor": TEXT_COLOR,
        "xtick.color": TEXT_COLOR,
        "ytick.color": TEXT_COLOR,
        "grid.color": GRID_COLOR,
        "grid.alpha": 0.3,
        "text.color": TEXT_COLOR,
        "font.size": 9,
    })


def _draw_candlesticks(ax, x, open_, high, low, close, width=0.6):
    """Draw candlestick bars on axis."""
    for i in range(len(x)):
        o, c, h, l = open_.iloc[i], close.iloc[i], high.iloc[i], low.iloc[i]
        color = UP_COLOR if c >= o else DOWN_COLOR
        body_bottom = min(o, c)
        body_height = abs(c - o) if abs(c - o) > 0 else (h - l) * 0.01
        ax.bar(x[i], body_height, width, bottom=body_bottom,
               color=color, edgecolor=color, linewidth=0.5)
        ax.vlines(x[i], l, h, color=color, linewidth=0.6)


def _set_date_xaxis(ax, dates, x):
    """Set x-axis to date labels."""
    date_strs = [d.strftime("%m/%d") for d in dates]
    step = max(1, len(x) // 8)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(
        [date_strs[i] for i in range(0, len(date_strs), step)],
        rotation=45, fontsize=7,
    )


def _format_volume(v, _):
    """Format volume labels."""
    if abs(v) >= 1e9:
        return f"{v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


def _format_price(v, _):
    """Format price labels."""
    return f"{v:,.0f}"


# ═══════════════════════════════════════════════════════════════
# 데이터 수집
# ═══════════════════════════════════════════════════════════════

def _fetch_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    """Fetch OHLCV data via yfinance."""
    import yfinance as yf
    for suffix in (".KS", ".KQ"):
        symbol = f"{ticker}{suffix}"
        try:
            tf = yf.Ticker(symbol)
            hist = tf.history(period="6mo")
            if hist is not None and not hist.empty and len(hist) >= 10:
                return hist.tail(days).copy()
        except Exception:
            logger.debug("yfinance fetch failed for %s", symbol, exc_info=True)
    return pd.DataFrame()


def _ensure_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure OHLCV columns are numeric."""
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


def _compute_macd(close: pd.Series):
    """Compute MACD line, signal, histogram."""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal
    return macd_line, signal, histogram


def _compute_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Compute RSI."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(length).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(length).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ═══════════════════════════════════════════════════════════════
# 기존 기본 차트 (호환성 유지)
# ═══════════════════════════════════════════════════════════════

async def generate_stock_chart(
    ticker: str,
    name: str = "",
    days: int = 60,
) -> str:
    """Generate technical chart image (기본 3패널: 캔들+BB, 거래량, RSI)."""
    try:
        return await asyncio.to_thread(_generate_chart_sync, ticker, name, days)
    except Exception:
        logger.error("generate_stock_chart failed for %s", ticker, exc_info=True)
        return ""


def _generate_chart_sync(ticker: str, name: str, days: int) -> str:
    """Synchronous chart generation (runs in thread pool)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    df = _fetch_ohlcv(ticker, days)
    if df.empty:
        logger.warning("No data available for chart: %s", ticker)
        return ""
    df = _ensure_numeric(df)
    if len(df) < 5:
        return ""

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]
    volume = df["Volume"]
    dates = df.index

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    bb_mid = ma20
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    rsi = _compute_rsi(close)

    _apply_dark_theme()

    fig, (ax_price, ax_vol, ax_rsi) = plt.subplots(
        3, 1, figsize=(12, 8),
        gridspec_kw={"height_ratios": [5, 1.2, 1.5], "hspace": 0.08},
        sharex=True,
    )
    x = np.arange(len(df))

    # Bollinger Bands
    ax_price.fill_between(x, bb_upper.values, bb_lower.values,
                          alpha=0.08, color=PURPLE_COLOR, label="BB(20,2)")
    ax_price.plot(x, bb_upper.values, color=PURPLE_COLOR, linewidth=0.5, alpha=0.5)
    ax_price.plot(x, bb_lower.values, color=PURPLE_COLOR, linewidth=0.5, alpha=0.5)

    _draw_candlesticks(ax_price, x, open_, high, low, close)

    ax_price.plot(x, ma5.values, color=YELLOW_COLOR, linewidth=1.0, label="MA5", alpha=0.9)
    ax_price.plot(x, ma20.values, color=PINK_COLOR, linewidth=1.0, label="MA20", alpha=0.9)
    ax_price.plot(x, ma60.values, color=GREEN_COLOR, linewidth=1.0, label="MA60", alpha=0.9)

    cur_price = close.iloc[-1]
    ax_price.axhline(y=cur_price, color=ACCENT_COLOR, linewidth=0.8, linestyle="--", alpha=0.6)
    ax_price.annotate(f"  {cur_price:,.0f}", xy=(x[-1], cur_price),
                      fontsize=10, fontweight="bold", color=ACCENT_COLOR, va="center")

    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_price.set_ylabel("가격 (원)", fontsize=9)
    ax_price.grid(True, alpha=0.2)
    ax_price.yaxis.set_major_formatter(mticker.FuncFormatter(_format_price))

    display_name = name or ticker
    today_str = datetime.now().strftime("%Y.%m.%d")
    ax_price.set_title(f"{display_name} ({ticker}) - {today_str}",
                       fontsize=14, fontweight="bold", color=TEXT_COLOR, pad=12)

    vol_colors = [UP_COLOR if close.iloc[i] >= open_.iloc[i] else DOWN_COLOR
                  for i in range(len(df))]
    ax_vol.bar(x, volume.values, width=0.6, color=vol_colors, alpha=0.7)
    ax_vol.set_ylabel("거래량", fontsize=8)
    ax_vol.grid(True, alpha=0.2)
    ax_vol.yaxis.set_major_formatter(mticker.FuncFormatter(_format_volume))

    ax_rsi.plot(x, rsi.values, color=ACCENT_COLOR, linewidth=1.2, label="RSI(14)")
    ax_rsi.axhline(70, color=UP_COLOR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.axhline(30, color=DOWN_COLOR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.fill_between(x, 70, 100, alpha=0.05, color=UP_COLOR)
    ax_rsi.fill_between(x, 0, 30, alpha=0.05, color=DOWN_COLOR)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", fontsize=8)
    ax_rsi.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_rsi.grid(True, alpha=0.2)

    _set_date_xaxis(ax_rsi, dates, x)

    plt.tight_layout()
    out_path = f"/tmp/kquant_chart_{ticker}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("Chart saved: %s", out_path)
    return out_path


# ═══════════════════════════════════════════════════════════════
# #1 확장 차트: 캔들 + BB + 거래량 + MACD + RSI (4패널)
# ═══════════════════════════════════════════════════════════════

async def generate_full_chart(
    ticker: str,
    name: str = "",
    days: int = 60,
    supply_data: list[dict] | None = None,
    short_data: list[dict] | None = None,
    buy_price: float = 0,
    stop_price: float = 0,
    target_1: float = 0,
    target_2: float = 0,
) -> str:
    """확장 차트: 캔들+BB+수급, 거래량, MACD, RSI+다이버전스.

    #1 MACD, #2 수급, #3 공매도, #5 다이버전스, #6 매수/매도선 통합.
    """
    try:
        return await asyncio.to_thread(
            _generate_full_chart_sync,
            ticker, name, days, supply_data, short_data,
            buy_price, stop_price, target_1, target_2,
        )
    except Exception:
        logger.error("generate_full_chart failed for %s", ticker, exc_info=True)
        return ""


def _generate_full_chart_sync(
    ticker: str, name: str, days: int,
    supply_data: list[dict] | None,
    short_data: list[dict] | None,
    buy_price: float, stop_price: float,
    target_1: float, target_2: float,
) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    df = _fetch_ohlcv(ticker, days)
    if df.empty:
        return ""
    df = _ensure_numeric(df)
    if len(df) < 20:
        return ""

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]
    volume = df["Volume"]
    dates = df.index

    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    bb_std = close.rolling(20).std()
    bb_upper = ma20 + 2 * bb_std
    bb_lower = ma20 - 2 * bb_std
    rsi = _compute_rsi(close)
    macd_line, macd_signal, macd_hist = _compute_macd(close)

    _apply_dark_theme()

    # 패널 개수 결정
    panels = 4  # 캔들+BB, 거래량+수급, MACD, RSI
    ratios = [5, 1.5, 1.5, 1.5]

    has_short = short_data and len(short_data) >= 3
    if has_short:
        panels += 1
        ratios.append(1.2)

    fig, axes = plt.subplots(
        panels, 1, figsize=(12, 3 + panels * 1.8),
        gridspec_kw={"height_ratios": ratios, "hspace": 0.08},
        sharex=True,
    )

    x = np.arange(len(df))
    ax_price = axes[0]
    ax_vol = axes[1]
    ax_macd = axes[2]
    ax_rsi = axes[3]
    ax_short = axes[4] if has_short else None

    # ── 1. 캔들스틱 + BB + MA + 시그널선 ──
    ax_price.fill_between(x, bb_upper.values, bb_lower.values,
                          alpha=0.08, color=PURPLE_COLOR, label="BB(20,2)")
    ax_price.plot(x, bb_upper.values, color=PURPLE_COLOR, linewidth=0.5, alpha=0.5)
    ax_price.plot(x, bb_lower.values, color=PURPLE_COLOR, linewidth=0.5, alpha=0.5)

    _draw_candlesticks(ax_price, x, open_, high, low, close)

    ax_price.plot(x, ma5.values, color=YELLOW_COLOR, linewidth=1.0, label="MA5", alpha=0.9)
    ax_price.plot(x, ma20.values, color=PINK_COLOR, linewidth=1.0, label="MA20", alpha=0.9)
    ax_price.plot(x, ma60.values, color=GREEN_COLOR, linewidth=1.0, label="MA60", alpha=0.9)

    cur_price = close.iloc[-1]
    ax_price.axhline(y=cur_price, color=ACCENT_COLOR, linewidth=0.8, linestyle="--", alpha=0.6)
    ax_price.annotate(f"  {cur_price:,.0f}", xy=(x[-1], cur_price),
                      fontsize=10, fontweight="bold", color=ACCENT_COLOR, va="center")

    # #6 매수/매도 시그널 수평선
    if buy_price > 0:
        ax_price.axhline(y=buy_price, color="#2ed573", linewidth=1.2, linestyle="-.", alpha=0.8)
        ax_price.annotate(f" 매수 {buy_price:,.0f}", xy=(x[0], buy_price),
                          fontsize=8, color="#2ed573", va="center")
    if stop_price > 0:
        ax_price.axhline(y=stop_price, color="#ff4757", linewidth=1.2, linestyle="-.", alpha=0.8)
        ax_price.annotate(f" 손절 {stop_price:,.0f}", xy=(x[0], stop_price),
                          fontsize=8, color="#ff4757", va="center")
    if target_1 > 0:
        ax_price.axhline(y=target_1, color="#ffa502", linewidth=1.0, linestyle=":", alpha=0.7)
        ax_price.annotate(f" T1 {target_1:,.0f}", xy=(x[0], target_1),
                          fontsize=8, color="#ffa502", va="center")
    if target_2 > 0:
        ax_price.axhline(y=target_2, color="#ff6348", linewidth=1.0, linestyle=":", alpha=0.7)
        ax_price.annotate(f" T2 {target_2:,.0f}", xy=(x[0], target_2),
                          fontsize=8, color="#ff6348", va="center")

    # #5 다이버전스 마커 (가격 패널에 표시)
    _draw_divergence_markers(ax_price, x, close, rsi, macd_hist, high, low)

    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_price.set_ylabel("가격 (원)", fontsize=9)
    ax_price.grid(True, alpha=0.2)
    ax_price.yaxis.set_major_formatter(mticker.FuncFormatter(_format_price))

    display_name = name or ticker
    today_str = datetime.now().strftime("%Y.%m.%d")
    ax_price.set_title(f"{display_name} ({ticker}) - {today_str}  [확장 차트]",
                       fontsize=14, fontweight="bold", color=TEXT_COLOR, pad=12)

    # ── 2. 거래량 + 수급 오버레이 ──
    vol_colors = [UP_COLOR if close.iloc[i] >= open_.iloc[i] else DOWN_COLOR
                  for i in range(len(df))]
    ax_vol.bar(x, volume.values, width=0.6, color=vol_colors, alpha=0.5)
    ax_vol.set_ylabel("거래량", fontsize=8)
    ax_vol.grid(True, alpha=0.2)
    ax_vol.yaxis.set_major_formatter(mticker.FuncFormatter(_format_volume))

    # #2 수급 오버레이 (외국인/기관 순매수)
    if supply_data:
        _draw_supply_overlay(ax_vol, dates, supply_data)

    # ── 3. MACD ──
    macd_colors = [UP_COLOR if v >= 0 else DOWN_COLOR for v in macd_hist.values]
    ax_macd.bar(x, macd_hist.values, width=0.6, color=macd_colors, alpha=0.7)
    ax_macd.plot(x, macd_line.values, color="#00d2d3", linewidth=1.0, label="MACD")
    ax_macd.plot(x, macd_signal.values, color="#ff9ff3", linewidth=1.0, label="Signal")
    ax_macd.axhline(0, color=GRID_COLOR, linewidth=0.5)
    ax_macd.set_ylabel("MACD", fontsize=8)
    ax_macd.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_macd.grid(True, alpha=0.2)

    # MACD 골든/데드 크로스 마커
    for i in range(1, len(x)):
        if macd_line.iloc[i] > macd_signal.iloc[i] and macd_line.iloc[i - 1] <= macd_signal.iloc[i - 1]:
            ax_macd.annotate("G", xy=(x[i], macd_line.iloc[i]), fontsize=8,
                             color="#2ed573", fontweight="bold", ha="center", va="bottom")
        elif macd_line.iloc[i] < macd_signal.iloc[i] and macd_line.iloc[i - 1] >= macd_signal.iloc[i - 1]:
            ax_macd.annotate("D", xy=(x[i], macd_line.iloc[i]), fontsize=8,
                             color="#ff4757", fontweight="bold", ha="center", va="top")

    # ── 4. RSI ──
    ax_rsi.plot(x, rsi.values, color=ACCENT_COLOR, linewidth=1.2, label="RSI(14)")
    ax_rsi.axhline(70, color=UP_COLOR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.axhline(30, color=DOWN_COLOR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.fill_between(x, 70, 100, alpha=0.05, color=UP_COLOR)
    ax_rsi.fill_between(x, 0, 30, alpha=0.05, color=DOWN_COLOR)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", fontsize=8)
    ax_rsi.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_rsi.grid(True, alpha=0.2)

    # 현재 RSI 값 표시
    cur_rsi = rsi.iloc[-1]
    if not np.isnan(cur_rsi):
        rsi_color = UP_COLOR if cur_rsi > 70 else (DOWN_COLOR if cur_rsi < 30 else ACCENT_COLOR)
        ax_rsi.annotate(f" {cur_rsi:.0f}", xy=(x[-1], cur_rsi),
                        fontsize=9, fontweight="bold", color=rsi_color, va="center")

    # ── 5. 공매도 비율 (데이터 있을 때) ──
    if ax_short and short_data:
        _draw_short_selling_panel(ax_short, dates, short_data)

    # X축 날짜
    last_ax = axes[-1]
    _set_date_xaxis(last_ax, dates, x)

    plt.tight_layout()
    out_path = f"/tmp/kquant_full_{ticker}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("Full chart saved: %s", out_path)
    return out_path


def _draw_supply_overlay(ax_vol, dates, supply_data: list[dict]):
    """#2 수급 오버레이: 외국인/기관 누적 순매수를 거래량 패널 위에."""
    date_map = {}
    for row in supply_data:
        d = str(row.get("date", ""))[:10]
        date_map[d] = row

    foreign_vals = []
    inst_vals = []
    matched_x = []

    for i, dt in enumerate(dates):
        d_str = dt.strftime("%Y-%m-%d")
        if d_str in date_map:
            r = date_map[d_str]
            foreign_vals.append(float(r.get("foreign_net", 0) or 0))
            inst_vals.append(float(r.get("institution_net", 0) or 0))
            matched_x.append(i)

    if not matched_x:
        return

    ax2 = ax_vol.twinx()
    mx = np.array(matched_x)
    fv = np.array(foreign_vals)
    iv = np.array(inst_vals)

    # 누적 순매수
    f_cum = np.cumsum(fv)
    i_cum = np.cumsum(iv)

    ax2.plot(mx, f_cum, color="#ff6b6b", linewidth=1.3, label="외국인", alpha=0.9)
    ax2.plot(mx, i_cum, color="#48dbfb", linewidth=1.3, label="기관", alpha=0.9)
    ax2.axhline(0, color=GRID_COLOR, linewidth=0.5, alpha=0.3)

    ax2.legend(loc="upper right", fontsize=6, framealpha=0.3, edgecolor="none")
    ax2.set_ylabel("누적 순매수", fontsize=7, color="#888888")
    ax2.tick_params(axis="y", labelsize=6, colors="#888888")
    ax2.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v / 1e6:.0f}M" if abs(v) >= 1e6 else f"{v / 1e3:.0f}K")
    )


def _draw_short_selling_panel(ax, dates, short_data: list[dict]):
    """#3 공매도 비율 패널."""
    date_map = {}
    for row in short_data:
        d = str(row.get("date", ""))[:10]
        date_map[d] = row

    ratios = []
    matched_x = []
    for i, dt in enumerate(dates):
        d_str = dt.strftime("%Y-%m-%d")
        if d_str in date_map:
            r = date_map[d_str]
            ratio = float(r.get("short_ratio", 0) or 0)
            ratios.append(ratio)
            matched_x.append(i)

    if not matched_x:
        ax.set_visible(False)
        return

    mx = np.array(matched_x)
    rv = np.array(ratios)

    colors = ["#ff4757" if r >= 20 else ("#ffa502" if r >= 10 else "#48dbfb") for r in rv]
    ax.bar(mx, rv, width=0.6, color=colors, alpha=0.7)
    ax.axhline(20, color="#ff4757", linewidth=0.8, linestyle="--", alpha=0.6)
    ax.axhline(10, color="#ffa502", linewidth=0.6, linestyle=":", alpha=0.4)
    ax.fill_between(mx, 20, rv, where=rv >= 20, alpha=0.15, color="#ff4757")

    ax.set_ylabel("공매도%", fontsize=8)
    ax.grid(True, alpha=0.2)

    # 최신 비율 표시
    if rv[-1] >= 20:
        ax.annotate(f" {rv[-1]:.1f}% 과열!", xy=(mx[-1], rv[-1]),
                    fontsize=8, fontweight="bold", color="#ff4757", va="bottom")


def _draw_divergence_markers(ax, x, close, rsi, macd_hist, high, low):
    """#5 RSI/MACD 다이버전스 마커."""
    n = len(x)
    if n < 20:
        return

    window = 10

    for i in range(window, n - 1):
        # Bullish RSI divergence: 가격 신저가 but RSI 더 높음
        price_min_recent = low.iloc[i - window:i + 1].min()
        price_min_prev = low.iloc[max(0, i - 2 * window):i - window + 1].min()
        rsi_min_recent = rsi.iloc[i - window:i + 1].min()
        rsi_min_prev = rsi.iloc[max(0, i - 2 * window):i - window + 1].min()

        if (not np.isnan(rsi_min_recent) and not np.isnan(rsi_min_prev)
                and price_min_recent < price_min_prev * 0.98
                and rsi_min_recent > rsi_min_prev * 1.02
                and low.iloc[i] == price_min_recent):
            ax.annotate("", xy=(x[i], low.iloc[i]),
                        xytext=(x[i], low.iloc[i] * 0.97),
                        arrowprops=dict(arrowstyle="->", color="#2ed573", lw=1.5))
            ax.annotate("Bull", xy=(x[i], low.iloc[i] * 0.965),
                        fontsize=6, color="#2ed573", ha="center", fontweight="bold")

        # Bearish RSI divergence: 가격 신고가 but RSI 더 낮음
        price_max_recent = high.iloc[i - window:i + 1].max()
        price_max_prev = high.iloc[max(0, i - 2 * window):i - window + 1].max()
        rsi_max_recent = rsi.iloc[i - window:i + 1].max()
        rsi_max_prev = rsi.iloc[max(0, i - 2 * window):i - window + 1].max()

        if (not np.isnan(rsi_max_recent) and not np.isnan(rsi_max_prev)
                and price_max_recent > price_max_prev * 1.02
                and rsi_max_recent < rsi_max_prev * 0.98
                and high.iloc[i] == price_max_recent):
            ax.annotate("", xy=(x[i], high.iloc[i]),
                        xytext=(x[i], high.iloc[i] * 1.03),
                        arrowprops=dict(arrowstyle="->", color="#ff4757", lw=1.5))
            ax.annotate("Bear", xy=(x[i], high.iloc[i] * 1.035),
                        fontsize=6, color="#ff4757", ha="center", fontweight="bold")


# ═══════════════════════════════════════════════════════════════
# #4 주봉 차트 + 매집 점수
# ═══════════════════════════════════════════════════════════════

async def generate_weekly_chart(
    ticker: str,
    name: str = "",
    weeks: int = 26,
    accumulation_score: dict | None = None,
) -> str:
    """주봉 차트 + 매집/세력 점수 표시."""
    try:
        return await asyncio.to_thread(
            _generate_weekly_chart_sync, ticker, name, weeks, accumulation_score,
        )
    except Exception:
        logger.error("generate_weekly_chart failed for %s", ticker, exc_info=True)
        return ""


def _generate_weekly_chart_sync(
    ticker: str, name: str, weeks: int,
    accumulation_score: dict | None,
) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    # 6개월 일봉 데이터를 주봉으로 리샘플
    df = _fetch_ohlcv(ticker, weeks * 5 + 20)
    if df.empty or len(df) < 20:
        return ""
    df = _ensure_numeric(df)

    weekly = df.resample("W").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna()
    weekly = weekly.tail(weeks)

    if len(weekly) < 5:
        return ""

    close = weekly["Close"]
    high = weekly["High"]
    low = weekly["Low"]
    open_ = weekly["Open"]
    volume = weekly["Volume"]
    dates = weekly.index

    ma5 = close.rolling(5).mean()
    ma13 = close.rolling(13).mean()
    rsi = _compute_rsi(close)

    _apply_dark_theme()

    fig, (ax_price, ax_vol, ax_rsi) = plt.subplots(
        3, 1, figsize=(12, 8),
        gridspec_kw={"height_ratios": [5, 1.2, 1.5], "hspace": 0.08},
        sharex=True,
    )
    x = np.arange(len(weekly))

    _draw_candlesticks(ax_price, x, open_, high, low, close, width=0.7)
    ax_price.plot(x, ma5.values, color=YELLOW_COLOR, linewidth=1.0, label="5주선", alpha=0.9)
    ax_price.plot(x, ma13.values, color=PINK_COLOR, linewidth=1.0, label="13주선", alpha=0.9)

    cur_price = close.iloc[-1]
    ax_price.axhline(y=cur_price, color=ACCENT_COLOR, linewidth=0.8, linestyle="--", alpha=0.6)
    ax_price.annotate(f"  {cur_price:,.0f}", xy=(x[-1], cur_price),
                      fontsize=10, fontweight="bold", color=ACCENT_COLOR, va="center")

    # 매집 점수 표시
    if accumulation_score:
        total = accumulation_score.get("total", 0)
        pattern = accumulation_score.get("pattern", "")
        score_color = "#2ed573" if total >= 70 else (ACCENT_COLOR if total >= 40 else "#888888")
        score_text = f"매집점수: {total}/100"
        if pattern:
            score_text += f" | {pattern}"
        ax_price.text(
            0.98, 0.95, score_text,
            transform=ax_price.transAxes, fontsize=10,
            fontweight="bold", color=score_color,
            ha="right", va="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=PANEL_COLOR, alpha=0.8, edgecolor=score_color),
        )

    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_price.set_ylabel("가격 (원)", fontsize=9)
    ax_price.grid(True, alpha=0.2)
    ax_price.yaxis.set_major_formatter(mticker.FuncFormatter(_format_price))

    display_name = name or ticker
    ax_price.set_title(f"{display_name} ({ticker}) - 주봉 차트",
                       fontsize=14, fontweight="bold", color=TEXT_COLOR, pad=12)

    vol_colors = [UP_COLOR if close.iloc[i] >= open_.iloc[i] else DOWN_COLOR
                  for i in range(len(weekly))]
    ax_vol.bar(x, volume.values, width=0.7, color=vol_colors, alpha=0.7)
    ax_vol.set_ylabel("거래량", fontsize=8)
    ax_vol.grid(True, alpha=0.2)
    ax_vol.yaxis.set_major_formatter(mticker.FuncFormatter(_format_volume))

    ax_rsi.plot(x, rsi.values, color=ACCENT_COLOR, linewidth=1.2, label="RSI(14)")
    ax_rsi.axhline(70, color=UP_COLOR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.axhline(30, color=DOWN_COLOR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", fontsize=8)
    ax_rsi.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_rsi.grid(True, alpha=0.2)

    date_strs = [d.strftime("%m/%d") for d in dates]
    step = max(1, len(x) // 8)
    ax_rsi.set_xticks(x[::step])
    ax_rsi.set_xticklabels([date_strs[i] for i in range(0, len(date_strs), step)],
                           rotation=45, fontsize=7)

    plt.tight_layout()
    out_path = f"/tmp/kquant_weekly_{ticker}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("Weekly chart saved: %s", out_path)
    return out_path


# ═══════════════════════════════════════════════════════════════
# #7 멀티타임프레임 (일봉 + 주봉 나란히)
# ═══════════════════════════════════════════════════════════════

async def generate_mtf_chart(
    ticker: str,
    name: str = "",
) -> str:
    """멀티타임프레임 차트: 일봉(60일) + 주봉(26주) 2열."""
    try:
        return await asyncio.to_thread(_generate_mtf_chart_sync, ticker, name)
    except Exception:
        logger.error("generate_mtf_chart failed for %s", ticker, exc_info=True)
        return ""


def _generate_mtf_chart_sync(ticker: str, name: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    df_raw = _fetch_ohlcv(ticker, 180)
    if df_raw.empty or len(df_raw) < 30:
        return ""
    df_raw = _ensure_numeric(df_raw)

    # 일봉 (60일)
    df_daily = df_raw.tail(60)
    # 주봉 (26주)
    df_weekly = df_raw.resample("W").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna().tail(26)

    if len(df_daily) < 10 or len(df_weekly) < 5:
        return ""

    _apply_dark_theme()
    fig, ((ax_d_price, ax_w_price), (ax_d_vol, ax_w_vol)) = plt.subplots(
        2, 2, figsize=(16, 8),
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08, "wspace": 0.15},
    )

    # 일봉
    xd = np.arange(len(df_daily))
    _draw_candlesticks(ax_d_price, xd, df_daily["Open"], df_daily["High"],
                       df_daily["Low"], df_daily["Close"])
    ma20d = df_daily["Close"].rolling(20).mean()
    ax_d_price.plot(xd, ma20d.values, color=PINK_COLOR, linewidth=1.0, label="MA20")
    ax_d_price.set_title(f"{name or ticker} - 일봉 (60일)",
                         fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax_d_price.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_d_price.grid(True, alpha=0.2)
    ax_d_price.yaxis.set_major_formatter(mticker.FuncFormatter(_format_price))

    vc_d = [UP_COLOR if df_daily["Close"].iloc[i] >= df_daily["Open"].iloc[i] else DOWN_COLOR
            for i in range(len(df_daily))]
    ax_d_vol.bar(xd, df_daily["Volume"].values, width=0.6, color=vc_d, alpha=0.7)
    ax_d_vol.yaxis.set_major_formatter(mticker.FuncFormatter(_format_volume))
    ax_d_vol.grid(True, alpha=0.2)
    _set_date_xaxis(ax_d_vol, df_daily.index, xd)

    # 주봉
    xw = np.arange(len(df_weekly))
    _draw_candlesticks(ax_w_price, xw, df_weekly["Open"], df_weekly["High"],
                       df_weekly["Low"], df_weekly["Close"], width=0.7)
    ma13w = df_weekly["Close"].rolling(13).mean()
    ax_w_price.plot(xw, ma13w.values, color=PINK_COLOR, linewidth=1.0, label="13주선")
    ax_w_price.set_title(f"{name or ticker} - 주봉 (26주)",
                         fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax_w_price.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_w_price.grid(True, alpha=0.2)
    ax_w_price.yaxis.set_major_formatter(mticker.FuncFormatter(_format_price))

    vc_w = [UP_COLOR if df_weekly["Close"].iloc[i] >= df_weekly["Open"].iloc[i] else DOWN_COLOR
            for i in range(len(df_weekly))]
    ax_w_vol.bar(xw, df_weekly["Volume"].values, width=0.7, color=vc_w, alpha=0.7)
    ax_w_vol.yaxis.set_major_formatter(mticker.FuncFormatter(_format_volume))
    ax_w_vol.grid(True, alpha=0.2)
    w_dates = [d.strftime("%m/%d") for d in df_weekly.index]
    step_w = max(1, len(xw) // 6)
    ax_w_vol.set_xticks(xw[::step_w])
    ax_w_vol.set_xticklabels([w_dates[i] for i in range(0, len(w_dates), step_w)],
                             rotation=45, fontsize=7)

    plt.tight_layout()
    out_path = f"/tmp/kquant_mtf_{ticker}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("MTF chart saved: %s", out_path)
    return out_path


# ═══════════════════════════════════════════════════════════════
# #8 한국형 리스크 게이지
# ═══════════════════════════════════════════════════════════════

async def generate_risk_gauge(
    risk_score: int = 0,
    risk_level: str = "안전",
    factors: list[dict] | None = None,
) -> str:
    """한국형 리스크 게이지 차트 (0-100 다이얼 + 요인별 바)."""
    try:
        return await asyncio.to_thread(
            _generate_risk_gauge_sync, risk_score, risk_level, factors,
        )
    except Exception:
        logger.error("generate_risk_gauge failed", exc_info=True)
        return ""


def _generate_risk_gauge_sync(
    risk_score: int, risk_level: str, factors: list[dict] | None,
) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Wedge, FancyArrowPatch

    _apply_dark_theme()

    has_factors = bool(factors)
    if has_factors:
        fig, (ax_gauge, ax_factors) = plt.subplots(
            1, 2, figsize=(14, 5),
            gridspec_kw={"width_ratios": [1, 1.2]},
        )
    else:
        fig, ax_gauge = plt.subplots(1, 1, figsize=(7, 5))

    # 게이지 (반원)
    ax_gauge.set_xlim(-1.3, 1.3)
    ax_gauge.set_ylim(-0.3, 1.3)
    ax_gauge.set_aspect("equal")
    ax_gauge.axis("off")

    # 배경 반원 구간
    zones = [
        (0, 25, "#2ed573", "안전"),
        (25, 50, "#ffa502", "주의"),
        (50, 75, "#ff6348", "위험"),
        (75, 100, "#ff4757", "극위험"),
    ]
    for lo, hi, color, label in zones:
        theta1 = 180 - hi * 180 / 100
        theta2 = 180 - lo * 180 / 100
        wedge = Wedge((0, 0), 1.0, theta1, theta2, width=0.25, color=color, alpha=0.7)
        ax_gauge.add_patch(wedge)

    # 바늘
    angle = math.radians(180 - risk_score * 180 / 100)
    needle_x = 0.8 * math.cos(angle)
    needle_y = 0.8 * math.sin(angle)
    ax_gauge.annotate(
        "", xy=(needle_x, needle_y), xytext=(0, 0),
        arrowprops=dict(arrowstyle="-|>", color=TEXT_COLOR, lw=2),
    )
    ax_gauge.plot(0, 0, "o", color=TEXT_COLOR, markersize=8)

    # 점수/레벨 텍스트
    score_color = "#2ed573" if risk_score < 25 else ("#ffa502" if risk_score < 50 else ("#ff6348" if risk_score < 75 else "#ff4757"))
    ax_gauge.text(0, -0.15, f"{risk_score}", fontsize=36, fontweight="bold",
                  color=score_color, ha="center", va="center")
    ax_gauge.text(0, -0.35, risk_level, fontsize=14, color=score_color,
                  ha="center", va="center")
    ax_gauge.set_title("한국형 리스크 게이지", fontsize=14, fontweight="bold",
                       color=TEXT_COLOR, pad=12)

    # 구간 라벨
    for lo, hi, color, label in zones:
        mid = (lo + hi) / 2
        a = math.radians(180 - mid * 180 / 100)
        lx = 1.15 * math.cos(a)
        ly = 1.15 * math.sin(a)
        ax_gauge.text(lx, ly, label, fontsize=7, color=color,
                      ha="center", va="center", fontweight="bold")

    # 요인별 바 차트
    if has_factors and factors:
        factor_names = [f.get("name", "?") for f in factors]
        factor_scores = [float(f.get("score", 0)) for f in factors]
        factor_max = [float(f.get("max_score", 15)) for f in factors]

        y_pos = np.arange(len(factor_names))
        colors = []
        for s, m in zip(factor_scores, factor_max):
            ratio = s / m if m > 0 else 0
            if ratio >= 0.7:
                colors.append("#ff4757")
            elif ratio >= 0.4:
                colors.append("#ffa502")
            else:
                colors.append("#2ed573")

        ax_factors.barh(y_pos, factor_scores, color=colors, alpha=0.8, height=0.6)
        # 최대값 배경
        ax_factors.barh(y_pos, factor_max, color=GRID_COLOR, alpha=0.2, height=0.6)

        ax_factors.set_yticks(y_pos)
        ax_factors.set_yticklabels(factor_names, fontsize=9)
        ax_factors.set_xlabel("리스크 점수", fontsize=9)
        ax_factors.set_title("요인별 리스크", fontsize=12, fontweight="bold",
                             color=TEXT_COLOR)
        ax_factors.grid(True, alpha=0.2, axis="x")
        ax_factors.invert_yaxis()

        # 점수 라벨
        for i, (s, m) in enumerate(zip(factor_scores, factor_max)):
            ax_factors.text(s + 0.3, i, f"{s:.0f}/{m:.0f}",
                            fontsize=8, va="center", color=TEXT_COLOR)

    plt.tight_layout()
    out_path = "/tmp/kquant_risk_gauge.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("Risk gauge saved: %s", out_path)
    return out_path


# ═══════════════════════════════════════════════════════════════
# #9 섹터 비교 차트
# ═══════════════════════════════════════════════════════════════

async def generate_sector_comparison(
    stocks: list[dict],
    title: str = "섹터 비교",
) -> str:
    """섹터 내 종목 비교 차트 (상대 수익률 + RSI).

    Args:
        stocks: [{"ticker": "005930", "name": "삼성전자", "returns_3m": 5.2, "rsi": 55}, ...]
    """
    try:
        return await asyncio.to_thread(
            _generate_sector_comparison_sync, stocks, title,
        )
    except Exception:
        logger.error("generate_sector_comparison failed", exc_info=True)
        return ""


def _generate_sector_comparison_sync(stocks: list[dict], title: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not stocks or len(stocks) < 2:
        return ""

    _apply_dark_theme()

    fig, (ax_ret, ax_rsi) = plt.subplots(
        1, 2, figsize=(14, 6),
        gridspec_kw={"wspace": 0.3},
    )

    names = [s.get("name", s.get("ticker", "?")) for s in stocks]
    returns = [float(s.get("returns_3m", 0)) for s in stocks]
    rsis = [float(s.get("rsi", 50)) for s in stocks]

    y_pos = np.arange(len(names))

    # 3개월 수익률 바
    ret_colors = [UP_COLOR if r >= 0 else DOWN_COLOR for r in returns]
    ax_ret.barh(y_pos, returns, color=ret_colors, alpha=0.8, height=0.6)
    ax_ret.axvline(0, color=GRID_COLOR, linewidth=0.5)
    ax_ret.set_yticks(y_pos)
    ax_ret.set_yticklabels(names, fontsize=9)
    ax_ret.set_xlabel("3개월 수익률 (%)", fontsize=9)
    ax_ret.set_title("상대 수익률", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax_ret.grid(True, alpha=0.2, axis="x")
    ax_ret.invert_yaxis()

    for i, r in enumerate(returns):
        ax_ret.text(r + (0.5 if r >= 0 else -0.5), i,
                    f"{r:+.1f}%", fontsize=8, va="center",
                    ha="left" if r >= 0 else "right", color=TEXT_COLOR)

    # RSI 바
    rsi_colors = []
    for r in rsis:
        if r > 70:
            rsi_colors.append(UP_COLOR)
        elif r < 30:
            rsi_colors.append(DOWN_COLOR)
        else:
            rsi_colors.append(ACCENT_COLOR)

    ax_rsi.barh(y_pos, rsis, color=rsi_colors, alpha=0.8, height=0.6)
    ax_rsi.axvline(70, color=UP_COLOR, linewidth=0.8, linestyle="--", alpha=0.5)
    ax_rsi.axvline(30, color=DOWN_COLOR, linewidth=0.8, linestyle="--", alpha=0.5)
    ax_rsi.set_xlim(0, 100)
    ax_rsi.set_yticks(y_pos)
    ax_rsi.set_yticklabels(names, fontsize=9)
    ax_rsi.set_xlabel("RSI(14)", fontsize=9)
    ax_rsi.set_title("과매수/과매도", fontsize=12, fontweight="bold", color=TEXT_COLOR)
    ax_rsi.grid(True, alpha=0.2, axis="x")
    ax_rsi.invert_yaxis()

    for i, r in enumerate(rsis):
        ax_rsi.text(r + 1, i, f"{r:.0f}", fontsize=8, va="center", color=TEXT_COLOR)

    fig.suptitle(title, fontsize=14, fontweight="bold", color=TEXT_COLOR, y=1.02)
    plt.tight_layout()
    out_path = "/tmp/kquant_sector_compare.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("Sector comparison saved: %s", out_path)
    return out_path


# ═══════════════════════════════════════════════════════════════
# #10 버블 밸류에이션 밴드
# ═══════════════════════════════════════════════════════════════

async def generate_valuation_band(
    ticker: str,
    name: str = "",
    days: int = 120,
    per: float = 0,
    sector_per: float = 0,
    fair_price: float = 0,
) -> str:
    """가격 차트 + PER 기반 적정가 밴드 오버레이."""
    try:
        return await asyncio.to_thread(
            _generate_valuation_band_sync,
            ticker, name, days, per, sector_per, fair_price,
        )
    except Exception:
        logger.error("generate_valuation_band failed for %s", ticker, exc_info=True)
        return ""


def _generate_valuation_band_sync(
    ticker: str, name: str, days: int,
    per: float, sector_per: float, fair_price: float,
) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    df = _fetch_ohlcv(ticker, days)
    if df.empty or len(df) < 20:
        return ""
    df = _ensure_numeric(df)

    close = df["Close"]
    dates = df.index
    x = np.arange(len(df))
    cur_price = close.iloc[-1]

    _apply_dark_theme()
    fig, ax = plt.subplots(1, 1, figsize=(12, 6))

    # 가격 라인
    ax.plot(x, close.values, color=ACCENT_COLOR, linewidth=1.5, label=f"주가 {cur_price:,.0f}")

    # 밸류에이션 밴드 (EPS 기반)
    if per > 0 and cur_price > 0:
        eps = cur_price / per
        bands = [
            (eps * 8, "#1e90ff", "PER 8x (저평가)"),
            (eps * 12, "#48dbfb", "PER 12x"),
            (eps * 15, "#ffa502", "PER 15x (적정)"),
            (eps * 20, "#ff6348", "PER 20x"),
            (eps * 25, "#ff4757", "PER 25x (고평가)"),
        ]
        for band_price, color, label in bands:
            ax.axhline(y=band_price, color=color, linewidth=0.8, linestyle=":", alpha=0.6)
            ax.annotate(f" {label} ({band_price:,.0f})",
                        xy=(x[-1], band_price), fontsize=7,
                        color=color, va="center")

        # 저평가/고평가 영역 쉐이딩
        ax.fill_between(x, eps * 8, eps * 12, alpha=0.05, color="#1e90ff")
        ax.fill_between(x, eps * 20, eps * 25, alpha=0.05, color="#ff4757")

    # 적정가 표시
    if fair_price > 0:
        ax.axhline(y=fair_price, color="#2ed573", linewidth=1.5, linestyle="-.", alpha=0.8)
        ax.annotate(f" 적정가 {fair_price:,.0f}", xy=(x[0], fair_price),
                    fontsize=9, fontweight="bold", color="#2ed573", va="center")

    # 섹터 PER 대비 표시
    if sector_per > 0 and per > 0:
        ratio = per / sector_per
        if ratio > 1.3:
            valuation_text = f"PER {per:.1f}x (섹터 {sector_per:.1f}x 대비 고평가)"
            val_color = "#ff4757"
        elif ratio < 0.7:
            valuation_text = f"PER {per:.1f}x (섹터 {sector_per:.1f}x 대비 저평가)"
            val_color = "#2ed573"
        else:
            valuation_text = f"PER {per:.1f}x (섹터 {sector_per:.1f}x 대비 적정)"
            val_color = ACCENT_COLOR
        ax.text(0.02, 0.05, valuation_text, transform=ax.transAxes,
                fontsize=10, color=val_color, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor=PANEL_COLOR,
                          alpha=0.8, edgecolor=val_color))

    ax.legend(loc="upper left", fontsize=8, framealpha=0.3, edgecolor="none")
    ax.set_ylabel("가격 (원)", fontsize=9)
    ax.grid(True, alpha=0.2)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_format_price))

    display_name = name or ticker
    ax.set_title(f"{display_name} ({ticker}) - 밸류에이션 밴드",
                 fontsize=14, fontweight="bold", color=TEXT_COLOR, pad=12)

    _set_date_xaxis(ax, dates, x)

    plt.tight_layout()
    out_path = f"/tmp/kquant_valuation_{ticker}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=BG_COLOR, edgecolor="none")
    plt.close(fig)
    logger.info("Valuation band saved: %s", out_path)
    return out_path
