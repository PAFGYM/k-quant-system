"""Centralized timezone definitions for K-Quant.

Usage::

    from kstock.core.tz import KST, US_EASTERN, now_kst, now_us_eastern

Korea does NOT observe DST, so a fixed UTC+9 offset is fine.
US Eastern *does* observe DST (EST=UTC-5 / EDT=UTC-4), so we use
``zoneinfo.ZoneInfo`` which handles DST transitions automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo          # Python 3.9+
except ImportError:                        # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# ── Korea Standard Time (UTC+9, no DST) ─────────────────────────
KST = timezone(timedelta(hours=9))

# ── US Eastern (auto DST: EST ↔ EDT) ────────────────────────────
US_EASTERN = ZoneInfo("America/New_York")


def now_kst() -> datetime:
    """Return timezone-aware current datetime in KST."""
    return datetime.now(KST)


def now_us_eastern() -> datetime:
    """Return timezone-aware current datetime in US Eastern (DST-aware)."""
    return datetime.now(US_EASTERN)
