"""Cross-process hostname pacing, independent of per-job capture storage."""
from __future__ import annotations

import fcntl
import hashlib
import os
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit


def pacing_directory() -> Path:
    configured = os.environ.get("VCLIST_RATE_LIMIT_DIR")
    if configured:
        return Path(configured).expanduser()
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    # Same default across generated projects on this host/user. Containers or
    # multiple users must explicitly mount/configure the same flock-capable dir.
    return state / "scraping" / "rate-limit"


class DomainGate:
    def __init__(self, root: Path | None = None, interval: float = 10):
        if interval <= 0:
            raise ValueError("Pacing interval must be positive")
        self.root = root if root is not None else pacing_directory()
        self.interval = interval
        self.root.mkdir(parents=True, exist_ok=True)

    def acquire(self, url: str):
        domain = urlsplit(url).hostname
        if not domain:
            raise ValueError("Cannot pace a request without a hostname")
        path = self.root / hashlib.sha256(domain.lower().encode()).hexdigest()
        stream = path.open("a+")
        try:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            last = float(stream.read() or 0)
            delay = last + self.interval - time.time()
            if delay > 0:
                time.sleep(delay)
            stream.seek(0)
            stream.truncate()
            stream.write(str(time.time()))
            stream.flush()
            return stream
        except BaseException:
            stream.close()
            raise

    def release(self, stream, cooldown: float = 0):
        try:
            stream.seek(0)
            stream.truncate()
            stream.write(str(time.time() + max(0, cooldown - self.interval)))
            stream.flush()
        finally:
            stream.close()

    def run(self, url: str, start=lambda: None):
        stream = self.acquire(url)
        try:
            return start()
        finally:
            self.release(stream)


def retry_after_seconds(value: bytes | None) -> float:
    if not value:
        return 0
    try:
        text = value.decode("ascii")
        if text.strip().isdigit():
            return max(0, int(text))
        return max(0, parsedate_to_datetime(text).timestamp() - time.time())
    except (UnicodeError, ValueError, TypeError, OverflowError):
        return 0
