"""Keep-alive pinger for Render's free plan.

Render spins a free web service down after 15 minutes without inbound HTTP traffic.
This starts one daemon thread that requests the service's own public URL every
KEEPALIVE_INTERVAL_SECONDS (default 600 = 10 min), which counts as inbound traffic.

It only runs when RENDER_EXTERNAL_URL is set (Render sets it automatically), so local
runs and tests never start it. Set KEEPALIVE_DISABLED=1 to turn it off on Render.
Note: if the service is already asleep it cannot wake itself; the pinger simply keeps
it from falling asleep after each deploy or wake-up.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("safepay.keepalive")
_started = False
_lock = threading.Lock()


def _loop(url: str, interval: int) -> None:
    time.sleep(60)  # let the app finish booting
    while True:
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                log.info("keep-alive ping %s -> %s", url, r.status)
        except Exception as e:  # never let the thread die
            log.warning("keep-alive ping %s failed: %s", url, e)
        time.sleep(interval)


def start() -> bool:
    """Start the pinger once per process. Returns True if it was started."""
    global _started
    base = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base or os.environ.get("KEEPALIVE_DISABLED") == "1":
        return False
    with _lock:
        if _started:
            return False
        interval = int(os.environ.get("KEEPALIVE_INTERVAL_SECONDS", "600"))
        threading.Thread(target=_loop, args=(f"{base}/api/health", interval),
                         name="keepalive", daemon=True).start()
        _started = True
    return True
