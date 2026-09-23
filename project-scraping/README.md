# Vclist

A standalone scraping canvas: ordinary Scrapy spiders, shared extraction
primitives, retained inputs, a durable SQLite queue, and a Vue/TanStack web and
admin frontend served by two small FastAPI apps. Models help author and repair
spiders; production extraction does not require a model runtime.

<!-- template-only -->
Generate into a new directory:

```bash
mkdir myproject && cd myproject
gen project-scraping myproject --records company
```

`--plural` overrides inflection. Generation renames the package and record nouns,
not their meaning; customize the project contract before adding real sources.
<!-- /template-only -->

## Setup and the offline example

```bash
uv venv
uv pip install -e '.[dev]'
cp .env.example .env
npm install && npm run build
python -m scrapectl init-db
python -m pytest tests/test_EXAMPLE.py -q
```

The example tests include a local fake proxy and the real
crawl/stage/publish/replay/reprocess path; they do not contact a source website.
Configure the proxy credentials in `.env` before inspecting real sources.

## Implement one source

Start with [the project contract](docs/project-contract.md),
[the short spider workflow](docs/spider-workflow.md), and one
[extraction recipe](docs/recipes/README.md). The detailed
[primitive reference](docs/scraping-primitives.md) is available when needed.

```bash
python -m scrapectl scrapers
python -m scrapectl check SOURCE --output /tmp/source.jsonl --receipt /tmp/preview.json
python -m scrapectl coverage --job JOB_ID
python -m scrapectl enable SOURCE  # explicit manual approval, after reviewing live completeness
python -m scrapectl scrape SOURCE
```

`check` retains inputs and stages a clean preview without publishing. Failed jobs
retain their inputs and diagnostics. Every item has an explicit stable
`source_key`; several facts can cite the same document without sharing identity.
The project-owned `scrapectl/project_contract.py` hook adds domain validation.

An optional [contributor dispatcher](docs/agent-dispatch.md) supplies isolated
preview databases, scoped tasks and bounded retries. It independently checks
preview jobs and tests before requesting semantic review, then binds acceptance
to the reviewed implementation and output. Manual approval remains available;
it is not equivalent to automated verified acceptance.

## Operate

```bash
python -m scrapectl enqueue --all
python -m scrapectl worker
python -m scrapectl replay JOB_ID --output /tmp/replayed.jsonl
python -m scrapectl reprocess --source SOURCE --output /tmp/processed.jsonl
python -m scrapectl serve
python -m scrapectl serve-admin
```

Replay and reprocessing default to previews; `--publish` requests publication.
Historical observations cannot overwrite newer data. Publication upserts explicit
keys and retains disappeared entities; it does not infer retirement or implement
an incremental event-feed policy. Empty output and execution/processing errors
fail closed. Source completeness needs source-specific evidence and assertions.

Production HTTP and browser traffic share a per-hostname gate: at least ten
seconds between requests, conservatively measured after HTTP completion. Workers
coordinate through `VCLIST_RATE_LIMIT_DIR`, defaulting to
`$XDG_STATE_HOME/scraping/rate-limit` (or `~/.local/state/scraping/rate-limit`).
Capture directories may differ. Preview checks retain ordinary Scrapy pacing;
replay is offline. Restart workers after shared code or contract changes.

The public API/browser defaults to loopback port 8000; admin uses loopback port
8001 and has no login. Queued jobs need a running worker. Frontend build output
lives in `web/`; rerun `npm run build` after changing `src/`.

Run the existing numbered `ci/001_update.sh` through `ci/004_test.sh` scripts in
order. `ci/005_deploy.sh` starts loopback web/admin services and the worker; ports
are recorded in `ci/ports.env`. These scripts are unchanged by the acceptance work.

See [operations](docs/scraping-operations.md) for recovery and capture retention,
and [template lineage](docs/template-lineage.md) for the generated manifest and
how to carry shared fixes into independently evolving projects.
