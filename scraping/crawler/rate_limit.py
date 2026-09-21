"""Cross-process domain pacing for workers sharing the capture volume."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from scrapy.core.downloader.handlers.http11 import HTTP11DownloadHandler

from scraping.crawler.capture import capture_directory


class DomainGate:
    def __init__(self, root: Path | None = None, interval: float = 10):
        self.root = root or capture_directory() / ".throttle"
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
            # Leave a timestamp even if the worker is killed during dispatch.
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


def production_live(spider) -> bool:
    job = getattr(spider, "scrape_job", None)
    return job is not None and job.kind == "live" and job.publish


class PacedHTTPDownloadHandler(HTTP11DownloadHandler):
    async def download_request(self, request):
        if not production_live(self._crawler.spider):
            return await super().download_request(request)
        gate = DomainGate()
        # Hold the shared lock through the actual download. Pacing a middleware
        # before Scrapy's downloader slots can accumulate reservations and burst
        # when a stalled reactor resumes. Completion + 10s is conservative.
        acquire = asyncio.create_task(asyncio.to_thread(gate.acquire, request.url))
        try:
            stream = await asyncio.shield(acquire)
        except asyncio.CancelledError:
            # A thread waiting for flock cannot be cancelled; release its eventual
            # lock so a crawl timeout never strands the shared domain gate.
            acquire.add_done_callback(lambda task: gate.release(task.result()) if not task.exception() else None)
            raise
        cooldown = 0
        try:
            response = await super().download_request(request)
            if response.status in (429, 503):
                cooldown = retry_after_seconds(response.headers.get("Retry-After"))
            return response
        finally:
            gate.release(stream, cooldown)


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
