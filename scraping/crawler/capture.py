"""Per-job response snapshots: replay is offline, never a network cache."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from scrapy.http import Response, TextResponse

from scraping.crawler.scrapy_selenium import SeleniumRequest

# Explicit allowlist: do not persist cookies or authorization headers.
RESPONSE_HEADERS = (
    b"Content-Type",
    b"Content-Encoding",
    b"Location",
    b"Content-Language",
    b"X-WP-Total",
    b"X-WP-TotalPages",
)
REQUEST_HEADERS = (b"Accept", b"Accept-Language", b"Content-Type")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def capture_directory() -> Path:
    return Path(os.environ.get("VCLIST_CAPTURE_DIR", _repo_root() / "var" / "captures"))


def request_key(request) -> str:
    browser = isinstance(request, SeleniumRequest)
    representation = {
        "method": request.method,
        "url": request.url,
        "body": base64.b64encode(request.body).decode(),
        "headers": [[name.decode(), [v.decode("latin1") for v in request.headers.getlist(name)]]
                    for name in REQUEST_HEADERS],
        "browser": browser,
        "variant": request.meta.get("capture_variant", ""),
    }
    if browser:
        representation["script"] = request.script
        representation["prepare"] = getattr(request.init, "__qualname__", None)
    return hashlib.sha256(json.dumps(representation, sort_keys=True).encode()).hexdigest()


class CaptureStore:
    def __init__(self, root: Path | None = None):
        self.root = root or capture_directory()

    def path(self, job_id: int, request) -> Path:
        return self.root / str(int(job_id)) / f"{request_key(request)}.json.gz"

    def save(self, job_id: int, request, response) -> None:
        path = self.path(job_id, request)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = urlsplit(request.url)
        safe_url = urlunsplit((url.scheme, url.hostname or "", url.path, "", ""))
        data = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "request": {"method": request.method, "url": safe_url,
                        "body_sha256": hashlib.sha256(request.body).hexdigest(),
                        "fingerprint": request_key(request),
                        "browser": isinstance(request, SeleniumRequest)},
            "url": response.url,
            "status": response.status,
            "body": base64.b64encode(response.body).decode(),
            "encoding": response.encoding if isinstance(response, TextResponse) else None,
            "headers": [[name.decode(), [v.decode("latin1") for v in response.headers.getlist(name)]]
                        for name in RESPONSE_HEADERS if name in response.headers],
        }
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as stream:
            json.dump(data, stream)
        if path.exists():
            path.replace(path.with_name(f"{path.stem}.{uuid4().hex}.gz"))
        temporary.replace(path)

    def read(self, job_id: int, request):
        with gzip.open(self.path(job_id, request), "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        kwargs = dict(url=data["url"], status=data["status"], body=base64.b64decode(data["body"]),
                      headers={name: [v.encode("latin1") for v in values] for name, values in data["headers"]},
                      request=request, flags=["replay"])
        if data["encoding"]:
            return TextResponse(**kwargs, encoding=data["encoding"])
        return Response(**kwargs)


class CaptureMiddleware:
    def __init__(self, crawler):
        self.crawler = crawler
        self.store = CaptureStore()

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def process_request(self, request, spider=None):
        spider = spider or self.crawler.spider
        job = getattr(spider, "scrape_job", None)
        request.meta["dont_cache"] = True
        if job is None or job.kind != "replay":
            return None
        try:
            response = self.store.read(job.source_job_id, request)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.crawler.stats.inc_value("capture/missing")
            spider.logger.error("Offline replay capture is missing or invalid; network fallback is prohibited")
            raise ValueError("Offline replay capture missing or invalid") from exc
        self.crawler.stats.inc_value("capture/replayed")
        return response



class CaptureResponseMiddleware(CaptureMiddleware):
    def process_request(self, request, spider=None):
        return None

    def process_response(self, request, response, spider=None):
        spider = spider or self.crawler.spider
        job = getattr(spider, "scrape_job", None)
        if job is not None and job.kind == "live":
            self.store.save(job.id, request, response)
            self.crawler.stats.inc_value("capture/written")
        return response
