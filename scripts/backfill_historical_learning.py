#!/usr/bin/env python3
"""One-off historical learning backfill for K-Quant."""

from __future__ import annotations

import asyncio
import json

from kstock.bot.historical_learning import (
    backfill_historical_recommendations,
    backfill_learning_memory,
)
from kstock.store.sqlite import SQLiteStore


async def main() -> None:
    db = SQLiteStore()
    rec_stats = await backfill_historical_recommendations(db, limit=500)
    learn_stats = backfill_learning_memory(db, days=180)
    print(
        json.dumps(
            {
                "recommendations": rec_stats,
                "learning": learn_stats,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
