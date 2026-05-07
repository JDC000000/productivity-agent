"""Entry point — boots the Telegram bot.

Run with: `./run.sh` (or `python -m src.main` inside the venv).
"""
import asyncio
import logging

from .bot import build_application
from .logger import setup_logging


def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)

    # Python 3.14 removed the implicit event-loop creation that older
    # versions of asyncio.get_event_loop() used to do. python-telegram-bot
    # v21's sync run_polling() still relies on that older behavior, so we
    # create and install a loop explicitly before calling it.
    asyncio.set_event_loop(asyncio.new_event_loop())

    log.info("Starting productivity agent V1 — Phase 1 (plumbing)")
    app = build_application()
    log.info("Telegram bot initialized, starting polling loop...")

    # `run_polling` blocks forever; Ctrl+C exits cleanly.
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
