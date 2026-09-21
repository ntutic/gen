from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

from sqlalchemy import func, select, update

from scrapectl.db import Session
from scrapectl.models import ScrapeJob, Scraper, SourceRecord, utc_now
from scrapectl.processing import process_job, publish_job
from scrapectl.queue import claim, claim_next, fail
from scrapectl.settings import DATABASE_URL_ENV, REPO_ROOT, database_url
from scraping.crawler.report import check_report

MAX_LOG_CHARS = 1_000_000

#: Eval/check previews (anything that is not a live publishing run) crawl at
#: most 1 request per 2 seconds. AutoThrottle clamps its adaptive delay at
#: DOWNLOAD_DELAY, so overriding this one setting is the whole throttle.
PREVIEW_DOWNLOAD_DELAY = 2


def crawl_command(job: ScrapeJob, timeout: int) -> list[str]:
    """argv for one crawl subprocess, overriding the delay setting for previews."""
    settings = ["-s", "HTTPCACHE_ENABLED=False", "-s", f"CLOSESPIDER_TIMEOUT={timeout}"]
    if not (job.kind == "live" and job.publish):
        settings += ["-s", f"DOWNLOAD_DELAY={PREVIEW_DOWNLOAD_DELAY}"]
    return [sys.executable, "-m", "scrapy", "crawl", job.scraper_id,
            *settings, "-a", f"job_id={job.id}"]


@contextmanager
def _heartbeat(job_id: int):
    stopped = Event()

    def beat():
        while not stopped.wait(30):
            try:
                with Session() as session:
                    session.execute(update(ScrapeJob).where(
                        ScrapeJob.id == job_id, ScrapeJob.status == "running"
                    ).values(heartbeat_at=utc_now()))
                    session.commit()
            except Exception:
                # A failed heartbeat never authorizes publication: completion
                # checks the lease/status again in its transaction.
                continue

    thread = Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join()


def _run_claimed(job: ScrapeJob) -> bool:
    log = ""
    report = None
    try:
        with _heartbeat(job.id):
            with Session() as session:
                scraper = session.get(Scraper, job.scraper_id)
                if scraper is None:
                    raise ValueError(f"scraper {job.scraper_id!r} disappeared")
                if job.publish and not scraper.enabled:
                    raise ValueError(f"scraper {job.scraper_id!r} is disabled")
            if job.kind != "reprocess":
                env = os.environ.copy()
                env[DATABASE_URL_ENV] = database_url()
                timeout = int(env.get("VCLIST_CRAWL_TIMEOUT_SECONDS", "86400"))
                if timeout <= 0:
                    raise ValueError("VCLIST_CRAWL_TIMEOUT_SECONDS must be positive")
                with TemporaryDirectory(prefix="vclist-crawl-") as directory:
                    report_path = Path(directory) / "report.json"
                    env["VCLIST_CRAWL_REPORT"] = str(report_path)
                    result = subprocess.run(
                        crawl_command(job, timeout),
                        cwd=Path(REPO_ROOT), env=env, capture_output=True,
                        text=True, timeout=timeout + 60,
                    )
                    log = (result.stdout + result.stderr)[-MAX_LOG_CHARS:]
                    if report_path.is_file():
                        report = json.loads(report_path.read_text())
                    if result.returncode != 0:
                        raise ValueError(f"Scrapy exited with code {result.returncode}")
            with Session() as session:
                current = session.get(ScrapeJob, job.id)
                if current is None or current.status != "running":
                    raise ValueError("Job no longer owns its running lease")
                current.log = log
                if job.kind == "reprocess":
                    source = session.get(ScrapeJob, job.source_job_id)
                    source_report = (source.report or {}).get("crawl") if source is not None else None
                    if source_report is None:
                        raise ValueError("Source job has no verified crawl completion report")
                    count = check_report(source_report)
                    retained = session.scalar(select(func.count()).select_from(SourceRecord)
                        .where(SourceRecord.job_id == source.id))
                    if retained != count:
                        raise ValueError(f"Source crawl reported {count} records but retained {retained}")
                    current.report = {"crawl": source_report}
                if job.kind != "reprocess":
                    if report is None:
                        raise ValueError("Scrapy did not write a completion report")
                    count = check_report(report)
                    staged = session.scalar(select(func.count()).select_from(SourceRecord)
                        .where(SourceRecord.job_id == job.id))
                    if staged != count:
                        raise ValueError(f"Crawl reported {count} records but staged {staged}")
                    current.report = {"crawl": report}
                try:
                    current.result_count = process_job(session, job.id)
                except ValueError:
                    session.commit()
                    raise
                # Preserve raw records and normalization reports even if validation
                # or publication fails; they are the input for diagnosis/reprocessing.
                session.commit()
                if current.report.get("processing", {}).get("errors"):
                    raise ValueError("Normalization failed; see processing report")
            with Session() as session:
                current = session.get(ScrapeJob, job.id)
                if current.status != "running":
                    raise ValueError("Job no longer owns its running lease")
                if current.publish:
                    publish_job(session, job.id)
                else:
                    current.status = "succeeded"
                    current.finished_at = utc_now()
                    current.log = "Preview: results staged; records unchanged.\n" + log
                session.commit()
            return True
    except Exception as exc:
        with Session() as session:
            current = session.get(ScrapeJob, job.id)
            if current is not None and current.status == "running":
                if report is not None:
                    current.report = {**(current.report or {}), "crawl": report}
                fail(session, job.id, f"crawl/processing/publication failed: {exc}\n{log}")
                session.commit()
        return False


def run_one(job_id: int | None = None) -> bool | None:
    with Session() as session:
        job = claim(session, job_id) if job_id is not None else claim_next(session)
        session.commit()
    if job is None:
        return None
    return _run_claimed(job)


def run_forever(poll_seconds: float = 2.0, *, concurrency: int = 10) -> None:
    if concurrency < 1:
        raise ValueError("Worker concurrency must be positive")
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        running = set()
        while True:
            # Claim only jobs with an available slot, so leases never wait in
            # the executor's queue without a heartbeat.
            while len(running) < concurrency:
                with Session() as session:
                    job = claim_next(session)
                    session.commit()
                if job is None:
                    break
                running.add(pool.submit(_run_claimed, job))
            if running:
                completed, running = wait(running, timeout=poll_seconds, return_when=FIRST_COMPLETED)
                for future in completed:
                    future.result()
            else:
                time.sleep(poll_seconds)
