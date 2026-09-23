import socketserver
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from scrapy.http import HtmlResponse
from sqlalchemy import select

import scrapectl.worker as worker
from scrapectl.identity import source_key
from scrapectl.models import FeatureUnit, Record, RecordFeature, ScrapeJob, Scraper, ScrapeResult, Source, SourceRecord
from scrapectl.processing import stage_result
from scrapectl.queue import claim, enqueue
from scraping.crawler.proxy import _read_headers
from scraping.crawler.spiders.EXAMPLE import ExampleSpider

FIXTURES = Path(__file__).parent / "fixtures"


def response():
    return HtmlResponse(
        url=ExampleSpider.start_urls[0],
        body=(FIXTURES / "EXAMPLE-list.html").read_bytes(),
        encoding="utf8",
    )


def items():
    return list(ExampleSpider().parse(response()))


def test_three_records_with_native_keys():
    rows = items()
    assert len(rows) == 3
    assert [item["source_key"] for item in rows] == [
        source_key("example-id", native_id) for native_id in ("101", "102", "103")
    ]
    assert [item["name"] for item in rows] == ["Alpha Hall", "Beta Pavilion", "Gamma Dome"]
    assert rows[0]["url"] == "http://example.invalid/venues/101"


def test_capacity_feature_with_unit_and_missing_stays_missing():
    rows = items()
    assert rows[0]["features"]["Capacity"] == {"value": "1200", "unit": "seats"}
    assert rows[1]["features"]["Capacity"] == {"value": "850", "unit": "seats"}
    assert rows[2].get("features", {}) == {}


def test_changed_listing_count_fails():
    original = response()
    changed = original.replace(
        body=original.body.replace(b'<p class="total">3 venues</p>', b'<p class="total">9 venues</p>')
    )
    with pytest.raises(ValueError, match="listing changed"):
        list(ExampleSpider().parse(changed))


def test_missing_native_id_fails():
    original = response()
    changed = original.replace(body=original.body.replace(b'data-id="101"', b'data-id=""', 1))
    with pytest.raises(ValueError, match="native data-id"):
        list(ExampleSpider().parse(changed))


def test_duplicate_source_key_fails_staging(sessions):
    item = dict(items()[0])
    with sessions() as session:
        session.add(Source(id="EXAMPLE", name="Example Source"))
        session.add(Scraper(
            id="EXAMPLE", source_id="EXAMPLE",
            module="scraping.crawler.spiders.EXAMPLE", enabled=True,
        ))
        session.commit()
        job = enqueue(session, "EXAMPLE", publish=False)
        session.commit()
        assert claim(session, job.id) is not None
        session.commit()
        stage_result(session, job.id, item)
        session.flush()
        with pytest.raises(ValueError, match="Duplicate source_key"):
            stage_result(session, job.id, item)


def test_publish_replay_reprocess_through_local_proxy(sessions, monkeypatch, tmp_path):
    body = (FIXTURES / "EXAMPLE-list.html").read_bytes()
    requests = []

    class Proxy(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            headers, _ = _read_headers(self.request)
            requests.append(headers)
            self.request.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )

    with sessions() as session:
        session.add(Source(id="EXAMPLE", name="Example Source"))
        session.add(Scraper(
            id="EXAMPLE", source_id="EXAMPLE",
            module="scraping.crawler.spiders.EXAMPLE", enabled=True,
        ))
        session.commit()
        job = enqueue(session, "EXAMPLE")
        session.commit()
    monkeypatch.setattr(worker, "Session", sessions)
    monkeypatch.setattr(worker, "database_url", lambda: str(sessions.kw["bind"].url))
    monkeypatch.setenv("VCLIST_CAPTURE_DIR", str(tmp_path / "captures"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    server = socketserver.TCPServer(("127.0.0.1", 0), Proxy)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    real_run = subprocess.run

    def run_fixture(command, **kwargs):
        fixture_job_id = command[-1].split("=", 1)[1]
        offline = int(fixture_job_id) != job.id
        kwargs["env"].update(
            {
                "PROXY_SERVER": "" if offline else f"http://127.0.0.1:{server.server_address[1]}",
                "PROXY_USERNAME": "" if offline else "test-user",
                "PROXY_PASSWORD": "" if offline else "test-pass",
                "NO_PROXY": "*",
                "FIXTURE_JOB_ID": fixture_job_id,
                "FIXTURE_SPIDER": "EXAMPLE",
                "FIXTURE_FAIL": "",
            }
        )
        return real_run([sys.executable, str(Path(__file__).with_name("crawl_fixture.py"))], **kwargs)

    monkeypatch.setattr(worker.subprocess, "run", run_fixture)
    try:
        assert worker.run_one(job.id) is True
        assert len(requests) == 1
        assert requests[0].startswith(b"GET http://example.invalid/list HTTP/1.1")
        assert b"Proxy-Authorization: Basic " in requests[0]
        with sessions() as session:
            rows = list(session.scalars(select(Record).order_by(Record.id)))
            assert len(rows) == 3
            assert rows[0].source_key == source_key("example-id", "101")
            assert rows[0].name == "Alpha Hall"
            assert "test-pass" not in session.get(ScrapeJob, job.id).log
            assert session.scalar(select(SourceRecord).where(SourceRecord.job_id == job.id)) is not None
            assert [unit.name for unit in session.scalars(select(FeatureUnit))] == ["seats"]
            assert len(list(session.scalars(select(RecordFeature)))) == 2
        with sessions() as session:
            replay = enqueue(session, "EXAMPLE", kind="replay", source_job_id=job.id, publish=False)
            session.commit()
        assert worker.run_one(replay.id) is True
        assert len(requests) == 1, "Replay must not contact the proxy"
        with sessions() as session:
            assert session.get(ScrapeJob, replay.id).observed_at == session.get(ScrapeJob, job.id).observed_at
            original = session.scalar(select(ScrapeResult).where(ScrapeResult.job_id == job.id))
            replayed = session.scalar(select(ScrapeResult).where(ScrapeResult.job_id == replay.id))
            assert replayed.payload == original.payload
            reprocess = enqueue(session, "EXAMPLE", kind="reprocess", source_job_id=job.id, publish=False)
            session.commit()

        def forbidden_subprocess(*args, **kwargs):
            raise AssertionError("Reprocessing must not launch a crawler")

        monkeypatch.setattr(worker.subprocess, "run", forbidden_subprocess)
        assert worker.run_one(reprocess.id) is True
        with sessions() as session:
            processed = session.scalar(select(ScrapeResult).where(ScrapeResult.job_id == reprocess.id))
            assert processed.payload == original.payload
            assert session.scalar(select(Record)).last_job_id == job.id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
