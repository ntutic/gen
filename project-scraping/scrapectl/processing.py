"""Deterministic processing of retained source records; no network access."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from scrapectl import project_contract
from scrapectl.identity import record_hash
from scrapectl.models import FeatureUnit, Record, RecordFeature, ScrapeJob, Scraper, ScrapeResult, SourceRecord, utc_now
from scraping.crawler.processors import clean_features, clean_text
from scraping.crawler.report import check_report

# Version 2 is the generic record payload; earlier version numbers predate this canvas.
PROCESSING_VERSION = "2"

RECORD_FIELDS = (
    "url",
    "name",
)


def parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    return utc_now()


def normalize_payload(item: dict[str, Any], source_id: str) -> dict[str, Any]:
    if "_loader_values" in item:
        from urllib.parse import urljoin

        raw = item["_loader_values"]
        extracted = {}
        for field, values in raw.items():
            if field == "features":
                extracted[field] = clean_features(values)
            elif field == "source_key":
                extracted[field] = next((value for value in values if value is not None and value != ""), None)
            else:
                cleaned = [clean_text(value) for value in values]
                extracted[field] = next((value for value in cleaned if value is not None and value != ""), None)
        if extracted.get("url"):
            extracted["url"] = urljoin(item.get("_base_url") or "", clean_text(extracted["url"]) or "")
        item = extracted
    item_source = str(item.get("source") or source_id)
    if item_source != source_id:
        raise ValueError(f"item source {item_source!r} does not match job source {source_id!r}")
    features = item.get("features") or {}
    if not isinstance(features, dict):
        raise ValueError("record features must be an object")
    key = item.get("source_key")
    record_hash(key, source_id)
    payload = {field: clean_text(item.get(field)) for field in RECORD_FIELDS}
    if payload["url"]:
        from urllib.parse import urlsplit

        url = urlsplit(payload["url"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("Record URL must be an absolute HTTP(S) URL without credentials")
    payload.update(
        {
            "source": source_id,
            "source_key": key,
            "timestamp": parse_timestamp(item.get("timestamp")).isoformat(),
            "features": clean_features([features]),
        }
    )
    if not any(payload.get(field) for field in ("name", "url")):
        raise ValueError("record requires at least a name or URL")
    project_contract.validate_payload(payload)
    return payload


def source_payload(item: dict[str, Any]) -> dict[str, Any]:
    """Retain extraction values, including explicit edits made after load_item."""
    from copy import deepcopy

    raw = deepcopy(item.get("raw_payload", item))
    if "_loader_values" not in raw:
        return raw
    loaded = raw.pop("_loaded_values", {})
    for field in set(loaded) | (set(item) - {"raw_payload"}):
        if field not in item:
            raw["_loader_values"].pop(field, None)
        elif field not in loaded or item[field] != loaded[field]:
            raw["_loader_values"][field] = [deepcopy(item[field])]
    return raw


def sync_features(session: Session, row: Record, features: dict) -> None:
    existing = {feature.name: feature for feature in row.features}
    for name, payload in features.items():
        unit_name = payload.get("unit")
        unit = None
        if unit_name is not None:
            unit = session.scalar(select(FeatureUnit).where(FeatureUnit.name == unit_name))
            if unit is None:
                unit = FeatureUnit(name=unit_name)
                session.add(unit)
                session.flush()
        feature = existing.pop(name, None)
        if feature is None:
            feature = RecordFeature(name=name, value=payload["value"], unit=unit)
            row.features.append(feature)
        else:
            feature.value = payload["value"]
            feature.unit = unit
    for feature in existing.values():
        row.features.remove(feature)


def feature_payload(row: Record) -> dict:
    """Serialize related features for source coverage and reviewed import checks."""
    return {feature.name: {"value": feature.value, "unit": feature.unit.name if feature.unit else None}
            for feature in row.features}


def stage_result(session: Session, job_id: int, item: dict[str, Any]) -> ScrapeResult:
    job = session.get(ScrapeJob, job_id)
    if job is None:
        raise LookupError(f"scrape job {job_id} does not exist")
    if job.status != "running":
        raise RuntimeError(f"scrape job {job_id} is not running")
    scraper = session.get(Scraper, job.scraper_id)
    if scraper is None:
        raise LookupError(f"scraper {job.scraper_id!r} does not exist")
    payload = normalize_payload(item, scraper.source_id)
    source_hash = record_hash(payload["source_key"], scraper.source_id)
    result = session.scalar(
        select(ScrapeResult).where(ScrapeResult.job_id == job_id, ScrapeResult.source_hash == source_hash)
    )
    if result is None:
        result = ScrapeResult(job_id=job_id, source_hash=source_hash, payload=payload)
        session.add(result)
    else:
        raise ValueError(f"Duplicate source_key in scrape job {job_id}: {payload['source_key']}")
    session.flush()
    return result


def stage_source(session: Session, job_id: int, item: dict[str, Any]) -> SourceRecord:
    job = session.get(ScrapeJob, job_id)
    if job is None or job.status != "running" or job.kind not in ("live", "replay"):
        raise ValueError("Source staging requires a running live or replay job")
    source = SourceRecord(job_id=job_id, payload=source_payload(item), observed_at=job.observed_at or utc_now())
    session.add(source)
    session.flush()
    return source


def process_job(session: Session, job_id: int) -> int:
    from scrapectl.coverage import field_coverage

    job = session.get(ScrapeJob, job_id)
    if job is None or job.status != "running":
        raise ValueError("Processing requires a running job")
    source_id = job.source_job_id if job.kind == "reprocess" else job.id
    sources = list(session.scalars(select(SourceRecord).where(SourceRecord.job_id == source_id).order_by(SourceRecord.id)))
    if not sources:
        raise ValueError("No retained source records; run a new live scrape before reprocessing")
    session.execute(delete(ScrapeResult).where(ScrapeResult.job_id == job.id))
    errors = []
    processed = 0
    payloads = []
    for source in sources:
        try:
            scraper = session.get(Scraper, job.scraper_id)
            payload = normalize_payload(source.payload, scraper.source_id)
            payload["timestamp"] = source.observed_at.isoformat()
            stage_result(session, job.id, payload)
            payloads.append(payload)
            processed += 1
        except (ValueError, TypeError) as exc:
            errors.append({"source_record_id": source.id, "error": str(exc)})
    job.report = {**(job.report or {}), "processing": {
        "version": PROCESSING_VERSION, "source_count": len(sources),
        "result_count": processed, "errors": errors,
    }, "coverage": field_coverage(payloads)}
    job.processing_version = PROCESSING_VERSION
    job.observed_at = min(source.observed_at for source in sources)
    session.flush()
    return processed


def publish_job(session: Session, job_id: int) -> int:
    # Begin a write transaction before checking freshness. Concurrent publication
    # must not both pass a stale read and then overwrite in the opposite order.
    session.execute(update(Scraper).where(
        Scraper.id == select(ScrapeJob.scraper_id).where(ScrapeJob.id == job_id).scalar_subquery()
    ).values(enabled=Scraper.enabled))
    job = session.get(ScrapeJob, job_id, populate_existing=True)
    if job is None:
        raise LookupError(f"scrape job {job_id} does not exist")
    if job.status != "running":
        raise RuntimeError(f"scrape job {job_id} is not running")
    scraper = session.get(Scraper, job.scraper_id)
    if scraper is None:
        raise LookupError(f"scraper {job.scraper_id!r} does not exist")

    if job.kind == "reprocess":
        source = session.get(ScrapeJob, job.source_job_id)
        crawl = (source.report or {}).get("crawl") if source else None
        if crawl is None:
            raise ValueError("Source job has no verified crawl completion report")
        expected = check_report(crawl)
        retained = session.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.job_id == source.id))
        if retained != expected:
            raise ValueError("Source crawl completion count does not match retained records")

    if (job.report or {}).get("processing", {}).get("errors"):
        raise ValueError("Refusing to publish a job with processing errors")

    unkeyed = session.scalar(
        select(func.count())
        .select_from(Record)
        .where(Record.source_id == scraper.source_id, Record.source_key.is_(None))
    )
    if unkeyed:
        raise ValueError(
            f"{unkeyed} imported {scraper.source_id} records need source keys. "
            "Review a check run and use bind-keys before publishing."
        )

    results = list(session.scalars(select(ScrapeResult).where(ScrapeResult.job_id == job_id).order_by(ScrapeResult.id)))
    if not results:
        raise ValueError("Refusing to publish an empty record collection")
    # Preflight the complete set before mutating any production row.
    for result in results:
        existing = session.scalar(select(Record).execution_options(populate_existing=True).where(
            Record.source_id == scraper.source_id, Record.source_hash == result.source_hash
        ))
        if existing is not None and existing.scraped_at > parse_timestamp(result.payload.get("timestamp")):
            raise ValueError("Refusing to overwrite a newer source observation with historical data")
    published_at = utc_now()
    for result in results:
        row = session.scalar(
            select(Record).where(
                Record.source_id == scraper.source_id,
                Record.source_hash == result.source_hash,
            )
        )
        if row is None:
            row = Record(
                source_id=scraper.source_id,
                source_hash=result.source_hash,
                source_key=result.payload["source_key"],
                scraped_at=parse_timestamp(result.payload.get("timestamp")),
            )
            session.add(row)
        row.scraped_at = parse_timestamp(result.payload.get("timestamp"))
        row.source_key = result.payload["source_key"]
        row.published_at = published_at
        row.last_job_id = job_id
        sync_features(session, row, result.payload.get("features") or {})
        for field in RECORD_FIELDS:
            setattr(row, field, result.payload.get(field))

    job.status = "succeeded"
    job.finished_at = published_at
    job.result_count = len(results)
    session.flush()
    return len(results)


def bind_source_keys(session: Session, mappings: list[dict]) -> int:
    """Assign reviewed keys to existing record IDs, in one caller-owned transaction."""
    seen = set()
    for mapping in mappings:
        record_id = mapping["record_id"]
        if record_id in seen:
            raise ValueError(f"Repeated record ID {record_id}")
        seen.add(record_id)
        row = session.get(Record, record_id)
        if row is None:
            raise LookupError(f"Record {record_id} does not exist")
        key = mapping["source_key"]
        digest = record_hash(key, row.source_id)
        conflict = session.scalar(
            select(Record).where(
                Record.source_id == row.source_id, Record.source_hash == digest, Record.id != row.id
            )
        )
        if conflict:
            raise ValueError(f"Key for record {row.id} already belongs to record {conflict.id}")
        row.source_key = key
        row.source_hash = digest
        session.flush()
    return len(seen)
