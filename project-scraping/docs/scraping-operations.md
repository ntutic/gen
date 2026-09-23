# Scraping operations

Ordinary Scrapy spiders, SQLite jobs, compressed response files, and plain Python
processing functions own execution. There is no separate workflow service.

## Storage and publication

| Storage | Content |
| --- | --- |
| `sources` | Registered sources seeded from the roster |
| `scrapers` | Spider registrations; the database owns the enabled flag |
| `scrape_jobs` | Source, kind, status, heartbeats, versions and structured reports |
| `scrape_source_records` | Selected source values, before shared normalization |
| `scrape_results` | Validated clean job-scoped output |
| `records` | Published entities/facts, explicit keys and observation times |
| `record_features` / `feature_units` | Published values and exact source unit labels |

The worker requires a complete nonempty crawl report, matching retained/staged
counts and no processing errors before publication. Publication is transactional
and serializes freshness checks. Historical inputs cannot overwrite newer
observations. Missing entities are not automatically retired; features absent
from an updated entity's observation are removed. There is no generic automatic
record-count regression rule: reviewed totals and exhaustion assertions belong
to the source spider. See [the project contract](project-contract.md).

`init-db` initializes the schema and registrations. Outdated schemas require an
explicit upgrade: stop workers, resolve running jobs, and use the existing
`upgrade-db`/`prepare-db` workflow. A shared database file lock serializes upgrades.
This change adds report fields, not database columns.

## Queue and pacing

The host scheduler, not the template, owns recurring timing:

```bash
.venv/bin/python -m scrapectl enqueue --all
.venv/bin/python -m scrapectl worker
```

Enqueue reuses pending/running production jobs for the same source. A completed
job does not suppress the next scheduled scrape. `worker --concurrency N` controls
simultaneous jobs (default ten); `worker --once` processes one pending job. Each
crawl gets a subprocess, heartbeat and publication checks.

Production live jobs share a fixed minimum ten-second interval per exact
hostname across protocols, ports and processes. HTTP holds the domain lock
through download completion; 429/503 `Retry-After` may extend the cooldown.
Browser navigation, assets and action-triggered requests use the same gate
through Chrome interception. Scrapy AutoThrottle may slow requests further.
Preview live jobs retain ordinary Scrapy pacing (the worker requests a two-second
delay); they do not participate in the production gate. Replay is offline.

Pacing state is independent of captures. All coordinating processes must use the
same `VCLIST_RATE_LIMIT_DIR` on a filesystem supporting cross-process `flock`.
The default is `$XDG_STATE_HOME/scraping/rate-limit`, falling back to
`~/.local/state/scraping/rate-limit`. This coordinates projects running as the
same OS user even when their capture directories differ. Separate users or
containers need an explicit shared directory/mount. Do not delete or switch the
pacing directory while workers are active. Stop old workers before migrating
from the former capture-local `.throttle` directory, then start all workers
with the new setting; mixed versions do not share limits.

The gate groups exact hostnames, not all subdomains of a registrable domain.
Browser helpers support one instrumented tab. Unsupported child worker/iframe
contexts fail closed rather than allowing unpaced traffic. Such sources need a
reviewed helper extension, not a direct-network fallback.

`VCLIST_CRAWL_TIMEOUT_SECONDS` defaults to 86400, with sixty seconds for subprocess
shutdown. Browser page/interaction waits remain bounded. Runtime timeouts are
separate from the contributor's bounded exploration budget.

## Captures, replay and reprocessing

```bash
python -m scrapectl check SOURCE --output /tmp/source.jsonl --receipt /tmp/preview.json
python -m scrapectl replay JOB_ID --output /tmp/replayed.jsonl --receipt /tmp/replay.json
python -m scrapectl reprocess --job JOB_ID --output /tmp/processed.jsonl
```

Live jobs store compressed responses or prepared browser DOM under
`VCLIST_CAPTURE_DIR` (default `var/captures`). Captures and the corresponding
SQLite database must be retained together. Separate contributor databases use
separate capture directories so job IDs cannot collide.

Replay runs the current parser from saved responses before proxy/browser
initialization. Missing/invalid captures fail with no network fallback. Request
identity includes URL, method, body, selected representation headers and browser
variant. Use `request.meta['capture_variant']` for explicitly distinct prepared
representations. Repeated captures preserve attempt files; replay reads the
latest response for each request identity. No credentials are required offline.

Browser replay feeds saved DOM to callbacks. It does not rerun clicks, scrolling
or JavaScript and cannot validate newly changed browser preparation. New request
paths or preparation changes require a new live preview. Reprocessing runs the
current normalizer on retained source values without launching Scrapy or making
requests; it is not evidence for the current parser. It requires a verified
complete source crawl and matching retained counts. Observation times remain
unchanged. `--publish` uses the same validation/freshness guards.

A failed preview's JSONL can contain only the valid subset: never infer success
from the presence of a file. Check job status and reports. `--receipt` writes a
pointer to the actual successful job and database; it is not a success certificate.
Automated acceptance reopens the assigned database read-only and checks the
actual job, counts, implementation fingerprint and output digest. It rejects
failed jobs, stale code, wrong databases and ordinary reprocess jobs. An offline
replay needs a successfully completed live ancestor. Historical runs lacking
implementation fingerprints need a fresh preview under the new worker.

Shared runtime changes require restarting long-lived workers. Workers refuse to
label previously imported processing code as the newly edited source. Source
edits during a run also fail rather than producing misleading acceptance evidence.
See [contributor dispatch](agent-dispatch.md) for the review/apply boundary.

Captures and source values have no automatic expiry. Deleting a capture job
folder removes replay ability but leaves source-value reprocessing available.
Back up SQLite and captures together. Pacing state is a separate operational
resource, not an artifact to remove with a disposable preview.

## Recovery and enrichment

Workers heartbeat every thirty seconds. `recover --stale-seconds N` marks expired
running jobs failed; partial values and diagnostics remain for investigation.
Enqueue again to refetch, replay saved responses, or reprocess complete inputs.
Do not publish partial output as a replacement for a complete observation.

External enrichment runs as a separately queued consumer, never inside the
spider or deterministic normalization. A future consumer must be idempotent,
record provider/version and preserve observed values. This is an extension
boundary, not an enrichment service implemented by the template.
