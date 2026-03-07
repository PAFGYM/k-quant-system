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

from kstock.core.tz import KST

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
        now = datetime.now(KST)
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
        now = datetime.now(KST)
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
        now = datetime.now(KST)
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
    """네이버 종목 메인 페이지에서 재무정보 추출.

    v9.3.2: per_table 파싱 강화 (외국주 포함 모든 종목 지원).
    """
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

        # ── PER/PBR/EPS/BPS: per_table 클래스 우선, 그 다음 시세 테이블 ──
        per_table = soup.find("table", class_="per_table")
        if per_table:
            per_text = " ".join(per_table.get_text(separator=" ").split())
            # PER 값: 첫 번째 "X.XX 배" (추정PER 전)
            m = re.search(r"PER.*?(\d+\.?\d*)\s*배", per_text)
            if m:
                result["per"] = float(m.group(1))
            # PBR 값: PBR 섹션 내 "계산합니다." 이후 첫 번째 소수점 숫자
            m = re.search(r"PBR.*?계산합니다\.\s*(\d+\.?\d*)", per_text)
            if m:
                result["pbr"] = float(m.group(1))
            elif "PBR" in per_text:
                # 대안: PBR 이후 섹션에서 첫 소수점 숫자 (100 미만)
                pbr_section = per_text.split("PBR")[-1][:200]
                candidates = re.findall(r"(\d+\.\d+)", pbr_section)
                for c in candidates:
                    v = float(c)
                    if 0 < v < 100:  # PBR은 보통 100 미만
                        result["pbr"] = v
                        break
            # EPS 값
            m = re.search(r"EPS.*?(\d[\d,]*)\s*원", per_text)
            if m:
                result["eps"] = _to_float(m.group(1))
            # BPS 값
            bps_section = per_text.split("BPS")[-1][:200] if "BPS" in per_text else ""
            m = re.search(r"(\d[\d,]*)\s*원", bps_section)
            if m:
                result["bps"] = _to_float(m.group(1))

        # 시세 테이블 (per_table에서 못 잡은 경우 보완)
        if not result.get("per"):
            table = soup.find("table", {"summary": re.compile("시세|투자정보")})
            if table:
                text = table.get_text(separator="|")
                m = re.search(r"PER[|\s]*([0-9,.]+)", text)
                if m:
                    result["per"] = _to_float(m.group(1))
                if not result.get("pbr"):
                    m = re.search(r"PBR[|\s]*([0-9,.]+)", text)
                    if m:
                        result["pbr"] = _to_float(m.group(1))

        # v9.3.2: 전체 텍스트에서 공백/파이프 정리 후 검색
        full_text = soup.get_text(separator=" ")
        full_clean = " ".join(full_text.split())

        # ROE: "ROE(지배주주) 7.93" 패턴
        m = re.search(r"ROE.*?(\d+\.?\d*)\s", full_clean)
        if m and 0 < float(m.group(1)) < 200:
            result["roe"] = float(m.group(1))

        # ── 시가총액: tab_con1 우선 (가장 정확) ──
        tab_con = soup.find(id="tab_con1")
        if tab_con:
            tc = " ".join(tab_con.get_text().split())
            m = re.search(r"시가총액\s*([\d,]+)\s*억", tc)
            if m:
                cap_uk = _to_float(m.group(1))
                result["market_cap"] = cap_uk * 100_000_000
        # 전체 페이지 폴백: "시가총액(억) 5,449"
        if not result.get("market_cap"):
            m = re.search(r"시가총액.*?([\d,]+)\s*억", full_clean)
            if m:
                cap_uk = _to_float(m.group(1))
                if cap_uk > 0:
                    result["market_cap"] = cap_uk * 100_000_000

        # ── 배당수익률 ──
        m = re.search(r"배당수익률.*?([0-9,.]+)\s*%", full_clean)
        if m:
            result["dividend_yield"] = float(m.group(1))

        # ── 52주 최고/최저: "52주최고 l 최저 6,750 l 2,495" ──
        m = re.search(r"52주.*?최고.*?최저.*?([\d,]+).*?([\d,]+)", full_clean)
        if m:
            result["52w_high"] = _to_float(m.group(1))
            result["52w_low"] = _to_float(m.group(2))
        else:
            # 개별 검색
            m = re.search(r"52주최고.*?([\d,]+)", full_clean)
            if m:
                result["52w_high"] = _to_float(m.group(1))
            m = re.search(r"52주최저.*?([\d,]+)", full_clean)
            if m:
                result["52w_low"] = _to_float(m.group(1))

        # ── 외국인 비율: "외국인비율(%) 78.65" ──
        m = re.search(r"외국인비율.*?([0-9.]+)", full_clean)
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


