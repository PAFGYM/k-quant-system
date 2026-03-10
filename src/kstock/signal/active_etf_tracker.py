"""v11.0: 액티브 ETF 구성종목 추적 — 수급 예측.

KoAct 코스닥액티브, TIME 코스닥액티브 등 신규 액티브 ETF의
구성종목·비중을 추적하여 매수 압력 시그널을 생성합니다.

로직:
1. 네이버금융 ETF API에서 구성종목/비중 조회
2. AUM × 비중 = 종목별 추정 매수금액
3. 전일 대비 비중 변화 = 신규 편입/이탈 감지
4. 보유종목 교집합 → 수급 시그널 생성
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

# ── 추적 대상 액티브 ETF ──────────────────────────────────────────
ACTIVE_ETFS: Dict[str, dict] = {
    # 코드는 상장 후 확인 필요 — auto_discover_codes()로 자동 갱신
    # KoAct 코스닥액티브 (삼성액티브자산운용)
    "489050": {
        "name": "KoAct 코스닥액티브",
        "manager": "삼성액티브자산운용",
        "benchmark": "KOSDAQ",
        "strategy": "7개 성장산업 집중 (성장7:가치3)",
        "fee": 0.5,
        "search_keywords": ["KoAct", "코스닥액티브", "삼성"],
    },
    # TIME 코스닥액티브 (타임폴리오자산운용)
    "489060": {
        "name": "TIME 코스닥액티브",
        "manager": "타임폴리오자산운용",
        "benchmark": "KOSDAQ",
        "strategy": "코어-위성 (대형주+신성장테마)",
        "fee": 0.8,
        "search_keywords": ["TIME", "코스닥액티브", "타임폴리오"],
    },
}

# 자동 검색된 코드 캐시 (런타임)
_discovered_codes: Dict[str, str] = {}


def auto_discover_codes() -> Dict[str, str]:
    """pykrx에서 액티브 ETF 종목코드를 자동 검색.

    Returns:
        {검색명: 종목코드} 매핑
    """
    global _discovered_codes
    if _discovered_codes:
        return _discovered_codes

    try:
        from pykrx import stock as pykrx_stock
        today_str = date.today().strftime("%Y%m%d")
        etf_codes = pykrx_stock.get_etf_ticker_list(today_str)
        for code in etf_codes:
            try:
                name = pykrx_stock.get_etf_ticker_name(code)
                if not name:
                    continue
                # "코스닥" + "액티브" → 매칭
                if "코스닥" in name and "액티브" in name:
                    _discovered_codes[name] = code
                    logger.info("Auto-discovered active ETF: %s = %s", code, name)
            except Exception:
                pass
    except Exception as e:
        logger.debug("ETF auto-discovery failed: %s", e)

    # 발견된 코드를 ACTIVE_ETFS에 자동 반영
    for discovered_name, code in _discovered_codes.items():
        if code not in ACTIVE_ETFS:
            for existing_code, info in list(ACTIVE_ETFS.items()):
                # 키워드 매칭으로 기존 플레이스홀더 업데이트
                keywords = info.get("search_keywords", [])
                if any(kw in discovered_name for kw in keywords):
                    ACTIVE_ETFS[code] = {**info}
                    if existing_code != code:
                        del ACTIVE_ETFS[existing_code]
                    logger.info("Updated ETF code: %s → %s (%s)", existing_code, code, discovered_name)
                    break

    return _discovered_codes


@dataclass
class ETFHolding:
    """ETF 구성종목 정보."""
    ticker: str
    name: str
    weight_pct: float  # 비중 (%)
    shares: int = 0  # 보유주수
    market_value: float = 0  # 평가금액 (억원)


@dataclass
class ActiveETFSnapshot:
    """액티브 ETF 스냅샷."""
    etf_code: str
    etf_name: str
    date: str
    nav: float = 0  # 순자산(억원)
    price: float = 0  # 현재가
    aum_billion: float = 0  # AUM (억원)
    holdings: List[ETFHolding] = field(default_factory=list)
    top10_weight: float = 0  # 상위10종목 비중합
    sector_weights: Dict[str, float] = field(default_factory=dict)


@dataclass
class ETFFlowSignal:
    """ETF 수급 시그널."""
    ticker: str
    name: str
    etf_count: int  # 몇 개 ETF에 편입
    total_weight: float  # 합산 비중
    estimated_buy_billion: float  # 추정 매수금액 (억원)
    etf_names: List[str] = field(default_factory=list)
    signal: str = ""  # "strong_inflow" / "inflow" / "neutral"


def fetch_etf_holdings(etf_code: str) -> Optional[ActiveETFSnapshot]:
    """네이버금융 ETF API에서 구성종목 조회.

    네이버금융 ETF 구성종목 API:
    https://finance.naver.com/api/sise/etfItemList.nhn?etfCd=XXXXXX
    """
    etf_info = ACTIVE_ETFS.get(etf_code)
    if not etf_info:
        logger.warning("Unknown active ETF code: %s", etf_code)
        return None

    snap = ActiveETFSnapshot(
        etf_code=etf_code,
        etf_name=etf_info["name"],
        date=date.today().isoformat(),
    )

    # 방법 1: 네이버금융 ETF 구성종목 API
    try:
        url = f"https://finance.naver.com/api/sise/etfItemList.nhn?etfCd={etf_code}"
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("result", {})
            etf_items = result.get("etfItemList", [])
            for item in etf_items:
                ticker = item.get("itemCode", "")
                name = item.get("itemName", "")
                weight = float(item.get("weight", 0))
                shares = int(item.get("holdingQty", 0) or 0)
                mval = float(item.get("holdingAmt", 0) or 0) / 100_000_000  # 원→억
                if ticker and weight > 0:
                    snap.holdings.append(ETFHolding(
                        ticker=ticker, name=name,
                        weight_pct=weight, shares=shares,
                        market_value=mval,
                    ))
            # NAV/AUM
            snap.nav = float(result.get("nav", 0) or 0)
            snap.price = float(result.get("nowVal", 0) or 0)
            snap.aum_billion = float(result.get("totalMktAmt", 0) or 0)
    except Exception as e:
        logger.warning("Naver ETF API failed for %s: %s", etf_code, e)

    # 방법 2: pykrx 폴백 (상장 후)
    if not snap.holdings:
        try:
            from pykrx import stock as pykrx_stock
            today_str = date.today().strftime("%Y%m%d")
            pdf = pykrx_stock.get_etf_portfolio_deposit_file(etf_code, today_str)
            if pdf is not None and not pdf.empty:
                total_val = pdf["평가금액"].sum()
                for idx, row in pdf.iterrows():
                    ticker = str(idx)
                    name = row.get("종목명", ticker)
                    val = float(row.get("평가금액", 0))
                    weight = (val / total_val * 100) if total_val > 0 else 0
                    shares = int(row.get("수량", 0) or 0)
                    if ticker and len(ticker) == 6 and weight > 0.1:
                        snap.holdings.append(ETFHolding(
                            ticker=ticker, name=name,
                            weight_pct=round(weight, 2),
                            shares=shares,
                            market_value=round(val / 100_000_000, 2),
                        ))
                snap.aum_billion = round(total_val / 100_000_000, 2)
        except Exception as e:
            logger.warning("pykrx ETF portfolio failed for %s: %s", etf_code, e)

    # 정렬 및 통계
    snap.holdings.sort(key=lambda h: h.weight_pct, reverse=True)
    if snap.holdings:
        snap.top10_weight = sum(h.weight_pct for h in snap.holdings[:10])

    return snap


def fetch_all_active_etfs() -> List[ActiveETFSnapshot]:
    """모든 추적 액티브 ETF의 구성종목 조회."""
    # 자동 코드 검색 시도
    auto_discover_codes()

    results = []
    for code in list(ACTIVE_ETFS.keys()):
        snap = fetch_etf_holdings(code)
        if snap and snap.holdings:
            results.append(snap)
    return results


def compute_flow_signals(
    snapshots: List[ActiveETFSnapshot],
    watched_tickers: Optional[List[str]] = None,
) -> List[ETFFlowSignal]:
    """ETF 구성종목 → 수급 시그널 계산.

    Args:
        snapshots: 액티브 ETF 스냅샷 목록
        watched_tickers: 관심 종목 목록 (None이면 전체)

    Returns:
        종목별 수급 시그널 (합산 비중 순 정렬)
    """
    # 종목별 집계
    ticker_agg: Dict[str, dict] = {}
    for snap in snapshots:
        for h in snap.holdings:
            if watched_tickers and h.ticker not in watched_tickers:
                continue
            if h.ticker not in ticker_agg:
                ticker_agg[h.ticker] = {
                    "name": h.name,
                    "etf_count": 0,
                    "total_weight": 0.0,
                    "estimated_buy": 0.0,
                    "etf_names": [],
                }
            agg = ticker_agg[h.ticker]
            agg["etf_count"] += 1
            agg["total_weight"] += h.weight_pct
            agg["estimated_buy"] += snap.aum_billion * h.weight_pct / 100
            agg["etf_names"].append(snap.etf_name)

    # 시그널 생성
    signals = []
    for ticker, agg in ticker_agg.items():
        signal = "neutral"
        if agg["etf_count"] >= 2 and agg["total_weight"] >= 5:
            signal = "strong_inflow"
        elif agg["total_weight"] >= 3:
            signal = "inflow"

        signals.append(ETFFlowSignal(
            ticker=ticker,
            name=agg["name"],
            etf_count=agg["etf_count"],
            total_weight=round(agg["total_weight"], 2),
            estimated_buy_billion=round(agg["estimated_buy"], 2),
            etf_names=agg["etf_names"],
            signal=signal,
        ))

    signals.sort(key=lambda s: s.total_weight, reverse=True)
    return signals


def format_active_etf_report(
    snapshots: List[ActiveETFSnapshot],
    signals: List[ETFFlowSignal],
) -> str:
    """텔레그램용 액티브 ETF 리포트 포맷."""
    lines = ["📊 액티브 ETF 구성종목 분석\n"]

    for snap in snapshots:
        lines.append(f"━━ {snap.etf_name} ({snap.etf_code}) ━━")
        if snap.aum_billion:
            lines.append(f"AUM: {snap.aum_billion:,.0f}억원 | 상위10: {snap.top10_weight:.1f}%")
        lines.append(f"구성종목 {len(snap.holdings)}개:")
        for h in snap.holdings[:10]:
            lines.append(f"  {h.name} ({h.ticker}): {h.weight_pct:.1f}%")
        if len(snap.holdings) > 10:
            lines.append(f"  ... 외 {len(snap.holdings) - 10}종목")
        lines.append("")

    # 수급 시그널
    strong = [s for s in signals if s.signal == "strong_inflow"]
    inflow = [s for s in signals if s.signal == "inflow"]

    if strong or inflow:
        lines.append("🔥 수급 시그널")
        for s in (strong + inflow)[:10]:
            emoji = "🔥" if s.signal == "strong_inflow" else "📈"
            etfs = ", ".join(s.etf_names)
            lines.append(
                f"  {emoji} {s.name}: {s.total_weight:.1f}% "
                f"(~{s.estimated_buy_billion:.0f}억, {etfs})"
            )

    return "\n".join(lines)


def format_etf_context_for_ai(signals: List[ETFFlowSignal]) -> str:
    """AI 프롬프트용 액티브 ETF 수급 컨텍스트."""
    if not signals:
        return ""

    lines = ["[액티브ETF 수급 시그널]"]
    for s in signals[:15]:
        if s.signal != "neutral":
            lines.append(
                f"{s.name}({s.ticker}): {s.signal}, "
                f"ETF {s.etf_count}개 편입, 비중합 {s.total_weight:.1f}%, "
                f"추정매수 ~{s.estimated_buy_billion:.0f}억"
            )

    return "\n".join(lines) if len(lines) > 1 else ""
