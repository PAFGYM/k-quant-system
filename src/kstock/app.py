"""Main runner: starts scheduler and Telegram bot."""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Start the K-Quant system (Telegram bot + scheduled jobs)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env file!")
        sys.exit(1)

    from kstock.bot.bot import KQuantBot

    bot = KQuantBot()
    app = bot.build_app()
    bot.schedule_jobs(app)

    logger.info("K-Quant System v5.9.5 started. Press Ctrl+C to stop.")
    app.run_polling(
        poll_interval=1.0,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
