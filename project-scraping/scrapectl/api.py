from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrapectl.db import get_db, init_db
from scrapectl.models import Record, Source
from scrapectl.settings import REPO_ROOT

DbSession = Annotated[Session, Depends(get_db)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="Vclist API", lifespan=lifespan)


def _record_dict(row: Record, *, details: bool = False) -> dict:
    result = {
        "id": row.id,
        "source_id": row.source_id,
        "source_key": row.source_key,
        "url": row.url,
        "name": row.name,
        "scraped_at": row.scraped_at,
        "published_at": row.published_at,
    }
    if details:
        result["features"] = [
            {
                "id": feature.id,
                "record_id": feature.record_id,
                "name": feature.name,
                "value": feature.value,
                "sanitized_name": feature.sanitized_name,
                "unit_id": feature.unit_id,
                "unit": {"id": feature.unit.id, "name": feature.unit.name,
                         "sanitized_name": feature.unit.sanitized_name} if feature.unit else None,
            }
            for feature in row.features
        ]
    return result


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/sources")
def sources(db: DbSession) -> list[dict]:
    rows = db.execute(
        select(Source, func.count(Record.id))
        .outerjoin(Record, Record.source_id == Source.id)
        .group_by(Source.id)
        .order_by(Source.name)
    ).all()
    return [
        {"id": row.id, "name": row.name, "url": row.website_url, "kind": row.source_kind,
         "expected_count": row.expected_count, "record_count": total}
        for row, total in rows
    ]


@app.get("/api/records")
def records(
    db: DbSession,
    source_id: str | None = None,
    limit: int = Query(50, ge=1, le=1_000),
    offset: int = Query(0, ge=0),
) -> dict:
    filters = []
    if source_id:
        filters.append(Record.source_id == source_id)
    total = db.scalar(select(func.count()).select_from(Record).where(*filters)) or 0
    rows = db.scalars(select(Record).where(*filters).order_by(Record.id).limit(limit).offset(offset)).all()
    return {"items": [_record_dict(row) for row in rows], "has_more": offset + len(rows) < total, "total": total}


@app.get("/api/records/{record_id}")
def record_detail(record_id: int, db: DbSession) -> dict:
    row = db.get(Record, record_id)
    if row is None:
        raise HTTPException(status_code=404, detail="record not found")
    return _record_dict(row, details=True)


app.mount("/", StaticFiles(directory=REPO_ROOT / "web" / "web", html=True), name="web")
