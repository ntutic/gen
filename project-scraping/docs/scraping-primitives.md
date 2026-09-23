# Scraping primitives

Keep each spider as ordinary Python: request a page, extract records, fill a
`RecordLoader`, and yield items. Reuse a helper when an actual pattern repeats.
Scrapy owns requests, retries, duplicate request filtering, and scheduling.

## Working on a spider

1. Run `python -m scrapectl scrapers`. Pick one source.
2. Inspect the official source. Use Scrapy's shell or fetch with the project
   settings (`scraping/crawler/settings.py`) and `-s ITEM_PIPELINES='{}'` for
   inspection without a scrape job. This still uses the mandatory proxy middleware.
3. Write selectors or API mappings and a small fixture test
   (`tests/test_<SOURCE>.py`) with expected records.
4. Run `python -m scrapectl check EXAMPLE --output /tmp/example.jsonl`. This uses
   the real queue, saves responses and source records, processes a clean preview,
   and never publishes.
5. Inspect completeness, duplicate keys, and fields against the source. Update
   the spider's `notes` only after live verification.
6. Run `python -m scrapectl enable EXAMPLE` once live completeness is verified,
   then `python -m scrapectl scrape EXAMPLE` to publish, or enqueue it for a worker.

Every spider has `name = source_id`, `source_kind`, `key_description`, and
`start_urls`. New registrations default to disabled and need live verification
before production; the database owns the enabled flag, flipped only by an
explicit `enable`. These class attributes are the catalog;
`scrapectl/scrapers.py` synchronizes the DB registrations from them. A newly
initialized database registers the current source IDs automatically. To add a
source, add its spider module with a readable catalog name. The
`docs/sources.csv` catalog tracks the known sources.

## Proxy for every scraping request

Set these in the project `.env` (ignored by Git):

```
PROXY_SERVER=http://brd.superproxy.io:33335
PROXY_USERNAME=...
PROXY_PASSWORD=...
```

Both HTTP and browser requests require all three values and fail before making a
request if they are missing (`scraping/crawler/proxy.py`). Per-request overrides
and `NO_PROXY` cannot bypass the project proxy. Chrome uses an authenticated
loopback bridge for navigation and subresources; it disables direct DNS
resolution, QUIC, and non-proxied WebRTC. No credentials are placed in browser
arguments. Browser and driver installation are environment setup, separate from
scraping a source.

Use Scrapy requests rather than private HTTP clients inside spiders. For an
external browser inspection tool, configure this same proxy before opening sites.
Do not add direct-network fallbacks when a proxy request fails.

## One record loader

```python
loader = RecordLoader(spider=self, response=response, selector=card)
loader.add_value("source_key", source_key("native-id", record_id))
loader.add_css("name", "h3::text")
loader.add_css("url", "a::attr(href)")
loader.add_feature("Capacity", "1200", "seats")
yield loader.load_item()
```

`RecordLoader` lives in `scraping/crawler/loaders/record_loader.py`. Use the
native `add_css`, `add_xpath`, and `add_value` methods. Pass the response so
relative links resolve correctly. Zero and false feature values survive.
Features always have `{value: string, unit: string|null}`. Missing optional
fields remain absent. A record needs a name or URL as well as its key.
Source staging retains items before validation, including items that bypass the
loader. Shared processing validates every record before clean staging or publication.
Published record columns are only `url` and `name`; any other payload keys
survive in retained source records but are not published.

For an explicitly selected quantity, `positive_number` from
`scraping/crawler/processors.py` validates a complete positive number and removes
thousands separators. Strip only a reviewed unit suffix before calling it; it
rejects prose, malformed grouping, and placeholder values. Preserve each
metric's basis: keep source labels, units, and qualifications distinct.
Retain component notes separately and do not sum components into an already
published total.

## Record keys

Import `source_key` from `scrapectl/identity.py`. Every emitted record needs an
explicit stable key. The hash is SHA-256 of the JSON pair `[source_id, source_key]`.
It never hashes the record's current name, address, URL, features, or timestamp.
The chosen key is stored alongside the hash and is visible in record API results.

Prefer a native record ID. If unavailable, deliberately choose a stable site
slug, distinct detail URL, or documented natural key. `source_key(namespace,
*components)` encodes components without delimiter collisions and preserves opaque
IDs, including case. Spiders explicitly normalize human-readable components.

Examples:

- A source with native numeric IDs: `source_key("site-id", row["site_id"])`.
- A source with stable slugs: `source_key("slug", row["slug"])`.
- Normalized name, when every record shares one listing URL.
- Normalized name plus city, when names repeat across cities.

