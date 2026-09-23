"""Project-owned validation, separate from queue and publication mechanics.

Customize this ordinary function for the generated project's domain. Keep it
pure: no network/database access, identity inference, or mutation. Implement
extra normalization in processing.normalize_payload and bump PROCESSING_VERSION
when output semantics change. source_key is already chosen explicitly by spiders.
"""
from typing import Any


def validate_payload(payload: dict[str, Any]) -> None:
    """Raise ValueError for domain violations; the generic canvas has no extras.

    Example customization: require a reporting period for each period fact, or
    reject a monetary feature with no explicit currency. Do not require fields
    that the project contract permits the upstream source to omit.
    """
