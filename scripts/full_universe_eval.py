#!/usr/bin/env python3
"""K-Quant v9.3.1: 60종목 종합 평가 스크립트 (v2 — 인기도/주봉/월봉 포함).

9개 카테고리 × 100점 기반 종합 점수:
  1. 기술적 분석 (20점) - RSI, MACD, 이동평균 정렬, 볼린저밴드
  2. 수급 분석 (20점) - 외국인/기관 순매매, 보유율, 연속매수
  3. 재무 건전성 (10점) - PER, PBR, ROE, 부채비율
  4. 가격 모멘텀 (12점) - 1주/1월/3월 수익률
  5. 거래량/인기도 (12점) - 거래대금, 거래량 급증, 시장관심도
  6. 섹터 강도 (8점)  - 업종 등락률
  7. 주봉 추세 (8점)  - 주봉 이동평균, 추세방향
  8. 장기 수급 (5점)  - 60일/120일 외국인 순매수 추세
  9. 리스크 감점 (-5점) - 변동성, 과열

실행: PYTHONPATH=src python3 scripts/full_universe_eval.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

from kstock.core.tz import KST

UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"
BATCH_SIZE = 10
DELAY = 1.0


def load_stocks() -> list[dict]:
    with open(UNIVERSE_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg["universe"]["stocks"]


# ═══════════════════════════════════════════════════════════
# 1. 기술적 분석 (20점)
# ═══════════════════════════════════════════════════════════
def score_technical(ohlcv: pd.DataFrame) -> dict:
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
        return {"tech_score": 0, "rsi": 0, "macd_text": "N/A", "ma_align": "N/A"}

    close = ohlcv["close"].astype(float)
    cur = float(close.iloc[-1])
    score = 0

    # RSI (0~5)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float(100 - (100 / (1 + rs)).iloc[-1]) if not rs.empty else 50

    if 40 <= rsi <= 60:
        score += 5
    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        score += 3
    elif rsi < 30:
        score += 4  # 과매도 반등
    else:
        score += 1  # >70 과매수

    # MACD (0~5)
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    mv, sv = float(macd.iloc[-1]), float(signal.iloc[-1])

    if mv > sv and mv > 0:
        score += 5; macd_text = "강세"
    elif mv > sv:
        score += 3; macd_text = "골든크로스"
    elif mv < sv and mv < 0:
        score += 1; macd_text = "약세"
    else:
        score += 2; macd_text = "데드크로스"

    # 이동평균 정렬 (0~6)
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(min(60, len(close))).mean().iloc[-1])

    if cur > ma5 > ma20 > ma60:
        score += 6; ma_align = "정배열↑"
    elif cur > ma20 > ma60:
        score += 4; ma_align = "정배열"
    elif cur > ma20:
        score += 3; ma_align = "20일↑"
    elif cur < ma5 < ma20 < ma60:
        score += 0; ma_align = "역배열↓"
    else:
        score += 2; ma_align = "혼조"

    # 볼린저 (0~4)
    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bw = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])
    bb_pos = float((cur - bb_lower.iloc[-1]) / bw) if bw > 0 else 0.5

    if 0.3 <= bb_pos <= 0.7:
        score += 4
    elif 0.2 <= bb_pos < 0.3:
        score += 3
    elif bb_pos > 0.9:
        score += 1
    else:
        score += 2

    return {"tech_score": min(score, 20), "rsi": round(rsi, 1),
            "macd_text": macd_text, "ma_align": ma_align, "bb_pos": round(bb_pos, 2)}


# ═══════════════════════════════════════════════════════════
# 2. 수급 분석 (20점)
# ═══════════════════════════════════════════════════════════
def score_supply_demand(inv_data: list[dict]) -> dict:
    if not inv_data:
        return {"sd_score": 0, "foreign_net_5d": 0, "inst_net_5d": 0,
                "foreign_ratio": 0, "sd_signal": "데이터없음"}

    from kstock.ingest.naver_finance import analyze_investor_trend
    a = analyze_investor_trend(inv_data)
    raw = a.get("score", 0)  # -6 ~ +8
    score = max(0, min(20, int((raw + 6) / 14 * 20)))

    return {"sd_score": score, "foreign_net_5d": a.get("foreign_net_5d", 0),
            "foreign_net_20d": a.get("foreign_net_20d", 0),
            "inst_net_5d": a.get("inst_net_5d", 0),
            "inst_net_20d": a.get("inst_net_20d", 0),
            "foreign_ratio": a.get("foreign_ratio", 0),
            "consec_foreign": a.get("consecutive_foreign_buy", 0),
            "consec_inst": a.get("consecutive_inst_buy", 0),
            "sd_signal": a.get("signal", "중립")}


# ═══════════════════════════════════════════════════════════
# 3. 재무 건전성 (10점)
# ═══════════════════════════════════════════════════════════
def score_financials(info: dict) -> dict:
    per = info.get("per", 0) or 0
    pbr = info.get("pbr", 0) or 0
    roe = info.get("roe", 0) or 0
    debt = info.get("debt_ratio", 0) or 0
    div_y = info.get("dividend_yield", 0) or 0
    score = 0

    # PER (0~3)
    if per <= 0: score += 0
    elif per < 10: score += 3
    elif per < 20: score += 2
    elif per < 35: score += 1

    # ROE (0~3)
    if roe >= 15: score += 3
    elif roe >= 8: score += 2
    elif roe > 0: score += 1

    # PBR (0~2)
    if 0 < pbr < 1: score += 2
    elif pbr < 2: score += 1

    # 부채+배당 (0~2)
    if 0 < debt < 100: score += 1
    if div_y >= 2: score += 1

    return {"fin_score": min(score, 10), "per": per, "pbr": pbr,
            "roe": roe, "debt_ratio": debt, "div_yield": div_y}


# ═══════════════════════════════════════════════════════════
# 4. 가격 모멘텀 (12점)
# ═══════════════════════════════════════════════════════════
def score_momentum(ohlcv: pd.DataFrame) -> dict:
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 5:
        return {"mom_score": 0, "ret_1w": 0, "ret_1m": 0, "ret_3m": 0}

    close = ohlcv["close"].astype(float)
    cur = float(close.iloc[-1])

    ret_1w = (cur / float(close.iloc[-5]) - 1) * 100 if len(close) >= 5 else 0
    ret_1m = (cur / float(close.iloc[-20]) - 1) * 100 if len(close) >= 20 else 0
    ret_3m = (cur / float(close.iloc[-60]) - 1) * 100 if len(close) >= 60 else ret_1m * 2
    score = 0

    # 1주 (0~3): 건강한 상승이 최고
    if 1 <= ret_1w <= 5: score += 3
    elif 0 < ret_1w < 1: score += 2
    elif 5 < ret_1w <= 10: score += 2
    elif ret_1w > 10: score += 1
    elif -3 <= ret_1w < 0: score += 1

    # 1월 (0~4)
    if 3 <= ret_1m <= 15: score += 4
    elif 0 < ret_1m < 3: score += 2
    elif 15 < ret_1m <= 30: score += 2
    elif ret_1m > 30: score += 1
    elif -5 <= ret_1m < 0: score += 1

    # 3월 (0~5)
    if 10 <= ret_3m <= 30: score += 5
    elif 5 <= ret_3m < 10: score += 3
    elif 30 < ret_3m <= 50: score += 2
    elif ret_3m > 50: score += 1
    elif 0 <= ret_3m < 5: score += 2
    elif -10 <= ret_3m < 0: score += 1

    return {"mom_score": min(score, 12),
            "ret_1w": round(ret_1w, 1), "ret_1m": round(ret_1m, 1), "ret_3m": round(ret_3m, 1)}


# ═══════════════════════════════════════════════════════════
# 5. 거래량 + 인기도 (12점) ★ NEW
# ═══════════════════════════════════════════════════════════
def score_popularity(ohlcv: pd.DataFrame) -> dict:
    """거래대금(인기도) + 거래량 변화율 기반."""
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
        return {"pop_score": 0, "trade_value_5d": 0, "vol_ratio": 0, "vol_surge": False}

    close = ohlcv["close"].astype(float)
    vol = ohlcv["volume"].astype(float)
    score = 0

    # 5일 평균 거래대금 (원)
    trade_val_5d = float((close.iloc[-5:] * vol.iloc[-5:]).mean())
    trade_val_20d = float((close.iloc[-20:] * vol.iloc[-20:]).mean())

    # 거래대금 규모 (0~5): 대형 거래 = 기관/외국인 관심 = 인기
    if trade_val_5d >= 100_000_000_000:  # 1000억+
        score += 5
    elif trade_val_5d >= 50_000_000_000:  # 500억+
        score += 4
    elif trade_val_5d >= 10_000_000_000:  # 100억+
        score += 3
    elif trade_val_5d >= 3_000_000_000:   # 30억+
        score += 2
    elif trade_val_5d >= 1_000_000_000:   # 10억+
        score += 1

    # 거래대금 증가율 (0~4): 최근 관심 급증
    val_ratio = trade_val_5d / trade_val_20d if trade_val_20d > 0 else 1.0
    vol_avg_5 = float(vol.iloc[-5:].mean())
    vol_avg_20 = float(vol.iloc[-20:].mean())
    vol_ratio = vol_avg_5 / vol_avg_20 if vol_avg_20 > 0 else 1.0

    if 1.5 <= val_ratio <= 3.0:
        score += 4  # 거래대금 급증 → 시장 관심 폭발
    elif 1.2 <= val_ratio < 1.5:
        score += 3
    elif 1.0 <= val_ratio < 1.2:
        score += 2
    elif val_ratio > 3.0:
        score += 2  # 너무 급증은 과열
    else:
        score += 1

    # 거래량 서지 감지 (0~3)
    vol_surge = False
    latest_vol = float(vol.iloc[-1])
    if vol_avg_20 > 0 and latest_vol / vol_avg_20 >= 2.0:
        score += 3
        vol_surge = True
    elif vol_avg_20 > 0 and latest_vol / vol_avg_20 >= 1.5:
        score += 2
    elif vol_avg_20 > 0 and latest_vol / vol_avg_20 >= 1.0:
        score += 1

    return {"pop_score": min(score, 12),
            "trade_value_5d": trade_val_5d,
            "val_ratio": round(val_ratio, 2),
            "vol_ratio": round(vol_ratio, 2),
            "vol_surge": vol_surge}


# ═══════════════════════════════════════════════════════════
# 6. 섹터 강도 (8점)
# ═══════════════════════════════════════════════════════════
def score_sector(sector: str, sector_rankings: list[dict]) -> dict:
    if not sector_rankings or not sector:
        return {"sector_score": 4, "sector_detail": "N/A"}

    matched = None
    for s in sector_rankings:
        sn = s.get("name", "")
        if sector in sn or sn in sector:
            matched = s
            break

    if not matched:
        return {"sector_score": 4, "sector_detail": f"{sector} 미매칭"}

    change = matched.get("change_pct", 0)
    rank = sector_rankings.index(matched) + 1
    total = len(sector_rankings)
    score = 0

    if change >= 3: score += 6
    elif change >= 1: score += 4
    elif change >= 0: score += 3
    elif change >= -1: score += 2
    else: score += 0

    pct_rank = rank / total
    if pct_rank <= 0.2: score += 2
    elif pct_rank <= 0.5: score += 1

    return {"sector_score": min(score, 8),
            "sector_change": change,
            "sector_detail": f"{matched['name']} {change:+.1f}%"}


# ═══════════════════════════════════════════════════════════
# 7. 주봉 추세 (8점) ★ NEW
# ═══════════════════════════════════════════════════════════
def score_weekly_trend(ohlcv: pd.DataFrame) -> dict:
    """주봉 기반 추세 분석."""
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 30:
        return {"weekly_score": 0, "weekly_trend": "N/A"}

    close = ohlcv["close"].astype(float)
    score = 0

    # 주봉 대신 5일 단위로 종가 추출
    weekly_closes = close.iloc[::5].values
    if len(weekly_closes) < 5:
        return {"weekly_score": 4, "weekly_trend": "데이터부족"}

    # 주봉 이동평균 (4주, 13주, 26주)
    w4 = np.mean(weekly_closes[-4:]) if len(weekly_closes) >= 4 else weekly_closes[-1]
    w13 = np.mean(weekly_closes[-13:]) if len(weekly_closes) >= 13 else w4
    w26 = np.mean(weekly_closes[-26:]) if len(weekly_closes) >= 26 else w13
    cur = float(close.iloc[-1])

    # 주봉 정배열 (0~4)
    if cur > w4 > w13 > w26:
        score += 4; trend = "주봉정배열↑"
    elif cur > w13 > w26:
        score += 3; trend = "주봉상승"
    elif cur > w13:
        score += 2; trend = "주봉회복"
    elif cur < w4 < w13 < w26:
        score += 0; trend = "주봉역배열↓"
    else:
        score += 1; trend = "주봉혼조"

    # 최근 4주 방향 (0~4)
    recent_4w = weekly_closes[-4:]
    if len(recent_4w) >= 4:
        up_weeks = sum(1 for i in range(1, len(recent_4w)) if recent_4w[i] > recent_4w[i-1])
        if up_weeks >= 3:
            score += 4  # 4주 중 3주 이상 상승
        elif up_weeks >= 2:
            score += 2
        elif up_weeks == 1:
            score += 1

    return {"weekly_score": min(score, 8), "weekly_trend": trend}


# ═══════════════════════════════════════════════════════════
# 8. 장기 수급 추세 (5점) ★ NEW
# ═══════════════════════════════════════════════════════════
def score_long_term_flow(inv_data: list[dict]) -> dict:
    """20일 넘는 장기 수급 패턴 분석."""
    if not inv_data or len(inv_data) < 10:
        return {"lt_flow_score": 0, "lt_detail": "데이터부족"}

    score = 0
    all_data = inv_data[:20]

    # 전체 기간 외국인 매수일수 비율 (0~3)
    f_buy_days = sum(1 for d in all_data if d.get("foreign_net", 0) > 0)
    f_ratio = f_buy_days / len(all_data)
    if f_ratio >= 0.7:
        score += 3  # 70%+ 매수일 → 강한 축적
    elif f_ratio >= 0.5:
        score += 2
    elif f_ratio >= 0.3:
        score += 1

    # 기관 매수일수 비율 (0~2)
    i_buy_days = sum(1 for d in all_data if d.get("institution_net", 0) > 0)
    i_ratio = i_buy_days / len(all_data)
    if i_ratio >= 0.6:
        score += 2
    elif i_ratio >= 0.4:
        score += 1

    lt_detail = f"외인매수{f_buy_days}/{len(all_data)}일 기관매수{i_buy_days}/{len(all_data)}일"

    return {"lt_flow_score": min(score, 5), "lt_detail": lt_detail}


# ═══════════════════════════════════════════════════════════
# 9. 리스크 감점 (-5점)
# ═══════════════════════════════════════════════════════════
def score_risk(ohlcv: pd.DataFrame, rsi: float = 50) -> dict:
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
        return {"risk_penalty": 0, "volatility": 0}

    close = ohlcv["close"].astype(float)
    returns = close.pct_change().dropna()
    volatility = float(returns.std() * 100)
    penalty = 0

    if volatility > 5: penalty -= 3
    elif volatility > 3: penalty -= 1

    if rsi > 85: penalty -= 2
    elif rsi < 15: penalty -= 1

    return {"risk_penalty": max(penalty, -5), "volatility": round(volatility, 2)}


# ═══════════════════════════════════════════════════════════
# 종합 평가
# ═══════════════════════════════════════════════════════════
async def evaluate_stock(stock: dict, yf_client, sector_rankings: list[dict]) -> dict | None:
    code = stock["code"]
    name = stock["name"]
    market = stock.get("market", "KOSPI")
    sector = stock.get("sector", "")

    try:
        ohlcv, yf_info = await asyncio.gather(
            yf_client.get_ohlcv(code, market),
            yf_client.get_stock_info(code, name, market),
        )

        from kstock.ingest.naver_finance import get_investor_trading
        inv_data = await get_investor_trading(code, days=20)

        tech = score_technical(ohlcv)
        sd = score_supply_demand(inv_data)
        fin = score_financials(yf_info)
        mom = score_momentum(ohlcv)
        pop = score_popularity(ohlcv)
        sec = score_sector(sector, sector_rankings)
        wk = score_weekly_trend(ohlcv)
        ltf = score_long_term_flow(inv_data)
        risk = score_risk(ohlcv, tech.get("rsi", 50))

        cur_price = float(ohlcv["close"].iloc[-1]) if not ohlcv.empty else 0

        total = (tech["tech_score"] + sd["sd_score"] + fin["fin_score"]
                 + mom["mom_score"] + pop["pop_score"] + sec["sector_score"]
                 + wk["weekly_score"] + ltf["lt_flow_score"] + risk["risk_penalty"])

        return {
            "code": code, "name": name, "sector": sector, "market": market,
            "price": cur_price, "total_score": round(total, 1),
            # 세부 점수
            "s_tech": tech["tech_score"], "s_sd": sd["sd_score"],
            "s_fin": fin["fin_score"], "s_mom": mom["mom_score"],
            "s_pop": pop["pop_score"], "s_sec": sec["sector_score"],
            "s_wk": wk["weekly_score"], "s_ltf": ltf["lt_flow_score"],
            "s_risk": risk["risk_penalty"],
            # 핵심 지표
            "rsi": tech.get("rsi", 0), "ma_align": tech.get("ma_align", ""),
            "macd": tech.get("macd_text", ""),
            "weekly_trend": wk.get("weekly_trend", ""),
            "foreign_net_5d": sd.get("foreign_net_5d", 0),
            "inst_net_5d": sd.get("inst_net_5d", 0),
            "foreign_ratio": sd.get("foreign_ratio", 0),
            "sd_signal": sd.get("sd_signal", ""),
            "per": fin.get("per", 0), "roe": fin.get("roe", 0),
            "ret_1w": mom.get("ret_1w", 0), "ret_1m": mom.get("ret_1m", 0),
            "ret_3m": mom.get("ret_3m", 0),
            "trade_value_5d": pop.get("trade_value_5d", 0),
            "vol_ratio": pop.get("vol_ratio", 0),
            "vol_surge": pop.get("vol_surge", False),
            "volatility": risk.get("volatility", 0),
            "lt_detail": ltf.get("lt_detail", ""),
            "sector_detail": sec.get("sector_detail", ""),
        }

    except Exception as e:
        logger.warning("평가 실패 %s(%s): %s", name, code, e)
        return None


async def main():
    start = time.time()
    print("=" * 90)
    print(f"  K-Quant v9.3.1 — 60종목 종합 평가 v2 (인기도/주봉/장기수급 포함)")
    print(f"  {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")
    print(f"  평가기준: 기술(20) + 수급(20) + 재무(10) + 모멘텀(12)")
    print(f"           + 인기도(12) + 섹터(8) + 주봉(8) + 장기수급(5) - 리스크(5)")
    print("=" * 90)

    stocks = load_stocks()
    print(f"\n📊 평가 대상: {len(stocks)}종목")

    from kstock.ingest.yfinance_kr_client import YFinanceKRClient
    yf_client = YFinanceKRClient()

    print("📈 섹터 동향 로딩...")
    from kstock.ingest.naver_finance import get_sector_rankings
    sector_rankings = await get_sector_rankings(limit=30)
    print(f"   {len(sector_rankings)}개 업종 로드")

    results = []
    total = len(stocks)

    for i in range(0, total, BATCH_SIZE):
        batch = stocks[i:i + BATCH_SIZE]
        names = [s["name"] for s in batch]
        print(f"\n🔄 [{i+1}-{min(i+BATCH_SIZE, total)}/{total}] {', '.join(names[:4])}...")

        tasks = [evaluate_stock(s, yf_client, sector_rankings) for s in batch]
        batch_res = await asyncio.gather(*tasks, return_exceptions=True)

        for j, r in enumerate(batch_res):
            if isinstance(r, Exception):
                print(f"   ❌ {batch[j]['name']}: {r}")
            elif r:
                results.append(r)
                surge = "🔥" if r.get("vol_surge") else "  "
                print(f"   {surge} {r['name']:<12} {r['total_score']:>4.0f}점 "
                      f"[기술{r['s_tech']:>2} 수급{r['s_sd']:>2} 재무{r['s_fin']:>2} "
                      f"모멘{r['s_mom']:>2} 인기{r['s_pop']:>2} 섹터{r['s_sec']:>1} "
                      f"주봉{r['s_wk']:>1} 장기{r['s_ltf']:>1} 리스크{r['s_risk']:>2}]")

        if i + BATCH_SIZE < total:
            await asyncio.sleep(DELAY)

    results.sort(key=lambda x: x["total_score"], reverse=True)
    for idx, r in enumerate(results):
        r["rank"] = idx + 1

    elapsed = time.time() - start

    # ═══ 결과 출력 ═══
    print("\n" + "=" * 90)
    print(f"  🏆 종합 순위 ({len(results)}종목, {elapsed:.0f}초)")
    print("=" * 90)

    hdr = (f"{'#':>2} {'종목':<12} {'총점':>3} "
           f"{'기술':>2} {'수급':>2} {'재무':>2} {'모멘':>2} {'인기':>2} "
           f"{'섹터':>2} {'주봉':>2} {'장기':>2} {'리스크':>3} │ "
           f"{'RSI':>5} {'정렬':>5} {'주봉추세':>7} {'수급신호':>5} "
           f"{'외인5d':>9} {'기관5d':>9} {'PER':>5} {'ROE':>5} "
           f"{'1주':>5} {'1월':>5} {'3월':>5} {'거래대금':>8}")
    print(hdr)
    print("─" * 155)

    def fmt_val(v):
        if abs(v) >= 1e9:
            return f"{v/1e9:.0f}B"
        elif abs(v) >= 1e6:
            return f"{v/1e6:.0f}M"
        return f"{v:,.0f}"

    for r in results:
        surge = "🔥" if r.get("vol_surge") else "  "
        tv = r.get("trade_value_5d", 0)
        tv_str = f"{tv/1e8:,.0f}억" if tv > 0 else "0"
        print(
            f"{r['rank']:>2} {surge}{r['name']:<10} {r['total_score']:>3.0f} "
            f"{r['s_tech']:>2} {r['s_sd']:>2} {r['s_fin']:>2} {r['s_mom']:>2} {r['s_pop']:>2} "
            f"{r['s_sec']:>2} {r['s_wk']:>2} {r['s_ltf']:>2} {r['s_risk']:>3} │ "
            f"{r['rsi']:>5.1f} {r['ma_align']:>5} {r['weekly_trend']:>7} {r['sd_signal']:>5} "
            f"{r['foreign_net_5d']:>+9,} {r['inst_net_5d']:>+9,} "
            f"{r['per']:>5.1f} {r['roe']:>5.1f} "
            f"{r['ret_1w']:>+4.1f}% {r['ret_1m']:>+4.1f}% {r['ret_3m']:>+4.1f}% "
            f"{tv_str:>8}"
        )

    # 섹터별
    print(f"\n📊 섹터별 평균")
    sec_scores: dict[str, list] = {}
    for r in results:
        sec_scores.setdefault(r["sector"], []).append(r["total_score"])
    sec_avg = [(s, np.mean(v), len(v)) for s, v in sec_scores.items()]
    sec_avg.sort(key=lambda x: x[1], reverse=True)
    for s, avg, cnt in sec_avg:
        bar = "█" * int(avg / 3)
        print(f"  {s:<10} {avg:>5.1f}점 ({cnt}종목) {bar}")

    # 인기도 TOP
    print(f"\n🔥 거래대금 TOP 10 (시장 인기)")
    tv_sorted = sorted(results, key=lambda x: x.get("trade_value_5d", 0), reverse=True)
    for r in tv_sorted[:10]:
        tv = r.get("trade_value_5d", 0)
        print(f"  {r['name']:<12} {tv/1e8:>8,.0f}억원 (총점 {r['total_score']:.0f})")

    # 거래량 서지
    surges = [r for r in results if r.get("vol_surge")]
    if surges:
        print(f"\n⚡ 거래량 서지 종목 (최근 거래량 2배↑)")
        for r in surges:
            print(f"  {r['name']:<12} 거래량 {r['vol_ratio']:.1f}x "
                  f"(총점 {r['total_score']:.0f}, {r['ret_1w']:+.1f}%/주)")

    # 수급 특이
    print(f"\n💰 수급 TOP 5 (외국인+기관)")
    combo = sorted(results,
                   key=lambda x: x.get("foreign_net_5d", 0) + x.get("inst_net_5d", 0),
                   reverse=True)
    for r in combo[:5]:
        total_net = r.get("foreign_net_5d", 0) + r.get("inst_net_5d", 0)
        print(f"  {r['name']:<12} 순매수합 {total_net:>+12,}주 "
              f"(외인 {r['foreign_net_5d']:>+10,} 기관 {r['inst_net_5d']:>+10,})")

    print(f"\n📌 평균: {np.mean([r['total_score'] for r in results]):.1f}/100점")
    print(f"📌 소요: {elapsed:.0f}초 | 완료: {len(results)}/{len(stocks)}종목")

    return results


if __name__ == "__main__":
    asyncio.run(main())
