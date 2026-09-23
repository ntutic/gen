# Vclist

Blank-slate scraping canvas: ordinary Scrapy spiders, shared extraction
primitives, a durable scrape queue publishing to generic records, and a
Vue/TanStack web + admin frontend served by two small FastAPI apps.

<!-- template-only -->
Start a new project from this template (renames the package, database,
API paths, frontend, docs, and tests; `--plural` overrides the inflection;
writes into the current directory):

```bash
mkdir myproject && cd myproject
gen project-scraping myproject --records company
```
<!-- /template-only -->
Setup:

```bash
uv venv
uv pip install -e '.[dev]'
cp .env.example .env  # fill in the scraping proxy credentials
npm install && npm run build  # build src/admin + src/web into web/
python -m scrapectl init-db
python -m scrapectl scrapers
python -m scrapectl check EXAMPLE --output /tmp/example.jsonl
python -m scrapectl enable EXAMPLE  # after verifying live completeness
python -m scrapectl scrape EXAMPLE
```

`check` runs the real spider through the proxy, retains its inputs, and stages a
clean preview without publishing. `scrape` publishes enabled spiders only after
complete extraction and successful processing. Failed jobs retain their inputs
and reports for diagnosis. `python -m scrapectl scrapers` lists the current
spider catalog and each source's live verification notes.

Production HTTP and browser requests share a per-hostname gate across workers:
at least ten seconds between requests. HTTP uses a conservative ten-second wait
after completion. Workers must share the capture directory and its file locks.

```bash
python -m scrapectl enqueue --all        # weekly scheduler entry point
python -m scrapectl worker               # consume the durable queue
python -m scrapectl replay 123 --output /tmp/replayed.jsonl
python -m scrapectl reprocess --source EXAMPLE --output /tmp/processed.jsonl
```

Replay and reprocessing default to previews; add `--publish` to publish validated
results. An older observation cannot overwrite a newer record. Weekly enqueue
deduplicates active production jobs; the host scheduler supplies the weekly timing.

```bash
python -m scrapectl serve         # record browser + public API at http://127.0.0.1:8000/
python -m scrapectl serve-admin   # spider runs + job history at http://127.0.0.1:8001/
```

The record browser lists sources and their published records with feature
detail. Administration shows one row per spider with a **Run spider** button
(reused when a live run is already pending) plus active and previous runs with
staging progress, reports, and logs. Queued runs need `python -m scrapectl
worker` running. Admin has no login and stays on loopback. The `web/` bundles
are committed build output; rerun `npm run build` after changing `src/`.

Run the numbered `ci/` scripts in order (`001_update`, `002_build`,
`003_check`, `004_test`); `ci/005_deploy.sh` starts the web/admin services
plus the worker on loopback, autoselecting `WEB_PORT`/`ADMIN_PORT` from the
40xx space and pinning them in `ci/ports.env`.

See [scraping operations](docs/scraping-operations.md) for capture retention,
replay limits, and recovery. See
[scraping primitives](docs/scraping-primitives.md) for spider development, source
formats, proxy requirements, and record identity rules.
