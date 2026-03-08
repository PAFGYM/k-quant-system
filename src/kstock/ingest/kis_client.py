"""KIS OpenAPI client for Korean stock data.

Implements real KIS API calls with mock fallback.
Real endpoints:
  Base URL: https://openapi.koreainvestment.com:9443 (실전)
            https://openapivts.koreainvestment.com:29443 (모의)
  Auth:     POST /oauth2/tokenP
  Price:    GET  /uapi/domestic-stock/v1/quotations/inquire-price
  Daily:    GET  /uapi/domestic-stock/v1/quotations/inquire-daily-price
  Investor: GET  /uapi/domestic-stock/v1/quotations/inquire-investor
  Balance:  GET  /uapi/domestic-stock/v1/trading/inquire-balance
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv

from kstock.core.tz import KST

load_dotenv(override=True)
logger = logging.getLogger(__name__)


@dataclass
class StockInfo:
    """Basic stock information."""

    ticker: str
    name: str
    market: str
    market_cap: float
    per: float
    roe: float
    debt_ratio: float
    consensus_target: float
    current_price: float


class KISClient:
    """KIS OpenAPI client with real API calls and mock fallback."""

    def __init__(self) -> None:
        self.app_key = os.getenv("KIS_APP_KEY", "")
        self.app_secret = os.getenv("KIS_APP_SECRET", "")
        self.account_no = os.getenv("KIS_ACCOUNT_NO", "")
        self.hts_id = os.getenv("KIS_HTS_ID", "")
        is_virtual = os.getenv("KIS_VIRTUAL", "true").lower() in ("true", "1", "yes")

        if is_virtual:
            self.base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self.base_url = "https://openapi.koreainvestment.com:9443"

        self._is_virtual = is_virtual
        self._access_token: str = ""
        self._token_expires: datetime = datetime.min
        self._is_configured = bool(self.app_key and self.app_secret)

        if self._is_configured:
            logger.info("KIS API configured (key=%s****, virtual=%s)",
                        self.app_key[:4], is_virtual)
        else:
            logger.info("KIS API not configured, using mock data")

    # ------------------------------------------------------------------
    # OAuth Token
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> bool:
        """Ensure we have a valid access token. Returns True if ready."""
        if not self._is_configured:
            return False

        if self._access_token and datetime.now(KST) < self._token_expires:
            return True

        try:
            token_data = await asyncio.to_thread(self._fetch_token_sync)
            self._access_token = token_data["access_token"]
            expires_in = int(token_data.get("expires_in", 86400))
            self._token_expires = datetime.now(KST) + timedelta(seconds=expires_in - 60)
            logger.info("KIS access token refreshed (expires in %ds)", expires_in)
            return True
        except Exception as e:
            logger.error("KIS token fetch failed: %s", e)
            return False

    def _fetch_token_sync(self) -> dict:
        """Synchronous token fetch - runs in thread pool."""
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=body)
            if resp.status_code != 200:
                try:
                    err_data = resp.json()
                    err_code = err_data.get("error_code", "")
                    err_desc = err_data.get("error_description", "")
                    logger.error(
                        "KIS token error: HTTP %d, code=%s, desc=%s",
                        resp.status_code, err_code, err_desc,
                    )
                except Exception:
                    logger.debug("_fetch_token_sync: failed to parse error response", exc_info=True)
            resp.raise_for_status()
            return resp.json()

    def _auth_headers(self, tr_id: str) -> dict:
        """Build authentication headers for API calls."""
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    # ------------------------------------------------------------------
    # Real API calls (sync, run in thread pool)
    # ------------------------------------------------------------------

    def _api_get_sync(self, path: str, tr_id: str, params: dict) -> dict:
        """Generic synchronous GET request to KIS API (v9.6.3: 재시도)."""
        import time as _time
        url = f"{self.base_url}{path}"
        headers = self._auth_headers(tr_id)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=15) as client:
                    resp = client.get(url, headers=headers, params=params)
                    if resp.status_code >= 500 or resp.status_code == 429:
                        if attempt < max_retries - 1:
                            _time.sleep(1.5 * (attempt + 1))
                            continue
                    resp.raise_for_status()
                    return resp.json()
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt < max_retries - 1:
                    _time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        return {}

    def _fetch_current_price_sync(self, ticker: str) -> dict:
        """Fetch current stock price from KIS API."""
        return self._api_get_sync(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            },
        )

    def _fetch_daily_price_sync(self, ticker: str, period: str = "D",
                                 adj_prc: str = "1") -> dict:
        """Fetch daily OHLCV from KIS API."""
        return self._api_get_sync(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            "FHKST01010400",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_PERIOD_DIV_CODE": period,
                "FID_ORG_ADJ_PRC": adj_prc,
            },
        )

    def _fetch_investor_sync(self, ticker: str) -> dict:
        """Fetch investor trading data (외인/기관 매매동향)."""
        return self._api_get_sync(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
            },
        )

    def _fetch_balance_sync(self) -> dict:
        """Fetch account balance from KIS API."""
        acct_parts = self.account_no.split("-")
        cano = acct_parts[0] if len(acct_parts) >= 1 else ""
        acnt_prdt_cd = acct_parts[1] if len(acct_parts) >= 2 else "01"

        tr_id = "VTTC8434R" if self._is_virtual else "TTTC8434R"
        return self._api_get_sync(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id,
            {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "01",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )

    def _fetch_short_selling_sync(self, ticker: str,
                                   start_date: str, end_date: str) -> dict:
        """Fetch daily short selling data from KIS API.

        모의투자/실전 구분 없이 현재 토큰으로 조회합니다.
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/daily-short-sale"
        headers = self._auth_headers("FHPST04830000")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
        }
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                logger.debug("Short selling API %d for %s", resp.status_code, ticker)
                return {"rt_cd": "-1", "output2": []}
            return resp.json()

    # ------------------------------------------------------------------
    # Public async methods
    # ------------------------------------------------------------------

    async def get_current_price(self, ticker: str, base_price: float = 0) -> float:
        """Get real-time current price. KIS API only — no mock data."""
        if await self._ensure_token():
            try:
                data = await asyncio.to_thread(self._fetch_current_price_sync, ticker)
                output = data.get("output", {})
                price = float(output.get("stck_prpr", 0))
                if price > 0:
                    return price
            except Exception as e:
                logger.warning("KIS price API failed for %s: %s", ticker, e)

        # base_price 그대로 반환 (mock 가격 생성 안 함)
        if base_price > 0:
            return base_price
        return 0.0

    async def get_price_detail(self, ticker: str, base_price: float = 0) -> dict:
        """Get price with previous close and daily change info.

        Returns dict with keys:
            price: 현재가
            prev_close: 전일 종가
            day_change: 전일 대비 금액
            day_change_pct: 전일 대비율
        """
        result = {"price": 0.0, "prev_close": 0.0, "day_change": 0.0, "day_change_pct": 0.0}
        if await self._ensure_token():
            try:
                data = await asyncio.to_thread(self._fetch_current_price_sync, ticker)
                output = data.get("output", {})
                price = float(output.get("stck_prpr", 0))
                if price > 0:
                    prev_close = float(output.get("stck_sdpr", 0))  # 전일 종가
                    day_change = float(output.get("prdy_vrss", 0))  # 전일 대비
                    day_change_pct = float(output.get("prdy_ctrt", 0))  # 전일 대비율
                    return {
                        "price": price,
                        "prev_close": prev_close if prev_close > 0 else price - day_change,
                        "day_change": day_change,
                        "day_change_pct": day_change_pct,
                    }
            except Exception as e:
                logger.warning("KIS price detail API failed for %s: %s", ticker, e)

        # Fallback: base_price 그대로 (mock 가격 생성 안 함)
        result["price"] = base_price if base_price > 0 else 0.0
        result["prev_close"] = result["price"]
        return result

    async def get_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        """Fetch OHLCV data for a ticker."""
        if await self._ensure_token():
            try:
                data = await asyncio.to_thread(
                    self._fetch_daily_price_sync, ticker
                )
                output = data.get("output", [])
                if output and isinstance(output, list) and len(output) >= 5:
                    return self._parse_daily_ohlcv(output, days)
            except Exception as e:
                logger.warning("KIS OHLCV API failed for %s: %s", ticker, e)

        logger.warning("KIS OHLCV: all sources failed for %s, returning empty", ticker)
        return pd.DataFrame()

    async def get_stock_info(self, ticker: str, name: str = "") -> StockInfo:
        """Fetch stock fundamental info."""
        if await self._ensure_token():
            try:
                data = await asyncio.to_thread(
                    self._fetch_current_price_sync, ticker
                )
                output = data.get("output", {})
                if output:
                    return StockInfo(
                        ticker=ticker,
                        name=name or output.get("rprs_mrkt_kor_name", ticker),
                        market="KOSPI" if output.get("rprs_mrkt_kor_name") else "KOSDAQ",
                        market_cap=float(output.get("hts_avls", 0)) * 100_000_000,
                        per=float(output.get("per", 0) or 0),
                        roe=float(output.get("stck_fcam", 0) or 0),
                        debt_ratio=0.0,
                        consensus_target=0.0,
                        current_price=float(output.get("stck_prpr", 0)),
                    )
            except Exception as e:
                logger.warning("KIS stock info failed for %s: %s", ticker, e)

        logger.warning("KIS stock info: all sources failed for %s, returning empty", ticker)
        return StockInfo(
            ticker=ticker, name=name or ticker, market="KOSPI",
            market_cap=0, per=0, roe=0, debt_ratio=0,
            consensus_target=0, current_price=0,
        )

    async def get_short_selling(self, ticker: str, days: int = 20) -> list[dict]:
        """Fetch daily short selling data.

        Returns:
            [{date, short_volume, total_volume, short_ratio,
              short_balance, short_balance_ratio}, ...]
        """
        if await self._ensure_token():
            try:
                end_dt = datetime.now(KST)
                start_dt = end_dt - timedelta(days=days + 10)
                data = await asyncio.to_thread(
                    self._fetch_short_selling_sync,
                    ticker,
                    start_dt.strftime("%Y%m%d"),
                    end_dt.strftime("%Y%m%d"),
                )
                results = []
                for item in data.get("output2", []):
                    date_raw = item.get("stck_bsop_date", "")
                    if len(date_raw) != 8:
                        continue
                    date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
                    short_vol = int(item.get("ssts_cntg_qty", 0) or 0)
                    total_vol = int(item.get("acml_vol", 0) or 0)
                    short_ratio = float(item.get("ssts_vol_rlim", 0) or 0)
                    results.append({
                        "date": date_str,
                        "short_volume": short_vol,
                        "total_volume": total_vol,
                        "short_ratio": round(short_ratio, 2),
                        "short_balance": 0,
                        "short_balance_ratio": 0.0,
                    })
                    if len(results) >= days:
                        break
                return results
            except Exception as e:
                logger.warning("KIS short selling API failed for %s: %s", ticker, e)
        return []

    async def get_foreign_flow(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """Fetch foreign investor flow data."""
        if await self._ensure_token():
            try:
                data = await asyncio.to_thread(
                    self._fetch_investor_sync, ticker
                )
                output = data.get("output", [])
                if output and isinstance(output, list):
                    return self._parse_investor_flow(output, "frgn", days)
            except Exception as e:
                logger.warning("KIS foreign flow failed for %s: %s", ticker, e)

        logger.warning("KIS foreign flow: all sources failed for %s, returning empty", ticker)
        return pd.DataFrame(columns=["date", "net_buy_volume", "net_buy_amount"])

    async def get_institution_flow(self, ticker: str, days: int = 5) -> pd.DataFrame:
        """Fetch institutional investor flow data."""
        if await self._ensure_token():
            try:
                data = await asyncio.to_thread(
                    self._fetch_investor_sync, ticker
                )
                output = data.get("output", [])
                if output and isinstance(output, list):
                    return self._parse_investor_flow(output, "orgn", days)
            except Exception as e:
                logger.warning("KIS inst flow failed for %s: %s", ticker, e)

        logger.warning("KIS inst flow: all sources failed for %s, returning empty", ticker)
        return pd.DataFrame(columns=["date", "net_buy_volume", "net_buy_amount"])

    async def get_balance(self) -> dict | None:
        """Fetch account balance."""
        if not self.account_no:
            return None
        if not await self._ensure_token():
            return None
        try:
            data = await asyncio.to_thread(self._fetch_balance_sync)
            output1 = data.get("output1", [])
            output2 = data.get("output2", [{}])
            summary = output2[0] if output2 else {}

            holdings = []
            for h in output1:
                if int(h.get("hldg_qty", 0)) > 0:
                    holdings.append({
                        "ticker": h.get("pdno", ""),
                        "name": h.get("prdt_name", ""),
                        "quantity": int(h.get("hldg_qty", 0)),
                        "avg_price": float(h.get("pchs_avg_pric", 0)),
                        "current_price": float(h.get("prpr", 0)),
                        "eval_amount": float(h.get("evlu_amt", 0)),
                        "profit_pct": float(h.get("evlu_pfls_rt", 0)),
                        "profit_amount": float(h.get("evlu_pfls_amt", 0)),
                    })

            return {
                "holdings": holdings,
                "total_eval": float(summary.get("tot_evlu_amt", 0)),
                "total_profit": float(summary.get("evlu_pfls_smtl_amt", 0)),
                "cash": float(summary.get("dnca_tot_amt", 0)),
            }
        except Exception as e:
            logger.error("KIS balance query failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_daily_ohlcv(output: list[dict], max_rows: int) -> pd.DataFrame:
        """Parse KIS daily price output into DataFrame."""
        rows = []
        for item in output[:max_rows]:
            try:
                rows.append({
                    "date": item.get("stck_bsop_date", ""),
                    "open": float(item.get("stck_oprc", 0)),
                    "high": float(item.get("stck_hgpr", 0)),
                    "low": float(item.get("stck_lwpr", 0)),
                    "close": float(item.get("stck_clpr", 0)),
                    "volume": int(item.get("acml_vol", 0)),
                })
            except (ValueError, TypeError):
                continue

        if not rows:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(rows)
        # KIS returns newest first, reverse to oldest first
        df = df.sort_values("date").reset_index(drop=True)
        return df

    @staticmethod
    def _parse_investor_flow(output: list[dict], investor_type: str,
                              max_rows: int) -> pd.DataFrame:
        """Parse KIS investor data into flow DataFrame.

        investor_type: 'frgn' for foreign, 'orgn' for institution
        """
        vol_key = f"{investor_type}_ntby_qty"    # 순매수수량
        amt_key = f"{investor_type}_ntby_tr_pbmn"  # 순매수대금

        rows = []
        for item in output[:max_rows]:
            try:
                rows.append({
                    "date": item.get("stck_bsop_date", ""),
                    "net_buy_volume": int(item.get(vol_key, 0)),
                    "net_buy_amount": int(item.get(amt_key, 0)),
                })
            except (ValueError, TypeError):
                continue

        if not rows:
            return pd.DataFrame(columns=["date", "net_buy_volume", "net_buy_amount"])

        df = pd.DataFrame(rows)
        df = df.sort_values("date").reset_index(drop=True)
        return df

