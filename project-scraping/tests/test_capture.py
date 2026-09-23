"""Retained-input regressions carried over from reitmaps-kiss."""
import gzip
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse, Response

from scraping.crawler.capture import CaptureMiddleware, CaptureStore, request_key
from scraping.crawler.scrapy_selenium import SeleniumRequest


def test_capture_restores_compressed_response_metadata_without_credentials(tmp_path):
    store = CaptureStore(tmp_path)
    request = Request("https://example.test/data", method="POST", body=b'{"page":2}')
    response = Response(request.url, status=201, body=gzip.compress(b'{"name":"Building"}'), headers={
        "Content-Type": "application/json", "Content-Encoding": "gzip",
        "X-WP-Total": "199", "X-WP-TotalPages": "2",
        "Set-Cookie": "secret", "Authorization": "private",
    })
    store.save(42, request, response)
    replay = store.read(42, request)
    assert replay.status == 201
    assert replay.body == response.body
    assert replay.headers["Content-Encoding"] == b"gzip"
    assert replay.headers["X-WP-Total"] == b"199"
    assert replay.headers["X-WP-TotalPages"] == b"2"
    assert "Set-Cookie" not in replay.headers
    assert "Authorization" not in replay.headers
    with gzip.open(store.path(42, request), "rt") as stream:
        assert "secret" not in json.dumps(json.load(stream))


def test_fingerprints_distinguish_method_body_and_representation():
    url = "https://example.test/data"
    requests = [Request(url), Request(url, method="POST"), Request(url, method="POST", body=b"x"),
                SeleniumRequest(url), SeleniumRequest(url, meta={"capture_variant": "expanded"})]
    assert len({request_key(request) for request in requests}) == len(requests)


def test_browser_replay_returns_prepared_dom_and_never_runs_init(tmp_path):
    store = CaptureStore(tmp_path)
    request = SeleniumRequest("https://example.test", init=Mock())
    response = HtmlResponse("https://example.test/final", body=b"<body>Expanded listing</body>", encoding="utf-8")
    store.save(1, request, response)
    replay = store.read(1, request)
    assert replay.url.endswith("/final")
    assert replay.css("body::text").get() == "Expanded listing"
    request.init.assert_not_called()


def test_missing_replay_is_error_with_no_live_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("VCLIST_CAPTURE_DIR", str(tmp_path))
    spider = SimpleNamespace(scrape_job=SimpleNamespace(kind="replay", source_job_id=1),
                             logger=logging.getLogger("capture-test"))
    crawler = SimpleNamespace(spider=spider, stats=Mock())
    with pytest.raises(ValueError, match="Offline replay"):
        CaptureMiddleware(crawler).process_request(Request("https://example.test/missing"))
    crawler.stats.inc_value.assert_called_once_with("capture/missing")
