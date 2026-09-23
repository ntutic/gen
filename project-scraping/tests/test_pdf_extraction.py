"""Generic wrapper regressions adapted from reitmaps-kiss; no company fixture.

These isolate our header selection, page selection and digest contracts from
pdfplumber's layout engine. Source-specific fixtures still test real PDF layouts.
"""
from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace

import pytest

from scraping.crawler import pdf_extraction as pdf


def table_page(*tables):
    return SimpleNamespace(page_number=3, find_tables=lambda _: [
        SimpleNamespace(extract=lambda rows=rows: rows) for rows in tables
    ])


def test_stacked_headers_wrapping_zero_and_missing_values():
    page = table_page([["Name", "Reported"], [None, "quantity"],
                       ["Alpha\n Hall", "0"], ["Beta", None]])
    assert pdf.pdf_table_records(page, {"name": "Name", "quantity": "Reported quantity"}, header_rows=2) == [
        {"name": "Alpha Hall", "quantity": "0"}, {"name": "Beta", "quantity": ""},
    ]


def test_selects_intended_header_not_largest_table():
    page = table_page([["Other"], ["a"], ["b"], ["c"]], [["Name"], ["Alpha"]])
    assert pdf.pdf_table_records(page, {"name": "Name"}) == [{"name": "Alpha"}]


def test_ambiguous_table_is_rejected():
    rows = [["Name", "City"], ["Centre", "Toronto"]]
    with pytest.raises(ValueError, match="found 2"):
        pdf.pdf_table_records(table_page(rows, rows), {"name": "Name", "city": "City"})


@pytest.mark.parametrize("rows", [[["Name"]], [["Name"], ["Alpha", "Extra"]], [["Wrong"], ["Alpha"]]])
def test_empty_malformed_or_changed_headers_fail(rows):
    with pytest.raises(ValueError):
        pdf.pdf_table_records(table_page(rows), {"name": "Name"})


def test_page_selection_requires_all_markers_and_closes_unmatched_pages():
    closed = []
    pages = [SimpleNamespace(extract_text=lambda: "Schedule only", close=lambda: closed.append(1)),
             SimpleNamespace(extract_text=lambda: "Schedule\nOwnership", close=lambda: closed.append(2))]
    assert pdf.pdf_pages(SimpleNamespace(pages=pages), containing=["Schedule", "Ownership"]) == [pages[1]]
    assert closed == [1]
    with pytest.raises(ValueError, match="No PDF pages"):
        pdf.pdf_pages(SimpleNamespace(pages=pages), containing=["Missing"])


@pytest.mark.parametrize("markers", [[], [""], [" "]])
def test_page_selection_rejects_empty_markers(markers):
    with pytest.raises(ValueError, match="nonempty"):
        pdf.pdf_pages(SimpleNamespace(pages=[]), containing=markers)


def test_digest_is_bytes_based_and_only_reported_after_success(monkeypatch):
    @contextmanager
    def fake_open(stream):
        assert stream.read().startswith(b"%PDF-")
        yield SimpleNamespace(pages=[])

    monkeypatch.setattr(pdf.pdfplumber, "open", fake_open)
    values = {}
    stats = SimpleNamespace(get_value=lambda key, default: values.get(key, default),
                            set_value=lambda key, value: values.update({key: value}))
    spider = SimpleNamespace(crawler=SimpleNamespace(stats=stats))
    body = b"%PDF-unit-test"
    for url in ("https://example.invalid/a.pdf", "https://example.invalid/b.pdf"):
        with pdf.open_pdf(SimpleNamespace(url=url, body=body), spider=spider):
            pass
    assert set(values["pdf/documents"].values()) == {sha256(body).hexdigest()}
    with pytest.raises(ValueError, match="parser failure"):
        with pdf.open_pdf(SimpleNamespace(url="failed", body=body), spider=spider):
            raise ValueError("parser failure")
    assert "failed" not in values["pdf/documents"]


def test_error_html_is_not_a_pdf():
    with pytest.raises(ValueError, match="Expected PDF response"):
        with pdf.open_pdf(SimpleNamespace(body=b"<h1>Access denied</h1>")):
            pass
