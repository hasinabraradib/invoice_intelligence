"""Background worker placeholder.

Later this will consume extraction jobs from Redis. For now it only proves the container
starts with the same code and settings as the API, then waits until Docker stops it.
"""

import asyncio
import logging
import signal

from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


async def main() -> None:
    get_settings()  # validate configuration at startup, same as the API
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    # `docker compose down` sends SIGTERM; exit promptly instead of being killed after 10s.
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    logger.info("worker ready")
    await stop.wait()
    logger.info("worker shutting down")


if __name__ == "__main__":
    asyncio.run(main())