There is no automatic URL/address/name fallback chain and no fuzzy matching.
A natural key can change or collide: document that limitation and reconcile it
explicitly. Duplicate keys in one crawl are errors; the entire run fails publication.
If a record gets a new upstream key, attach it to its existing record ID with a
reviewed mapping before publishing:

```bash
python -m scrapectl bind-keys /tmp/reviewed-keys.jsonl
```

Each mapping line contains `record_id` and `source_key`, for example:

```json
{"record_id": 42, "source_key": "[\"site-id\",\"123\"]"}
```

The mapping applies transactionally and refuses collisions. It never merges or
deletes records. Publications update existing keys and insert new keys; they
do not remove disappeared records. Retirement policy is separate future work.
Spiders needing manual stable IDs can keep a small site-specific mapping; do not
build a global entity-resolution engine.

## Source formats

- **HTML/XML:** use response CSS/XPath directly, including tables and detail links.
- **JSON/API/GeoJSON:** `records(response.json(), "data.records")` from
  `scraping/crawler/script_extraction.py` selects a required list using JMESPath.
  Use `data_value(row, "address.city")` for optional values. Wrong types and
  missing collections raise; an empty crawl cannot publish. `mode="singleton"`
  and `mode="keyed"` explicitly select other object shapes. If a keyed object's
  keys are native IDs, iterate its `.items()` to retain them.
- **Embedded JSON:** `json_script(response, "script#data::text")` decodes one
  selected block; pass its result through `records`. JSON-LD uses the same tools.
- **JavaScript literals:** select the intended object/array text, then use
  `parse_js_literal`, backed by chompjs. It parses data without running the script.
  Do not rebuild a JavaScript parser or parse nested objects with a whole-object regex.
- **Browser pages:** subclass `SeleniumBaseSpider` from
  `scraping/crawler/base_spider.py`, set `ready_selector`, and override
  `prepare_page(driver)` for interactions after `super().prepare_page(driver)`.
  Use `click_load_more` for growing lists and `collect_scroll_container_items`
  for virtualized lists. Supply stable element keys when links aren't unique.
  Limits or stalled collection raise instead of returning a partial successful result.
- **Pagination:** follow ordinary next links or API cursors. Check pagination
  progress and advertised totals; fail on repeated cursors or incomplete collections.
- **PDF:** follow the official document link with an ordinary Scrapy request and
  parse its response in the same source spider (`source_kind = "pdf"`). Use
  `open_pdf`, `pdf_pages`, and `pdf_table_records` from
  `scraping/crawler/pdf_extraction.py`; feed each record into `RecordLoader`.

For an explicitly linked XLSX roster, use `xlsx_rows(response, sheet_name="<roster sheet>")`
from `scraping/crawler/spreadsheet_extraction.py`. It reads the named worksheet with
openpyxl, retaining native cell values, dates, blanks and zeroes. Formulas are not
executed: only cached source values are read, and a missing cache remains missing.
The source spider validates headers, record identities, units and subtotals
before feeding records into `RecordLoader`.

Add other decoders only when an actual source requires them. Avoid an automatic
format-detection framework.

