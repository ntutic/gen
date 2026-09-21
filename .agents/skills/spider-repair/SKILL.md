---
name: spider-repair
description: Repair a failed vclist source spider from its evaluator verdict, gaps, and notes, then re-verify with tests and a proxy-backed preview.
---

# Repair a failed source spider from eval feedback

Your assignment provides: the source ID, the latest eval record
(`verdict`, `reasons`, `gaps`, `notes`, `detail`, local-gate facts), and
paths to the spider file, test file, builder log, and eval log. The eval
record is the task list. Fix what it names; do not re-litigate what passed.

Read `AGENTS.md` and `docs/scraping-primitives.md` first (paths relative to
the repository root). Work the same pipeline as the builder skill.

## Procedure

1. Read the eval record fully (reasons, gaps, notes, detail), then the full
   spider and test files, then the eval log and builder log tail.
2. Reproduce first: run `python3 -m pytest tests/test_SOURCE.py -q` and note
   the local-gate facts given in the assignment. If the failure is a rule
   violation, fix the code before any live run.
3. Repair only the named failures, in this order:
   - tests / worker crash;
   - repo-rule violations (name, `source_key`, proxy, loader, fixtures,
     `source_kind`/`key_description`/`notes`);
   - record-completeness (counts/keys vs. a verified advertised total);
   - field-completeness (source-to-field gaps, backed by coverage output).
4. If you instead find a terminal condition (proxy auth failure/ban,
   403/407/captcha wall, site has no listing section, clean-but-empty
   check with zero items and no errors), stop: leave the spider disabled,
   record the blocker, do not force a fix.
5. Keep each repair minimal and source-native. Preserve explicit source keys
   and metric semantics; record unjoinable remainder as gaps, never as bulk
   manual tables.

## Hard rules (reject on violation)

- Touch only `scraping/crawler/spiders/SOURCE.py`,
  `tests/test_SOURCE.py`, and small fixtures under `tests/fixtures/`
  named `SOURCE-*` (`.html`, `.json`, `.pdf`, `.txt`). Never edit shared
  primitives, docs, or other sources' files.
- `name` and filename must both be `SOURCE`. Keep the explicit
  `enabled = False` line; never set it to True and never publish.
- Native Scrapy requests and shared browser/proxy primitives only. No
  private HTTP clients (`requests`, `httpx`, `aiohttp`, `urllib.request`)
  and no direct-network fallback.
- Explicit `source_key` from `scrapectl.identity` on every item (native IDs
  preferred). No whole-payload hashing, no automatic fallback chains.
- Chain multi-page enrichment through `cb_kwargs`/`meta`. No
  cross-callback instance accumulation (`self.details`, `self.seen`, or
  similar) with barrier counts. No bulk manual tables (report crosswalks,
  per-slug/per-file regex fact tables, pixel-geometry/SHA-guard blocks
  over ~30 entries). Keep aggregate totals distinct from record facts;
  never conflate a secondary inventory with the primary listing.
- Budgets: at most ~5 representative detail pages, ~5 record-schedule PDF
  pages (schedule preferred over a full filing; stop on aggregate-only
  schedules), at most 2 live `scrapectl check` runs (parser-only changes
  replay offline against existing captures). One `coverage --job` output,
  at most 2 `--feature` lookups. Run only the source's test file plus
  `ruff check` on changed files. Never page-survey a full filing.

## Verify

Use a disposable database and capture directory for every live preview:

```sh
VCLIST_DATABASE_URL="sqlite:////tmp/vclist-SOURCE.db" \
VCLIST_CAPTURE_DIR=/tmp/captures-SOURCE \
python -m scrapectl check SOURCE --output /tmp/SOURCE.jsonl
python -m scrapectl coverage --job JOB_ID --output /tmp/SOURCE-coverage.json
```

Report back: which eval reasons/gaps you fixed and how, before/after
identities and counts, field coverage (`coverage --job JOB_ID --feature
"Exact label"` for each requested field, including wholly absent ones),
preview status, tests run, and remaining gaps or blockers. Distinguish
record-completeness (counts reconcile) from field-completeness (audited
fields extracted), and implemented changes from live-verified results.
