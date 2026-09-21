# Vclist

Blank-slate scraping canvas: ordinary Scrapy spiders, shared extraction
primitives, and a durable scrape queue publishing to generic records.

```bash
uv venv
uv pip install -e '.[dev]'
cp .env.example .env  # fill in the scraping proxy credentials
python -m scrapectl init-db
python -m scrapectl scrapers
python -m scrapectl check EXAMPLE --output /tmp/example.jsonl
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

See [scraping operations](docs/scraping-operations.md) for capture retention,
replay limits, and recovery. See
[scraping primitives](docs/scraping-primitives.md) for spider development, source
formats, proxy requirements, and record identity rules.
