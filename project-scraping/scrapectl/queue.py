from __future__ import annotations

from datetime import timedelta

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from scrapectl.models import ScrapeJob, Scraper, utc_now


def enqueue(session: Session, scraper_id: str, *, publish: bool = True,
            kind: str = "live", source_job_id: int | None = None) -> ScrapeJob:
    scraper = session.get(Scraper, scraper_id)
    if scraper is None:
        raise LookupError(f"scraper {scraper_id!r} does not exist")
    if publish and not scraper.enabled:
        raise RuntimeError(f"scraper {scraper_id!r} is disabled")
    if kind not in {"live", "replay", "reprocess"}:
        raise ValueError(f"Unknown job kind: {kind}")
    source = session.get(ScrapeJob, source_job_id) if source_job_id is not None else None
    if kind != "live" and (source is None or source.scraper_id != scraper_id or source.status not in ("succeeded", "failed")):
        raise ValueError("Replay/reprocess requires a completed source job for the same source")
    seen = set()
    while source is not None and (source.kind == "reprocess" or (kind == "replay" and source.kind == "replay")):
        if source.id in seen or source.source_job_id is None:
            raise ValueError("Source job has no usable retained source lineage")
        seen.add(source.id)
        source = session.get(ScrapeJob, source.source_job_id)
        if source is None or source.scraper_id != scraper_id:
            raise ValueError("Source job lineage is missing or belongs to another source")
        source_job_id = source.id
    if kind == "live" and source_job_id is not None:
        raise ValueError("Live jobs cannot have a source job")
    if kind == "live" and publish:
        # Serialize enqueue for this source, including multiple weekly schedulers.
        session.execute(update(Scraper).where(Scraper.id == scraper_id).values(enabled=Scraper.enabled))
        existing = session.scalar(select(ScrapeJob).where(
            ScrapeJob.scraper_id == scraper_id, ScrapeJob.kind == "live",
            ScrapeJob.publish.is_(True), ScrapeJob.status.in_(("pending", "running")),
        ).order_by(ScrapeJob.id))
        if existing is not None:
            return existing
    job = ScrapeJob(scraper_id=scraper_id, status="pending", publish=publish,
                    kind=kind, source_job_id=source_job_id,
                    observed_at=source.observed_at if source is not None else None)
    session.add(job)
    session.flush()
    return job


def enqueue_all(session: Session) -> list[ScrapeJob]:
    return [enqueue(session, scraper_id) for scraper_id in session.scalars(
        select(Scraper.id).where(Scraper.enabled.is_(True)).order_by(Scraper.id)
    ).all()]


def claim_next(session: Session) -> ScrapeJob | None:
    candidate = (select(ScrapeJob.id).where(ScrapeJob.status == "pending")
                 .order_by(ScrapeJob.queued_at, ScrapeJob.id).limit(1).scalar_subquery())
    return _claim(session, candidate)


def _claim(session: Session, candidate) -> ScrapeJob | None:
    now = utc_now()
    job_id = session.scalar(update(ScrapeJob)
        .where(ScrapeJob.id == candidate, ScrapeJob.status == "pending")
        .values(status="running", started_at=now, heartbeat_at=now,
                observed_at=case((ScrapeJob.kind == "live", now), else_=ScrapeJob.observed_at),
                attempts=ScrapeJob.attempts + 1)
        .returning(ScrapeJob.id))
    return session.get(ScrapeJob, job_id) if job_id is not None else None


def claim(session: Session, job_id: int) -> ScrapeJob | None:
    return _claim(session, job_id)


def recover(session: Session, *, stale_seconds: int = 300) -> list[int]:
    if stale_seconds < 120:
        raise ValueError("Recovery requires at least 120 seconds without a heartbeat")
    cutoff = utc_now() - timedelta(seconds=stale_seconds)
    ids = list(session.scalars(update(ScrapeJob).where(
        ScrapeJob.status == "running",
        ((ScrapeJob.heartbeat_at < cutoff) |
         (ScrapeJob.heartbeat_at.is_(None) & (ScrapeJob.started_at < cutoff))),
    ).values(status="failed", finished_at=utc_now(), log="Worker heartbeat expired; enqueue a new job to retry.")
        .returning(ScrapeJob.id)))
    return ids


def fail(session: Session, job_id: int, log: str) -> ScrapeJob:
    job = session.get(ScrapeJob, job_id)
    if job is None:
        raise LookupError(f"scrape job {job_id} does not exist")
    job.status = "failed"
    job.finished_at = utc_now()
    job.log = log
    session.flush()
    return job
