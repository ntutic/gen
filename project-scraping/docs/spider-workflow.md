# Spider quick path

Read the [project contract](project-contract.md), then one
[recipe](recipes/README.md). The [primitive reference](scraping-primitives.md)
is detailed API documentation, not mandatory context for every task.

A source task needs an ID, official starting URL, requested scope/fields, one
relevant example, allowed files, and verification commands. The optional `recipe`
CSV column selects `html-list`, `json-pagination`, `pdf-table` or `pdf-text`.
Do not use an estimated CSV count as proof of completeness.

Use native Scrapy requests and the configured proxy, the shared `RecordLoader`,
and explicit `source_key`. Keep source interpretation in ordinary Python. Reuse
helpers for mechanisms rather than inventing a private HTTP/PDF framework.

```bash
python -m scrapectl check SOURCE --output /tmp/preview.jsonl --receipt /tmp/preview.json
python -m scrapectl replay JOB_ID --output /tmp/replay.jsonl --receipt /tmp/preview.json
python -m scrapectl coverage --job JOB_ID --feature "Requested label"
```

The dispatcher supplies isolated database/capture/receipt paths and environment;
use those instead of the illustrative paths above. Preserve the same workspace
across repairs to reuse captures. It validates the actual job referenced by the
receipt against the current source, tests and shared code. A failed check removes
its old receipt; hand-editing a receipt is never verification.

Run `pytest tests/test_SOURCE.py -q` and lint changed files while implementing.
The dispatcher independently runs the source test file before semantic review.
The project maintainer runs the full suite for shared changes and integration.

Completeness of records, coverage of requested fields and correctness of their
meaning are separate questions. Keep compact fixtures with explicit expectations
and a meaningful negative case. A repair handoff should contain the failing
assertion, last good fixture/capture, changed input, and exact unresolved gap—not
an invitation to investigate the entire source again.

Replay exercises parsing of old responses or prepared DOM; it does not validate
new browser preparation, requests that were never captured, or today's source
availability. Use a live check when those change. See
[agent dispatch](agent-dispatch.md) for automated acceptance and
[operations](scraping-operations.md) for publication/recovery.
