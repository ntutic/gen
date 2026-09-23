"""Small value normalizers shared by every record loader."""

import math
import re
from decimal import Decimal
from html import unescape
from urllib.parse import urljoin

from itemloaders.processors import MapCompose
from w3lib.html import replace_tags


def clean_text(value):
    if value is None:
        return None
    text = replace_tags(unescape(str(value)), " ")
    return re.sub(r"\s+", " ", text).strip().strip(",").strip() or None


str_clean = MapCompose(clean_text)


def clean_float(value):
    text = clean_text(value)
    if text is None:
        return None
    number = float(text.replace(",", ""))
    if not math.isfinite(number):
        raise ValueError(f"Expected a finite number, received {value!r}")
    return number


float_clean = MapCompose(clean_float)


def positive_number(value):
    """Parse a complete, explicitly selected quantity; never pull numbers from prose."""
    text = clean_text(value) or ""
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        raise ValueError(f"Expected a positive numeric quantity, received {value!r}")
    number = Decimal(text.replace(",", ""))
    if number <= 0:
        raise ValueError(f"Expected a positive numeric quantity, received {value!r}")
    return format(number, "f")


def absolute_url(value, loader_context):
    value = clean_text(value)
    if value is None:
        return None
    response = loader_context.get("response")
    return urljoin(response.url, value) if response is not None else value


def clean_features(values):
    """Normalize feature scalars to {value: string, unit: string | None}."""
    result = {}
    for features in values:
        if not isinstance(features, dict):
            raise ValueError("record features must be an object")
        for name, feature in features.items():
            name = clean_text(name)
            if isinstance(feature, dict):
                value = clean_text(feature.get("value"))
                unit = clean_text(feature.get("unit"))
            else:
                value, unit = clean_text(feature), None
            if name and value is not None:
                result[name] = {"value": value, "unit": unit}
    return result
