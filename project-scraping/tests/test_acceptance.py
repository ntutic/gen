"""Acceptance operates on real SQLite rows, not model-written success text."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scrapectl.acceptance import implementation_digest, parse_verdict, verify_preview, write_receipt
from scraping.crawler.report import check_report


def verdict(**changes):
    return {"source_id": "EXAMPLE", "verdict": "succeeded", "tests_pass": True,
            "builder_preview_ok": True, "reasons": [], "gaps": [], "notes": "Verified", **changes}


def line(value):
    return "VERDICT_JSON: " + json.dumps(value)


def test_valid_verdict():
    assert parse_verdict(line(verdict()), "EXAMPLE")["verdict"] == "succeeded"


@pytest.mark.parametrize("changes", [
    {"tests_pass": False}, {"builder_preview_ok": False}, {"tests_pass": "true"},
    {"builder_preview_ok": 1}, {"reasons": ["Incomplete"]}, {"reasons": "none"},
    {"gaps": [1]}, {"notes": None}, {"source_id": "OTHER"}, {"verdict": "ready"},
])
def test_contradictory_or_malformed_verdict(changes):
    with pytest.raises(ValueError):
        parse_verdict(line(verdict(**changes)), "EXAMPLE")


@pytest.mark.parametrize("text", ["", "VERDICT_JSON: []", "VERDICT_JSON: not-json",
                                  line(verdict()) + "\n" + line(verdict())])
def test_missing_multiple_or_invalid_verdict(text):
    with pytest.raises(ValueError):
        parse_verdict(text, "EXAMPLE")


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for name, content in {
        "scraping/crawler/spiders/EXAMPLE.py": 'class Example:\n    name = "EXAMPLE"\n',
        "tests/test_EXAMPLE.py": "def test_example(): pass\n",
        "scrapectl/processing.py": "VERSION = 1\n",
        "docs/project-contract.md": "Explicit source keys.\n",
        "tests/fixtures/EXAMPLE-list.html": "<p>Two facts</p>",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


@pytest.fixture
def preview(tmp_path):
    root = make_root(tmp_path)
    database = tmp_path / "preview.db"
    report = {"implementation_sha256": implementation_digest(root, "EXAMPLE"),
              "crawl": {"reason": "finished", "stats": {"item_scraped_count": 2}},
              "processing": {"version": "2", "source_count": 2, "result_count": 2, "errors": []},
              "coverage": {}}
    with sqlite3.connect(database) as connection:
        connection.executescript('''
          CREATE TABLE scrape_jobs (
            id INTEGER PRIMARY KEY, scraper_id TEXT, status TEXT, publish INTEGER,
            kind TEXT, source_job_id INTEGER, result_count INTEGER,
            processing_version TEXT, report TEXT, observed_at TEXT);
          CREATE TABLE scrape_source_records (id INTEGER PRIMARY KEY, job_id INTEGER);
          CREATE TABLE scrape_results (id INTEGER PRIMARY KEY, job_id INTEGER, payload TEXT);
        ''')
        connection.execute("INSERT INTO scrape_jobs VALUES (1, 'EXAMPLE', 'succeeded', 0, 'live', NULL, 2, '2', ?, '2026-09-22')",
                           (json.dumps(report),))
        for index in (1, 2):
            connection.execute("INSERT INTO scrape_source_records VALUES (?, 1)", (index,))
            # Sharing a document URL is legitimate; the explicit fact keys differ.
            payload = {"source": "EXAMPLE", "source_key": f"fact-{index}",
                       "url": "https://example.invalid/report.pdf", "name": f"Fact {index}"}
            connection.execute("INSERT INTO scrape_results VALUES (?, 1, ?)", (index, json.dumps(payload)))
    receipt = tmp_path / "preview.json"
    write_receipt(receipt, source_id="EXAMPLE", job_id=1, database_path=database)
    return root, database, receipt, report


def verify(preview):
    root, database, receipt, _ = preview
    return verify_preview(receipt, root=root, source_id="EXAMPLE", expected_database=database)


def test_concrete_success_and_shared_evidence_url(preview):
    assert verify(preview)["result_count"] == 2


@pytest.mark.parametrize("sql", [
    "UPDATE scrape_jobs SET status='failed'",
    "UPDATE scrape_jobs SET publish=1",
    "UPDATE scrape_jobs SET scraper_id='OTHER'",
    "UPDATE scrape_jobs SET kind='reprocess'",
    "UPDATE scrape_jobs SET result_count=1",
    "UPDATE scrape_jobs SET processing_version='old'",
    "DELETE FROM scrape_source_records WHERE id=2",
    "DELETE FROM scrape_results WHERE id=2",
])
def test_failed_unrelated_or_incomplete_job_rejected(preview, sql):
    with sqlite3.connect(preview[1]) as connection:
        connection.execute(sql)
    with pytest.raises(ValueError):
        verify(preview)


@pytest.mark.parametrize("change", ["digest", "errors", "crawl", "count", "empty"])
def test_report_inconsistency(preview, change):
    report = preview[3]
    if change == "digest":
        report.pop("implementation_sha256")
    elif change == "errors":
        report["processing"]["errors"] = [{"error": "bad unit"}]
    elif change == "crawl":
        report["crawl"]["reason"] = "closespider_timeout"
    elif change == "count":
        report["processing"]["source_count"] = 99
    else:
        report["crawl"]["stats"]["item_scraped_count"] = 0
    with sqlite3.connect(preview[1]) as connection:
        connection.execute("UPDATE scrape_jobs SET report=?", (json.dumps(report),))
    with pytest.raises(ValueError):
        verify(preview)


@pytest.mark.parametrize("path", ["scraping/crawler/spiders/EXAMPLE.py", "tests/test_EXAMPLE.py",
                                  "scrapectl/processing.py", "docs/project-contract.md",
                                  "tests/fixtures/EXAMPLE-list.html"])
def test_modified_implementation_or_evidence_is_stale(preview, path):
    (preview[0] / path).write_text("changed")
    with pytest.raises((ValueError, SyntaxError)):
        verify(preview)


def test_other_contributors_do_not_invalidate_this_source(preview):
    root = preview[0]
    (root / "scraping/crawler/spiders/OTHER.py").write_text("unrelated = True")
    (root / "tests/test_OTHER.py").write_text("unrelated = True")
    assert verify(preview)["result_count"] == 2


def test_receipt_cannot_redirect_to_another_database(preview, tmp_path):
    receipt = json.loads(preview[2].read_text())
    receipt["database_path"] = str(tmp_path / "production.db")
    preview[2].write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="database"):
        verify(preview)
    assert not (tmp_path / "production.db").exists(), "Read-only verification must not create a DB"


def test_missing_db_is_not_created(preview):
    preview[1].unlink()
    with pytest.raises(ValueError):
        verify(preview)
    assert not preview[1].exists()


def test_replay_requires_a_successful_live_ancestor(preview):
    root, database, receipt, report = preview
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO scrape_jobs SELECT 2, scraper_id, status, publish, 'replay', 1, result_count, processing_version, report, observed_at FROM scrape_jobs WHERE id=1")
        connection.execute("UPDATE scrape_source_records SET job_id=2")
        connection.execute("UPDATE scrape_results SET job_id=2")
    write_receipt(receipt, source_id="EXAMPLE", job_id=2, database_path=database)
    assert verify(preview)["live_job_id"] == 1
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE scrape_jobs SET status='failed' WHERE id=1")
    with pytest.raises(ValueError, match="ancestor"):
        verify(preview)


def test_replay_cycles_are_rejected(preview):
    with sqlite3.connect(preview[1]) as connection:
        connection.execute("UPDATE scrape_jobs SET kind='replay', source_job_id=1")
    with pytest.raises(ValueError, match="ancestor"):
        verify(preview)


def test_duplicate_explicit_keys_rejected(preview):
    with sqlite3.connect(preview[1]) as connection:
        connection.execute("UPDATE scrape_results SET payload=(SELECT payload FROM scrape_results WHERE id=1) WHERE id=2")
    with pytest.raises(ValueError, match="identities"):
        verify(preview)


@pytest.mark.parametrize("report", [{}, {"reason": "finished", "stats": {}},
                                      {"reason": "finished", "stats": {"item_scraped_count": True}},
                                      {"reason": "finished", "stats": {"item_scraped_count": 1, "capture/missing": 1}}])
def test_completion_report_fails_closed(report):
    with pytest.raises(ValueError):
        check_report(report)
