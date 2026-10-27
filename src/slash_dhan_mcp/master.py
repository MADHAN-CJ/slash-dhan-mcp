from __future__ import annotations

import contextlib
import logging
import os
import threading
from pathlib import Path

import httpx

from slashdhan.config import today_ist

from .config import Settings

log = logging.getLogger(__name__)


class MasterCache:
    """Downloads Dhan's scrip master once per IST day and serves the file to every script."""

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self._settings = settings
        self._client = client
        self._lock = threading.Lock()

    def path(self) -> Path:
        day = today_ist().isoformat()
        folder = self._settings.cache_dir
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"scrip-master-{day}.csv"
        with self._lock:
            if target.exists():
                return target
            tmp = target.with_suffix(f".{os.getpid()}.part")
            with self._client.stream("GET", self._settings.scrip_master_url) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            os.replace(tmp, target)
            for old in folder.glob("scrip-master-*.csv"):
                if old != target:
                    with contextlib.suppress(OSError):
                        old.unlink()
            log.info("scrip master cached: %s (%d bytes)", target.name, target.stat().st_size)
            return target
