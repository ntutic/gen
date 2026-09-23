# Vclist agent instructions

Keep this repository small. Do not introduce an agent harness, embedded LLM runtime, artifact ledger, evaluation framework, scraper release system, or compatibility layer.

Scrapers must use the shared item, loader, extraction, browser, and persistence primitives documented in `docs/scraping-primitives.md`. Add a primitive when a pattern repeats; do not create a private framework inside an individual spider.

Scrapers stage results against a `scrape_jobs` row. They never write directly to production records. A successful worker process publishes the staged result set.

Use clean cutoffs: remove replaced code and its tests. Keep dependencies and operational commands minimal.

Do not add or change `ci/` without asking the user first.


Always use the configured proxy for scraping HTTP and browser traffic. Use native
Scrapy requests and shared browser helpers; never add a direct-network fallback.

One spider file per source, named by source ID. Every item needs an explicit
source_key; prefer native site IDs and document natural-key limitations. Never
hash a whole record payload or invent automatic identity fallback chains.

Use `python -m scrapectl check SOURCE` for preview staging. Enable a spider only
after verifying its live completeness. Keep fixtures small and test extracted
records. Update the spider's source_kind, key_description, and notes when changing it.
