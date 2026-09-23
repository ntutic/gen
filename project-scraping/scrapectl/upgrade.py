"""Explicit, backed-up database upgrades."""

import fcntl
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import inspect, text

from scrapectl import models  # noqa: F401
from scrapectl.db import Base, engine, init_db


def upgrade_db(*, initialize: bool = False) -> dict:
    if engine.url.get_backend_name() != "sqlite":
        raise ValueError("Database upgrades support SQLite")
    database = Path(engine.url.database or "")
    if not database.is_file() and not initialize:
        raise ValueError("No existing database; use init-db instead")
    # Concurrent starters share one lock beside their shared DB, outside any
    # per-service private temp dir, through backup and migration.
    with database.with_name(database.name + ".upgrade.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if initialize and not inspect(engine).get_table_names():
            init_db(engine)
            return {"backup": None, "added_columns": [], "created_tables": sorted(Base.metadata.tables)}
        return _upgrade_existing(database)


def _upgrade_existing(database: Path) -> dict:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if not {"records", "scrape_jobs", "scrape_results"}.issubset(tables):
            raise ValueError("Expected an existing scrapectl record database")
        missing_tables = sorted(set(Base.metadata.tables) - tables)
        missing_columns = []
        for table in Base.metadata.sorted_tables:
            if table.name in tables:
                existing = {column["name"] for column in inspector.get_columns(table.name)}
                missing_columns.extend(
                    f"{table.name}.{column.name}" for column in table.columns if column.name not in existing
                )
        if not missing_tables and not missing_columns:
            # An ordinary restart must not require stopping healthy workers.
            init_db(engine)
            return {"backup": None, "added_columns": [], "created_tables": []}
        if connection.scalar(text("SELECT COUNT(*) FROM scrape_jobs WHERE status = 'running'")):
            raise ValueError("Stop workers and resolve running jobs before upgrading")

    backup = database.parent / "var" / "backups" / f"before-upgrade-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        for table in Base.metadata.sorted_tables:
            if table.name in missing_tables:
                continue
            existing = {column["name"] for column in inspect(connection).get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    if not column.nullable and column.server_default is None:
                        raise ValueError(f"Cannot add required column {table.name}.{column.name} to a live database")
                    ddl = column.type.compile(dialect=engine.dialect)
                    connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl}"))
        Base.metadata.create_all(connection)
    init_db(engine)
    return {"backup": str(backup), "added_columns": sorted(missing_columns), "created_tables": sorted(missing_tables)}
