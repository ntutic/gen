# Contributor dispatch and acceptance

`ops/dispatch_sources.py` remains an optional bounded wrapper around `muse exec`,
not a required service. Directly authored source spiders use the same preview
and test path. It defaults to the pinned contributor model; `--review-model`
optionally chooses a different semantic reviewer without changing worker models.

```bash
python ops/dispatch_sources.py --list
python ops/dispatch_sources.py --dry-run --limit 2
python ops/dispatch_sources.py --only SOURCE --no-apply
python ops/dispatch_sources.py --reconcile --only SOURCE
python ops/dispatch_sources.py --apply-succeeded --only SOURCE
python ops/dispatch_sources.py --repair --only SOURCE
python ops/dispatch_sources.py --evaluate --only SOURCE
```

Each source has a persistent disposable workspace under `--log-dir/SOURCE/`:
`preview.db`, `captures/`, `preview.json` and per-attempt prompts/logs. Builder,
repair and evaluator subprocesses inherit its database and capture environment.
The receipt is only a source/database/job pointer. The gate opens the assigned
SQLite database read-only and checks the actual job, not a model's booleans or
unrelated staged rows.

Acceptance requires a successful nonpublishing live preview or replay from a
successful live baseline, matching crawl/retained/processed/staged counts, no
processing errors, explicit unique keys, a processing version, and the current
implementation fingerprint. The fingerprint includes this source, its test and
fixtures, shared runtime code, dependency declarations and the project contract;
another source's work does not invalidate it. The worker also rejects edits that
occur during a run. Old receipts/reviews without provenance are intentionally
not accepted: run a new preview and evaluate, rather than editing their metadata.

The script runs the source tests before spending a semantic-review call. The
reviewer reads the current source/tests and relevant captured evidence and judges
meaning and scope. Its JSON must name the correct source, have actual booleans,
and contain exactly one supported verdict; contradictory success is rejected.
Application rechecks the reviewed fingerprint and exact preview job. Enabling and
production enqueue happen in one database transaction. The ordinary `scrapectl
enable` command remains an explicit **manual** operation, not an automated
acceptance override.

`--no-eval` implements only; `--no-apply` records a review without enabling.
`--repair` implements the named fixes and requires a later evaluation. Use
`--force` deliberately to rebuild an existing disabled source; the dispatcher
refuses to rewrite an enabled source. Disable it explicitly first. Saved failed
or blocked work is not silently redispatched on every default run. `--max-retries`
bounds automatic attempts, and blocked verdicts do not auto-retry. Failed modes
return nonzero. ESC starts no further stages/sources; Ctrl-C kills tracked process
groups. Agent/test timeouts are configurable and also kill descendants.

Per-attempt `result.json` reports role, model, exit status, duration and log path.
Use attempts per accepted source and repair time to locate wasted work. These
are not token or billing measurements: no price is inferred from transcript text.
Measured usage can be joined from the runner's authoritative billing data.

This is reliability checking for trusted local contributors, not a security
sandbox. Agents still have the workspace filesystem permissions granted by Muse.
A reviewer must require fresh live captures when discovery/browser preparation
changes or evidence is too old for the project's scope. Offline replay proves
parsing of saved inputs, not today's source availability. If a crash occurs after
transactional apply but before saving the local review file, inspect the enabled
flag and queued job manually; do not blindly re-enable a later-disabled source.

Default log directories are namespaced by the absolute checkout path. An explicit
`--log-dir` must not be shared between independent concurrent dispatch runs.
A source's database and captures remain stable across its bounded repair attempts.
Confirmed contributor `BLOCKED_JSON` reports stop retries without an evaluator
call or acceptance; only the documented external-blocker categories are allowed.
An empty result alone is not a terminal blocker. Resume a blocked source after
fixing its external problem with an explicit `--force`.

The former unused `--include-non-confirmed` flag is removed. The CSV roster and
explicit `--only`/`--force` options define the source selection.
