---
name: spider-repair
description: Repair only the named source failures using retained inputs and the current project contract.
---

# Repair one source

Read `AGENTS.md`, `docs/project-contract.md`, `docs/spider-workflow.md`, then only
the relevant recipe. The assignment's previous `evaluation.reasons`,
`evaluation.gaps`, `detail`, and attempt logs are the task list. Do not rediscover
what already passed or expand the requested fields.

Reproduce the named failure against fixtures or captures. Fix tests/crashes,
rule violations, identity/completeness, then requested-field semantics. Keep
custom parsing source-native and preserve explicit keys. A repeated document URL
is not a duplicate identity. Do not mix periods, currencies, components and totals.

Touch only the assigned source spider, test file and source-prefixed fixtures.
No shared primitive, schema, documentation, CI or other-source edits in this
assignment. Never enable, publish, access production or modify evidence to pass a
gate. Shared problems need a separate owner, not a private replacement framework.

Use the same disposable database/capture directory supplied by the dispatcher.
For parser-only changes, replay the last successful live baseline and write a
**new** receipt with `replay JOB_ID --receipt PATH`. A reprocess is a normalizer
check, not proof that a changed parser works. New URLs, browser interactions or
capture preparation require a live check. Old receipts lack provenance and must
be replaced by an actual verified run, never hand-edited.

Limit inspection to about five representative detail/PDF pages, selected with
local search. Runtime extraction must still cover the whole assigned scope.
At most two live previews; run the source test file and lint changed files only.
Stop on persistent proxy/authentication/challenge blockers and report them. Zero
items alone is not proof the source has no relevant information; do not fake an
item, relax validation, or silently treat a broken selector as a valid omission.

Report which failures changed, before/after keys and requested-field coverage,
receipt/job ID, tests and remaining blockers. The next semantic evaluation judges
whether the result is acceptable; you never change the database enabled flag.

For a confirmed external blocker, finish with one line (never use this merely
because output is empty):
```text
BLOCKED_JSON: {"source_id": "SOURCE", "category": "access_denied", "reason": "Specific probes and the observed blocker"}
```
Categories: `proxy_auth`, `access_denied`, `source_unavailable`,
`scope_unavailable`. This stops retries and never enables a source.
