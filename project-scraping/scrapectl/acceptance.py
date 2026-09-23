"""Small, deterministic acceptance checks shared by previews and the dispatcher.

Receipts are pointers, not attestations: always reopen their SQLite database in
read-only mode and inspect the actual job. This is error detection for trusted
local contributors, not a sandbox against code with filesystem/DB write access.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from scraping.crawler.report import check_report

SOURCE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
SCHEMA_VERSION = 1


def validate_source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        raise ValueError("source_id must be a Python identifier without path separators")
    return source_id


def _runtime_paths(root: Path) -> set[Path]:
    package = Path(__file__).parent.name
    paths = set()
    for folder in (root / package, root / "scraping/crawler"):
        for path in folder.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path.parent.name == "spiders" and path.name != "__init__.py":
                continue
            paths.add(path)
    for name in ("pyproject.toml", "uv.lock", "scrapy.cfg", "docs/project-contract.md",
                 "ops/dispatch_sources.py"):
        path = root / name
        if path.is_file():
            paths.add(path)
    return paths


def _digest(root: Path, paths: set[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Acceptance inputs must be regular files: {path}")
        relative = path.relative_to(root).as_posix().encode()
        body = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big") + relative)
        digest.update(len(body).to_bytes(8, "big") + body)
    return digest.hexdigest()


def runtime_digest(root: Path) -> str:
    """Shared code/config fingerprint; independent source edits do not change it."""
    root = root.resolve()
    return _digest(root, _runtime_paths(root))


def implementation_digest(root: Path, source_id: str) -> str:
    """Hash one source/test/fixture set and its shared runtime dependencies."""
    validate_source_id(source_id)
    root = root.resolve()
    spider = root / "scraping/crawler/spiders" / f"{source_id}.py"
    if not spider.is_file():
        raise ValueError("Source spider file is missing")
    paths = _runtime_paths(root) | {spider}
    paths.update((root / "tests/fixtures").glob(f"{source_id}-*"))
    for name in (f"tests/test_{source_id}.py", "tests/conftest.py"):
        path = root / name
        if path.is_file():
            paths.add(path)
    return _digest(root, paths)


def validate_spider_name(root: Path, source_id: str) -> None:
    """Check a declared class name without importing unrelated source spiders."""
    path = root / "scraping/crawler/spiders" / f"{validate_source_id(source_id)}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    matches = 0
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for entry in node.body:
            if isinstance(entry, ast.Assign):
                targets, value = entry.targets, entry.value
            elif isinstance(entry, ast.AnnAssign):
                targets, value = [entry.target], entry.value
            else:
                continue
            if any(isinstance(target, ast.Name) and target.id == "name" for target in targets):
                if isinstance(value, ast.Constant) and value.value == source_id:
                    matches += 1
    if matches != 1:
        raise ValueError("Exactly one spider class must explicitly declare name == source_id in its own file")


def parse_verdict(transcript: str, source_id: str) -> dict[str, Any]:
    """Reject missing, multiple, malformed, or internally contradictory verdicts."""
    validate_source_id(source_id)
    lines = [line.removeprefix("VERDICT_JSON:").strip()
             for line in transcript.splitlines() if line.startswith("VERDICT_JSON:")]
    if len(lines) != 1:
        raise ValueError("Evaluator must return exactly one VERDICT_JSON line")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError("Evaluator verdict is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("source_id") != source_id:
        raise ValueError("Evaluator source_id does not match the assignment")
    if value.get("verdict") not in ("succeeded", "failed", "blocked"):
        raise ValueError("Unknown evaluator verdict")
    for key in ("tests_pass", "builder_preview_ok"):
        if type(value.get(key)) is not bool:
            raise ValueError(f"Evaluator {key} must be a boolean")
    for key in ("reasons", "gaps"):
        if not isinstance(value.get(key), list) or any(not isinstance(x, str) for x in value[key]):
            raise ValueError(f"Evaluator {key} must be a list of strings")
    if not isinstance(value.get("notes"), str):
        raise ValueError("Evaluator notes must be a string")
    if value["verdict"] == "succeeded" and (
        not value["tests_pass"] or not value["builder_preview_ok"] or value["reasons"]
    ):
        raise ValueError("Succeeded verdict contradicts failed checks or rejection reasons")
    return value


def write_receipt(path: Path, *, source_id: str, job_id: int, database_path: Path) -> None:
    """Write a portable pointer; it never makes an unsuccessful job acceptable."""
    validate_source_id(source_id)
    payload = {"schema_version": SCHEMA_VERSION, "source_id": source_id,
               "job_id": job_id, "database_path": str(database_path.resolve())}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def verify_preview(receipt_path: Path, *, root: Path, source_id: str,
                   expected_database: Path, expected_digest: str | None = None) -> dict[str, Any]:
    """Verify a concrete preview and its retained/staged counts, without DB writes.

    Replay is acceptable only with a successfully completed live ancestor. An
    ordinary reprocess is not proof of the current parser and is never accepted.
    The dispatcher owns expected_database; an agent cannot redirect acceptance
    to an unrelated or production database by changing the receipt.
    """
    validate_source_id(source_id)
    if not (root / "tests" / f"test_{source_id}.py").is_file():
        raise ValueError("Acceptance requires the source test file")
    validate_spider_name(root, source_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unknown preview receipt schema")
    if receipt.get("source_id") != source_id:
        raise ValueError("Preview receipt belongs to another source")
    job_id = receipt.get("job_id")
    if type(job_id) is not int or job_id < 1:
        raise ValueError("Preview receipt needs a positive integer job_id")
    database_value = receipt.get("database_path")
    if not isinstance(database_value, str) or not Path(database_value).is_absolute():
        raise ValueError("Preview receipt needs an absolute database_path")
    if Path(database_value).is_symlink() or expected_database.is_symlink():
        raise ValueError("Preview database must not be a symlink")
    database = Path(database_value).resolve()
    if database != expected_database.resolve() or not database.is_file():
        raise ValueError("Preview database is missing or outside this source's assigned workspace")
    current_digest = implementation_digest(root, source_id)
    if expected_digest is not None and expected_digest != current_digest:
        raise ValueError("Implementation changed after the acceptance checks")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM scrape_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Preview job does not exist")
        job = dict(row)
        report = json.loads(job["report"] or "{}")
        if not isinstance(report, dict):
            raise ValueError("Preview report must be an object")
        if job["scraper_id"] != source_id or job["status"] != "succeeded" or job["publish"]:
            raise ValueError("Acceptance requires a successful non-publishing job for this source")
        if job["kind"] not in ("live", "replay"):
            raise ValueError("Acceptance requires a live preview or verified offline replay")
        if report.get("implementation_sha256") != current_digest:
            raise ValueError("Preview is stale: source, fixtures, contract, or shared code changed")
        count = check_report(report.get("crawl", {}))
        processing = report.get("processing", {})
        if (not isinstance(processing, dict) or not processing or processing.get("errors") != []
                or not job["processing_version"]
                or processing.get("version") != job["processing_version"]):
            raise ValueError("Preview lacks a successful, versioned processing report")
        retained = connection.execute(
            "SELECT COUNT(*) FROM scrape_source_records WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        staged = connection.execute(
            "SELECT COUNT(*) FROM scrape_results WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        counts = (job["result_count"], retained, staged, processing.get("source_count"),
                  processing.get("result_count"))
        if any(type(value) is not int or value != count for value in counts):
            raise ValueError("Preview crawl, retained, processed, and staged counts disagree")
        # Each result needs an explicit unique identity; evidence URLs may repeat.
        keys = set()
        output_digest = hashlib.sha256()
        for result in connection.execute("SELECT payload FROM scrape_results WHERE job_id = ? ORDER BY id", (job_id,)):
            item = json.loads(result[0])
            if not isinstance(item, dict):
                raise ValueError("Preview payload must be an object")
            serialized = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            output_digest.update(len(serialized).to_bytes(8, "big") + serialized)
            key = item.get("source_key")
            if (not isinstance(key, str) or not key.strip() or key in keys
                    or item.get("source") != source_id):
                raise ValueError("Preview contains missing, duplicate, or wrong-source identities")
            keys.add(key)
        ancestor = job
        visited = {job_id}
        while ancestor["kind"] == "replay":
            parent_id = ancestor["source_job_id"]
            if parent_id in visited or parent_id is None:
                raise ValueError("Replay has no valid live ancestor")
            visited.add(parent_id)
            parent = connection.execute("SELECT * FROM scrape_jobs WHERE id = ?", (parent_id,)).fetchone()
            if parent is None or parent["status"] != "succeeded" or parent["scraper_id"] != source_id:
                raise ValueError("Replay ancestor was not a successful run for this source")
            ancestor = dict(parent)
        if ancestor["kind"] != "live":
            raise ValueError("Replay must originate in a successfully completed live crawl")
        check_report(json.loads(ancestor["report"] or "{}").get("crawl", {}))
        return {"source_id": source_id, "job_id": job_id, "kind": job["kind"],
                "implementation_sha256": current_digest, "result_count": count,
                "processing_version": job["processing_version"],
                "result_sha256": output_digest.hexdigest(),
                "observed_at": job["observed_at"], "coverage": report.get("coverage", {}),
                "live_job_id": ancestor["id"]}
    finally:
        connection.close()
