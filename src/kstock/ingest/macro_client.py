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
                d[k] = datetime.fromisoformat(d[k])
            except (ValueError, TypeError):
                d[k] = None
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
        now = datetime.now()

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
            now = datetime.now()
            snapshot.fetched_at = now
            snapshot.is_cached = False
            self._cached_snapshot = snapshot
            self._cached_at = now
            # SQLite에도 저장
            await self._save_to_sqlite(snapshot)
            return snapshot
        except Exception as e:
            logger.warning("Live macro fetch failed (%s), using mock", e)
            mock = self._generate_mock_snapshot()
            mock.fetched_at = datetime.now()
            mock.is_cached = False
            return mock

    async def refresh_now(self) -> MacroSnapshot | None:
        """강제 갱신 (스케줄러용). 캐시 TTL 무시하고 즉시 새 데이터 가져옴."""
        try:
            snapshot = await asyncio.to_thread(self._fetch_live_snapshot)
            now = datetime.now()
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
                now = datetime.now()
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
                age = datetime.now() - snapshot.fetched_at
                if age > timedelta(minutes=30):
                    logger.debug("SQLite macro cache too old (%s), skipping", age)
                    return None

            return snapshot
        except Exception as e:
            logger.debug("SQLite macro cache load failed: %s", e)
            return None

    def _fetch_live_snapshot(self) -> MacroSnapshot:
        """Fetch live macro data from yfinance (runs in thread pool)."""
        # Batch download all tickers at once - much faster than individual calls
        symbols = ["^VIX", "^GSPC", "^IXIC", "KRW=X", "^TNX", "DX-Y.NYB", "BTC-USD", "GC=F"]
        data = yf.download(symbols, period="5d", group_by="ticker", progress=False)

        vix_hist = data["^VIX"]["Close"].dropna()
        spx_hist = data["^GSPC"]["Close"].dropna()
        ndx_hist = data["^IXIC"]["Close"].dropna()
        krw_hist = data["KRW=X"]["Close"].dropna()
        tny_hist = data["^TNX"]["Close"].dropna()
        dxy_hist = data["DX-Y.NYB"]["Close"].dropna()
        btc_hist = data["BTC-USD"]["Close"].dropna()
        gold_hist = data["GC=F"]["Close"].dropna()

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

        regime = self._classify_regime(spx_change, vix, usdkrw_change)

        # Fear & Greed composite score (0=극도공포, 100=극도탐욕)
        fg_score, fg_label = self._compute_fear_greed(
            vix=vix, spx_change=spx_change, usdkrw_change=usdkrw_change,
            btc_change=btc_change, gold_change=gold_change,
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
            fetched_at=datetime.now(),
            is_cached=False,
        )

    @staticmethod
    def _compute_fear_greed(
        vix: float, spx_change: float, usdkrw_change: float,
        btc_change: float, gold_change: float,
    ) -> tuple[float, str]:
        """Compute Fear & Greed composite score (0-100).

        Components:
        - VIX level (40% weight): VIX < 15 = greed, VIX > 30 = fear
        - S&P500 change (20%): positive = greed
        - USD/KRW change (15%): KRW strengthen = greed
        - BTC momentum (15%): positive = greed
        - Gold as safe haven (10%): gold up = fear (flight to safety)
        """
        # VIX component: 40 at VIX=20, higher VIX = lower score
        vix_score = max(0, min(100, 100 - (vix - 12) * (100 / 28)))

        # S&P500 momentum
        spx_score = max(0, min(100, 50 + spx_change * 20))

        # USD/KRW: KRW weakening (positive change) = fear
        krw_score = max(0, min(100, 50 - usdkrw_change * 30))

        # BTC momentum
        btc_score = max(0, min(100, 50 + btc_change * 10))

        # Gold: rising gold = flight to safety = fear
        gold_score = max(0, min(100, 50 - gold_change * 15))

        composite = (
            vix_score * 0.40
            + spx_score * 0.20
            + krw_score * 0.15
            + btc_score * 0.15
            + gold_score * 0.10
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
        spx_change_pct: float, vix: float, usdkrw_change_pct: float
    ) -> str:
        """Classify macro regime."""
        risk_off_signals = 0
        if spx_change_pct < -1.0:
            risk_off_signals += 1
        if vix > 25:
            risk_off_signals += 1
        if usdkrw_change_pct > 0.5:
            risk_off_signals += 1

        if risk_off_signals >= 2:
            return "risk_off"
        elif risk_off_signals == 0 and vix < 18 and spx_change_pct > 0:
            return "risk_on"
        return "neutral"
