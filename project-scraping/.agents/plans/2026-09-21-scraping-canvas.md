# Scraping canvas build plan

Source: `~/code/reitmaps-kiss` (scraping + db parts). Target: this repo (`vclist`).
Locked decisions from review 2026-09-21: see Key Decisions below. Only open item
from approval was features storage — decided: keep generic `record_features` /
`feature_units` join tables (not nested dict).

## Goal

Blank-slate scraping project reusing proven machinery: shared extraction
primitives, DB-tracked spiders, queue/worker with capture/replay, coverage,
multi-agent dispatch skills. Zero REIT concepts on arrival.

## Success criteria

- [ ] New scrape = 1 spider file + 1 test file + 1 roster row.
- [ ] `init-db`, `scrapers`, `check`, `scrape`, `coverage`, `replay`,
  `reprocess`, `enqueue`, `worker` work against generic records.
- [ ] Builder + repair skills work with same budgets, pointed at new paths.
- [ ] `pytest -q` and `ruff check .` green with one EXAMPLE spider.
- [ ] `rg -i 'reit|nominatim|geocod'` clean (except this plan's history note).

## Key decisions (locked)

- Core package: `scrapectl` (replaces `reitmaps`). Crawler stays `scraping/crawler`.
- Loader: `RecordLoader` (rename of `PropertyLoader`), item `RecordItem`.
- Tables: `sources`, `scrapers`, `scrape_jobs`, `scrape_source_records`,
  `scrape_results`, `records`, `record_features`, `feature_units`.
  `record_features(record_id, name, value, unit_id, sanitized_name)`,
  `feature_units(id, name, sanitized_name)` — generic port of the
  property_features primitive, same publication semantics (update by
  record+name, delete absent, cascade on record delete).
- Identity: keep `source_key` / `record_hash` pattern from `identity.py`.
- CLI verbs unchanged; keep `bind-keys`, drop `unkeyed` import-repair ops.
- Enrichment: docs-only hook, no `enrichment_jobs` table.
- Roster: `docs/sources.csv` (`source_id,name,start_url,source_kind,expected_count`).
- Dispatch: `ops/dispatch_sources.py`, same pipeline/flags as
  `ops/dispatch_missing_spiders.py`.

## Contracts (all streams must follow, no drift)

- Spider attrs: `name = source_id`, `source_kind`, `key_description`,
  `start_urls`, `notes`. One file per source in `scraping/crawler/spiders/`.
- Proxy mandatory, no bypass, native Scrapy requests only.
- Spiders stage via jobs; worker publishes. No direct `records` writes.
- `source_key` explicit on every item; no fallback chains / payload hashing.
- Capture dir shared per host, `.throttle` kept; production ≥10s/host gate.
- Clean cutoffs: no legacy names, no tests for dropped features.

## Streams (parallelizable)

File ownership is disjoint except Stream 0. Streams 1–4 can run in parallel
after Stream 0 lands. Stream 5 runs last.

### Stream 0 — scaffold (blocking, small, do first)

- [ ] `pyproject.toml` (Scrapy, itemloaders, jmespath, chompjs, pdfplumber,
  openpyxl, Shapely, selenium, seleniumbase, SQLAlchemy; dev: pytest, ruff,
  httpx2), `scrapy.cfg`, `.env.example` (proxy keys), `.gitignore`
- [ ] `README.md` (quickstart: venv, init-db, scrapers, check, worker)
- [ ] `AGENTS.md` (port of reitmaps-kiss rules, generic names)
- [ ] `ci/` stubs — ASK USER before creating (standing rule)
- Validate: `uv pip install -e '.[dev]'` resolves.

### Stream 1 — crawler primitives (owns `scraping/`)

- [ ] Port from `reitmaps-kiss/scraping/crawler/`: `base_spider.py`,
  `processors.py`, `script_extraction.py`, `pdf_extraction.py`,
  `spreadsheet_extraction.py`, `geometry.py`, `capture.py`, `proxy.py`,
  `rate_limit.py`, `settings.py`, `report.py`, `scrapy_selenium/`,
  `utils/driver.py`, `datamodels/address.py` (only if project-agnostic,
  else drop)
- [ ] Rename `items.py` → `RecordItem`/`RecordFields` (keep `name/url/coords/
  address parts/features` as optional payload keys), `loaders/property_loader.py`
  → `loaders/record_loader.py` (`RecordLoader`, same raw-payload retention)
- [ ] Empty `spiders/__init__.py` (+ `EXAMPLE.py` left to Stream 5)
- Validate: `ruff check scraping/`; `python -c 'import scraping.crawler.settings'`.

### Stream 2 — core store/queue/worker (owns `scrapectl/`)

- [ ] Port + generalize: `db.py`, `models.py` (8 tables above),
  `identity.py` (`source_key`, `record_hash`), `queue.py`, `worker.py`,
  `processing.py` (`normalize_payload`, `process_job`, version bump),
  `coverage.py`, `scrapy_pipeline.py`, `scrapers.py` (catalog sync from
  spider attrs), `settings.py`, `upgrade.py`/`prepare-db` minimal
- [ ] `cli.py`: `init-db` (seed `sources`+`scrapers` from roster),
  `scrapers`, `check`, `scrape`, `coverage`, `replay`, `reprocess`,
  `enqueue`, `worker`, `bind-keys`
- [ ] Drop: `api.py`, `admin.py`, `geocoding.py`, `nominatim.py`,
  `enrichment.py`, `properties.py`, `features.py` (fold unit reuse into
  processing), Vue/`web/`, `src/`
- Validate: `init-db` on tmp DB → `scrapers` lists seeds;
  `ruff check scrapectl/`.

### Stream 3 — skills + dispatch (owns `.agents/`, `ops/`, roster)

- [ ] `.agents/skills/spider-builder/SKILL.md` (generic port of
  spider-builder-assistant; `COMPANY→SOURCE`, same budgets: ≤5 PDF pages,
  ≤5 detail pages, ≤2 live checks, company test + ruff only)
- [ ] `.agents/skills/spider-repair/SKILL.md` (same hard rules, new paths)
- [ ] `ops/dispatch_sources.py` (port of `dispatch_missing_spiders.py`;
  roster-driven, `muse exec` pool, VERDICT_JSON gate, disposable DB/capture
  per worker)
- [ ] `docs/sources.csv` with single EXAMPLE row
- Validate: `--list`/`--dry-run --limit 1` run without launching workers.

### Stream 4 — docs (owns `docs/` except roster)

- [ ] `docs/scraping-primitives.md` (generic port: loader, keys, formats,
  browser, pagination, PDF/XLSX, geometry, validation, retained inputs,
  `record_features`/`feature_units` publication semantics)
- [ ] `docs/scraping-operations.md` (storage table, queue/pacing, replay,
  capture retention; drop Nominatim section, keep enrichment-hook paragraph)
- Validate: every cited path/command exists post-Streams 1–2
  (fix in this stream's docs, not by editing others' code).

### Stream 5 — example + green build (depends on 0–2, owns `tests/`)

- [ ] `scraping/crawler/spiders/EXAMPLE.py` (static HTML fixture, 2–3
  records, native IDs, one feature with unit)
- [ ] `tests/`: `conftest.py`, `crawl_fixture.py`, `test_EXAMPLE.py`
  (records, keys, units, dupe-key failure case), `fixtures/EXAMPLE-*.html`
- [ ] End-to-end: `init-db` → `check EXAMPLE` → `coverage --job` →
  `replay` → `reprocess` → `worker --once` (publish path)
- [ ] `pytest -q`, `ruff check .`, `rg -i reit` clean
- Validate: all success criteria boxes ticked.

## Handoff notes for next agent(s)

- Work from `~/code/reitmaps-kiss` as read-only reference; never edit it.
- Keep a disposable DB/capture dir for live checks; never touch real data.
- One commit per stream; Stream 5 last. Report: files added, commands run,
  coverage output, remaining gaps.
