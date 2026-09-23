"""Scrapy download pacing; lock state is independent of capture directories."""
from __future__ import annotations

import asyncio

from scrapy.core.downloader.handlers.http11 import HTTP11DownloadHandler

from scraping.crawler.domain_gate import DomainGate as DomainGate
from scraping.crawler.domain_gate import retry_after_seconds as retry_after_seconds


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
