"""Telegram bot with multi-strategy system v9.1 — Multi-AI + Real-time + Security.

Modular architecture: KQuantBot is composed of 6 mixin classes.
Each mixin handles a specific domain (core, menus, trading, scheduler, commands, admin).
"""
from __future__ import annotations

import logging
import os
import time

from dotenv import load_dotenv

from kstock import APP_NAME
from kstock.bot.mixins import (
    CoreHandlersMixin,
    MenusKisMixin,
    TradingMixin,
    SchedulerMixin,
    CommandsMixin,
    AdminExtrasMixin,
    ControlMixin,
    RemoteClaudeMixin,
)

logger = logging.getLogger(__name__)


class KQuantBot(
    CoreHandlersMixin,
    MenusKisMixin,
    TradingMixin,
    SchedulerMixin,
    CommandsMixin,
    AdminExtrasMixin,
    ControlMixin,
    RemoteClaudeMixin,
):
    """Latest K-Quant Telegram Bot — Multi-AI + Real-time + Modular + Control."""
    pass


def main() -> None:
    """Entry point: build and run the latest K-Quant Telegram bot."""
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
            # 이전 이벤트 루프가 닫혀있을 수 있으므로 새로 생성
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            app = bot.build_app()
            bot.schedule_jobs(app)
            logger.info("%s bot starting (polling)...", APP_NAME)
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
