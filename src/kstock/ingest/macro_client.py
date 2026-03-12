"""Macro data client using yfinance (FRED optional).

v3.5-phase8 speed optimization:
- SQLite 로컬 캐시로 즉시 응답 (0ms)
- 백그라운드에서 yfinance 데이터 갱신
- 사용자 요청 시 API 대기 없음
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, fields, asdict
from datetime import datetime, timedelta

import numpy as np
import yfinance as yf
from dotenv import load_dotenv

from kstock.core.tz import KST

load_dotenv(override=True)
logger = logging.getLogger(__name__)


@dataclass
class MacroSnapshot:
    """Current macro environment snapshot."""

    vix: float
    vix_change_pct: float
    spx_change_pct: float
    usdkrw: float
    usdkrw_change_pct: float
    us10y: float
    dxy: float
    regime: str  # risk_on, neutral, risk_off
    # Extended fields (defaults for backward compat)
    nasdaq_change_pct: float = 0.0
    btc_price: float = 0.0
    btc_change_pct: float = 0.0
    institution_total: float = 0.0
    foreign_total: float = 0.0
    gold_price: float = 0.0
    gold_change_pct: float = 0.0
    # v6.1.3: 한국 시장 지수
    kospi: float = 0.0
    kospi_change_pct: float = 0.0
    kosdaq: float = 0.0
    kosdaq_change_pct: float = 0.0
    # v3.5: time metadata
    fetched_at: datetime | None = None
    is_cached: bool = False
    # v3.5: previous values for trend comparison
    spx_prev_close: float = 0.0
    vix_prev: float = 0.0
    usdkrw_prev: float = 0.0
    btc_prev: float = 0.0
    gold_prev: float = 0.0
    us10y_change_pct: float = 0.0
    dxy_change_pct: float = 0.0
    # v3.5: Fear & Greed composite (0-100)
    fear_greed_score: float = 50.0
    fear_greed_label: str = "중립"
    # v6.6: 미국 레버리지 ETF 시그널
    koru_price: float = 0.0       # Direxion 3x Korea Bull
    koru_change_pct: float = 0.0
    soxl_price: float = 0.0       # Direxion 3x Semiconductor Bull
    soxl_change_pct: float = 0.0
    tqqq_price: float = 0.0       # ProShares 3x NASDAQ-100
    tqqq_change_pct: float = 0.0
    # v13.1: 국내 레버리지/인버스 ETF 실시간값
    kodex_leverage_price: float = 0.0
    kodex_leverage_change_pct: float = 0.0
    kodex_inverse2x_price: float = 0.0
    kodex_inverse2x_change_pct: float = 0.0
    # v9.0: 선물지수 (장 마감 후에도 방향성 파악 가능)
    es_futures: float = 0.0       # S&P500 E-mini 선물
    es_futures_change_pct: float = 0.0
    nq_futures: float = 0.0       # 나스닥100 E-mini 선물
    nq_futures_change_pct: float = 0.0
    us2y: float = 0.0             # 미국 2년물 금리
    # v9.0: 한국 실현변동성 (VKOSPI 프록시)
    korean_vol: float = 0.0       # 한국 실현변동성 (연환산 %)
    vol_regime: str = ""          # 변동성 레짐: low/normal/high/extreme
    # v10.2: 유가/원자재 + 글로벌 매크로 쇼크 피처
    wti_price: float = 0.0        # WTI 원유 선물 (CL=F)
    wti_change_pct: float = 0.0
    brent_price: float = 0.0      # Brent 원유 선물 (BZ=F)
    brent_change_pct: float = 0.0
    natural_gas_price: float = 0.0  # 천연가스 선물 (NG=F)
    natural_gas_change_pct: float = 0.0
    ewy_price: float = 0.0        # iShares MSCI South Korea ETF
    ewy_change_pct: float = 0.0
    nikkei_change_pct: float = 0.0  # 닛케이225
    hsi_change_pct: float = 0.0     # 항셍지수
    us2y_change_pct: float = 0.0    # 미국 2년물 변화율
    # v13: FRED 신용 스트레스 지표
    hy_spread: float = 0.0           # ICE BofA HY OAS (%)
    hy_spread_prev: float = 0.0      # 전일 HY OAS
    nfci: float = 0.0                # Chicago Fed NFCI
    nfci_prev: float = 0.0           # 전주 NFCI


def _snapshot_to_json(snap: MacroSnapshot) -> str:
    """MacroSnapshot → JSON (datetime 직렬화 포함)."""
    d = asdict(snap)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return json.dumps(d, ensure_ascii=False)


def _json_to_snapshot(json_str: str) -> MacroSnapshot:
    """JSON → MacroSnapshot 복원."""
    d = json.loads(json_str)
    # datetime 역직렬화
    for k in ("fetched_at",):
        if d.get(k) and isinstance(d[k], str):
            try:
                dt = datetime.fromisoformat(d[k])
                # v9.3.3: naive datetime → KST aware로 변환
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=KST)
                d[k] = dt
            except (ValueError, TypeError):
                d[k] = None
    # 알 수 없는 키 제거 (구버전 호환)
    valid_keys = {f.name for f in fields(MacroSnapshot)}
    d = {k: v for k, v in d.items() if k in valid_keys}
    return MacroSnapshot(**d)


class MacroClient:
    """Client for fetching macro indicators.

    v3.5-phase8: 3-tier cache architecture for instant response.
    1. Memory cache (0ms) - 인스턴스 변수
    2. SQLite cache (1ms) - 로컬 디스크, 영속성
    3. yfinance API (2-5s) - 백그라운드 갱신
    """

    _CACHE_TTL = timedelta(minutes=10)

    def __init__(self, db=None) -> None:
        self.fred_api_key = os.getenv("FRED_API_KEY", "")
        self._cached_snapshot: MacroSnapshot | None = None
        self._cached_at: datetime | None = None
        self._db = db  # SQLiteStore instance (optional)
        self._bg_refresh_task: asyncio.Task | None = None
        self._bg_refresh_lock = asyncio.Lock()

    def set_db(self, db) -> None:
        """DB 연결 설정 (봇 초기화 후 호출)."""
        self._db = db

    async def get_snapshot(self) -> MacroSnapshot:
        """매크로 스냅샷 반환. 3-tier cache로 즉시 응답.

        1단계: 메모리 캐시 (0ms)
        2단계: SQLite 캐시 (1ms)
        3단계: yfinance API (백그라운드 갱신, 기다리지 않음)
        """
        now = datetime.now(KST)

        # ── 1단계: 메모리 캐시 ──
        if (
            self._cached_snapshot is not None
            and self._cached_at is not None
            and now - self._cached_at < self._CACHE_TTL
        ):
            cached = MacroSnapshot(**{
                f.name: getattr(self._cached_snapshot, f.name)
                for f in fields(self._cached_snapshot)
            })
            cached.is_cached = True
            cached.fetched_at = self._cached_at
            return cached

        # ── 2단계: SQLite 캐시 ──
        db_snapshot = await self._load_from_sqlite()
        if db_snapshot is not None:
            # 메모리에도 올려놓기
            self._cached_snapshot = db_snapshot
            self._cached_at = db_snapshot.fetched_at or now
            db_snapshot.is_cached = True
            # 백그라운드 갱신 시작 (이미 진행 중이 아니면)
            self._trigger_background_refresh()
            return db_snapshot

        # ── 3단계: 첫 실행 → 동기 fetch (어쩔 수 없이 대기) ──
        try:
            snapshot = await asyncio.to_thread(self._fetch_live_snapshot)
            now = datetime.now(KST)
            snapshot.fetched_at = now
            snapshot.is_cached = False
            self._cached_snapshot = snapshot
            self._cached_at = now
            # SQLite에도 저장
            await self._save_to_sqlite(snapshot)
            return snapshot
        except Exception as e:
            logger.warning("Live macro fetch failed (%s), using fallback defaults", e)
            mock = self._generate_mock_snapshot()
            mock.fetched_at = datetime.now(KST)
            mock.is_cached = False
            mock._data_source = "mock_fallback"  # v9.3.3: 데이터 소스 추적
            return mock

    async def refresh_now(self) -> MacroSnapshot | None:
        """강제 갱신 (스케줄러용). 캐시 TTL 무시하고 즉시 새 데이터 가져옴."""
        try:
            snapshot = await asyncio.to_thread(self._fetch_live_snapshot)
            now = datetime.now(KST)
            snapshot.fetched_at = now
            snapshot.is_cached = False
            self._cached_snapshot = snapshot
            self._cached_at = now
            await self._save_to_sqlite(snapshot)
            logger.info("Macro data refreshed at %s", now.strftime("%H:%M:%S"))
            return snapshot
        except Exception as e:
            logger.warning("Background macro refresh failed: %s", e)
            return None

    def _trigger_background_refresh(self) -> None:
        """백그라운드에서 데이터 갱신 시작 (non-blocking)."""
        if self._bg_refresh_task and not self._bg_refresh_task.done():
            return  # 이미 진행 중

        try:
            loop = asyncio.get_running_loop()
            self._bg_refresh_task = loop.create_task(self._background_refresh())
        except RuntimeError:
            pass  # 이벤트 루프 없으면 무시

    async def _background_refresh(self) -> None:
        """백그라운드 갱신 태스크."""
        async with self._bg_refresh_lock:
            try:
                snapshot = await asyncio.to_thread(self._fetch_live_snapshot)
                now = datetime.now(KST)
                snapshot.fetched_at = now
                snapshot.is_cached = False
                self._cached_snapshot = snapshot
                self._cached_at = now
                await self._save_to_sqlite(snapshot)
                logger.debug("Background macro refresh completed")
            except Exception as e:
                logger.warning("Background refresh failed: %s", e)

    async def _save_to_sqlite(self, snapshot: MacroSnapshot) -> None:
        """스냅샷을 SQLite에 저장."""
        if not self._db:
            return
        try:
            json_str = _snapshot_to_json(snapshot)
            await asyncio.to_thread(self._db.save_macro_cache, json_str)
        except Exception as e:
            logger.debug("SQLite macro cache save failed: %s", e)

    async def _load_from_sqlite(self) -> MacroSnapshot | None:
        """SQLite에서 캐시된 스냅샷 로드."""
        if not self._db:
            return None
        try:
            row = await asyncio.to_thread(self._db.get_macro_cache)
            if not row or not row.get("snapshot_json"):
                return None

            snapshot = _json_to_snapshot(row["snapshot_json"])

            # SQLite 캐시도 TTL 체크 (너무 오래된 건 사용 안 함 - 30분)
            if snapshot.fetched_at:
                age = datetime.now(KST) - snapshot.fetched_at
                if age > timedelta(minutes=30):
                    logger.debug("SQLite macro cache too old (%s), skipping", age)
                    return None

            return snapshot
        except Exception as e:
            logger.debug("SQLite macro cache load failed: %s", e)
            return None

    def _fetch_fred_data(self) -> dict:
        """FRED API에서 HY Spread + NFCI 조회. 실패 시 빈 dict."""
        if not self.fred_api_key:
            return {}
        try:
            from fredapi import Fred
            fred = Fred(api_key=self.fred_api_key)

            result = {}
            # HY Spread (daily, %)
            try:
                hy = fred.get_series("BAMLH0A0HYM2", observation_start="2025-01-01")
                hy = hy.dropna()
                if len(hy) >= 2:
                    result["hy_spread"] = round(float(hy.iloc[-1]), 2)
                    result["hy_spread_prev"] = round(float(hy.iloc[-2]), 2)
                elif len(hy) == 1:
                    result["hy_spread"] = round(float(hy.iloc[-1]), 2)
            except Exception as e:
                logger.debug("FRED HY Spread fetch failed: %s", e)

            # NFCI (weekly)
            try:
                nf = fred.get_series("NFCI", observation_start="2025-01-01")
                nf = nf.dropna()
                if len(nf) >= 2:
                    result["nfci"] = round(float(nf.iloc[-1]), 4)
                    result["nfci_prev"] = round(float(nf.iloc[-2]), 4)
                elif len(nf) == 1:
                    result["nfci"] = round(float(nf.iloc[-1]), 4)
            except Exception as e:
                logger.debug("FRED NFCI fetch failed: %s", e)

            return result
        except ImportError:
            logger.warning("fredapi not installed, skipping FRED data")
            return {}
        except Exception as e:
            logger.warning("FRED fetch failed: %s", e)
            return {}

    def _fetch_live_snapshot(self) -> MacroSnapshot:
        """Fetch live macro data from yfinance (runs in thread pool)."""
        # Batch download all tickers at once - much faster than individual calls
        symbols = [
            "^VIX", "^GSPC", "^IXIC", "KRW=X", "^TNX", "DX-Y.NYB",
            "BTC-USD", "GC=F", "^KS11", "^KQ11",
            "KORU", "SOXL", "TQQQ",  # v6.6: 미국 레버리지 ETF
            "122630.KS", "252670.KS",  # v13.1: 국내 레버리지/인버스 ETF
            "ES=F", "NQ=F",  # v9.0: 미국 선물지수
            "CL=F", "BZ=F", "NG=F",  # v10.2: 유가/원자재
            "EWY",  # v10.2: 한국ETF
            "^N225", "^HSI",  # v10.2: 아시아 지수
            "^IRX",  # v10.2: 미국 2년물 프록시
        ]
        data = yf.download(symbols, period="5d", group_by="ticker", progress=False)

        vix_hist = data["^VIX"]["Close"].dropna()
        spx_hist = data["^GSPC"]["Close"].dropna()
        ndx_hist = data["^IXIC"]["Close"].dropna()
        krw_hist = data["KRW=X"]["Close"].dropna()
        tny_hist = data["^TNX"]["Close"].dropna()
        dxy_hist = data["DX-Y.NYB"]["Close"].dropna()
        btc_hist = data["BTC-USD"]["Close"].dropna()
        gold_hist = data["GC=F"]["Close"].dropna()

        # 한국 시장 지수
        kospi_hist = data["^KS11"]["Close"].dropna() if "^KS11" in data.columns.get_level_values(0) else None
        kosdaq_hist = data["^KQ11"]["Close"].dropna() if "^KQ11" in data.columns.get_level_values(0) else None

        vix = float(vix_hist.iloc[-1])
        vix_prev = float(vix_hist.iloc[-2])
        vix_change = (vix - vix_prev) / vix_prev * 100

        spx = float(spx_hist.iloc[-1])
        spx_prev = float(spx_hist.iloc[-2])
        spx_change = (spx - spx_prev) / spx_prev * 100

        ndx = float(ndx_hist.iloc[-1])
        ndx_prev = float(ndx_hist.iloc[-2])
        ndx_change = (ndx - ndx_prev) / ndx_prev * 100

        usdkrw = float(krw_hist.iloc[-1])
        usdkrw_prev = float(krw_hist.iloc[-2])
        usdkrw_change = (usdkrw - usdkrw_prev) / usdkrw_prev * 100

        us10y = float(tny_hist.iloc[-1])
        us10y_prev = float(tny_hist.iloc[-2]) if len(tny_hist) >= 2 else us10y
        us10y_change = (us10y - us10y_prev) / us10y_prev * 100 if us10y_prev > 0 else 0

        dxy = float(dxy_hist.iloc[-1])
        dxy_prev = float(dxy_hist.iloc[-2]) if len(dxy_hist) >= 2 else dxy
        dxy_change = (dxy - dxy_prev) / dxy_prev * 100 if dxy_prev > 0 else 0

        btc = float(btc_hist.iloc[-1])
        btc_prev = float(btc_hist.iloc[-2])
        btc_change = (btc - btc_prev) / btc_prev * 100

        gold = float(gold_hist.iloc[-1]) if not gold_hist.empty else 0
        gold_prev = float(gold_hist.iloc[-2]) if len(gold_hist) >= 2 else gold
        gold_change = (gold - gold_prev) / gold_prev * 100 if gold_prev > 0 else 0

        # KOSPI
        kospi_val = kospi_change = 0.0
        if kospi_hist is not None and len(kospi_hist) >= 2:
            kospi_val = float(kospi_hist.iloc[-1])
            kospi_prev = float(kospi_hist.iloc[-2])
            kospi_change = (kospi_val - kospi_prev) / kospi_prev * 100 if kospi_prev > 0 else 0

        # KOSDAQ
        kosdaq_val = kosdaq_change = 0.0
        if kosdaq_hist is not None and len(kosdaq_hist) >= 2:
            kosdaq_val = float(kosdaq_hist.iloc[-1])
            kosdaq_prev = float(kosdaq_hist.iloc[-2])
            kosdaq_change = (kosdaq_val - kosdaq_prev) / kosdaq_prev * 100 if kosdaq_prev > 0 else 0

        # v6.6: 미국 레버리지 ETF
        def _etf_data(ticker_key: str):
            try:
                if ticker_key not in data.columns.get_level_values(0):
                    return 0.0, 0.0
                hist = data[ticker_key]["Close"].dropna()
                if len(hist) < 2:
                    return 0.0, 0.0
                price = float(hist.iloc[-1])
                prev = float(hist.iloc[-2])
                chg = (price - prev) / prev * 100 if prev > 0 else 0
                return price, chg
            except Exception:
                return 0.0, 0.0

        koru_price, koru_change = _etf_data("KORU")
        soxl_price, soxl_change = _etf_data("SOXL")
        tqqq_price, tqqq_change = _etf_data("TQQQ")
        kodex_leverage_price, kodex_leverage_change = _etf_data("122630.KS")
        kodex_inverse2x_price, kodex_inverse2x_change = _etf_data("252670.KS")

        # v9.0: 미국 선물지수
        es_price, es_change = _etf_data("ES=F")
        nq_price, nq_change = _etf_data("NQ=F")

        # v10.2: 유가/원자재
        wti_price, wti_change = _etf_data("CL=F")
        brent_price, brent_change = _etf_data("BZ=F")
        ng_price, ng_change = _etf_data("NG=F")

        # v10.2: 한국 ETF + 아시아 지수
        ewy_price, ewy_change = _etf_data("EWY")
        _, nikkei_change = _etf_data("^N225")
        _, hsi_change = _etf_data("^HSI")

        # v10.2: 미국 2년물 (^IRX = 13주 T-bill 프록시)
        us2y_val = 0.0
        us2y_chg = 0.0
        try:
            irx_hist = data["^IRX"]["Close"].dropna() if "^IRX" in data.columns.get_level_values(0) else None
            if irx_hist is not None and len(irx_hist) >= 2:
                us2y_val = float(irx_hist.iloc[-1])
                irx_prev = float(irx_hist.iloc[-2])
                us2y_chg = (us2y_val - irx_prev) / irx_prev * 100 if irx_prev > 0 else 0
        except Exception:
            pass

        # v9.0: 한국 실현변동성 (VKOSPI 프록시)
        kr_vol = 0.0
        vol_regime_str = ""
        try:
            from kstock.signal.volatility_regime import (
                compute_korean_volatility,
                classify_volatility_regime,
            )
            # KOSPI 종가 데이터가 이미 있으면 활용
            if kospi_hist is not None and len(kospi_hist) >= 5:
                kospi_closes = [float(v) for v in kospi_hist.values]
                kr_vol = compute_korean_volatility(kospi_closes)
            else:
                kr_vol = compute_korean_volatility()
            vol_result = classify_volatility_regime(vix, kr_vol)
            vol_regime_str = vol_result.level
        except Exception as e:
            logger.debug("Korean vol computation in macro: %s", e)

        # v13: FRED 신용 스트레스 지표
        fred_data = self._fetch_fred_data()

        regime = self._classify_regime(spx_change, vix, usdkrw_change, wti_change)

        # Fear & Greed composite score (0=극도공포, 100=극도탐욕)
        fg_score, fg_label = self._compute_fear_greed(
            vix=vix, spx_change=spx_change, usdkrw_change=usdkrw_change,
            btc_change=btc_change, gold_change=gold_change, wti_change=wti_change,
        )

        # KIS API 미연동 — 기관/외국인 수급 데이터 없음 (0 = 미제공)
        inst_total = 0.0
        foreign_total = 0.0

        return MacroSnapshot(
            vix=round(vix, 2),
            vix_change_pct=round(vix_change, 2),
            spx_change_pct=round(spx_change, 2),
            usdkrw=round(usdkrw, 2),
            usdkrw_change_pct=round(usdkrw_change, 2),
            us10y=round(us10y, 2),
            dxy=round(dxy, 2),
            regime=regime,
            nasdaq_change_pct=round(ndx_change, 2),
            btc_price=round(btc, 0),
            btc_change_pct=round(btc_change, 2),
            institution_total=inst_total,
            foreign_total=foreign_total,
            gold_price=round(gold, 0),
            gold_change_pct=round(gold_change, 2),
            # v6.1.3: 한국 시장 지수
            kospi=round(kospi_val, 2),
            kospi_change_pct=round(kospi_change, 2),
            kosdaq=round(kosdaq_val, 2),
            kosdaq_change_pct=round(kosdaq_change, 2),
            # v3.5 additions
            spx_prev_close=round(spx_prev, 2),
            vix_prev=round(vix_prev, 2),
            usdkrw_prev=round(usdkrw_prev, 2),
            btc_prev=round(btc_prev, 0),
            gold_prev=round(gold_prev, 0),
            us10y_change_pct=round(us10y_change, 2),
            dxy_change_pct=round(dxy_change, 2),
            fear_greed_score=fg_score,
            fear_greed_label=fg_label,
            # v6.6: 미국 레버리지 ETF
            koru_price=round(koru_price, 2),
            koru_change_pct=round(koru_change, 2),
            soxl_price=round(soxl_price, 2),
            soxl_change_pct=round(soxl_change, 2),
            tqqq_price=round(tqqq_price, 2),
            tqqq_change_pct=round(tqqq_change, 2),
            kodex_leverage_price=round(kodex_leverage_price, 2),
            kodex_leverage_change_pct=round(kodex_leverage_change, 2),
            kodex_inverse2x_price=round(kodex_inverse2x_price, 2),
            kodex_inverse2x_change_pct=round(kodex_inverse2x_change, 2),
            # v9.0: 선물지수
            es_futures=round(es_price, 2),
            es_futures_change_pct=round(es_change, 2),
            nq_futures=round(nq_price, 2),
            nq_futures_change_pct=round(nq_change, 2),
            # v9.0: 변동성 레짐
            korean_vol=round(kr_vol, 2),
            vol_regime=vol_regime_str,
            # v10.2: 유가/원자재 + 글로벌 매크로
            wti_price=round(wti_price, 2),
            wti_change_pct=round(wti_change, 2),
            brent_price=round(brent_price, 2),
            brent_change_pct=round(brent_change, 2),
            natural_gas_price=round(ng_price, 3),
            natural_gas_change_pct=round(ng_change, 2),
            ewy_price=round(ewy_price, 2),
            ewy_change_pct=round(ewy_change, 2),
            nikkei_change_pct=round(nikkei_change, 2),
            hsi_change_pct=round(hsi_change, 2),
            us2y=round(us2y_val, 2),
            us2y_change_pct=round(us2y_chg, 2),
            # v13: FRED 신용 스트레스
            hy_spread=fred_data.get("hy_spread", 0.0),
            hy_spread_prev=fred_data.get("hy_spread_prev", 0.0),
            nfci=fred_data.get("nfci", 0.0),
            nfci_prev=fred_data.get("nfci_prev", 0.0),
        )

    @staticmethod
    def _generate_mock_snapshot() -> MacroSnapshot:
        """Generate mock macro data for testing."""
        rng = np.random.default_rng(seed=42)
        vix = float(rng.uniform(12, 30))
        spx_change = float(rng.normal(0, 1))
        nasdaq_change = float(rng.normal(0, 1.2))
        usdkrw = float(rng.uniform(1200, 1450))
        usdkrw_change = float(rng.normal(0, 0.5))
        btc = float(rng.uniform(50000, 80000))
        btc_change = float(rng.normal(0, 2))
        inst_total = 0.0
        foreign_total = 0.0
        gold = float(rng.uniform(1800, 2500))
        gold_change = float(rng.normal(0, 1))

        regime = MacroClient._classify_regime(spx_change, vix, usdkrw_change)
        fg_score, fg_label = MacroClient._compute_fear_greed(
            vix=vix, spx_change=spx_change, usdkrw_change=usdkrw_change,
            btc_change=btc_change, gold_change=gold_change,
        )

        us10y_val = round(float(rng.uniform(3.5, 5.0)), 2)
        dxy_val = round(float(rng.uniform(100, 110)), 2)

        return MacroSnapshot(
            vix=round(vix, 2),
            vix_change_pct=round(float(rng.normal(0, 5)), 2),
            spx_change_pct=round(spx_change, 2),
            usdkrw=round(usdkrw, 2),
            usdkrw_change_pct=round(usdkrw_change, 2),
            us10y=us10y_val,
            dxy=dxy_val,
            regime=regime,
            nasdaq_change_pct=round(nasdaq_change, 2),
            btc_price=round(btc, 0),
            btc_change_pct=round(btc_change, 2),
            institution_total=inst_total,
            foreign_total=foreign_total,
            gold_price=round(gold, 0),
            gold_change_pct=round(gold_change, 2),
            # v6.1.3: 한국 시장 지수 (mock)
            kospi=round(float(rng.uniform(2400, 2800)), 2),
            kospi_change_pct=round(float(rng.normal(0, 1)), 2),
            kosdaq=round(float(rng.uniform(700, 900)), 2),
            kosdaq_change_pct=round(float(rng.normal(0, 1.2)), 2),
            # v3.5
            spx_prev_close=0.0,
            vix_prev=round(vix * 0.98, 2),
            usdkrw_prev=round(usdkrw * 0.998, 2),
            btc_prev=round(btc * 0.99, 0),
            gold_prev=round(gold * 0.995, 0),
            us10y_change_pct=round(float(rng.normal(0, 2)), 2),
            dxy_change_pct=round(float(rng.normal(0, 0.5)), 2),
            fear_greed_score=fg_score,
            fear_greed_label=fg_label,
            kodex_leverage_price=round(float(rng.uniform(14_000, 24_000)), 2),
            kodex_leverage_change_pct=round(float(rng.normal(0, 2.4)), 2),
            kodex_inverse2x_price=round(float(rng.uniform(2_000, 4_000)), 2),
            kodex_inverse2x_change_pct=round(float(rng.normal(0, 2.2)), 2),
            fetched_at=datetime.now(KST),
            is_cached=False,
        )

    @staticmethod
    def _compute_fear_greed(
        vix: float, spx_change: float, usdkrw_change: float,
        btc_change: float, gold_change: float, wti_change: float = 0.0,
    ) -> tuple[float, str]:
        """Compute Fear & Greed composite score (0-100).

        Components:
        - VIX level (35% weight): VIX < 15 = greed, VIX > 30 = fear
        - S&P500 change (20%): positive = greed
        - USD/KRW change (15%): KRW strengthen = greed
        - BTC momentum (15%): positive = greed
        - Gold as safe haven (10%): gold up = fear (flight to safety)
        - WTI oil (5%): oil spike = inflation fear
        """
        # VIX component: higher VIX = lower score
        vix_score = max(0, min(100, 100 - (vix - 12) * (100 / 28)))

        # S&P500 momentum
        spx_score = max(0, min(100, 50 + spx_change * 20))

        # USD/KRW: KRW weakening (positive change) = fear
        krw_score = max(0, min(100, 50 - usdkrw_change * 30))

        # BTC momentum
        btc_score = max(0, min(100, 50 + btc_change * 10))

        # Gold: rising gold = flight to safety = fear
        gold_score = max(0, min(100, 50 - gold_change * 15))

        # WTI: 유가 급등 = 인플레이션 공포 = fear (급락도 경기침체 신호 = fear)
        wti_score = max(0, min(100, 50 - abs(wti_change) * 8))

        composite = (
            vix_score * 0.35
            + spx_score * 0.20
            + krw_score * 0.15
            + btc_score * 0.15
            + gold_score * 0.10
            + wti_score * 0.05
        )
        composite = round(max(0, min(100, composite)), 1)

        if composite >= 80:
            label = "극도 탐욕"
        elif composite >= 60:
            label = "탐욕"
        elif composite >= 40:
            label = "중립"
        elif composite >= 20:
            label = "공포"
        else:
            label = "극도 공포"

        return composite, label

    @staticmethod
    def _classify_regime(
        spx_change_pct: float, vix: float, usdkrw_change_pct: float,
        wti_change_pct: float = 0.0,
    ) -> str:
        """Classify macro regime."""
        risk_off_signals = 0
        if spx_change_pct < -1.0:
            risk_off_signals += 1
        if vix > 25:
            risk_off_signals += 1
        if usdkrw_change_pct > 0.5:
            risk_off_signals += 1
        # v10.2: 유가 급등(+3%) = 인플레/지정학 리스크 = risk_off 신호
        if wti_change_pct > 3.0:
            risk_off_signals += 1

        if risk_off_signals >= 2:
            return "risk_off"
        elif risk_off_signals == 0 and vix < 18 and spx_change_pct > 0:
            return "risk_on"
        return "neutral"