For an explicitly published record boundary, use `boundary_representative_point`
from `scraping/crawler/geometry.py`. It validates geographic polygon coordinates,
repairs source rings with Shapely and returns a point inside the largest connected
component, respecting holes. Empty editor polygons contribute nothing; an entirely
empty boundary fails. Label the resulting location as a boundary representative
point. Never pass nearby amenities or a generic map viewport as the record
boundary. These points identify sites, not entrances or building rooftops.
The [Shapely manual](https://shapely.readthedocs.io/en/stable/manual.html#object.representative_point)
describes the interior-point operation.

## PDF tables

Inspect the downloaded PDF's text and rendered pages when building the spider.
The parser is ordinary reviewed Python, with no runtime model or separate PDF job.
The [pdfplumber documentation](https://github.com/jsvine/pdfplumber#extracting-tables)
describes table settings, cropping, and visual debugging. Work from the source's
actual layout; do not assume the largest detected table is the record roster.

```python
def parse(self, response):
    yield response.follow(response.css("a.roster-pdf::attr(href)").get(), self.parse_pdf)

def parse_pdf(self, response):
    with open_pdf(response, spider=self) as document:
        pages = pdf_pages(document, containing=["Record roster", "Ownership"])
        for page in pages:
            rows = pdf_table_records(page, {"name": "Name", "city": "City"})
            for row in rows:
                loader = RecordLoader(spider=self, response=response)
                # Choose and document the source's identity explicitly.
                loader.add_value("source_key", source_key("name-city", row["name"].casefold(), row["city"].casefold()))
                loader.add_value("name", row["name"])
                loader.add_value("city", row["city"])
                loader.add_value("url", response.url)
                yield loader.load_item()
```

Page selection matches all supplied text markers and fails if none match. The
table helper requires exactly one table matching the complete ordered header on
each selected page. Use `header_rows` for stacked headers and `table_settings` for
pdfplumber's line/text/explicit boundary strategies. Cropped pages are also accepted.
Wrapped cell text becomes one space-separated value. Ambiguous, empty, or malformed
tables fail; image-only PDFs need a separately designed OCR implementation.

The spider interprets totals, footnotes, units, and missing values, verifies the
complete record set, and chooses stable keys. Never use the PDF URL, page number,
row position, or document hash as the record identity. A changed layout or record
count requires review and fails the preview.

`open_pdf(response, spider=self)` reports SHA-256 of the exact downloaded bytes.
The worker records the URL and digest alongside the successful scrape job, in the
same transaction as job completion. Failed extraction, staging, or publication
never marks a PDF successfully parsed. `python -m scrapectl init-db` creates this
digest tracking for existing databases as well as new ones.

The job report identifies the previous successful job for the same spider and bytes,
even if the download URL changed. Job inspection exposes these digests as `pdfs`.
This tracks document identity, not semantic equality or parser versions: repeated
bytes are still parsed and staged so parser fixes take effect and every job has
a complete result set. No PDF files or cached record payloads are stored here.

## Validation

Successful previews print field n/m and store the same diagnostics in the job's
`report.coverage` (`scrapectl/coverage.py`). Inspect a preview or compare published
data without crawling:

```sh
python -m scrapectl coverage --job 123 --output /tmp/coverage.json
python -m scrapectl coverage --source EXAMPLE
python -m scrapectl coverage --job 123 --feature "Year built" --feature "Parking" --output /tmp/features.json
```

The JSON includes missing record identities; the terminal shows field and
feature counts. Repeat `--feature` with exact source feature labels to include
missing identities for those features, even when absent from every record.
These are inspection candidates, not proof the source publishes those fields.
Per-feature counts show how many records carry a value plus a histogram of the
exact source units observed. Coverage does not infer units or compare unlike
metrics; generic unlabeled measures remain unspecified. Judge numeric
completeness by counting values with known units separately from merely
populated feature text.

The denominator is stored records, including any historical imports. Coverage
describes extracted data; it does not prove that a spider collected the complete
upstream roster or every published field. Compare against official counts,
detail pages and current reports, and investigate missing examples. Coverage from
failed jobs describes only whatever results were staged. `coverage` reads the
existing database without initializing it or changing scraper registrations.

Run `.venv/bin/pytest -q` and `.venv/bin/ruff check .`. Fixtures check output
meaning and key uniqueness. Real local-proxy integration tests exercise Scrapy,
staging, and failure reporting without accessing source websites.

The worker requires a completion report (`scraping/crawler/report.py`), nonzero
item count, matching staged count, and no errors/drops/timeouts before publication.
These detect execution failure; source completeness still needs a reviewed total or
pagination/collection termination condition. A successful process exit alone is
insufficient.

## Retained inputs and processing

Every live job saves compressed HTTP responses or the prepared browser DOM. The
pipeline (`scrapectl/scrapy_pipeline.py`) stores extracted values in
`scrape_source_records` before normalization. `RecordLoader` attaches a
`raw_payload` containing the original selected values; its ordinary fields remain
convenient for source-specific keys and detail-page navigation. Keep that payload
when passing an item between callbacks. Explicit item edits after `load_item()`
are included in the retained record. Do not modify stored source records to apply
a cleaning fix.

`normalize_payload` in `scrapectl/processing.py` is the shared deterministic
normalization entry point. `process_job` turns retained inputs into job-scoped
`scrape_results`; the worker alone publishes those results to `records`. Change
the processing version when changing output semantics. Add shared processing
functions here when patterns repeat. Enrichment or other external requests belong
in a separately queued enrichment consumer, never in a spider or this deterministic
processing stage.

See [operations](scraping-operations.md) for replay, backfills, production pacing,
and the enrichment hook.

### Published features

Source and staged payloads carry feature dictionaries. Publication stores them in
`record_features` (`id`, `record_id`, `name`, `value`, nullable `unit_id`,
nullable `sanitized_name`). Values are text, preserving source formatting.
`feature_units` stores `id`, unique source `name`, and nullable `sanitized_name`.
Sanitized names are enrichment fields for future standard-field mappings; they
are not inferred from source labels. Units are reused by exact source name.
Publication updates features by record and name, preserving IDs and enrichment,
and deletes features absent from the new observation. Deleting a record cascades
to its features. Staged payloads remain available for preview and reprocessing.
