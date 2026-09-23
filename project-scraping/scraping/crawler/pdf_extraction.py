"""PDF bytes and tables decoded inside ordinary Scrapy response callbacks."""

import logging
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO

import pdfplumber

# Scrapy defaults to DEBUG; pdfminer otherwise logs every token and can displace
# useful crawl errors from the worker's bounded log. Keep warnings and errors.
logging.getLogger("pdfminer").setLevel(logging.WARNING)


def _text(value: str | None) -> str:
    return " ".join((value or "").split())


@contextmanager
def open_pdf(response, *, spider=None):
    """Open downloaded bytes; use as a context manager to release page caches.

    No requests, OCR, or format guessing. A login/error page must fail the crawl.
    """
    if not response.body.lstrip().startswith(b"%PDF-"):
        raise ValueError("Expected PDF response bytes")
    with pdfplumber.open(BytesIO(response.body)) as document:
        yield document
    # The worker persists these only after the entire crawl and staging succeed.
    # Inspection/fixture parsing without a crawler never touches the database.
    if spider is not None and hasattr(spider, "crawler"):
        stats = spider.crawler.stats
        documents = dict(stats.get_value("pdf/documents", {}))
        documents[response.url] = sha256(response.body).hexdigest()
        stats.set_value("pdf/documents", documents)


def pdf_pages(document: pdfplumber.PDF, *, containing: Sequence[str]) -> list:
    """Find pages containing every explicit text marker, independent of page number."""
    if not containing or any(not _text(marker) for marker in containing):
        raise ValueError("PDF page selection requires nonempty text markers")
    pages = []
    for page in document.pages:
        text = _text(page.extract_text())
        if all(_text(marker) in text for marker in containing):
            pages.append(page)
        else:
            page.close()
    if not pages:
        raise ValueError(f"No PDF pages matched {list(containing)!r}; check layout or image-only content")
    return pages


def pdf_table_records(
    page,
    columns: Mapping[str, str],
    *,
    header_rows: int = 1,
    table_settings: dict | None = None,
) -> list[dict[str, str]]:
    """Select exactly one table by its complete header and return named records.

    `columns` maps record keys to printed column labels in source order. Stacked
    headers and wrapped cells are joined with spaces. Supply pdfplumber settings
    (and, if needed, a cropped page) from the inspected source layout. Subtotals,
    footnotes, missing-value conventions and item mapping belong to the spider.
    """
    labels = [_text(label) for label in columns.values()]
    if header_rows < 1 or not labels or any(not label for label in labels):
        raise ValueError("PDF table selection requires columns and a positive header row count")
    matches = []
    for table in page.find_tables(table_settings):
        rows = table.extract()
        header = rows[:header_rows]
        if len(header) != header_rows or any(len(row) != len(labels) for row in header):
            continue
        actual = [_text(" ".join(row[index] or "" for row in header)) for index in range(len(labels))]
        if actual == labels:
            matches.append(rows[header_rows:])
    if len(matches) != 1:
        raise ValueError(f"Expected one PDF table on page {page.page_number}, found {len(matches)} matching headers")
    records = []
    for row in matches[0]:
        if len(row) != len(columns):
            raise ValueError(f"Malformed PDF table row on page {page.page_number}: {row!r}")
        cells = [_text(cell) for cell in row]
        if any(cells):
            records.append(dict(zip(columns, cells, strict=True)))
    if not records:
        raise ValueError(f"PDF table on page {page.page_number} contains no records")
    return records
