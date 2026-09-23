from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.orm import Session as OrmSession

from scrapectl.settings import database_url


class Base(DeclarativeBase):
    pass


def build_engine(url: str | None = None) -> Engine:
    resolved = url or database_url()
    connect_args = {"check_same_thread": False, "timeout": 30} if resolved.startswith("sqlite") else {}
    engine = create_engine(resolved, connect_args=connect_args)
    if resolved.startswith("sqlite"):
        event.listen(engine, "connect", _configure_sqlite)
    return engine


def _configure_sqlite(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


engine = build_engine()
Session = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(bind: Engine | None = None) -> None:
    from scrapectl import models  # noqa: F401

    target = bind or engine
    inspector = inspect(target)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name in existing:
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            if set(table.columns.keys()) - columns:
                raise RuntimeError("Database schema needs updating; stop workers and run python -m scrapectl upgrade-db")
    Base.metadata.create_all(target)


def get_db() -> Iterator[OrmSession]:
    with Session() as session:
        yield session
