"""Acceptance orchestration uses real SQLite; external commands/models are fakes."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from ops import dispatch_sources as dispatch
from scrapectl.acceptance import implementation_digest, verify_preview, write_receipt
from scrapectl.db import Base, build_engine
from scrapectl.models import ScrapeJob, Scraper, ScrapeResult, Source, SourceRecord


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    spiders = root / "scraping/crawler/spiders"
    spiders.mkdir(parents=True)
    (spiders / "EXAMPLE.py").write_text('class Example:\n    name = "EXAMPLE"\n')
    (root / "tests").mkdir()
    (root / "tests/test_EXAMPLE.py").write_text("def test_example():\n    assert True\n")
    (root / "docs").mkdir()
    (root / "docs/project-contract.md").write_text("Use explicit keys.\n")
    roster = root / "docs/sources.csv"
    roster.write_text("source_id,name,start_url,recipe\nEXAMPLE,Example,https://example.invalid,html-list\n")
    for skill in ("spider-builder", "spider-repair"):
        path = root / ".agents/skills" / skill / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("Implement only the assigned scope.")
    args = dispatch.parser().parse_args(["--log-dir", str(tmp_path / "attempts")])
    args.log_dir.mkdir()
    args.review_model = args.model
    prod = build_engine(f"sqlite:///{tmp_path / 'production.db'}")
    Base.metadata.create_all(prod)
    sessions = sessionmaker(bind=prod, expire_on_commit=False)
    monkeypatch.setattr(dispatch, "Session", sessions)
    monkeypatch.setattr(dispatch, "REPO_ROOT", root)
    monkeypatch.setattr(dispatch, "SPIDERS_DIR", spiders)
    monkeypatch.setattr(dispatch, "ROSTER_CSV", roster)
    dispatch.STOP.clear()
    row = dispatch.read_roster(roster)["EXAMPLE"]
    dispatch.ensure_source(row)
    database, receipt = dispatch.preview_paths(args.log_dir, "EXAMPLE")
    database.parent.mkdir(parents=True)
    preview = build_engine(f"sqlite:///{database}")
    Base.metadata.create_all(preview)
    preview_sessions = sessionmaker(bind=preview, expire_on_commit=False)
    revision = implementation_digest(root, "EXAMPLE")
    with preview_sessions() as session:
        session.add(Source(id="EXAMPLE", name="Example"))
        session.flush()
        session.add(Scraper(id="EXAMPLE", source_id="EXAMPLE", module="example", enabled=False))
        session.flush()
        job = ScrapeJob(scraper_id="EXAMPLE", status="succeeded", publish=False, kind="live",
                        result_count=1, processing_version="2", report={
                            "implementation_sha256": revision,
                            "crawl": {"reason": "finished", "stats": {"item_scraped_count": 1}},
                            "processing": {"version": "2", "source_count": 1, "result_count": 1, "errors": []},
                        })
        session.add(job)
        session.flush()
        payload = {"source": "EXAMPLE", "source_key": "native-id", "name": "Example"}
        session.add(SourceRecord(job_id=job.id, payload=payload))
        session.add(ScrapeResult(job_id=job.id, source_hash="digest", payload=payload))
        session.commit()
        write_receipt(receipt, source_id="EXAMPLE", job_id=job.id, database_path=database)
    evidence = verify_preview(receipt, root=root, source_id="EXAMPLE", expected_database=database)
    verdict = {"source_id": "EXAMPLE", "verdict": "succeeded", "tests_pass": True,
               "builder_preview_ok": True, "reasons": [], "gaps": [], "notes": "Reviewed scope"}
    record = {"source_id": "EXAMPLE", "evaluation": verdict, "verdict": "succeeded", "preview": evidence,
              "implementation_sha256": revision, "applied": False}
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return 0

    monkeypatch.setattr(dispatch, "run_command", run)
    yield SimpleNamespace(root=root, args=args, row=row, sessions=sessions, preview=preview_sessions,
                          database=database, receipt=receipt, record=record, commands=commands)
    dispatch.STOP.clear()
    prod.dispose()
    preview.dispose()


def test_apply_is_transactional_and_idempotent(setup):
    job_id = dispatch.apply_record(setup.record, setup.args)
    assert dispatch.enabled("EXAMPLE")
    assert dispatch.apply_record(setup.record, setup.args) == job_id
    with setup.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ScrapeJob)) == 1
        assert session.get(ScrapeJob, job_id).publish is True
    assert len(setup.commands) == 2, "Stored applies re-run source tests"


def test_failed_enqueue_rolls_back_enable(setup, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("queue unavailable")

    monkeypatch.setattr(dispatch, "enqueue", fail)
    with pytest.raises(ValueError, match="queue unavailable"):
        dispatch.apply_record(setup.record, setup.args)
    assert not dispatch.enabled("EXAMPLE")
    assert not setup.record["applied"]


def test_applied_record_does_not_undo_manual_disabling(setup):
    dispatch.apply_record(setup.record, setup.args)
    with setup.sessions() as session:
        session.get(Scraper, "EXAMPLE").enabled = False
        session.commit()
    with pytest.raises(ValueError, match="disabled"):
        dispatch.apply_record(setup.record, setup.args)
    assert not dispatch.enabled("EXAMPLE")


@pytest.mark.parametrize("field", ["tests_pass", "builder_preview_ok"])
def test_contradictory_success_never_applies(setup, field):
    setup.record["evaluation"][field] = False
    with pytest.raises(ValueError, match="contradicts"):
        dispatch.apply_record(setup.record, setup.args)
    assert not dispatch.enabled("EXAMPLE")
    assert not setup.commands


def test_stale_code_never_applies(setup):
    (setup.root / "tests/test_EXAMPLE.py").write_text("# a later edit\n")
    with pytest.raises(ValueError, match="changed"):
        dispatch.apply_record(setup.record, setup.args)
    assert not dispatch.enabled("EXAMPLE")


def test_changed_output_never_applies(setup):
    with setup.preview() as session:
        result = session.scalar(select(ScrapeResult))
        result.payload = {**result.payload, "name": "Changed after review"}
        session.commit()
    with pytest.raises(ValueError, match="after review"):
        dispatch.apply_record(setup.record, setup.args)
    assert not dispatch.enabled("EXAMPLE")


def test_failed_tests_stop_before_model_cost(setup, monkeypatch):
    monkeypatch.setattr(dispatch, "run_command", lambda *a, **k: 1)
    monkeypatch.setattr(dispatch, "run_agent", lambda *a, **k: pytest.fail("Must not call a reviewer"))
    with pytest.raises(ValueError, match="tests failed"):
        dispatch.evaluate(setup.row, setup.args, {})
    assert not dispatch.enabled("EXAMPLE")


def test_missing_preview_stops_before_tests_and_model(setup, monkeypatch):
    setup.receipt.unlink()
    monkeypatch.setattr(dispatch, "run_agent", lambda *a, **k: pytest.fail("Must not call a reviewer"))
    with pytest.raises(FileNotFoundError):
        dispatch.evaluate(setup.row, setup.args, {})
    assert not setup.commands


def reviewer(setup, monkeypatch, *, exit_code=0, change_output=False):
    def fake(role, row, args, prompt):
        assert role == "eval"
        assert len(setup.commands) == 1, "Tests precede review"
        assert "Do not rerun tests or crawl" in prompt
        if change_output:
            with setup.preview() as session:
                result = session.scalar(select(ScrapeResult))
                result.payload = {**result.payload, "name": "Replaced during review"}
                session.commit()
        log = args.log_dir / "model.log"
        log.write_text("VERDICT_JSON: " + json.dumps(setup.record["evaluation"]) + "\n")
        return exit_code, "", {"role": role, "model": "test", "exit_code": exit_code,
                               "seconds": 1, "log": str(log)}

    monkeypatch.setattr(dispatch, "run_agent", fake)


def test_fresh_acceptance_does_not_repeat_tests(setup, monkeypatch):
    reviewer(setup, monkeypatch)
    result = dispatch.evaluate(setup.row, setup.args, {})
    assert result["applied"]
    assert len(setup.commands) == 1
    assert result["attempts"][0]["role"] == "eval"


def test_no_apply_records_evidence_without_enabling(setup, monkeypatch):
    reviewer(setup, monkeypatch)
    setup.args.no_apply = True
    result = dispatch.evaluate(setup.row, setup.args, {})
    assert result["verdict"] == "succeeded" and not result["applied"]
    assert not dispatch.enabled("EXAMPLE")


def test_evaluator_crash_cannot_apply_success_in_its_output(setup, monkeypatch):
    reviewer(setup, monkeypatch, exit_code=1)
    with pytest.raises(ValueError, match="Evaluator failed"):
        dispatch.evaluate(setup.row, setup.args, {})
    assert not dispatch.enabled("EXAMPLE")


def test_evaluator_cannot_change_accepted_output(setup, monkeypatch):
    reviewer(setup, monkeypatch, change_output=True)
    with pytest.raises(ValueError, match="during review"):
        dispatch.evaluate(setup.row, setup.args, {})
    assert not dispatch.enabled("EXAMPLE")


def test_source_environment_and_assignment_are_scoped(setup):
    env = dispatch.source_env(setup.args.log_dir, "EXAMPLE")
    assert env[dispatch.DATABASE_URL_ENV] == f"sqlite:///{setup.database}"
    assert Path(env["VCLIST_CAPTURE_DIR"]).parent == setup.database.parent
    prompt = dispatch.assignment(setup.row, setup.args.log_dir)
    assert "docs/recipes/html-list.md" in prompt
    assert str(setup.receipt) in prompt
    assert "Do not override" in prompt


def test_roster_rejects_duplicate_ids_and_unknown_recipes(tmp_path):
    path = tmp_path / "sources.csv"
    for rows in ("EXAMPLE,html-list\nEXAMPLE,pdf-table\n", "EXAMPLE,unknown\n", "../BAD,html-list\n"):
        path.write_text("source_id,recipe\n" + rows)
        with pytest.raises(ValueError):
            dispatch.read_roster(path)


def test_dry_run_does_not_launch_agents_or_initialize_db(setup, monkeypatch):
    monkeypatch.setattr(dispatch, "init_db", lambda: pytest.fail("Dry run must not initialize DB"))
    monkeypatch.setattr(dispatch, "run_agent", lambda *a: pytest.fail("Dry run must not run agents"))
    assert dispatch.main(["--dry-run", "--only", "EXAMPLE", "--log-dir", str(setup.args.log_dir)]) == 0


def test_default_dispatch_picks_up_no_eval_build(setup, monkeypatch):
    dispatch.save_record(setup.args.log_dir, {"source_id": "EXAMPLE", "verdict": None, "applied": False})
    monkeypatch.setattr(dispatch, "init_db", lambda: None)
    monkeypatch.setattr(dispatch.shutil, "which", lambda _: "/fake/muse")
    seen = []
    monkeypatch.setattr(dispatch, "run_source", lambda row, args: seen.append(row["source_id"]) or True)
    assert dispatch.main(["--log-dir", str(setup.args.log_dir)]) == 0
    assert seen == ["EXAMPLE"]


def test_reconcile_does_not_register_or_enable(setup, monkeypatch):
    monkeypatch.setattr(dispatch, "ensure_source", lambda _: pytest.fail("Reconcile is read-only"))
    setup.args.reconcile = True
    assert dispatch.run_source(setup.row, setup.args)
    assert not dispatch.enabled("EXAMPLE")


@pytest.mark.parametrize("payload", [
    {"source_id": "OTHER", "category": "access_denied", "reason": "403"},
    {"source_id": "EXAMPLE", "category": "empty_output", "reason": "Zero rows"},
    {"source_id": "EXAMPLE", "category": "access_denied", "reason": ""},
])
def test_invalid_blocker_is_not_terminal(payload, tmp_path):
    path = tmp_path / "worker.log"
    path.write_text("BLOCKED_JSON: " + json.dumps(payload))
    assert dispatch.blocked_report(path, "EXAMPLE") is None


def test_confirmed_blocker_stops_retry_budget_without_acceptance(setup, monkeypatch):
    setup.args.force = True
    setup.args.max_retries = 3
    calls = []

    def agent(role, row, args, prompt):
        calls.append(role)
        output = args.log_dir / "blocked.log"
        output.write_text('BLOCKED_JSON: {"source_id":"EXAMPLE","category":"access_denied","reason":"Three proxied probes blocked"}\n')
        return 0, "Blocked", {"role": role, "log": str(output)}

    monkeypatch.setattr(dispatch, "run_agent", agent)
    assert dispatch.run_source(setup.row, setup.args) is False
    assert calls == ["build"]
    assert dispatch.read_record(setup.args.log_dir, "EXAMPLE")["verdict"] == "blocked"
    assert not dispatch.enabled("EXAMPLE")
