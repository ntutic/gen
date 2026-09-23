# Extraction recipes

Choose one recipe, not a new framework. Each names existing primitives, a small
example and the assertions that matter. Adapt site-native logic directly.

| Source shape | Recipe | Regression anchor |
| --- | --- | --- |
| HTML cards/table with detail links | [HTML list](html-list.md) | `tests/test_EXAMPLE.py` |
| JSON collection with explicit pagination | [JSON pagination](json-pagination.md) | `tests/test_collection.py` |
| PDF roster/table with identified headers | [PDF table](pdf-table.md) | `tests/test_pdf_extraction.py` |
| PDF narrative or several facts in one document | [PDF text](pdf-text.md) | `tests/test_project_contract.py` |

For a browser-only list, use the documented `SeleniumBaseSpider` and existing
load-more/virtualized-list helpers; do not add another browser adapter. For an
explicit XLSX source, use `xlsx_rows` and validate the source's headers and cached
formula values. Detailed APIs remain in `docs/scraping-primitives.md`.
