"""OpenDART 공시 조회 클라이언트.

DART_API_KEY 환경변수가 없으면 빈 결과를 반환합니다 (graceful degradation).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
DART_BASE = "https://opendart.fss.or.kr/api"


class DartClient:
    """OpenDART REST API 클라이언트."""

    def __init__(self) -> None:
        self.api_key = os.getenv("DART_API_KEY", "")
        if not self.api_key:
            logger.info("DART_API_KEY not set — DART features disabled")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def get_today_disclosures(
        self, corp_codes: list[str] | None = None,
    ) -> list[dict]:
        """오늘의 공시 목록 조회.

        Args:
            corp_codes: DART 고유번호 리스트 (없으면 전체 조회).

        Returns:
            list of dicts with keys: corp_name, report_nm, rcept_dt, rcept_no.
        """
        if not self.api_key:
            return []

        today = datetime.now(KST).strftime("%Y%m%d")
        params = {
            "crtfc_key": self.api_key,
            "bgn_de": today,
            "end_de": today,
            "page_count": "100",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{DART_BASE}/list.json", params=params,
                )
                if resp.status_code != 200:
                    logger.warning("DART API returned %d", resp.status_code)
                    return []

                data = resp.json()
                status = data.get("status", "")
                if status == "013":  # 조회 결과 없음
                    return []
                if status != "000":
                    logger.warning("DART API status: %s - %s", status, data.get("message", ""))
                    return []

                items = data.get("list", [])
                results = []
                for item in items:
                    results.append({
                        "corp_code": item.get("corp_code", ""),
                        "corp_name": item.get("corp_name", ""),
                        "report_nm": item.get("report_nm", ""),
                        "rcept_dt": item.get("rcept_dt", ""),
                        "rcept_no": item.get("rcept_no", ""),
                    })
                return results
        except Exception as e:
            logger.warning("DART API error: %s", e)
            return []
