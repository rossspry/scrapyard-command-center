from __future__ import annotations

import logging
import os
import signal
import threading

logger = logging.getLogger(__name__)

def _poll_seconds() -> int:
    try:
        return int(os.getenv("SCC_REOLINK_POLL_SECONDS", "60"))
    except ValueError:
        return 60


def _rtsp_target() -> str:
    host = os.getenv("SCC_RTSP_HOST", "")
    username = os.getenv("SCC_RTSP_USERNAME", "")
    if not host:
        return "unset"
    return host if not username else f"{username}@{host}"


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handler(signum, _frame):
        logger.info("Received signal %s; stopping Reolink watcher", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    logger.info("Starting Reolink watcher", extra={"rtsp_target": _rtsp_target()})

    while not stop_event.is_set():
        logger.debug("Reolink watcher heartbeat", extra={"rtsp_target": _rtsp_target()})
        stop_event.wait(_poll_seconds())


if __name__ == "__main__":  # pragma: no cover
    main()
