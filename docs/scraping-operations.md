# Scraping operations

The system uses ordinary Scrapy spiders, SQLite jobs, compressed response files,
and plain Python processing functions. There is no separate workflow service.

## Storage and publication

| Storage | Content |
| --- | --- |
| `sources` | Registered sources seeded from the roster |
| `scrapers` | Spider registrations with enabled flags, synced from spider class attributes |
| `scrape_jobs` | Job rows: source, kind, status, heartbeats, structured reports |
| `scrape_source_records` | Original extracted values per job, before shared normalization |
| `scrape_results` | Validated clean preview/output per processing job |
| `records` | Published records; stable IDs and source observation times |
| `record_features` | Published features keyed by record and name |
| `feature_units` | Unit names reused by exact source label |

Source records survive normalization errors, missing keys, and duplicate keys.
The worker requires a complete extraction report, matching source count, and no
processing errors before publication. Publication is transactional and serializes
freshness checks with other publishers. Historical input cannot overwrite newer
observations. Records absent from a scrape are not automatically retired.

Run `python -m scrapectl init-db` to initialize a new database or bring an
existing one to the current schema. A shared database-side file lock serializes
concurrent starts. Stop workers and resolve running jobs when a schema upgrade is
required; a current schema needs no worker interruption. Startup refuses an
outdated schema rather than silently migrating a hosted database.

## Weekly queue and pacing

The weekly scheduler should invoke this from the checkout using its virtualenv:

```bash
.venv/bin/python -m scrapectl enqueue --all
```

This enqueues enabled sources and reuses any pending/running production job
for a source (`scrapectl/queue.py`). It does not suppress a new scrape after the
previous job completes. `enqueue EXAMPLE` selects one source. Run
`python -m scrapectl worker` to consume up to ten scrape jobs concurrently
(`scrapectl/worker.py`), refilling each slot as its job finishes. Use
`worker --concurrency N` to set a different limit, or `worker --once` to process
one pending job. Each scrape runs in its own subprocess with its own heartbeat
and publication checks. These commands do not install a scheduler or host
service; the existing host scheduler owns weekly timing. Site-specific dispatch
wrappers such as `ops/dispatch_sources.py` may choose the source set.

Production live jobs (`publish=True`) use a fixed minimum ten-second interval per
hostname, across protocols, ports, queued jobs, and worker processes
(`scraping/crawler/rate_limit.py`). Ordinary HTTP holds the domain lock through
request completion and then waits ten seconds before the next request. HTTP
429/503 `Retry-After` can extend that cooldown. Browser HTTP requests, including
navigation, page assets, and action-triggered fetches, are paused through
Chrome's Fetch interception and released through the same domain gate. Scrapy
AutoThrottle can slow HTTP down further. Preview live checks retain ordinary
Scrapy pacing and do not participate in the production gate. Replay is offline.

All workers must share `VCLIST_CAPTURE_DIR` (default `var/captures`,
`scraping/crawler/capture.py`) on a filesystem supporting cross-process `flock`.
Keep its `.throttle` directory while workers run; separate capture volumes do
not share limits. The gate groups exact hostnames, not all subdomains of a
registrable domain.

Browser helpers support one instrumented tab. Unsupported child worker/iframe
contexts are held paused and fail the job, preventing unpaced traffic. Browser
service-worker bypass and disabled cache keep page requests on the instrumented
path. A browser source needing such a context needs a helper extension and live
verification before production use. New browser initialization remains proxied.

`VCLIST_CRAWL_TIMEOUT_SECONDS` defaults to 86400 to accommodate the slower rate;
the worker allows another sixty seconds for process shutdown. Browser page and
interaction waits remain bounded. Large dynamic pages may need explicit wait
budgets in their spider after completeness verification.

## Replay, reprocessing, and backfills

```bash
python -m scrapectl check EXAMPLE --output /tmp/example.jsonl
python -m scrapectl replay 123 --output /tmp/replayed.jsonl
python -m scrapectl reprocess --job 123 --output /tmp/processed.jsonl
python -m scrapectl reprocess --source EXAMPLE --publish
```

These commands create new jobs. Replay runs the current spider using a selected
job's saved responses, before proxy or browser initialization. Missing/invalid
captures fail the job; there is no live-network fallback and no proxy credentials
are needed. Request identity includes URL, method, body, selected representation
headers, and browser variant. Use `request.meta['capture_variant']` for distinct
prepared representations of the same URL. Repeated captures retain previous
attempt files; replay uses the latest response for that request identity.

Browser replay feeds the saved prepared DOM to the parser. It does not rerun clicks,
scrolling, or JavaScript; changes to browser preparation or newly needed endpoints
may require a new live capture. Replay is for rerunning extraction, not reproducing
a browser's complete network session or exact retry timing.

Reprocessing does not launch Scrapy or make network requests. It runs the current
normalizer against retained source records. `--source` selects the latest retained
source job by observation time; `--job` selects an explicit input. Reprocessing
requires a verified complete crawl and matching retained record count. A crawl with
normalization errors can be reprocessed after fixing the normalizer; a partial crawl
cannot be treated as a complete roster. Reprocessing/replay chains resolve to
their retained input job. Observation times remain unchanged.

Replay and reprocessing default to preview; `--publish` runs the same validation and
freshness guards as a live publication. Preview JSONL contains clean output; a failed
preview may contain only the valid subset. Check its exit status and processing
report before using it. The original source rows remain unchanged.

Captures and source records have **no automatic expiry**. Keep them as long as you
need to recover previously unextracted data. Deleting a capture job directory removes
its replay ability but leaves source-record reprocessing available. Do not delete
`.throttle` during operation. Back up the SQLite database and capture directory
together; captures alone are insufficient to identify and replay a job.

## Enrichment hook

External enrichment — filling fields the source does not publish — runs as a
separately queued consumer over published records, never inside a spider or the
deterministic processing stage. Any future consumer must be idempotent, record
its provider and version with each result, and never overwrite source-observed
values. Queueing, retries, and inspection for enrichment are future work; this
document defines only the hook.

## Recovery and inspection

Workers heartbeat every thirty seconds. After an interruption, running jobs with
expired heartbeats are marked failed; their retained inputs and partial results
stay available for diagnosis, and partial work is never silently published.
Enqueue a new live job to refetch, replay retained responses, or reprocess a
complete source job. Job inspection covers job kinds, source jobs, versions,
structured reports, and logs.
