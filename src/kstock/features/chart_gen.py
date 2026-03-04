"""Technical analysis chart image generator for Telegram.

Generates candlestick charts with volume, moving averages, RSI, and
Bollinger Bands using a dark theme suitable for Telegram photo messages.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


async def generate_stock_chart(
    ticker: str,
    name: str = "",
    days: int = 60,
) -> str:
    """Generate technical chart image.

    Args:
        ticker: Korean stock ticker code (e.g. "005930").
        name: Stock name for title (e.g. "삼성전자").
        days: Number of trading days to display.

    Returns:
        Path to PNG file (in /tmp/), or empty string on failure.
    """
    try:
        return await asyncio.to_thread(_generate_chart_sync, ticker, name, days)
    except Exception:
        logger.error("generate_stock_chart failed for %s", ticker, exc_info=True)
        return ""


def _fetch_ohlcv(ticker: str, days: int) -> pd.DataFrame:
    """Fetch OHLCV data via yfinance. Returns DataFrame with OHLCV columns."""
    import yfinance as yf

    # Korean stocks use .KS (KOSPI) or .KQ (KOSDAQ) suffix.
    # Try .KS first, fall back to .KQ if empty.
    for suffix in (".KS", ".KQ"):
        symbol = f"{ticker}{suffix}"
        try:
            tf = yf.Ticker(symbol)
            # Fetch extra data so we have enough after trimming
            hist = tf.history(period="6mo")
            if hist is not None and not hist.empty and len(hist) >= 10:
                return hist.tail(days).copy()
        except Exception:
            logger.debug("yfinance fetch failed for %s", symbol, exc_info=True)

    return pd.DataFrame()


def _generate_chart_sync(ticker: str, name: str, days: int) -> str:
    """Synchronous chart generation (runs in thread pool)."""
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    from matplotlib.patches import FancyBboxPatch

    # ── 데이터 수집 ──
    df = _fetch_ohlcv(ticker, days)
    if df.empty:
        logger.warning("No data available for chart: %s", ticker)
        return ""

    # Ensure numeric columns
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 5:
        return ""

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_ = df["Open"]
    volume = df["Volume"]
    dates = df.index

    # ── 기술적 지표 계산 ──
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # Bollinger Bands (20-day, 2 std)
    bb_mid = ma20
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # RSI(14)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # ── 다크 테마 설정 ──
    bg_color = "#1a1a2e"
    panel_color = "#16213e"
    text_color = "#e0e0e0"
    grid_color = "#2a2a4a"
    up_color = "#ff4757"     # 한국 시장: 빨강 = 상승
    down_color = "#1e90ff"   # 파랑 = 하락

    plt.rcParams.update({
        "figure.facecolor": bg_color,
        "axes.facecolor": panel_color,
        "axes.edgecolor": grid_color,
        "axes.labelcolor": text_color,
        "xtick.color": text_color,
        "ytick.color": text_color,
        "grid.color": grid_color,
        "grid.alpha": 0.3,
        "text.color": text_color,
        "font.size": 9,
    })

    # ── 차트 레이아웃 (3 서브플롯) ──
    fig, (ax_price, ax_vol, ax_rsi) = plt.subplots(
        3, 1, figsize=(12, 8),
        gridspec_kw={"height_ratios": [5, 1.2, 1.5], "hspace": 0.08},
        sharex=True,
    )

    # x-axis as integer indices for even spacing
    x = np.arange(len(df))

    # ── 1. 캔들스틱 + 이동평균 + 볼린저 밴드 ──
    # Bollinger Bands shading
    ax_price.fill_between(
        x, bb_upper.values, bb_lower.values,
        alpha=0.08, color="#a29bfe", label="BB(20,2)",
    )
    ax_price.plot(x, bb_upper.values, color="#a29bfe", linewidth=0.5, alpha=0.5)
    ax_price.plot(x, bb_lower.values, color="#a29bfe", linewidth=0.5, alpha=0.5)

    # Candlesticks
    width = 0.6
    for i in range(len(df)):
        o, c, h, l = open_.iloc[i], close.iloc[i], high.iloc[i], low.iloc[i]
        color = up_color if c >= o else down_color

        # Body
        body_bottom = min(o, c)
        body_height = abs(c - o) if abs(c - o) > 0 else (h - l) * 0.01
        ax_price.bar(x[i], body_height, width, bottom=body_bottom,
                     color=color, edgecolor=color, linewidth=0.5)
        # Wicks
        ax_price.vlines(x[i], l, h, color=color, linewidth=0.6)

    # Moving averages
    ax_price.plot(x, ma5.values, color="#ffd32a", linewidth=1.0, label="MA5", alpha=0.9)
    ax_price.plot(x, ma20.values, color="#ff6b81", linewidth=1.0, label="MA20", alpha=0.9)
    ax_price.plot(x, ma60.values, color="#7bed9f", linewidth=1.0, label="MA60", alpha=0.9)

    # Current price annotation
    cur_price = close.iloc[-1]
    ax_price.axhline(y=cur_price, color="#ffa502", linewidth=0.8, linestyle="--", alpha=0.6)
    ax_price.annotate(
        f"  {cur_price:,.0f}",
        xy=(x[-1], cur_price),
        fontsize=10,
        fontweight="bold",
        color="#ffa502",
        va="center",
    )

    ax_price.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_price.set_ylabel("가격 (원)", fontsize=9)
    ax_price.grid(True, alpha=0.2)
    ax_price.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    # Title
    display_name = name or ticker
    today_str = datetime.now().strftime("%Y.%m.%d")
    ax_price.set_title(
        f"{display_name} ({ticker}) - {today_str}",
        fontsize=14, fontweight="bold", color=text_color, pad=12,
    )

    # ── 2. 거래량 ──
    vol_colors = [up_color if close.iloc[i] >= open_.iloc[i] else down_color
                  for i in range(len(df))]
    ax_vol.bar(x, volume.values, width=0.6, color=vol_colors, alpha=0.7)
    ax_vol.set_ylabel("거래량", fontsize=8)
    ax_vol.grid(True, alpha=0.2)
    ax_vol.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{v / 1e6:.1f}M" if v >= 1e6 else f"{v / 1e3:.0f}K")
    )

    # ── 3. RSI(14) ──
    ax_rsi.plot(x, rsi.values, color="#ffa502", linewidth=1.2, label="RSI(14)")
    ax_rsi.axhline(70, color=up_color, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.axhline(30, color=down_color, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_rsi.fill_between(x, 70, 100, alpha=0.05, color=up_color)
    ax_rsi.fill_between(x, 0, 30, alpha=0.05, color=down_color)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI", fontsize=8)
    ax_rsi.legend(loc="upper left", fontsize=7, framealpha=0.3, edgecolor="none")
    ax_rsi.grid(True, alpha=0.2)

    # X-axis date labels (show every ~10th date)
    date_strs = [d.strftime("%m/%d") for d in dates]
    step = max(1, len(x) // 8)
    ax_rsi.set_xticks(x[::step])
    ax_rsi.set_xticklabels([date_strs[i] for i in range(0, len(date_strs), step)],
                           rotation=45, fontsize=7)

    # ── 저장 ──
    plt.tight_layout()
    out_path = f"/tmp/kquant_chart_{ticker}.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=bg_color, edgecolor="none")
    plt.close(fig)

    logger.info("Chart saved: %s", out_path)
    return out_path
