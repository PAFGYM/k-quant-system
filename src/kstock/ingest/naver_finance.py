"""네이버 금융 실시간 시세 폴백 클라이언트.

yfinance 실패 시 네이버 금융에서 현재가·시세·기본 재무정보를 가져온다.
무료 API 기반으로 별도 인증 불필요.

Sources:
    https://finance.naver.com/item/main.naver?code=005930
    https://api.finance.naver.com/siseJson.naver (OHLCV JSON)
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 캐시 ──────────────────────────────────────────────────
_naver_price_cache: dict[str, tuple[datetime, float]] = {}
_naver_ohlcv_cache: dict[str, tuple[datetime, pd.DataFrame]] = {}
_naver_info_cache: dict[str, tuple[datetime, dict]] = {}
_CACHE_TTL = timedelta(minutes=3)

# ── 상수 ──────────────────────────────────────────────────
_SISE_JSON_URL = "https://api.finance.naver.com/siseJson.naver"
_ITEM_MAIN_URL = "https://finance.naver.com/item/main.naver?code={code}"
_ITEM_SISE_URL = "https://finance.naver.com/item/sise.naver?code={code}"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


class NaverFinanceClient:
    """네이버 금융 시세 클라이언트 (yfinance 폴백용)."""

    def __init__(self) -> None:
        self._failed_tickers: set[str] = set()

    async def get_current_price(self, code: str) -> float:
        """네이버 금융에서 현재가 조회.

        Returns:
            현재가 (원). 실패 시 0.0.
        """
        now = datetime.now()
        if code in _naver_price_cache:
            cached_time, cached_price = _naver_price_cache[code]
            if now - cached_time < _CACHE_TTL and cached_price > 0:
                return cached_price

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = _ITEM_SISE_URL.format(code=code)
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
                price = _parse_current_price(resp.text)
                if price > 0:
                    _naver_price_cache[code] = (now, price)
                    return price
        except Exception as e:
            logger.debug("Naver price fetch failed for %s: %s", code, e)

        return 0.0

    async def get_ohlcv(
        self, code: str, period_days: int = 120
    ) -> pd.DataFrame:
        """네이버 금융 OHLCV 데이터 조회.

        siseJson API: 일별 시세 JSON 제공.
        """
        now = datetime.now()
        cache_key = f"{code}_{period_days}"
        if cache_key in _naver_ohlcv_cache:
            cached_time, cached_df = _naver_ohlcv_cache[cache_key]
            if now - cached_time < _CACHE_TTL and not cached_df.empty:
                return cached_df

        try:
            import httpx
            end_date = now.strftime("%Y%m%d")
            start_date = (now - timedelta(days=period_days * 1.5)).strftime("%Y%m%d")

            params = {
                "symbol": code,
                "requestType": "1",
                "startTime": start_date,
                "endTime": end_date,
                "timeframe": "day",
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    _SISE_JSON_URL, params=params, headers=_HEADERS
                )
                resp.raise_for_status()
                df = _parse_sise_json(resp.text)
                if not df.empty:
                    # 최근 period_days만 유지
                    df = df.tail(period_days).reset_index(drop=True)
                    _naver_ohlcv_cache[cache_key] = (now, df)
                    return df
        except Exception as e:
            logger.debug("Naver OHLCV fetch failed for %s: %s", code, e)

        return pd.DataFrame()

    async def get_stock_info(self, code: str, name: str = "") -> dict:
        """네이버 금융에서 기본 재무정보 조회.

        PER, PBR, 시가총액, 외국인 비율 등.
        """
        now = datetime.now()
        if code in _naver_info_cache:
            cached_time, cached_info = _naver_info_cache[code]
            if now - cached_time < _CACHE_TTL:
                return cached_info

        result: dict[str, Any] = {
            "ticker": code,
            "name": name or code,
            "current_price": 0,
            "market_cap": 0,
            "per": 0,
            "pbr": 0,
            "roe": 0,
            "debt_ratio": 0,
            "dividend_yield": 0,
            "foreign_ratio": 0,
            "consensus_target": 0,
            "52w_high": 0,
            "52w_low": 0,
        }

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = _ITEM_MAIN_URL.format(code=code)
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
                parsed = _parse_main_page(resp.text)
                result.update(parsed)
                result["ticker"] = code
                if name:
                    result["name"] = name
                _naver_info_cache[code] = (now, result)
        except Exception as e:
            logger.debug("Naver info fetch failed for %s: %s", code, e)

        return result


# ── 파서 함수들 ──────────────────────────────────────────

def _parse_current_price(html: str) -> float:
    """네이버 시세 페이지에서 현재가 추출."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # 방법 1: <strong id="_nowVal">
        now_val = soup.find(id="_nowVal")
        if now_val:
            return _to_float(now_val.get_text(strip=True))

        # 방법 2: <p class="no_today"> 내 <span class="blind">
        no_today = soup.find("p", class_="no_today")
        if no_today:
            blind = no_today.find("span", class_="blind")
            if blind:
                return _to_float(blind.get_text(strip=True))

        # 방법 3: 정규식 폴백
        m = re.search(r'"now"\s*:\s*"?([0-9,]+)"?', html)
        if m:
            return _to_float(m.group(1))

    except ImportError:
        # bs4 없으면 정규식만
        m = re.search(r'class="no_today"[^>]*>.*?(\d[\d,]+)', html, re.S)
        if m:
            return _to_float(m.group(1))

    return 0.0


