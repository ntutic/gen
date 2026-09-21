from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from sqlalchemy import func, select

from scrapectl.coverage import field_coverage, print_coverage
from scrapectl.db import Session, init_db
from scrapectl.models import Record, ScrapeJob, Scraper, ScrapeResult, Source, SourceRecord
from scrapectl.processing import RECORD_FIELDS, bind_source_keys, feature_payload
from scrapectl.queue import enqueue, enqueue_all, recover
from scrapectl.scrapers import definitions, sync_scrapers
from scrapectl.settings import REPO_ROOT
from scrapectl.worker import run_forever, run_one

DEFAULT_ROSTER = REPO_ROOT / "docs" / "sources.csv"


def seed_roster(session, roster_path: Path) -> dict:
    if not roster_path.is_file():
        return {"roster": str(roster_path), "sources": 0, "scrapers": 0, "missing": True}
    with roster_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    sources = scrapers = 0
    for line, row in enumerate(rows, start=2):
        source_id = (row.get("source_id") or "").strip()
        name = (row.get("name") or "").strip()
        if not source_id or not name:
            raise ValueError(f"Roster {roster_path} line {line} needs source_id and name")
        try:
            expected = (row.get("expected_count") or "").strip().replace(",", "")
            expected_count = int(expected) if expected else None
        except ValueError:
            raise ValueError(f"Roster {roster_path} line {line} needs an integer expected_count") from None
        if session.get(Source, source_id) is None:
            session.add(Source(
                id=source_id,
                name=name,
                website_url=(row.get("start_url") or "").strip() or None,
                source_kind=(row.get("source_kind") or "").strip() or None,
                expected_count=expected_count,
            ))
            sources += 1
        if session.get(Scraper, source_id) is None:
            session.add(Scraper(
                id=source_id, source_id=source_id,
                module=f"scraping.crawler.spiders.{source_id}", enabled=False,
            ))
            scrapers += 1
    session.flush()
    return {"roster": str(roster_path), "sources": sources, "scrapers": scrapers, "missing": False}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="scrapectl",
        description="Scrape queue publishing to generic records.",
        epilog="Downstream enrichment, if any, consumes published records; see docs.",
    )
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init-db", help="Initialize the schema and seed sources from the roster")
    init.add_argument("--roster", type=Path, default=DEFAULT_ROSTER,
                      help="Roster CSV (source_id,name,start_url,source_kind,expected_count)")
    commands.add_parser("upgrade-db")
    commands.add_parser("prepare-db", help="Initialize or back up and upgrade the database before service startup")
    commands.add_parser("scrapers")
    coverage = commands.add_parser("coverage", help="Report stored field coverage; no network requests")
    target = coverage.add_mutually_exclusive_group()
    target.add_argument("--job", type=int, help="Inspect staged results instead of published records")
    target.add_argument("--source", help="Limit published records to one source")
    coverage.add_argument("--output", type=Path, help="Write JSON including missing record identities")
    coverage.add_argument("--feature", action="append", default=[],
                          help="Exact feature label to include with missing identities in JSON; repeatable")
    keys = commands.add_parser("bind-keys")
    keys.add_argument("mapping", type=Path)
    check = commands.add_parser("check")
    check.add_argument("scraper_id")
    check.add_argument("--output", type=Path, help="Write staged preview items as JSONL")
    enqueue_parser = commands.add_parser("enqueue")
    enqueue_parser.add_argument("scraper_id", nargs="?")
    enqueue_parser.add_argument("--all", action="store_true")
    recovery = commands.add_parser("recover")
    recovery.add_argument("--stale-seconds", type=int, default=300)
    replay = commands.add_parser("replay")
    replay.add_argument("job", type=int)
    reprocess = commands.add_parser("reprocess")
    source = reprocess.add_mutually_exclusive_group(required=True)
    source.add_argument("--job", type=int)
    source.add_argument("--source")
    for command in (replay, reprocess):
        command.add_argument("--publish", action="store_true")
        command.add_argument("--output", type=Path)
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--concurrency", type=int, default=10, help="Maximum simultaneous scrape jobs (default: 10)")
    scrape = commands.add_parser("scrape")
    scrape.add_argument("scraper_id")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "coverage":
        with Session() as session:
            if args.job is not None:
                job = session.get(ScrapeJob, args.job)
                if job is None:
                    raise ValueError(f"Unknown job {args.job}")
                print(f"Job {job.id}: {job.status}; staged results only")
                payloads = list(session.scalars(select(ScrapeResult.payload).where(ScrapeResult.job_id == job.id)))
            else:
                query = select(Record)
                if args.source:
                    query = query.where(Record.source_id == args.source)
                payloads = [{**{field: getattr(row, field) for field in RECORD_FIELDS},
                             "id": row.id, "source_key": row.source_key, "source": row.source_id,
                             "features": feature_payload(row)} for row in session.scalars(query)]
            report = field_coverage(payloads, requested_features=args.feature)
        print_coverage(report)
        if args.output:
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        return 0
    if args.command in ("upgrade-db", "prepare-db"):
        from scrapectl.upgrade import upgrade_db

        print(json.dumps(upgrade_db(initialize=args.command == "prepare-db"), indent=2))
        return 0
    init_db()
    if args.command == "init-db":
        with Session() as session:
            seeded = seed_roster(session, args.roster)
            sync_scrapers(session)
            session.commit()
        if seeded["missing"]:
            print(f"Schema ready; roster {seeded['roster']} not found, seeded nothing")
        else:
            print(f"Seeded {seeded['sources']} sources and {seeded['scrapers']} scrapers from {seeded['roster']}")
        return 0
    with Session() as session:
        sync_scrapers(session)
        session.commit()
    if args.command == "scrapers":
        spiders = definitions()
        with Session() as session:
            sources = list(session.scalars(select(Source).order_by(Source.id)))
            print("SOURCE   MODE      KIND           UNKEYED  KEY / NOTES")
            for source in sources:
                count = session.scalar(
                    select(func.count())
                    .select_from(Record)
                    .where(Record.source_id == source.id, Record.source_key.is_(None))
                )
                row = session.get(Scraper, source.id)
                mode = "enabled" if row is not None and row.enabled else "disabled"
                spider = spiders.get(source.id)
                kind = (getattr(spider, "source_kind", None) if spider else None) or source.source_kind or "-"
                detail = f"{spider.key_description}; {spider.notes}" if spider else "no spider file yet"
                print(f"{source.id:<8} {mode:<9} {kind:<14} {count:<8} {detail}")
                if spider is not None:
                    print(f"         {spider.__module__}")
                    for url in spider.start_urls:
                        print(f"         {url.split('?')[0]}")
                elif source.website_url:
                    print(f"         {source.website_url}")
        return 0
    if args.command == "bind-keys":
        mappings = [json.loads(line) for line in args.mapping.read_text().splitlines() if line.strip()]
        with Session() as session:
            count = bind_source_keys(session, mappings)
            session.commit()
            print(f"Assigned keys to {count} records")
        return 0
    if args.command == "recover":
        with Session() as session:
            recovered = recover(session, stale_seconds=args.stale_seconds)
            session.commit()
            print(json.dumps({"failed_expired_jobs": recovered}))
        return 0
    if args.command in ("replay", "reprocess"):
        with Session() as session:
            if args.command == "reprocess" and args.source:
                source = session.scalar(select(ScrapeJob).where(
                    ScrapeJob.scraper_id == args.source,
                    ScrapeJob.status.in_(("succeeded", "failed")),
                    select(SourceRecord.id).where(SourceRecord.job_id == ScrapeJob.id).exists(),
                ).order_by(ScrapeJob.observed_at.desc(), ScrapeJob.id.desc()))
            else:
                source = session.get(ScrapeJob, args.job)
            if source is None:
                raise ValueError("No retained source job found")
            job = enqueue(session, source.scraper_id, publish=args.publish,
                          kind=args.command, source_job_id=source.id)
            session.commit()
        return _execute_preview(job.id, args.output)
    if args.command == "check":
        with Session() as session:
            job = enqueue(session, args.scraper_id, publish=False)
            session.commit()
        return _execute_preview(job.id, args.output)
    if args.command == "enqueue":
        with Session() as session:
            if bool(args.scraper_id) == args.all:
                raise ValueError("Specify one source or --all")
            jobs = enqueue_all(session) if args.all else [enqueue(session, args.scraper_id)]
            session.commit()
            for job in jobs:
                print(job.id)
        return 0
    if args.command == "worker":
        if args.once:
            outcome = run_one()
            return 0 if outcome is not False else 1
        run_forever(concurrency=args.concurrency)
        return 0
    if args.command == "scrape":
        with Session() as session:
            job = enqueue(session, args.scraper_id)
            session.commit()
        return 0 if run_one(job.id) else 1
    raise AssertionError(args.command)


def _execute_preview(job_id: int, output_path: Path | None) -> int:
    success = run_one(job_id)
    with Session() as session:
        job = session.get(ScrapeJob, job_id)
        print(f"Job {job.id}: {job.status}; {job.result_count} records staged")
        if coverage := (job.report or {}).get("coverage"):
            print_coverage(coverage)
        if output_path:
            with output_path.open("w") as output:
                for item in session.scalars(select(ScrapeResult).where(ScrapeResult.job_id == job.id)
                                            .order_by(ScrapeResult.id)):
                    output.write(json.dumps(item.payload, ensure_ascii=False) + "\n")
        if not success:
            print(job.log)
    return 0 if success else 1
