# K-Quant System MVP Build

You are building a Korean stock auto-analysis system in ~/projects/k-quant-system/

## Project Structure
Create this exact structure and implement working code:
```
src/kstock/
  __init__.py
  app.py              # main runner (APScheduler + Telegram bot)
  ops/
    scheduler.py       # APScheduler AsyncIOScheduler
    rate_limit.py      # async rate limiter for API calls
  ingest/
    kis_client.py      # KIS OpenAPI client (mock for MVP, real endpoints documented)
    macro_client.py    # FRED + yfinance macro data
  store/
    sqlite.py          # SQLite for meta/portfolio/watermark
    parquet_store.py   # Parquet read/write for OHLCV
  features/
    technical.py       # RSI, BB, MACD using pandas_ta
  signal/
    scoring.py         # 100-point scoring from config/scoring.yaml
    sell_reason.py     # Foreign sell reason classifier (A/B/C/D)
  bot/
    bot.py             # Telegram bot with /scan /macro /status /help
    messages.py        # Message format templates
config/
  scoring.yaml         # Scoring weights and thresholds
  universe.yaml        # Universe definition
tests/
  test_scoring.py      # Test scoring logic
  test_sell_reason.py  # Test sell reason classification
requirements.txt
```

## Key Requirements

1. **config/scoring.yaml**: Implement the YAML-based dynamic weights:
   - weights: macro=0.10, flow=0.30, fundamental=0.30, technical=0.20, risk=0.10
   - thresholds: rsi_oversold=30, debt_ratio_max=200, min_avg_value_krw=3000000000
   - buy_threshold=70, watch_threshold=55

2. **signal/sell_reason.py**: Implement the 4-type foreign sell classifier:
   - A: Macro Risk-off (SPX<-1.2% + VIX>+10% + USDKRW>+0.6% + broad_sell>70%)
   - B: Flow/Mechanical (program_ratio>25% + sector_corr>0.7 or basis<-0.3%)
   - C: Idiosyncratic (stock_only_drop + consensus<-5% or dart_event)
   - D: Technical (near_high>95% + volume>2.5x + disparity>110%)
   - Return SellReason dataclass with code, confidence, rationale

3. **signal/scoring.py**: 100-point scoring that reads from scoring.yaml
   - score_macro(), score_flow(), score_fundamental(), score_technical(), score_risk()
   - Each returns 0.0~1.0, combined with YAML weights

4. **features/technical.py**: Using pandas_ta, compute RSI(14), BB(20,2), MACD(12,26,9), ATR(14)

5. **store/sqlite.py**: SQLite with tables: job_runs (watermark), portfolio, alerts
   - UPSERT support for idempotent writes

6. **store/parquet_store.py**: Save/load OHLCV DataFrames as Parquet files in data/lake/

7. **bot/bot.py**: Telegram bot with these commands:
   - /scan: trigger scan (returns top 10 scored stocks)
   - /macro: show current macro regime
   - /status: show pipeline health (last job success time)
   - /help: list commands

8. **app.py**: Main entry that starts both scheduler and telegram bot
   - APScheduler jobs: EOD scan at 16:00 KST, macro update at 08:45 KST

9. **tests/**: Write passing tests for scoring and sell_reason modules

10. **requirements.txt**: polars, duckdb, pandas-ta, httpx, apscheduler, python-telegram-bot, pyyaml, pydantic, python-dotenv

## Rules
- Use Python 3.11+ type hints everywhere
- Use async/await for I/O operations
- All config values from YAML, no hardcoded magic numbers
- Include docstrings for all public functions
- Make tests pass with: python -m pytest tests/

When ALL files are created and tests pass, output: <promise>COMPLETE</promise>
