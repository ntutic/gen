from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scrapectl.admin import app as admin_app
from scrapectl.api import app
from scrapectl.db import get_db
from scrapectl.models import (
    FeatureUnit,
    Record,
    RecordFeature,
    ScrapeJob,
    Scraper,
    Source,
)
from scrapectl.processing import parse_timestamp


@pytest.fixture
def clients(sessions, monkeypatch):
    monkeypatch.setattr("scrapectl.db.engine", sessions.kw["bind"])
    with sessions() as session:
        session.add(Source(id="ACME", name="ACME", website_url="https://example.com",
                           source_kind="html", expected_count=10))
        session.add(Scraper(id="ACME", source_id="ACME", module="scraping.crawler.spiders.ACME"))
        session.add(Scraper(id="OFF", source_id="ACME", module="scraping.crawler.spiders.OFF", enabled=False))
        session.flush()  # Record has no Source relationship; flush parents before children.
        session.add(
            Record(
                source_id="ACME",
                source_hash="hash",
                scraped_at=parse_timestamp("2026-01-01"),
                name="Example",
                url="https://example.com/example",
                features=[RecordFeature(name="Area", value="10", unit=FeatureUnit(name="sq ft"))],
            )
        )
        session.commit()

    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    admin_app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as public, TestClient(admin_app) as admin:
            yield public, admin
    finally:
        app.dependency_overrides.clear()
        admin_app.dependency_overrides.clear()


def test_public_record_api(clients):
    public, _ = clients
    assert public.get("/api/sources").json()[0] == {
        "id": "ACME", "name": "ACME", "url": "https://example.com", "kind": "html",
        "expected_count": 10, "record_count": 1,
    }
    listing = public.get("/api/records").json()
    assert listing["total"] == 1
    assert listing["items"][0]["name"] == "Example"
    assert public.get("/api/records?source_id=UNKNOWN").json()["total"] == 0
    detail = public.get("/api/records/1").json()
    assert detail["features"][0]["name"] == "Area"
    assert detail["features"][0]["unit"]["name"] == "sq ft"
    assert public.get("/api/records/999").status_code == 404


def test_private_admin_operations(clients):
    _, admin = clients
    assert admin.get("/api/scrapers").json()[0] == {
        "id": "ACME", "source_id": "ACME", "source_name": "ACME", "enabled": True, "record_count": 1,
    }
    created = admin.post("/api/scrape-jobs", json={"scraper_id": "ACME"})
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "pending"
    assert admin.post("/api/scrape-jobs", json={"scraper_id": "ACME"}).json()["id"] == job["id"]
    assert admin.get("/api/scrape-jobs").json()[0]["id"] == job["id"]
    assert admin.post("/api/scrape-jobs", json={"scraper_id": "UNKNOWN"}).status_code == 404
    assert admin.post("/api/scrape-jobs", json={"scraper_id": "OFF"}).status_code == 409


def test_admin_filters_active_runs_separately_from_paginated_source_history(clients, sessions):
    _, admin = clients
    with sessions() as session:
        session.add(Source(id="XYZ", name="XYZ"))
        session.add(Scraper(id="XYZ-spider", source_id="XYZ", module="scraping.crawler.spiders.XYZ"))
        session.commit()
        pending = ScrapeJob(scraper_id="ACME", status="pending")
        running = ScrapeJob(scraper_id="OFF", status="running")
        session.add_all([pending, running])
        session.flush()
        session.add_all([ScrapeJob(scraper_id="ACME", status="succeeded") for _ in range(105)])
        failed = ScrapeJob(scraper_id="ACME", status="failed", log="Extraction failed")
        other = ScrapeJob(scraper_id="XYZ-spider", status="succeeded")
        session.add_all([failed, other])
        session.commit()
        pending_id, running_id, failed_id = pending.id, running.id, failed.id

    active = admin.get("/api/scrape-jobs?source_id=ACME&status=pending&status=running").json()
    assert [job["id"] for job in active] == [running_id, pending_id]
    assert all(job["source_name"] == "ACME" for job in active)
    path = "/api/scrape-jobs?source_id=ACME&status=succeeded&status=failed&limit=100"
    recent = admin.get(path).json()
    assert len(recent) == 100
    assert recent[0]["id"] == failed_id
    assert recent[0]["log"] == "Extraction failed"
    older = admin.get(f"{path}&before_id={recent[-1]['id']}").json()
    assert len(older) == 6
    assert not {job["id"] for job in older} & {job["id"] for job in recent}
    assert all(job["source_id"] == "ACME" for job in recent + older)
    assert admin.get(f"{path}&before_id={older[-1]['id']}").json() == []
    assert admin.get("/api/scrape-jobs?source_id=XYZ").json()[0]["scraper_id"] == "XYZ-spider"
    assert admin.get("/api/scrape-jobs?source_id=UNKNOWN").json() == []
    assert admin.get("/api/scrape-jobs?status=unknown").status_code == 422


def test_admin_job_progress_reports_staged_records_and_captures(clients, sessions, tmp_path, monkeypatch):
    from datetime import datetime

    from scrapectl.models import SourceRecord

    monkeypatch.setenv("VCLIST_CAPTURE_DIR", str(tmp_path))
    _, admin = clients
    running_id = admin.post("/api/scrape-jobs", json={"scraper_id": "ACME"}).json()["id"]
    capture_dir = tmp_path / str(running_id)
    capture_dir.mkdir()
    (capture_dir / "response.json.gz").write_bytes(b"{}")
    with sessions() as session:
        session.get(ScrapeJob, running_id).status = "running"
        session.add_all([SourceRecord(job_id=running_id, payload={"a": 1}), SourceRecord(job_id=running_id, payload={"a": 2})])
        session.commit()
    jobs = {job["id"]: job for job in admin.get("/api/scrape-jobs").json()}
    assert jobs[running_id]["staged_records"] == 2
    assert jobs[running_id]["capture_files"] == 1
    assert datetime.fromisoformat(jobs[running_id]["latest_capture_at"])


def test_websites_keep_their_assets_and_routes_separate(clients):
    public, admin = clients
    assert "Vclist records" in public.get("/").text
    assert "Vclist administration" in admin.get("/").text
    assert "Vclist administration" not in public.get("/").text
    assert "Vclist records" not in admin.get("/").text
    assert public.get("/styles.css").status_code == admin.get("/styles.css").status_code == 200
    for path in ("/api/scrapers", "/api/scrape-jobs"):
        assert public.get(path).status_code == 404
        assert path not in public.get("/openapi.json").json()["paths"]
    assert public.post("/api/scrape-jobs", json={"scraper_id": "ACME"}).status_code == 405
    assert admin.get("/api/scrape-jobs").json() == []
    for path in ("/admin/", "/admin/app.js", "/admin/styles.css"):
        assert public.get(path).status_code == 404
    assert admin.get("/api/records").status_code == 404
