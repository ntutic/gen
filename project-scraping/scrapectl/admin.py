from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrapectl.db import get_db, init_db
from scrapectl.models import Record, ScrapeJob, Scraper, Source, SourceRecord
from scrapectl.queue import enqueue
from scrapectl.settings import REPO_ROOT
from scraping.crawler.capture import capture_directory

DbSession = Annotated[Session, Depends(get_db)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Vclist administration", lifespan=lifespan)


class EnqueueRequest(BaseModel):
    scraper_id: str


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/scrapers")
def scrapers(db: DbSession) -> list[dict]:
    record_counts = (
        select(Record.source_id, func.count(Record.id).label("total"))
        .group_by(Record.source_id).subquery()
    )
    return [
        {"id": row.id, "source_id": row.source_id, "source_name": name,
         "enabled": row.enabled, "record_count": total}
        for row, name, total in db.execute(
            select(Scraper, Source.name, func.coalesce(record_counts.c.total, 0))
            .select_from(Scraper).join(Source, Source.id == Scraper.source_id)
            .outerjoin(record_counts, record_counts.c.source_id == Source.id)
            .order_by(Scraper.source_id, Scraper.id)
        )
    ]


def capture_progress(job_id: int) -> dict:
    """Saved-response count and newest capture time for a job; missing directory means none."""
    try:
        files = [entry for entry in (capture_directory() / str(job_id)).iterdir() if entry.is_file()]
    except OSError:
        return {"files": 0, "latest_at": None}
    if not files:
        return {"files": 0, "latest_at": None}
    latest = max(entry.stat().st_mtime for entry in files)
    return {
        "files": len(files),
        "latest_at": datetime.fromtimestamp(latest, timezone.utc).replace(tzinfo=None).isoformat(),
    }


@app.get("/api/scrape-jobs")
def scrape_jobs(
    db: DbSession,
    source_id: str | None = None,
    status: Annotated[list[Literal["pending", "running", "succeeded", "failed"]] | None, Query()] = None,
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=1_000),
) -> list[dict]:
    query = select(ScrapeJob, Scraper.source_id, Source.name).select_from(ScrapeJob).join(Scraper).join(Source)
    if source_id:
        query = query.where(Scraper.source_id == source_id)
    if status:
        query = query.where(ScrapeJob.status.in_(status))
    if before_id is not None:
        query = query.where(ScrapeJob.id < before_id)
    rows = db.execute(query.order_by(ScrapeJob.id.desc()).limit(limit)).all()
    job_ids = [row.id for row, _, _ in rows]
    staged = dict(
        db.execute(
            select(SourceRecord.job_id, func.count(SourceRecord.id))
            .where(SourceRecord.job_id.in_(job_ids))
            .group_by(SourceRecord.job_id)
        ).all()
    )
    captures = {job_id: capture_progress(job_id) for job_id in job_ids}
    return [
        {
            "id": row.id,
            "scraper_id": row.scraper_id,
            "source_id": job_source_id,
            "source_name": source_name,
            "status": row.status,
            "publish": row.publish,
            "kind": row.kind,
            "source_job_id": row.source_job_id,
            "observed_at": row.observed_at,
            "heartbeat_at": row.heartbeat_at,
            "processing_version": row.processing_version,
            "report": row.report,
            "queued_at": row.queued_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "attempts": row.attempts,
            "result_count": row.result_count,
            "staged_records": staged.get(row.id, 0),
            "capture_files": captures[row.id]["files"],
            "latest_capture_at": captures[row.id]["latest_at"],
            "log": row.log,
        }
        for row, job_source_id, source_name in rows
    ]


@app.post("/api/scrape-jobs", status_code=201)
def enqueue_job(body: EnqueueRequest, db: DbSession) -> dict:
    try:
        job = enqueue(db, body.scraper_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return {"id": job.id, "scraper_id": job.scraper_id, "status": job.status}


app.mount("/", StaticFiles(directory=REPO_ROOT / "web" / "admin", html=True), name="admin")
