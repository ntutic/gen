"""Record identity is chosen by the spider, never inferred from mutable fields."""

import hashlib
import json
from urllib.parse import urlsplit


def source_key(namespace: str, *values: str | int) -> str:
    """Build an unambiguous key from an explicit site ID or documented natural key.

    Preserve case and punctuation in opaque IDs. Spiders deliberately normalize
    human-readable keys where appropriate. Empty components are errors.
    """
    parts = [namespace, *values]
    if not values or any(
        isinstance(part, bool) or not isinstance(part, (str, int)) or not str(part).strip() for part in parts
    ):
        raise ValueError("source_key requires a namespace and nonempty string/integer components")
    return json.dumps([str(part) for part in parts], ensure_ascii=False, separators=(",", ":"))


def record_hash(key: str, source_id: str) -> str:
    if not isinstance(key, str) or not key.strip() or len(key) > 2048:
        raise ValueError("Every record requires a nonempty source_key of at most 2048 characters")
    identity = json.dumps([source_id, key], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def url_key(url: str) -> str:
    """For spiders explicitly using a detail URL as identity; retain meaningful query parameters."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL identity requires an absolute HTTP(S) URL without credentials")
    canonical = parsed._replace(path=parsed.path.rstrip("/"), fragment="").geturl()
    return source_key("url", canonical)
