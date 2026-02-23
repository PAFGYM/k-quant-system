"""Telegram bot with multi-strategy system v3.6 — Multi-AI + Real-time + Security.

Modular architecture: KQuantBot is composed of 6 mixin classes.
Each mixin handles a specific domain (core, menus, trading, scheduler, commands, admin).
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from kstock.bot.mixins import (
    CoreHandlersMixin,
    MenusKisMixin,
    TradingMixin,
    SchedulerMixin,
    CommandsMixin,
    AdminExtrasMixin,
)

logger = logging.getLogger(__name__)


class KQuantBot(
    CoreHandlersMixin,
    MenusKisMixin,
    TradingMixin,
    SchedulerMixin,
    CommandsMixin,
    AdminExtrasMixin,
):
    """K-Quant v3.6 Telegram Bot — Multi-AI + Real-time + Modular."""
    pass


def main() -> None:
    """Entry point: build and run the K-Quant v3.6 Telegram bot with auto-restart."""
    load_dotenv(override=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    bot = KQuantBot()
    if not bot.token:
        logger.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        return

    while True:
        try:
            app = bot.build_app()
            bot.schedule_jobs(app)
            logger.info("K-Quant v3.6 bot starting (polling)...")
            app.run_polling(drop_pending_updates=True)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error("Bot crashed: %s", e, exc_info=True)
            logger.info("Restarting in 10 seconds...")
            time.sleep(10)
            continue


if __name__ == "__main__":
    main()