# ── 투자자별 매매동향 크롤링 (외국인/기관) ──────────────────────

_FRGN_URL = "https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
_investor_cache: dict[str, tuple[datetime, list[dict]]] = {}


async def get_investor_trading(code: str, days: int = 20) -> list[dict]:
    """네이버 금융에서 투자자별 매매동향 조회.

    외국인/기관 순매매량, 외국인 보유주수/보유율 포함.

    Returns:
        [{date, close, change_pct, volume,
          institution_net, foreign_net,
          foreign_holding, foreign_ratio}, ...]
    """
    now = datetime.now(KST)
    cache_key = f"{code}_{days}"
    if cache_key in _investor_cache:
        cached_time, cached_data = _investor_cache[cache_key]
        if now - cached_time < _CACHE_TTL and cached_data:
            return cached_data

    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("httpx/bs4 not available for investor trading")
        return []

    results: list[dict] = []
    pages_needed = max(1, (days // 20) + 1)

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            for page in range(1, pages_needed + 1):
                resp = await client.get(
                    _FRGN_URL.format(code=code, page=page),
                    headers=_HEADERS,
                )
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                tables = soup.find_all("table", class_="type2")
                if len(tables) < 2:
                    break

                table = tables[1]  # 두 번째 type2 테이블이 매매동향
                rows = table.find_all("tr")

                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) < 9:
                        continue

                    date_text = cols[0].get_text(strip=True)
                    if not date_text or not re.match(r"\d{4}\.\d{2}\.\d{2}", date_text):
                        continue

                    try:
                        date_str = date_text.replace(".", "-")
                        close = _to_float(cols[1].get_text(strip=True))
                        change_pct_raw = cols[3].get_text(strip=True)
                        change_pct = _to_float(change_pct_raw.replace("%", ""))
                        # 하락 감지: img 태그나 class 확인
                        down_img = cols[2].find("img")
                        if down_img and ("down" in str(down_img.get("src", ""))
                                        or "ico_down" in str(down_img.get("alt", ""))):
                            change_pct = -abs(change_pct)

                        volume = _to_float(cols[4].get_text(strip=True))
                        inst_net = _to_float(cols[5].get_text(strip=True))
                        foreign_net = _to_float(cols[6].get_text(strip=True))
                        foreign_holding = _to_float(cols[7].get_text(strip=True))
                        foreign_ratio_raw = cols[8].get_text(strip=True)
                        foreign_ratio = _to_float(
                            foreign_ratio_raw.replace("%", "")
                        )

                        # 순매매 부호 보정 (마이너스 감지)
                        # Naver는 td에 class="num" + span.tah으로 부호 표시
                        for idx, field in [(5, "inst_net"), (6, "foreign_net")]:
                            td_text = cols[idx].get_text(strip=True)
                            if td_text.startswith("-") or td_text.startswith("−"):
                                if field == "inst_net":
                                    inst_net = -abs(inst_net)
                                else:
                                    foreign_net = -abs(foreign_net)

                        results.append({
                            "date": date_str,
                            "close": close,
                            "change_pct": change_pct,
                            "volume": int(volume),
                            "institution_net": int(inst_net),
                            "foreign_net": int(foreign_net),
                            "foreign_holding": int(foreign_holding),
                            "foreign_ratio": foreign_ratio,
                        })

                        if len(results) >= days:
                            break
                    except (ValueError, IndexError):
                        continue

                if len(results) >= days:
                    break

        if results:
            _investor_cache[cache_key] = (now, results)

    except Exception as e:
        logger.debug("Investor trading crawl error for %s: %s", code, e)

    return results


def analyze_investor_trend(data: list[dict]) -> dict:
    """투자자 매매동향 분석 요약.

    Returns:
        {signal, summary, foreign_trend, institution_trend,
         foreign_ratio, foreign_net_total, inst_net_total,
         consecutive_foreign_buy, consecutive_inst_buy}
    """
    if not data:
        return {"signal": "데이터없음", "summary": "수급 데이터 없음"}

    recent = data[:5]  # 최근 5일
    all_data = data[:20]  # 최근 20일

    # 외국인 순매매 합계
    foreign_5d = sum(d.get("foreign_net", 0) for d in recent)
    foreign_20d = sum(d.get("foreign_net", 0) for d in all_data)
    # 기관 순매매 합계
    inst_5d = sum(d.get("institution_net", 0) for d in recent)
    inst_20d = sum(d.get("institution_net", 0) for d in all_data)

    # 연속 매수일 계산
    consec_foreign = 0
    for d in data:
        if d.get("foreign_net", 0) > 0:
            consec_foreign += 1
        else:
            break

    consec_inst = 0
    for d in data:
        if d.get("institution_net", 0) > 0:
            consec_inst += 1
        else:
            break

    # 외국인 보유율
    foreign_ratio = data[0].get("foreign_ratio", 0) if data else 0

    # 시그널 판단
    score = 0
    signals = []

    # 외국인
    if foreign_5d > 0:
        score += 1
        if consec_foreign >= 3:
            score += 1
            signals.append(f"외국인 {consec_foreign}일 연속 순매수")
        else:
            signals.append("외국인 5일 순매수")
    elif foreign_5d < 0:
        score -= 1
        signals.append("외국인 5일 순매도")

    if foreign_20d > 0:
        score += 1
    elif foreign_20d < 0:
        score -= 1

    # 기관
    if inst_5d > 0:
        score += 1
        if consec_inst >= 3:
            score += 1
            signals.append(f"기관 {consec_inst}일 연속 순매수")
        else:
            signals.append("기관 5일 순매수")
    elif inst_5d < 0:
        score -= 1
        signals.append("기관 5일 순매도")

    if inst_20d > 0:
        score += 1
    elif inst_20d < 0:
        score -= 1

    # 외국인+기관 동시 매수
    if foreign_5d > 0 and inst_5d > 0:
        score += 2
        signals.append("외국인+기관 동시 순매수 (강한 수급)")

    # 외국인 보유율 높으면 관심
    if foreign_ratio >= 50:
        signals.append(f"외국인 보유율 {foreign_ratio:.1f}% (고비중)")

    if score >= 4:
        signal = "강한매수"
    elif score >= 2:
        signal = "매수"
    elif score >= 0:
        signal = "중립"
    elif score >= -2:
        signal = "매도"
    else:
        signal = "강한매도"

    summary_parts = []
    summary_parts.append(
        f"외국인: 5일 {foreign_5d:+,}주 / 20일 {foreign_20d:+,}주 "
        f"(보유율 {foreign_ratio:.1f}%)"
    )
    summary_parts.append(
        f"기관: 5일 {inst_5d:+,}주 / 20일 {inst_20d:+,}주"
    )
    if signals:
        summary_parts.append("→ " + ", ".join(signals))

    return {
        "signal": signal,
        "summary": "\n".join(summary_parts),
        "foreign_trend": "매수" if foreign_5d > 0 else "매도",
        "institution_trend": "매수" if inst_5d > 0 else "매도",
        "foreign_ratio": foreign_ratio,
        "foreign_net_5d": foreign_5d,
        "foreign_net_20d": foreign_20d,
        "inst_net_5d": inst_5d,
        "inst_net_20d": inst_20d,
        "consecutive_foreign_buy": consec_foreign,
        "consecutive_inst_buy": consec_inst,
        "score": score,
    }


# ── 섹터/업종별 분석 ──────────────────────────────────────────

_SECTOR_SISE_URL = "https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={sector_id}"
_SECTOR_LIST_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
_sector_cache: dict[str, tuple[datetime, list[dict]]] = {}


async def get_sector_rankings(limit: int = 15) -> list[dict]:
    """네이버 금융에서 업종별 등락률 순위 조회.

    Returns:
        [{name, change_pct, market_cap, volume, top_stocks}, ...]
    """
    now = datetime.now(KST)
    cache_key = "sector_rankings"
    if cache_key in _sector_cache:
        cached_time, cached_data = _sector_cache[cache_key]
        if now - cached_time < _CACHE_TTL and cached_data:
            return cached_data

    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    results: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(_SECTOR_LIST_URL, headers=_HEADERS)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", class_="type_1")
            if not table:
                return []

            rows = table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 4:
                    continue

                name_tag = cols[0].find("a")
                if not name_tag:
                    continue

                name = name_tag.get_text(strip=True)
                if not name:
                    continue

                change_pct = _to_float(
                    cols[1].get_text(strip=True).replace("%", "").replace("+", "")
                )
                # 하락 감지
                td_class = cols[1].get("class", [])
                td_text = cols[1].get_text(strip=True)
                if any("nv01" in c for c in td_class) or td_text.startswith("-"):
                    change_pct = -abs(change_pct)

                results.append({
                    "name": name,
                    "change_pct": change_pct,
                })

                if len(results) >= limit:
                    break

        # 등락률 기준 내림차순 정렬
        results.sort(key=lambda x: x.get("change_pct", 0), reverse=True)

        if results:
            _sector_cache[cache_key] = (now, results)

    except Exception as e:
        logger.debug("Sector ranking crawl error: %s", e)

    return results


def analyze_sector_momentum(sectors: list[dict]) -> dict:
    """섹터 모멘텀 분석.

    Returns:
        {hot_sectors, cold_sectors, market_breadth, summary}
    """
    if not sectors:
        return {"summary": "섹터 데이터 없음"}

    hot = [s for s in sectors if s.get("change_pct", 0) > 1.0]
    cold = [s for s in sectors if s.get("change_pct", 0) < -1.0]
    advancing = len([s for s in sectors if s.get("change_pct", 0) > 0])
    total = len(sectors)

    breadth = advancing / total if total > 0 else 0.5

    summary_parts = []
    if hot:
        top3 = hot[:3]
        hot_str = ", ".join(
            f"{s['name']}({s['change_pct']:+.1f}%)" for s in top3
        )
        summary_parts.append(f"🔥 강세업종: {hot_str}")

    if cold:
        bot3 = cold[-3:]
        cold_str = ", ".join(
            f"{s['name']}({s['change_pct']:+.1f}%)" for s in bot3
        )
        summary_parts.append(f"❄️ 약세업종: {cold_str}")

    breadth_label = (
        "광범위한 상승" if breadth > 0.7
        else "혼조" if breadth > 0.4
        else "광범위한 하락"
    )
    summary_parts.append(
        f"시장 폭: 상승 {advancing}/{total} ({breadth_label})"
    )

    return {
        "hot_sectors": [s["name"] for s in hot[:5]],
        "cold_sectors": [s["name"] for s in cold[-5:]],
        "market_breadth": breadth,
        "advancing": advancing,
        "total": total,
        "summary": "\n".join(summary_parts),
    }


# ── 공매도 조회 (KIS OpenAPI) ────────────────────────────────────

_kis_client_singleton = None


def _get_kis_client():
    """KISClient 싱글톤 반환 (토큰 재사용)."""
    global _kis_client_singleton
    if _kis_client_singleton is None:
        from kstock.ingest.kis_client import KISClient
        _kis_client_singleton = KISClient()
    return _kis_client_singleton


async def get_short_selling(code: str, days: int = 20) -> list[dict]:
    """KIS OpenAPI로 종목별 공매도 일별추이 조회.

    Returns:
        [{date, short_volume, total_volume, short_ratio,
          short_balance, short_balance_ratio}, ...]
    """
    try:
        client = _get_kis_client()
        return await client.get_short_selling(code, days=days)
    except Exception as e:
        logger.debug("Short selling error for %s: %s", code, e)
        return []


# ── 뉴스 크롤링 ──────────────────────────────────────────────

_NEWS_URL = "https://finance.naver.com/item/news_news.naver?code={code}&page=1"


async def get_stock_news(code: str, limit: int = 5) -> list[dict]:
    """네이버 금융에서 종목별 뉴스 크롤링.

    Returns:
        [{title, date, source, url}, ...]
    """
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("httpx/bs4 not available for news")
        return []

    results = []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                _NEWS_URL.format(code=code),
                headers=_HEADERS,
            )
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            # 관련뉴스 테이블
            table = soup.find("table", class_="type5")
            if not table:
                return []

            rows = table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 3:
                    continue

                title_tag = cols[0].find("a")
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                if not title:
                    continue

                href = title_tag.get("href", "")
                url = f"https://finance.naver.com{href}" if href.startswith("/") else href

                source = cols[1].get_text(strip=True) if len(cols) > 1 else ""
                date = cols[2].get_text(strip=True) if len(cols) > 2 else ""

                results.append({
                    "title": title,
                    "date": date,
                    "source": source,
                    "url": url,
                })

                if len(results) >= limit:
                    break

    except Exception as e:
        logger.debug("News crawl error for %s: %s", code, e)

    return results
