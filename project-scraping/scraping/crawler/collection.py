"""Explicit progress checks for ordinary serial Scrapy pagination.

The spider supplies native keys, cursors and a reviewed advertised total. This
helper never fetches, guesses a next page, buffers items, or silently truncates.
"""
from __future__ import annotations

from collections.abc import Iterable


class CollectionProgress:
    def __init__(self, *, expected_total: int | None = None, max_pages: int = 1000, stats=None):
        if expected_total is not None and (type(expected_total) is not int or expected_total < 0):
            raise ValueError("Advertised total must be a nonnegative integer")
        if type(max_pages) is not int or max_pages < 1:
            raise ValueError("max_pages must be a positive integer")
        self.expected_total = expected_total
        self.max_pages = max_pages
        self.cursors: set[str] = set()
        self.keys: set[str] = set()
        self.complete = False
        self._next: str | None = None
        self._stats = stats
        if stats is not None:
            stats.inc_value("collection/incomplete")

    def page(self, cursor: str, keys: Iterable[str], *, next_cursor: str | None) -> bool:
        """Return True only on explicit exhaustion with all supplied checks met.

        Use an explicit sentinel for the first cursor; stringify native cursor
        numbers deliberately. A next link URL works as a cursor too. Supply
        crawler.stats at construction so a filtered/unvisited next request
        also fails the worker's completion report.
        """
        if self.complete:
            raise ValueError("Collection already completed")
        if not isinstance(cursor, str) or not cursor or cursor in self.cursors:
            raise ValueError("Missing or repeated pagination cursor")
        if self.cursors and cursor != self._next:
            raise ValueError("Pagination skipped the advertised next cursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
            raise ValueError("Next cursor must be a nonempty string or explicit None")
        if next_cursor == cursor or next_cursor in self.cursors:
            raise ValueError("Pagination cursor repeated instead of progressing")
        if isinstance(keys, (str, bytes)):
            raise ValueError("Collection keys must be an iterable of keys, not a single string")
        page_keys = list(keys)
        if any(not isinstance(key, str) or not key.strip() for key in page_keys):
            raise ValueError("Every collected record needs an explicit nonempty key")
        unique = set(page_keys)
        if len(unique) != len(page_keys) or unique & self.keys:
            raise ValueError("Duplicate record identities across the collection")
        if not unique and next_cursor is not None:
            raise ValueError("Nonterminal pagination made no record progress")
        count = len(self.keys) + len(unique)
        if self.expected_total is not None and count > self.expected_total:
            raise ValueError("Collection exceeds its advertised total")
        if len(self.cursors) + 1 >= self.max_pages and next_cursor is not None:
            raise ValueError("Pagination safety limit reached before exhaustion")
        if next_cursor is None and self.expected_total is not None and count != self.expected_total:
            raise ValueError(f"Incomplete collection: expected {self.expected_total}, got {count}")
        self.cursors.add(cursor)
        self.keys.update(unique)
        self._next = next_cursor
        self.complete = next_cursor is None
        if self.complete and self._stats is not None:
            self._stats.inc_value("collection/incomplete", -1)
        return self.complete
