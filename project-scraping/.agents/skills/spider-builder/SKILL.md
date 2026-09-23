---
name: spider-builder
description: Build, repair, and verify vclist source spiders, including listing completeness and missing record fields across HTML, embedded data, APIs, browsers, and PDFs.
---

# Build complete source spiders

Read the repository's `AGENTS.md` and `docs/scraping-primitives.md` (paths relative to the repository root).
Use the existing Scrapy, loader, browser, PDF, capture, and staging workflow. This
skill runs in the coding agent; it does not require a separate factory or model runtime.

## Exploration budget

Stay inside these caps. They override the discovery checklist below when the two conflict.

- PDFs: never page-survey a full annual report or filing. Prefer the current
  record schedule over a full document. Use `pdf_pages` with text markers to
  select only the record schedule pages (at most ~5 pages), then stop. If the
  schedule is aggregate-only with no per-record identities, record that gap
  and stop; do not read the rest of the document.
- Detail pages: at most ~5 representative pages (different record types plus
  one edge case). Prefer one complete embedded JSON/API connection
  over detail fan-out.
- Blocked sources: after 2-3 proxied probes (native request plus shared browser)
  all return blocks/challenges, write the fail-closed disabled spider and stop.
- Live previews: at most 2 `scrapectl check` runs. Parser-only changes replay
  offline against existing captures instead of a new live run.
- Coverage: one `coverage --job` output; at most 2 `--feature` lookups and only
  for requested fields.
- Tests: run only the source's test file plus `ruff check` on changed files.
  Never run the full suite.
- Web search: only to discover the official source; no open-ended crawling.

## Establish what complete means

Identify the source's record unit: item, listing, location, document row, or
secondary entry. Record the advertised listing total and date, then reconcile
the actual source identities against it. Look for edge-case records,
pagination, multiple regions or categories, and overlaps between sections.
Explain differences between listing cards and report counts.
A successful request or nonempty output is not evidence of completeness.

Audit **fields separately from records**. For the requested source, inspect its
unfiltered listing, embedded data/API, and representative detail pages. If they
omit important fields, inspect the source's current reports or data downloads.
Search online to discover official sources; perform scraping inspections
through native Scrapy requests or the shared browser with the configured proxy.
Preserve pacing. Prefer a complete embedded connection to hundreds of detail
requests when it contains the same information.

Build a short source-to-field comparison before choosing extraction boundaries:

| Field | Where published | Extracted? | Gap or limitation |
| --- | --- | --- | --- |
| Identity | Listing/detail | … | … |
| Numeric metrics, with units | Data/detail/PDF | … | … |
| Other published record facts | Detail/PDF | … | … |

Inspect the actual source keys and labelled detail sections, not just fields the
current parser selects. Cover published record facts beyond the headline
metric: quantities, dates, categories, status flags, specifications,
attribution, and related links where applicable. This is a discovery checklist,
not a requirement to invent absent values. Keep aggregate totals and per-record
values distinct; do not flatten varying child values into a parent metric.

For each useful published field, either extract it or record a concrete reason
it cannot be extracted reliably. "The listing omits it" is a reason to inspect
details and reports, not a source omission. Distinguish uninspected sources,
blocked requests, ambiguous meanings/joins, and confirmed source omissions.
Preserve meaningful source qualifications; skip navigation, promotional prose,
and contact information unrelated to the record's characteristics.

Use the comparison to guide work, not as a required new artifact. Follow detail
links when listing records omit published fields. Never treat a secondary
inventory as the primary listing or a source's aggregate total as each record's
value. Listing completeness and field completeness are independent: an enabled
spider or matching record count does not establish feature completeness.

## Extract without changing meaning or identity

- Reuse explicit source keys. When combining sources, use native cross-links or
  reviewed, explicit mappings; reject unknown or ambiguous matches. Names
  and document URLs are not automatic fallback identities.
- Keep each published metric distinct with its unit, basis, date, and
  qualifications. Do not add a component value to its parent total.
- Select the exact field first. Use `positive_number` for a complete
  quantity after removing a reviewed unit suffix; never extract the first number
  from arbitrary prose. Missing values remain missing.
- For PDFs, inspect rendered table pages and footnotes. Use `open_pdf`,
  `pdf_pages`, and `pdf_table_records`; validate headers, row identities and
  subtotals. Review newly added rows and changes in record granularity.
- Add hard completeness assertions only where the source supplies evidence:
  advertised counts, pagination exhaustion, unique keys, or reconcilable totals.
  A count or field regression should fail with a useful reason, not silently
  skip affected records. Document genuine source omissions.

## Verify the result

Keep small fixtures covering real source variants and assert extracted records,
including keys, values, units, and known overlaps. Include a failure case
for a meaningful completeness risk. Run relevant tests and repository checks.

Run a full proxy-backed preview:

```sh
python -m scrapectl check SOURCE --output /tmp/SOURCE.jsonl
python -m scrapectl coverage --job JOB_ID --output /tmp/SOURCE-coverage.json
```

`check` prints field coverage and retains it in the job report. `coverage` includes
missing identities in its JSON output. For a production baseline use
`python -m scrapectl coverage --source SOURCE`. Its denominator is stored
records, which may include stale or duplicate imported records. Inspect the
job status: coverage from an incomplete or failed job is not a verified listing.

Compare before/after keys and record counts, field n/m, units, and source totals.
Use the existing coverage report's per-feature counts.
Use `coverage --job JOB_ID --feature "Label" --output /tmp/features.json`
(repeat `--feature` for exact labels) to locate missing identities, including
fields absent across the entire result set.
Check representative missing values against sources, including different record
types and edge cases. Small fixtures must retain the relevant published
source fields so tests demonstrate extraction rather than hiding omissions.
Review missing-value examples against their official pages, plus examples with
unusually large/small values. Generic unlabeled measures remain unspecified;
coverage tooling cannot establish their basis.

Use offline replay for parser changes against captured responses. If new code
follows additional URLs, run a live preview to capture and verify those pages.
Keep a new spider disabled until its full live completeness has been verified.
Update `source_kind`, `key_description`,
and `notes` with verified scope, date, identity limitations, and unresolved
source gaps. Preview staging is sufficient for implementation review; publishing
uses the repository's worker workflow.

When corrections are delegated, assign disjoint source spider/test/fixture files
to fresh agents. Keep shared primitive and documentation edits with one owner.
Use a separate disposable database and capture directory per agent for live
previews. Each handoff must report inspected sources, extracted fields, before/
after identities and coverage, preview status, tests, and unresolved gaps. A
blocked source is an incomplete audit, not evidence that no more fields exist.

Report the verified record count, requested-field coverage, tests, and any
remaining source omissions or blockers. Distinguish implemented changes from
live-verified results and from data already published to production.
