"""Decode embedded data and select records using ordinary JMESPath expressions."""

import json

import chompjs
import jmespath
from parsel import Selector


def data_value(item, expression: str | None = None, html_selector: str | None = None):
    """Read an optional value; malformed expressions raise a JMESPath error."""
    value = jmespath.search(expression, item) if expression else item
    if value is not None and html_selector:
        return Selector(text=str(value)).css(html_selector).get()
    return value


def records(data, expression: str | None = None, *, mode: str = "list") -> list:
    """Select a required list, singleton object, or dictionary of keyed objects.

    Empty collections are valid. Missing/null collections and unexpected shapes
    raise errors so a changed source cannot silently become a successful crawl.
    List records may themselves be arrays (for tabular APIs).
    """
    value = data_value(data, expression)
    location = expression or "root"
    if mode == "list":
        if not isinstance(value, list) or any(not isinstance(row, (dict, list)) for row in value):
            raise ValueError(f"Expected a list of records at {location}")
        return value
    if mode == "singleton":
        if not isinstance(value, dict):
            raise ValueError(f"Expected a record object at {location}")
        return [value]
    if mode == "keyed":
        if not isinstance(value, dict) or any(not isinstance(row, dict) for row in value.values()):
            raise ValueError(f"Expected keyed record objects at {location}")
        return list(value.values())
    raise ValueError(f"Unknown record mode: {mode}")


def json_script(selector, css: str):
    """Decode exactly one JSON script selected with CSS (including ::text)."""
    blocks = selector.css(css).getall()
    if len(blocks) != 1:
        raise ValueError(f"Expected one JSON script for {css!r}, found {len(blocks)}")
    return json.loads(blocks[0])


def parse_js_literal(text: str):
    """Decode a selected JavaScript object/array literal without executing code.

    The spider must select the intended assignment's right-hand side first.
    """
    literal = text.lstrip()
    if not literal.startswith(("{", "[")):
        raise ValueError("Expected a selected JavaScript object or array literal")
    return chompjs.parse_js_object(literal)
