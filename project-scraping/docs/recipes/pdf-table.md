# A reviewed PDF table

Follow the official document link with a normal Scrapy request. Use `open_pdf`
on those response bytes, `pdf_pages(containing=[...])` for explicit page markers,
and `pdf_table_records` for the inspected table. Do not select a table merely
because it is the largest one.

The helper matches all supplied page markers and exactly one complete ordered
table header. It supports stacked headers (`header_rows`), explicit pdfplumber
settings and cropped pages. Wrapped cells are normalized; empty, ambiguous or
wrong-width tables fail. `tests/test_pdf_extraction.py` carries domain-independent
regressions adapted from the upstream helpers' tests.

Inspect the selected pages and footnotes. Interpret units, subtotals, qualifiers
and missing values in the source spider. The helper does not know whether a
subtotal is a record. Assert a representative row, required headers, correct
units and a failure for changed structure. Verify the entire requested schedule,
not only the few pages inspected while authoring.

A document URL, row position, page number and byte digest are evidence metadata,
not an automatic business identity. Emit explicit keys through `RecordLoader`.
Image-only PDFs require a separately designed OCR route; do not silently accept
an empty text/table extraction.
