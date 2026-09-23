"""Descriptive field coverage for stored records and staged previews."""

from collections import Counter

from scrapectl.processing import RECORD_FIELDS
from scraping.crawler.processors import clean_text

MISSING = {"", "n/a", "na", "none", "null", "unknown", "-", "—"}


def present(value):
    return (clean_text(value) or "").casefold() not in MISSING


def field_coverage(payloads, *, requested_features=()):
    rows = list(payloads)
    requested_features = tuple(dict.fromkeys(requested_features))
    total = len(rows)
    fields = {field: sum(present(row.get(field)) for row in rows) for field in RECORD_FIELDS}
    features = {name: {"present": 0, "units": Counter(), "missing": []} for name in requested_features}
    for row in rows:
        for name, feature in (row.get("features") or {}).items():
            feature = feature if isinstance(feature, dict) else {"value": feature}
            entry = features.setdefault(name, {"present": 0, "units": Counter()})
            if present(feature.get("value")):
                entry["present"] += 1
                entry["units"][feature.get("unit") or "unspecified"] += 1
        identity = {key: row.get(key) for key in ("id", "source", "source_key", "name", "url")}
        for name in requested_features:
            feature = (row.get("features") or {}).get(name)
            value = feature.get("value") if isinstance(feature, dict) else feature
            if not present(value):
                features[name]["missing"].append(identity)
    return {
        "total": total,
        "fields": fields,
        "features": dict(sorted(features.items())),
    }


def print_coverage(report):
    total = report["total"]
    print(f"Coverage denominator: {total} stored records (not an upstream completeness assertion)")
    print("Fields: " + "; ".join(f"{name} {count}/{total}" for name, count in report["fields"].items()))
    for name, feature in report["features"].items():
        print(f"Feature {name}: {feature['present']}/{total}; units {dict(feature['units'])}")