def _parse_sise_json(text: str) -> pd.DataFrame:
    """siseJson.naver 응답 파싱.

    응답 형식 (JavaScript-like array):
    [["날짜","시가","고가","저가","종가","거래량"],
     ["20260225","75000","76000","74500","75500","12345678"], ...]
    """
    try:
        # 줄바꿈·따옴표 정리
        cleaned = text.strip()
        if not cleaned:
            return pd.DataFrame()

        # 각 행 파싱
        rows = []
        for line in cleaned.split("\n"):
            line = line.strip().strip(",[]")
            if not line or "날짜" in line:
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 6:
                try:
                    date_str = parts[0].strip().strip('"')
                    if len(date_str) == 8 and date_str.isdigit():
                        rows.append({
                            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}",
                            "open": float(parts[1].strip().strip('"')),
                            "high": float(parts[2].strip().strip('"')),
                            "low": float(parts[3].strip().strip('"')),
                            "close": float(parts[4].strip().strip('"')),
                            "volume": int(float(parts[5].strip().strip('"'))),
                        })
                except (ValueError, IndexError):
                    continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df = df.sort_values("date").reset_index(drop=True)
        return df

    except Exception as e:
        logger.debug("siseJson parse error: %s", e)
        return pd.DataFrame()


def _parse_main_page(html: str) -> dict:
    """네이버 종목 메인 페이지에서 재무정보 추출."""
    result: dict[str, Any] = {}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # 현재가
        no_today = soup.find("p", class_="no_today")
        if no_today:
            blind = no_today.find("span", class_="blind")
            if blind:
                result["current_price"] = _to_float(blind.get_text(strip=True))

        # 시세 테이블에서 PER, PBR 등 추출
        table = soup.find("table", {"summary": re.compile("시세|투자정보")})
        if not table:
            # 대안: per_table 클래스
            table = soup.find("table", class_="per_table")

        if table:
            text = table.get_text(separator="|")
            # PER
            m = re.search(r"PER[|\s]*([0-9,.]+)", text)
            if m:
                result["per"] = _to_float(m.group(1))
            # PBR
            m = re.search(r"PBR[|\s]*([0-9,.]+)", text)
            if m:
                result["pbr"] = _to_float(m.group(1))
            # ROE
            m = re.search(r"ROE[|\s]*([0-9,.]+)", text)
            if m:
                result["roe"] = _to_float(m.group(1))

        # 시가총액 (tab_con1)
        tab_con = soup.find(id="tab_con1")
        if tab_con:
            text = tab_con.get_text(separator="|")
            m = re.search(r"시가총액[|\s]*([\d,]+)\s*억원", text)
            if m:
                cap_uk = _to_float(m.group(1))
                result["market_cap"] = cap_uk * 100_000_000

        # 52주 최고/최저
        full_text = soup.get_text(separator="|")
        m = re.search(r"52주.*?최고[|\s]*([\d,]+)", full_text)
        if m:
            result["52w_high"] = _to_float(m.group(1))
        m = re.search(r"52주.*?최저[|\s]*([\d,]+)", full_text)
        if m:
            result["52w_low"] = _to_float(m.group(1))

        # 외국인 비율
        m = re.search(r"외국인.*?([0-9.]+)\s*%", full_text)
        if m:
            result["foreign_ratio"] = float(m.group(1))

    except ImportError:
        logger.debug("bs4 not available for Naver main page parsing")
    except Exception as e:
        logger.debug("Naver main page parse error: %s", e)

    return result


def _to_float(text: str) -> float:
    """'75,000' → 75000.0"""
    if not text:
        return 0.0
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0
