"""v9.6.3 공통 안정성 유틸리티.

- retry_async: 지수 백오프 비동기 재시도
- validate_ticker: 콜백 데이터 티커 검증
- TTLCache: 만료 + 최대 크기 캐시
- safe_gather: asyncio.gather + 예외 필터
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ── 1. 지수 백오프 비동기 재시도 ──────────────────────────

async def retry_async(
    coro_fn: Callable[..., Coroutine],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple = (Exception,),
    **kwargs: Any,
) -> Any:
    """비동기 함수를 지수 백오프로 재시도.

    Args:
        coro_fn: 재시도할 async 함수
        max_retries: 최대 시도 횟수 (기본 3)
        base_delay: 기본 대기 시간 초 (기본 1.0)
        exceptions: 재시도 대상 예외 타입들

    Returns:
        coro_fn 실행 결과

    Raises:
        마지막 시도 실패 시 원본 예외 전파
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_fn(*args, **kwargs)
        except exceptions as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                logger.debug(
                    "retry_async [%s] attempt %d/%d failed: %s, retry in %.1fs",
                    coro_fn.__name__ if hasattr(coro_fn, "__name__") else "?",
                    attempt + 1, max_retries, e, delay,
                )
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ── 2. 티커 입력 검증 ────────────────────────────────────

def validate_ticker(code: str) -> str | None:
    """콜백 데이터에서 추출한 티커 코드 검증.

    한국 주식: 6자리 숫자 (005930, 000660)
    ETF/기타: 최대 10자 영숫자 (KODEX200, TIGER 등)

    Returns:
        유효한 코드 문자열 또는 None
    """
    if not code or not isinstance(code, str):
        return None
    code = code.strip()
    if not code:
        return None
    # 길이 제한 (최대 10자)
    if len(code) > 10:
        return None
    # 영숫자만 허용 (SQL injection 방지)
    if not code.isalnum():
        return None
    return code


# ── 3. TTL 캐시 ──────────────────────────────────────────

class TTLCache:
    """만료 시간 + 최대 크기 제한이 있는 간단한 딕셔너리 캐시.

    사용법::

        cache = TTLCache(maxsize=500, ttl_seconds=300)
        cache.set("key", value)
        result = cache.get("key")  # None if expired or missing
    """

    def __init__(self, maxsize: int = 500, ttl_seconds: float = 300) -> None:
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        """키에 해당하는 값 반환. 만료/미존재 시 None."""
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.time() - ts > self._ttl:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """키-값 저장. 최대 크기 초과 시 가장 오래된 항목 제거."""
        if len(self._store) >= self._maxsize and key not in self._store:
            self._evict()
        self._store[key] = (time.time(), value)

    def clear(self) -> None:
        """전체 캐시 초기화."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def _evict(self) -> None:
        """가장 오래된 항목부터 20% 제거."""
        if not self._store:
            return
        n_remove = max(1, len(self._store) // 5)
        sorted_keys = sorted(self._store, key=lambda k: self._store[k][0])
        for k in sorted_keys[:n_remove]:
            del self._store[k]


# ── 4. safe_gather ────────────────────────────────────────

async def safe_gather(
    *coros: Coroutine,
    default: Any = None,
) -> list[Any]:
    """asyncio.gather + return_exceptions=True + Exception을 default로 치환.

    사용법::

        results = await safe_gather(task_a(), task_b(), default=None)
        # Exception 발생한 태스크는 None으로 반환

    Returns:
        결과 리스트 (Exception → default 치환)
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    cleaned = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.warning(
                "safe_gather: task[%d] failed: %s", i, r, exc_info=r,
            )
            cleaned.append(default)
        else:
            cleaned.append(r)
    return cleaned
