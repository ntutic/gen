from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from scrapectl.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website_url: Mapped[str | None] = mapped_column(Text)
    source_kind: Mapped[str | None] = mapped_column(Text)
    expected_count: Mapped[int | None] = mapped_column(Integer)


class Scraper(Base):
    __tablename__ = "scrapers"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    module: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','succeeded','failed')", name="ck_scrape_jobs_status"),
        Index("ix_scrape_jobs_pending", "status", "queued_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scraper_id: Mapped[str] = mapped_column(Text, ForeignKey("scrapers.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    queued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    log: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="live")
    source_job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("scrape_jobs.id", ondelete="RESTRICT"))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    processing_version: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("source_id", "source_hash", name="uq_records_source_hash"),
        Index("ix_records_source", "source_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Imported rows remain unkeyed until explicitly reconciled. New items must
    # supply a key; publication cannot silently duplicate unkeyed records.
    source_key: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    last_job_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("scrape_jobs.id", ondelete="SET NULL"))
    url: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    features: Mapped[list[RecordFeature]] = relationship(
        back_populates="record", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RecordFeature.id",
    )


class FeatureUnit(Base):
    __tablename__ = "feature_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sanitized_name: Mapped[str | None] = mapped_column(Text)


class RecordFeature(Base):
    __tablename__ = "record_features"
    __table_args__ = (UniqueConstraint("record_id", "name", name="uq_record_features_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("records.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("feature_units.id", ondelete="RESTRICT"))
    sanitized_name: Mapped[str | None] = mapped_column(Text)

    record: Mapped[Record] = relationship(back_populates="features")
    unit: Mapped[FeatureUnit | None] = relationship(lazy="joined")


class ScrapeResult(Base):
    __tablename__ = "scrape_results"
    __table_args__ = (UniqueConstraint("job_id", "source_hash", name="uq_scrape_results_job_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False)
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class SourceRecord(Base):
    __tablename__ = "scrape_source_records"
    __table_args__ = (Index("ix_source_records_job", "job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("scrape_jobs.id", ondelete="CASCADE"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
