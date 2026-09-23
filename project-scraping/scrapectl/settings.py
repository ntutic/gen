from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
DEFAULT_DATABASE_PATH = REPO_ROOT / "vclist.db"
DATABASE_URL_ENV = "VCLIST_DATABASE_URL"


def database_url() -> str:
    return os.environ.get(DATABASE_URL_ENV, f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}")
