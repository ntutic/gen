"""Dispatch spider-building muse agents for sources that lack a spider.

Reads the curated roster in docs/sources.csv, selects rows whose
source_id has no file in scraping/crawler/spiders/, and launches one
`muse exec` worker per source with a bounded pool (default 5 concurrent).
The default run also picks up previously built spiders that have no eval
record in the log dir yet and evaluates them alongside, sharing --workers
and the --max-retries retry budget.

Each worker prompt is the tracked spider-builder skill plus the CSV details
for that source. CSV record counts are guidance only, never completeness
assertions.

Usage:
    python ops/dispatch_sources.py --list
    python ops/dispatch_sources.py --dry-run --limit 2
    python ops/dispatch_sources.py
    python ops/dispatch_sources.py --only SRC1,SRC2 --workers 2
    python ops/dispatch_sources.py --reconcile --only SRC1,SRC2
    python ops/dispatch_sources.py --evaluate --only SRC1,SRC2
    python ops/dispatch_sources.py --apply-succeeded --only SRC1,SRC2
    python ops/dispatch_sources.py --repair --only SRC1,SRC2

Pipeline per source: build, then evaluate, then mechanically apply the
verdict, then start one production run when newly enabled. Each build's
evaluation is the next step after that build, not a trailing wave: as soon
as a builder finishes, its evaluator starts (up to --workers concurrent
builds, repairs, plus evals). Source rows missing from the database are
created at dispatch start so every dispatched ID exists before its build
runs. The evaluator only judges and ends its response with a VERDICT_JSON
line; the script parses it and flips the database flag, saving notes per
source under the log dir. A retryable FAILED verdict runs
the spider-repair skill against that eval record before re-evaluating, up
to --max-retries (default 0, no retries); terminal BLOCKED verdicts never
retry and keep their notes for the next dispatch round. Crashed builders
retry the build directly since there is no eval record to repair from.

Spider files carry no enabled flag: the database owns it and new scrapers
register disabled. Mechanical rule: enabled flips to True only when the
evaluator verdict is "succeeded" AND the local gate (worker exit 0,
spider/test files present, name matches, tests pass) holds. Anything else
leaves the database flag untouched. An applied enable mechanically enqueues
one production run for it (`python -m scrapectl enqueue SOURCE`).

Hotkeys while builders or evaluators are running: ESC stops after what's
currently running (no new work starts); Ctrl-C interrupts immediately and
kills running workers. Both are logged at the start of each run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

from scrapectl.db import Session
from scrapectl.models import Source

REPO_ROOT = Path(__file__).resolve().parent.parent
SPIDERS_DIR = REPO_ROOT / "scraping" / "crawler" / "spiders"
SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "spider-builder" / "SKILL.md"
REPAIR_SKILL_PATH = REPO_ROOT / ".agents" / "skills" / "spider-repair" / "SKILL.md"
ROSTER_CSV = REPO_ROOT / "docs" / "sources.csv"

DEFAULT_MODEL = "muse-spark-1.3-contributor"

EVAL_TAIL_CHARS = 6000
EVAL_FILE_CHARS = 15000
LOG_TAIL_LINES = 8


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    """Every status line uniformly prefixed with [datetime]."""
    for line in str(message).splitlines() or [""]:
        print(f"[{stamp()}] {line}", flush=True)


def tail(text: str, lines: int = LOG_TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def elapsed(start: float) -> str:
    seconds = int(time.monotonic() - start)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds:02d}s"


_LIVE: dict[int, subprocess.Popen] = {}
_LIVE_LOCK = threading.Lock()


def run_tracked(cmd: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run a child in its own process group and track it until it exits."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=True,
    )
    with _LIVE_LOCK:
        _LIVE[proc.pid] = proc
    try:
        stdout, stderr = proc.communicate()
    finally:
        with _LIVE_LOCK:
            _LIVE.pop(proc.pid, None)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def live_count() -> int:
    with _LIVE_LOCK:
        return len(_LIVE)


_STOP_AFTER_RUNNING = threading.Event()

HOTKEYS_HELP = "[ESC] stop after what's currently running (no new work starts) | [CTRL-C] interrupt now (kills workers)"


def request_stop_after_running() -> None:
    """Flag no-new-work; safe to call from the ESC watcher thread."""
    if not _STOP_AFTER_RUNNING.is_set():
        _STOP_AFTER_RUNNING.set()
        log("ESC pressed: stopping after what's currently running; no new work will start.")


def stop_after_running_requested() -> bool:
    return _STOP_AFTER_RUNNING.is_set()


def _start_esc_watcher():
    """Watch stdin for an ESC press; no-op when stdin is not a TTY.

    Uses cbreak mode so Ctrl-C still raises KeyboardInterrupt. Returns an
    opaque state for _stop_esc_watcher, or None when watching is unavailable.
    """
    try:
        if not sys.stdin.isatty():
            return None
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except (ImportError, OSError, ValueError):
        return None
    done = threading.Event()

    def _watch() -> None:
        try:
            while not done.is_set() and not _STOP_AFTER_RUNNING.is_set():
                rlist, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not rlist:
                    continue
                try:
                    data = os.read(fd, 16)
                except OSError:
                    return
                if not data:
                    return
                if b"\x1b" in data:
                    request_stop_after_running()
                    return
        except (OSError, ValueError):
            return

    thread = threading.Thread(target=_watch, daemon=True, name="esc-watcher")
    thread.start()
    return (fd, old, done, thread)


def _stop_esc_watcher(state) -> None:
    if state is None:
        return
    fd, old, done, _thread = state
    done.set()
    try:
        import termios

        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except (ImportError, OSError, ValueError):
        pass


def kill_all(grace_seconds: float = 2.0) -> None:
    """SIGTERM every tracked process group, then SIGKILL stragglers."""
    with _LIVE_LOCK:
        procs = list(_LIVE.values())
    for proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + grace_seconds
    for proc in procs:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass
    with _LIVE_LOCK:
        stragglers = list(_LIVE.values())
    for proc in stragglers:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def spider_ids() -> set[str]:
    return {p.stem for p in SPIDERS_DIR.glob("*.py") if not p.stem.startswith("_")}


def ensure_sources(rows: list[dict[str, str]]) -> list[str]:
    """Create a Source row for each dispatched ID that lacks one.

    Builders verify against disposable databases, so without this a new
    source has no database identity until an unrelated CLI sync runs.
    Returns the created IDs.
    """
    created = []
    with Session() as session:
        for row in rows:
            source_id = (row.get("source_id") or "").strip()
            if not source_id or session.get(Source, source_id) is not None:
                continue
            expected = (row.get("expected_count") or "").strip().replace(",", "")
            session.add(Source(
                id=source_id,
                name=(row.get("name") or "").strip() or source_id,
                website_url=(row.get("start_url") or "").strip() or None,
                source_kind=(row.get("source_kind") or "").strip() or None,
                expected_count=int(expected) if expected else None,
            ))
            created.append(source_id)
        session.commit()
    return created


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def find_missing(
    roster_rows: list[dict[str, str]],
    existing: set[str],
) -> list[dict[str, str]]:
    missing = []
    for row in roster_rows:
        source_id = (row.get("source_id") or "").strip()
        if not source_id or source_id in existing:
            continue
        missing.append(row)
    return missing


def source_context(roster_row: dict[str, str]) -> str:
    source_id = roster_row["source_id"].strip()
    return "\n".join([
        f"Source ID (spider name and filename): {source_id}",
        f"Name: {roster_row.get('name', '')}",
        f"Start URL: {roster_row.get('start_url', '')}",
        f"Source kind: {roster_row.get('source_kind', '')}",
        f"Estimated size (guidance only, NOT a completeness assertion): {roster_row.get('expected_count', '')}",
    ])


def build_prompt(skill_text: str, roster_row: dict[str, str]) -> str:
    source_id = roster_row["source_id"].strip()
    return f"""{skill_text}

---

## Your assignment: build the spider for {source_id}

{source_context(roster_row)}

Instructions for this assignment:

- The estimated size above is guidance only. Reconcile the actual source
  identities against an advertised listing total you verify yourself; never
  treat the CSV estimate as a completeness assertion.
- Touch only these disjoint files: `scraping/crawler/spiders/{source_id}.py`,
  `tests/test_{source_id}.py`, and small fixtures under `tests/fixtures/`
  named `{source_id}-*` with one of these extensions: `.html`, `.json`,
  `.pdf`, `.txt`. Keep each fixture small and relevant; never commit a full
  site dump. Do not edit shared primitives, docs, or other sources' files.
- Spider `name` and filename must both be `{source_id}`. Set
  `source_kind`, `key_description`, `start_urls`, and `notes`.
  Spider files carry no enabled flag: the database owns it and new scrapers
  register disabled. Never try to enable anything; enabling is applied
  mechanically from the reviewer's verdict after your build.
- Use native Scrapy requests and the shared browser/proxy primitives only.
  Never add a private HTTP client (`requests`, `httpx`, `aiohttp`,
  `urllib.request`) or any direct-network fallback around the proxy.
- Chain multi-page enrichment through `cb_kwargs`/`meta`. Never accumulate
  items across callbacks in instance state (`self.details`, `self.brochures`,
  `self.pdf_text`, `self.seen`, or similar) with barrier counts such as
  `if len(self.details) == N`.
- Keep the spider focused (aim under ~300 lines). Do not embed bulk manual
  tables: report-to-site crosswalks, per-slug/per-file regex fact tables, or
  pixel-geometry/SHA-guard blocks over ~30 entries. Extract what the source
  natively joins; record the remainder as explicit gaps instead.
- Use the shared `source_key` helper on every item (native site IDs
  preferred). Never hash a whole payload or invent fallback identity chains.
- Use a disposable database and capture directory for every live preview so
  parallel workers never share state, e.g.
  `VCLIST_DATABASE_URL="sqlite:////tmp/vclist-{source_id}.db"`
  `VCLIST_CAPTURE_DIR=/tmp/captures-{source_id}`, and
  `python -m scrapectl check {source_id} --output /tmp/{source_id}.jsonl`.
  Never publish and never touch the production database.
- Report back: inspected sources, a source-to-field table (field, where
  published, extracted?, gap/limitation), before/after identities and counts,
  field coverage (`coverage --job JOB_ID --feature "Exact label"` for each
  requested field, including wholly absent ones), preview status, tests run,
  and unresolved gaps or blockers. Distinguish record-completeness (counts
  reconcile) from field-completeness (audited fields extracted).
"""


def muse_cmd(muse_bin: str, model: str, prompt_path: Path) -> list[str]:
    """Explicit `muse exec` invocation; the model is always pinned, never a CLI default."""
    return [
        muse_bin,
        "exec",
        "--trust-workspace",
        "--workspace",
        str(REPO_ROOT),
        "--model",
        model or DEFAULT_MODEL,
        "--prompt-file",
        str(prompt_path),
    ]


def run_worker(
    muse_bin: str, source_id: str, prompt: str, log_dir: Path, model: str = DEFAULT_MODEL,
    task: str = "build",
) -> tuple[str, int, str]:
    log(f"[start] {task} {source_id}")
    prompt_path = log_dir / f"muse-spider-{source_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    proc = run_tracked(
        muse_cmd(muse_bin, model, prompt_path),
        cwd=str(REPO_ROOT),
    )
    log_path = log_dir / f"muse-spider-{source_id}.log"
    log_path.write_text(
        f"$ {' '.join(proc.args)}\nreturncode={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n",
        encoding="utf-8",
    )
    err_tail = tail(proc.stderr) or tail(proc.stdout)
    return source_id, proc.returncode, err_tail


def log_returncode(log_dir: Path, source_id: str) -> int | None:
    """Worker exit code from its log file, or None when there is no log.

    The repair log wins when present: it is the freshest worker output, and
    on evaluate-only runs there is no builder log at all. A crashed repair
    therefore fails the gate instead of passing on a stale builder success.
    """
    for name in (f"muse-repair-{source_id}.log", f"muse-spider-{source_id}.log"):
        log_path = log_dir / name
        if not log_path.is_file():
            continue
        match = re.search(r"returncode=(\d+)", log_path.read_text(encoding="utf-8"))
        return int(match.group(1)) if match else None
    return None


def dispatched_batch(log_dir: Path) -> list[str]:
    """Source IDs dispatched earlier, inferred from prompt files in log_dir."""
    return sorted(p.name.removeprefix("muse-spider-").removesuffix(".md") for p in log_dir.glob("muse-spider-*.md"))


def eval_record_path(log_dir: Path, source_id: str) -> Path:
    return Path(log_dir) / f"muse-eval-{source_id}.json"


def evaluated_batch(log_dir: Path) -> list[str]:
    """Source IDs with a stored eval record in log_dir."""
    return sorted(
        p.name.removeprefix("muse-eval-").removesuffix(".json") for p in log_dir.glob("muse-eval-*.json")
    )


def apply_succeeded_batch(batch: list[str], log_dir: Path) -> int:
    """Re-gate stored succeeded verdicts and apply them; no new model calls.

    For each source whose eval record says "succeeded", re-run the local
    gate and mechanically enable + enqueue when it passes, recording the
    outcome back into the eval record. Anything else (blocked/failed/missing
    verdict, gate not ready, already applied) is left untouched. Returns 0
    only when every ID in the batch ends enabled.
    """
    applied: list[str] = []
    skipped: list[str] = []
    for source_id in batch:
        path = eval_record_path(log_dir, source_id)
        if not path.is_file():
            log(f"{source_id}: no eval record; skipping")
            skipped.append(source_id)
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"{source_id}: eval record unparsable; skipping")
            skipped.append(source_id)
            continue
        if record.get("applied"):
            log(f"{source_id}: already applied; skipping (no duplicate enqueue)")
            applied.append(source_id)
            continue
        if record.get("verdict") != "succeeded":
            log(f"{source_id}: verdict is {record.get('verdict') or 'missing'}, not succeeded; skipping")
            skipped.append(source_id)
            continue
        gate = reconcile_source(source_id, log_dir)
        decision = apply_decision(
            source_id, {"verdict": "succeeded"}, gate["verdict"] == "ready", gate["reasons"]
        )
        if not decision["applied"]:
            log(f"{source_id}: {decision['detail']} (notes: muse-eval-{source_id}.log)")
            skipped.append(source_id)
            continue
        record["applied"] = True
        record["local_ready"] = True
        record["detail"] = decision["detail"]
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        start_run(source_id)
        applied.append(source_id)
        log(f"[ENABLED] {source_id}: {decision['detail']}")
    log(f"Finished: {len(applied)} enabled, {len(skipped)} left disabled.")
    return 0 if not skipped else 1


def find_unevaluated_builds(
    roster_rows: list[dict[str, str]],
    log_dir: Path,
) -> list[dict[str, str]]:
    """Roster rows with a spider file on disk but no eval record in log_dir.

    These were built by an earlier run that never evaluated them (an
    interrupted run or one dispatched with --no-eval). They need an eval,
    not a rebuild; a retryable eval still runs repair then re-evaluates
    per --max-retries.
    """
    unevaluated = []
    for row in roster_rows:
        source_id = (row.get("source_id") or "").strip()
        if not source_id or not (SPIDERS_DIR / f"{source_id}.py").is_file():
            continue
        if eval_record_path(log_dir, source_id).is_file():
            continue
        unevaluated.append(row)
    return unevaluated


def scraper_enabled(source_id: str) -> bool | None:
    """Database enabled flag via the CLI; None when the query fails."""
    proc = run_tracked(
        [sys.executable, "-m", "scrapectl", "enabled", source_id],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        return None
    try:
        return bool(json.loads(proc.stdout.strip().splitlines()[-1])["enabled"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def spider_status(source_id: str) -> dict:
    """Filesystem facts about worker output plus the database flag; never runs the spider."""
    spider_path = SPIDERS_DIR / f"{source_id}.py"
    test_path = REPO_ROOT / "tests" / f"test_{source_id}.py"
    info: dict = {
        "spider_exists": spider_path.is_file(),
        "test_exists": test_path.is_file(),
        "enabled": None,
        "name_ok": None,
    }
    if spider_path.is_file():
        text = spider_path.read_text(encoding="utf-8")
        # All matches, not the first: local variables also called `name`
        # (e.g. `name = " ".join(...)`) precede the class attribute.
        matches = re.findall(
            r"(?m)^\s*name\s*=(?:\s*[A-Za-z_]\w*\s*=)*\s*[\"']([^\"']+)[\"']", text
        )
        info["name_ok"] = source_id in matches
        info["enabled"] = scraper_enabled(source_id)
    return info


def run_source_tests(source_id: str) -> bool:
    proc = run_tracked(
        [sys.executable, "-m", "pytest", f"tests/test_{source_id}.py", "-q"],
        cwd=str(REPO_ROOT),
    )
    return proc.returncode == 0


def reconcile_source(source_id: str, log_dir: Path, test_runner=run_source_tests) -> dict:
    """Verdict for one source's worker output: ready or needs_work + reasons."""
    reasons: list[str] = []
    returncode = log_returncode(log_dir, source_id)
    if returncode is None:
        reasons.append("no worker log")
    elif returncode != 0:
        reasons.append(f"worker failed (exit {returncode})")
    status = spider_status(source_id)
    if not status["spider_exists"]:
        reasons.append("no spider file")
    else:
        if not status["name_ok"]:
            reasons.append("spider name does not match source ID")
        if status["enabled"] is True:
            reasons.append("scraper enabled in database without verified completeness")
        elif status["enabled"] is None:
            reasons.append("database flag not readable")
    if not status["test_exists"]:
        reasons.append("no test file")
    elif not test_runner(source_id):
        reasons.append("tests fail")
    return {
        "source_id": source_id,
        "worker_exit": returncode,
        "verdict": "ready" if not reasons else "needs_work",
        "reasons": reasons,
    }


def eval_summary(log_dir: Path, source_id: str) -> str:
    """One-line eval decision for reconcile output, or '-' when not evaluated."""
    path = log_dir / f"muse-eval-{source_id}.json"
    if not path.is_file():
        return "-"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "unparsable"
    verdict = record.get("verdict") or "no-verdict"
    return f"{verdict}/applied" if record.get("applied") else verdict


def reconcile(batch: list[str], log_dir: Path) -> int:
    results = [reconcile_source(source_id, log_dir) for source_id in batch]
    for result in results:
        worker = result["worker_exit"] if result["worker_exit"] is not None else "no-log"
        detail = "; ".join(result["reasons"]) if result["reasons"] else "all checks pass"
        log(
            f"{result['source_id']}\tworker={worker}\t{result['verdict']}"
            f"\teval={eval_summary(log_dir, result['source_id'])}\t{detail}"
        )
    ready = sum(1 for r in results if r["verdict"] == "ready")
    log(f"{ready}/{len(results)} ready to stage; the rest stay uncommitted.")
    if ready:
        staged = " ".join(
            f"scraping/crawler/spiders/{r['source_id']}.py tests/test_{r['source_id']}.py"
            for r in results
            if r["verdict"] == "ready"
        )
        log(f"Stage only the ready ones, then review diffs:\n  git add {staged}")
    rework = sorted(r["source_id"] for r in results if r["verdict"] != "ready")
    if rework:
        log(
            "Rework stays out of the tree (delete stray files or leave them "
            f"untracked), then redispatch:\n  python3 ops/dispatch_sources.py --only {','.join(rework)}"
        )
        return 1
    return 0


def excerpt(text: str, limit: int = EVAL_FILE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated, read the full file in the workspace] ..."


def build_eval_prompt(
    source_id: str,
    roster_row: dict[str, str] | None,
    builder_tail: str,
    spider_text: str | None,
    test_text: str | None,
    local_facts: str,
) -> str:
    context = (
        source_context(roster_row)
        if roster_row
        else f"Source ID (spider name and filename): {source_id}"
    )
    spider_block = (
        f"Current spider source (excerpt; full file at scraping/crawler/spiders/{source_id}.py):\n"
        f"```python\n{excerpt(spider_text)}\n```"
        if spider_text is not None
        else "Spider file: MISSING, nothing was generated."
    )
    test_block = (
        f"Current test source (excerpt; full file at tests/test_{source_id}.py):\n```python\n{excerpt(test_text)}\n```"
        if test_text is not None
        else "Test file: MISSING, nothing was generated."
    )
    return f"""You are sanity-checking a newly generated Scrapy spider for the vclist canvas
repository. Be strict: a wrong enable pollutes production data. When in doubt,
verdict failed. Read AGENTS.md and docs/scraping-primitives.md first.

Source under review:
{context}

Local facts established by script (use as input, do not re-litigate):
{local_facts}

Builder's output (tail of its session log):
```
{builder_tail[-EVAL_TAIL_CHARS:]}
```

{spider_block}

{test_block}

Your tasks:
1. Read the full spider and test files in the workspace (excerpts above may be truncated).
2. Run `python3 -m pytest tests/test_{source_id}.py -q` and note the result.
3. Review against the repo rules: name == filename == source ID; explicit
   source_key from scrapectl.identity on every item (native IDs preferred, no
   whole-payload hashing, no automatic fallback chains); proxy via the shared
   primitives with no direct-network fallbacks or private HTTP clients
   (`requests`, `httpx`, `aiohttp`, `urllib.request` are rejects);
   RecordLoader usage with no private value frameworks; small realistic
   fixtures (fixtures use only `{source_id}-*` with
   `.html`/`.json`/`.pdf`/`.txt`, no full site dumps); source_kind,
   key_description, and notes updated. Never check, set, or
   mention the database enabled flag and never run any enable/disable
   command: the pipeline applies your verdict mechanically after this
   review. Judge only; do not act.
   Reject cross-callback instance accumulation (`self.details`,
   `self.brochures`, `self.pdf_text`, `self.seen`, or similar with barrier
   counts); enrichment must chain through `cb_kwargs`/`meta`. Reject bulk
   manual tables (report crosswalks, per-slug/per-file regex fact tables,
   pixel-geometry/SHA-guard blocks over ~30 entries): unjoined remainder must
   be recorded as gaps, not embedded as unreviewable bulk. Reject invented
   child-to-parent totals and aggregate-to-record conflation.
4. Assess completeness evidence in two independent parts. (a) Records: did the
   builder run `python -m scrapectl check {source_id}` against a disposable
   database and do staged counts/keys reconcile with a verified advertised
   total? If the evidence is doubtful, rerun the check yourself with
   VCLIST_DATABASE_URL="sqlite:////tmp/vclist-eval-{source_id}.db"
   VCLIST_CAPTURE_DIR=/tmp/captures-eval-{source_id} and
   `--output /tmp/eval-{source_id}.jsonl`. Never publish anything.
   Rerun at most once and only when the builder's evidence is doubtful;
   otherwise judge from the builder's logged check and coverage output.
   Never page-survey a full annual report or filing; check only the record
   schedule pages. Run only `tests/test_{source_id}.py`, never the full suite.
   (b) Fields: does the builder's source-to-field table cover the skill's
   discovery checklist, backed by `coverage --job JOB_ID --feature "Exact
   label"` output including wholly absent fields? A matching count never
   establishes field completeness.
5. Verdict "succeeded" ONLY if tests pass, the rules hold (including items 3a-3c
   above: no fan-in state, no bulk tables, no identity/metric conflation), AND
   live-check evidence shows a complete, reconciled listing with audited
   field coverage. Otherwise judge retryability: "blocked" is terminal and
   never retried (proxy auth failure/ban, 403/407/captcha wall, site has no
   listing section, or a clean-but-empty check with zero items and no
   errors); "failed" is repairable and may retry (tests fail, rule
   violations, partial/incomplete evidence, spider errors). Give concrete
   gaps a next attempt can act on.

End your response with exactly one line of the form:
VERDICT_JSON: {{"verdict": "succeeded|blocked|failed", "builder_preview_ok": true|false, "tests_pass": true|false, "reasons": ["..."], "gaps": ["..."], "notes": "..."}}
Keep notes concise but useful for the next attempt: what you checked, what
failed, and what to try next.
"""


RETRYABLE_VERDICTS = ("failed",)


def extract_verdict(transcript: str) -> dict | None:
    """Parse the evaluator's VERDICT_JSON line; None when missing or invalid."""
    matches = re.findall(r"(?m)^VERDICT_JSON:\s*(\{.*\})\s*$", transcript)
    if not matches:
        return None
    try:
        verdict = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(verdict, dict) or verdict.get("verdict") not in ("succeeded", "blocked", "failed"):
        return None
    verdict.setdefault("reasons", [])
    verdict.setdefault("gaps", [])
    verdict.setdefault("notes", "")
    return verdict


def is_retryable(verdict: dict | None, worker_exit: int | None = 0) -> bool:
    """FAILED verdicts (or missing verdicts / builder crashes) may retry; BLOCKED never does."""
    if worker_exit not in (0, None):
        return True
    if verdict is None:
        return True
    return verdict.get("verdict") in RETRYABLE_VERDICTS


def apply_decision(source_id: str, verdict: dict | None, local_ready: bool, local_reasons: list[str]) -> dict:
    """Mechanically apply succeeded/blocked/failed via the database flag. Never edits spider files."""
    if verdict is None:
        return {"applied": False, "detail": "no parsable verdict; left disabled"}
    if verdict["verdict"] == "blocked":
        return {"applied": False, "detail": "evaluator verdict: blocked (terminal; no retry)"}
    if verdict["verdict"] == "failed":
        return {"applied": False, "detail": "evaluator verdict: failed (retryable)"}
    if verdict["verdict"] != "succeeded":
        return {"applied": False, "detail": "no parsable verdict; left disabled"}
    if not local_ready:
        return {"applied": False, "detail": f"local gate failed: {'; '.join(local_reasons)}"}
    proc = run_tracked(
        [sys.executable, "-m", "scrapectl", "enable", source_id],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        detail = tail(proc.stderr) or tail(proc.stdout) or f"exit {proc.returncode}"
        return {"applied": False, "detail": f"enable failed (exit {proc.returncode}): {detail}"}
    return {"applied": True, "detail": "verdict succeeded + local gate passed; database enabled = True"}


def start_run(source_id: str) -> dict:
    """Enqueue a live publish job for a newly enabled spider; mechanical only."""
    proc = run_tracked(
        [sys.executable, "-m", "scrapectl", "enqueue", source_id],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        detail = tail(proc.stderr) or tail(proc.stdout) or f"exit {proc.returncode}"
        result = {"ok": False, "job_id": None, "detail": f"enqueue failed (exit {proc.returncode}): {detail}"}
        log(f"[run] {source_id}: {result['detail']}")
        return result
    job_id: int | None = None
    for line in reversed(proc.stdout.strip().splitlines()):
        if line.strip().isdigit():
            job_id = int(line.strip())
            break
    result = {
        "ok": True,
        "job_id": job_id,
        "detail": f"enqueued live job {job_id}" if job_id is not None else "enqueued (no job id parsed)",
    }
    log(f"[run] {source_id}: {result['detail']}")
    return result


def run_eval_worker(muse_bin: str, source_id: str, prompt: str, log_dir: Path, model: str = DEFAULT_MODEL) -> str:
    log(f"[start] eval {source_id}")
    prompt_path = log_dir / f"muse-eval-{source_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    proc = run_tracked(
        muse_cmd(muse_bin, model, prompt_path),
        cwd=str(REPO_ROOT),
    )
    transcript = (
        f"$ {' '.join(proc.args)}\nreturncode={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
    )
    (log_dir / f"muse-eval-{source_id}.log").write_text(transcript, encoding="utf-8")
    return transcript


def evaluate_source(
    source_id: str,
    log_dir: Path,
    muse_bin: str,
    roster_by_id: dict[str, dict[str, str]],
    *,
    apply: bool = True,
    model: str = DEFAULT_MODEL,
) -> dict:
    gate = reconcile_source(source_id, log_dir)
    local_ready = gate["verdict"] == "ready"
    worker_exit = gate["worker_exit"]
    local_facts = (
        f"worker exit: {worker_exit if worker_exit is not None else 'no log'}; "
        f"local gate: {gate['verdict']} ({'; '.join(gate['reasons']) if gate['reasons'] else 'all checks pass'})"
    )
    spider_path = SPIDERS_DIR / f"{source_id}.py"
    test_path = REPO_ROOT / "tests" / f"test_{source_id}.py"
    prompt = build_eval_prompt(
        source_id,
        roster_by_id.get(source_id),
        (log_dir / f"muse-spider-{source_id}.log").read_text(encoding="utf-8")
        if (log_dir / f"muse-spider-{source_id}.log").is_file()
        else "(no builder log)",
        spider_path.read_text(encoding="utf-8") if spider_path.is_file() else None,
        test_path.read_text(encoding="utf-8") if test_path.is_file() else None,
        local_facts,
    )
    transcript = run_eval_worker(muse_bin, source_id, prompt, log_dir, model)
    verdict = extract_verdict(transcript)
    decision = (
        apply_decision(source_id, verdict, local_ready, gate["reasons"])
        if apply
        else {
            "applied": False,
            "detail": "recorded only (--no-apply)",
        }
    )
    if decision["applied"]:
        start_run(source_id)
    record = {
        "source_id": source_id,
        "verdict": verdict["verdict"] if verdict else None,
        "applied": decision["applied"],
        "local_ready": local_ready,
        "reasons": verdict["reasons"] if verdict else ["no parsable verdict"],
        "gaps": verdict["gaps"] if verdict else [],
        "notes": verdict["notes"] if verdict else "",
        "detail": decision["detail"],
        "transcript": f"muse-eval-{source_id}.log",
    }
    (log_dir / f"muse-eval-{source_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def build_repair_prompt(
    skill_text: str,
    source_id: str,
    roster_row: dict[str, str] | None,
    record: dict,
) -> str:
    context = (
        source_context(roster_row)
        if roster_row
        else f"Source ID (spider name and filename): {source_id}"
    )
    brief = {
        "verdict": record.get("verdict"),
        "applied": record.get("applied"),
        "local_ready": record.get("local_ready"),
        "reasons": record.get("reasons", []),
        "gaps": record.get("gaps", []),
        "notes": record.get("notes", ""),
        "detail": record.get("detail", ""),
    }
    return f"""{skill_text}

---

## Your assignment: repair the spider for {source_id}

{context}

Latest evaluation (your task list; fix what it names, do not re-litigate what passed):
```json
{json.dumps(brief, indent=2)}
```

Files to work with in the workspace:
- spider: `scraping/crawler/spiders/{source_id}.py`
- test: `tests/test_{source_id}.py`
- builder log: the log directory's `muse-spider-{source_id}.log`
- eval log: the log directory's `muse-eval-{source_id}.log`

Instructions for this assignment are in the skill above. Touch only the
disjoint files it allows. Never enable anything; enabling is applied
mechanically from the next evaluation's verdict after your repair.
"""


def run_repair_worker(
    muse_bin: str, source_id: str, prompt: str, log_dir: Path, model: str = DEFAULT_MODEL
) -> tuple[str, int, str]:
    log(f"[start] repair {source_id}")
    prompt_path = log_dir / f"muse-repair-{source_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    proc = run_tracked(
        muse_cmd(muse_bin, model, prompt_path),
        cwd=str(REPO_ROOT),
    )
    log_path = log_dir / f"muse-repair-{source_id}.log"
    log_path.write_text(
        f"$ {' '.join(proc.args)}\nreturncode={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n",
        encoding="utf-8",
    )
    err_tail = tail(proc.stderr) or tail(proc.stdout)
    return source_id, proc.returncode, err_tail


def repair_source(
    source_id: str,
    record: dict,
    log_dir: Path,
    muse_bin: str,
    roster_by_id: dict[str, dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Run the spider-repair skill against the latest eval record for one source."""
    skill_text = REPAIR_SKILL_PATH.read_text(encoding="utf-8")
    prompt = build_repair_prompt(
        skill_text, source_id, roster_by_id.get(source_id), record
    )
    _, returncode, err_tail = run_repair_worker(muse_bin, source_id, prompt, log_dir, model)
    return {"source_id": source_id, "returncode": returncode, "err_tail": err_tail}


def repair_batch(
    batch: list[str],
    log_dir: Path,
    muse_bin: str,
    roster_by_id: dict[str, dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    workers: int = 5,
) -> int:
    """Run the spider-repair skill once per source; no evaluation, no enabling.

    Eligible records are parsable, unapplied, and not terminally blocked; the
    verdict itself may be succeeded (re-run the worker to produce fresh
    exit-0 evidence for the gate) or failed. Repair, re-gate with
    --reconcile, and enable with --apply-succeeded afterwards.
    """
    start = time.monotonic()
    log(f"Repairing {len(batch)} sources with {workers} workers (model={model}); logs in {log_dir}/muse-repair-<ID>.log")
    log(HOTKEYS_HELP)
    esc_state = _start_esc_watcher()
    eligible: list[tuple[str, dict]] = []
    skipped: list[str] = []
    for source_id in batch:
        path = eval_record_path(log_dir, source_id)
        if not path.is_file():
            log(f"{source_id}: no eval record; skipping")
            skipped.append(source_id)
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"{source_id}: eval record unparsable; skipping")
            skipped.append(source_id)
            continue
        if record.get("applied"):
            log(f"{source_id}: already applied; skipping (repair would rewrite a live spider)")
            skipped.append(source_id)
            continue
        if record.get("verdict") == "blocked":
            log(f"{source_id}: verdict is blocked (terminal); skipping")
            skipped.append(source_id)
            continue
        eligible.append((source_id, record))
    failed: list[str] = list(skipped)
    completed = 0
    total = len(eligible)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_id = {
                pool.submit(
                    repair_source, source_id, record, log_dir,
                    muse_bin, roster_by_id, model=model,
                ): source_id
                for source_id, record in eligible
                if not stop_after_running_requested()
            }
            for future in future_to_id:
                if stop_after_running_requested():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                source_id = future_to_id[future]
                result = future.result()
                completed += 1
                if result["returncode"] != 0:
                    failed.append(source_id)
                    log(
                        f"[{completed}/{total} {elapsed(start)}] [repair exit {result['returncode']}] "
                        f"{source_id} (log: {log_dir / f'muse-repair-{source_id}.log'})"
                    )
                    if result["err_tail"]:
                        log(f"--- {source_id} repair error tail ---\n{result['err_tail']}")
                else:
                    log(f"[{completed}/{total} {elapsed(start)}] [repaired] {source_id}")
    except KeyboardInterrupt:
        kill_all()
        log(f"Interrupted: killed worker processes, {live_count()} still tracked; partial logs kept in {log_dir}.")
        return 130
    finally:
        _stop_esc_watcher(esc_state)
    repaired = sorted(source_id for source_id, _ in eligible if source_id not in failed)
    log(f"Finished in {elapsed(start)}: {len(repaired)} repaired, {len(failed)} left disabled.")
    if repaired:
        log(f"Re-gate them, then enable:\n  python3 ops/dispatch_sources.py --reconcile --only {','.join(repaired)} --log-dir {log_dir}")
    return 0 if not failed else 1


def evaluate_batch(
    batch: list[str],
    log_dir: Path,
    muse_bin: str,
    workers: int,
    roster_by_id: dict[str, dict[str, str]],
    *,
    apply: bool = True,
    model: str = DEFAULT_MODEL,
    max_retries: int = 0,
) -> int:
    start = time.monotonic()
    log(f"Evaluating {len(batch)} sources with {workers} workers (model={model}, max_retries={max_retries}); notes in {log_dir}/muse-eval-<ID>.*")
    log(HOTKEYS_HELP)
    esc_state = _start_esc_watcher()
    records: list[dict] = []
    completed = 0
    total = len(batch)
    attempts: dict[str, int] = {source_id: 0 for source_id in batch}
    last_records: dict[str, dict] = {}
    pending: deque[tuple[str, str]] = deque(("eval", source_id) for source_id in batch)
    active: dict = {}
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        with pool:

            def submit_available() -> None:
                while pending and len(active) < workers and not stop_after_running_requested():
                    task, source_id = pending.popleft()
                    if task == "repair":
                        active[
                            pool.submit(
                                repair_source,
                                source_id,
                                last_records[source_id],
                                log_dir,
                                muse_bin,
                                roster_by_id,
                                model=model,
                            )
                        ] = ("repair", source_id)
                        continue
                    attempts[source_id] = attempts.get(source_id, 0) + 1
                    active[
                        pool.submit(
                            evaluate_source,
                            source_id,
                            log_dir,
                            muse_bin,
                            roster_by_id,
                            apply=apply,
                            model=model,
                        )
                    ] = ("eval", source_id)

            submit_available()
            while active:
                finished, _ = wait(list(active), return_when=FIRST_COMPLETED)
                for future in finished:
                    kind, source_id = active.pop(future)
                    if kind == "repair":
                        result = future.result()
                        if result["returncode"] != 0:
                            log(
                                f"[{completed}/{total} {elapsed(start)}] [repair exit {result['returncode']}] "
                                f"{source_id} (log: {log_dir / f'muse-repair-{source_id}.log'})"
                            )
                            if result["err_tail"]:
                                log(f"--- {source_id} repair error tail ---\n{result['err_tail']}")
                        else:
                            log(f"[{completed}/{total} {elapsed(start)}] [repaired] {source_id}: re-evaluating")
                        pending.append(("eval", source_id))
                        submit_available()
                        continue
                    completed += 1
                    record = future.result()
                    last_records[source_id] = record
                    verdict = {"verdict": record["verdict"]} if record["verdict"] else None
                    if (
                        not record["applied"]
                        and is_retryable(verdict)
                        and attempts[source_id] <= max_retries
                        and not stop_after_running_requested()
                    ):
                        log(
                            f"[{completed}/{total} {elapsed(start)}] [retry {attempts[source_id]}/{max_retries}] "
                            f"{source_id}: {record['detail']} (repairing)"
                        )
                        pending.append(("repair", source_id))
                    else:
                        records.append(record)
                        flag = "ENABLED" if record["applied"] else record["verdict"] or "no-verdict"
                        log(
                            f"[{completed}/{total} {elapsed(start)}] [{flag}] {record['source_id']}: "
                            f"{record['detail']} (notes: {record['transcript']})"
                        )
                    submit_available()
    except KeyboardInterrupt:
        kill_all()
        pool.shutdown(wait=False, cancel_futures=True)
        log(f"Interrupted: killed worker processes, {live_count()} still tracked; partial results kept in {log_dir}.")
        return 130
    finally:
        _stop_esc_watcher(esc_state)
    if pending:
        skipped = sorted(source_id for _task, source_id in pending)
        log(f"ESC stop: {len(skipped)} left unevaluated: {', '.join(skipped)}")
        log(f"Resume them later:\n  python3 ops/dispatch_sources.py --evaluate --only {','.join(skipped)}")
    enabled = sorted(r["source_id"] for r in records if r["applied"])
    disabled = sorted(r["source_id"] for r in records if not r["applied"])
    log(f"Finished in {elapsed(start)}: {len(enabled)} enabled, {len(disabled)} left disabled.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=5, help="Max concurrent muse agents (default: 5)")
    parser.add_argument("--limit", type=int, default=0, help="Only dispatch the first N missing sources")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild and re-evaluate even when an eval record already exists (ignores resume skips)",
    )
    parser.add_argument("--only", default="", help="Comma-separated source IDs to dispatch, skipping detection")
    parser.add_argument(
        "--include-non-confirmed", action="store_true", help="Also include roster rows not marked 'confirmed missing'"
    )
    parser.add_argument("--list", action="store_true", help="Print missing sources and exit without dispatching")
    parser.add_argument("--dry-run", action="store_true", help="Print worker prompts without launching muse")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Judge worker output (ready/needs_work) instead of dispatching",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run only the evaluation wave on a previous batch",
    )
    parser.add_argument(
        "--no-eval",
        action="store_true",
        help="Dispatch builders only; skip each build's follow-up evaluation",
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Record evaluator verdicts without flipping enabled flags",
    )
    parser.add_argument(
        "--apply-succeeded",
        action="store_true",
        help="Re-gate stored succeeded eval verdicts and enable those that pass; no new model calls",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Run the spider-repair skill once per source; no evaluation, no enabling",
    )
    parser.add_argument("--max-retries", type=int, default=0, help="Retries for failed builds; failed evals repair then re-evaluate; blocked never retries (default: 0)")
    parser.add_argument("--muse-bin", default="muse", help="muse executable")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model id passed as muse exec --model (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "muse-source-dispatch",
        help="Directory for prompts and worker logs",
    )
    args = parser.parse_args(argv)
    if args.max_retries < 0:
        parser.error("--max-retries must be >= 0")
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    _STOP_AFTER_RUNNING.clear()

    roster_rows = read_csv(ROSTER_CSV)
    roster_by_id = {r.get("source_id", "").strip(): r for r in roster_rows}

    if args.repair:
        if args.reconcile or args.evaluate or args.no_eval or args.no_apply or args.apply_succeeded:
            parser.error("--repair cannot be combined with --reconcile/--evaluate/--no-eval/--no-apply/--apply-succeeded")
        if args.only:
            batch = [c.strip() for c in args.only.split(",") if c.strip()]
        else:
            batch = evaluated_batch(args.log_dir)
        if args.limit:
            batch = batch[: args.limit]
        if not batch:
            log("Nothing to repair: no --only IDs and no eval records in log dir.")
            return 1
        return repair_batch(
            batch,
            args.log_dir,
            args.muse_bin,
            roster_by_id,
            model=args.model,
            workers=args.workers,
        )

    if args.apply_succeeded:
        if args.reconcile or args.evaluate or args.no_eval or args.no_apply or args.repair:
            parser.error("--apply-succeeded cannot be combined with --reconcile/--evaluate/--no-eval/--no-apply/--repair")
        if args.only:
            batch = [c.strip() for c in args.only.split(",") if c.strip()]
        else:
            batch = evaluated_batch(args.log_dir)
        if args.limit:
            batch = batch[: args.limit]
        if not batch:
            log("Nothing to apply: no --only IDs and no eval records in log dir.")
            return 1
        return apply_succeeded_batch(batch, args.log_dir)

    if args.evaluate:
        if args.only:
            batch = [c.strip() for c in args.only.split(",") if c.strip()]
        else:
            batch = dispatched_batch(args.log_dir)
        if args.limit:
            batch = batch[: args.limit]
        if not batch:
            log("Nothing to evaluate: no --only IDs and no prompts in log dir.")
            return 1
        args.log_dir.mkdir(parents=True, exist_ok=True)
        return evaluate_batch(
            batch,
            args.log_dir,
            args.muse_bin,
            args.workers,
            roster_by_id,
            apply=not args.no_apply,
            model=args.model,
            max_retries=args.max_retries,
        )

    if args.reconcile:
        if args.only:
            batch = [c.strip() for c in args.only.split(",") if c.strip()]
        else:
            batch = dispatched_batch(args.log_dir)
        if args.limit:
            batch = batch[: args.limit]
        if not batch:
            log("Nothing to reconcile: no --only IDs and no prompts in log dir.")
            return 1
        return reconcile(batch, args.log_dir)

    existing = spider_ids()
    unevaluated: list[dict[str, str]] = []

    if args.only:
        wanted = [c.strip() for c in args.only.split(",") if c.strip()]
        by_id = roster_by_id
        missing = []
        for source_id in wanted:
            if source_id in existing and not args.force:
                row = by_id.get(source_id)
                if (
                    row is not None
                    and not args.no_eval
                    and not eval_record_path(args.log_dir, source_id).is_file()
                ):
                    unevaluated.append(row)
                    log(f"pickup {source_id}: spider already built but not evaluated; will evaluate")
                else:
                    log(f"skip {source_id}: spider already exists")
                continue
            if source_id not in by_id:
                log(f"skip {source_id}: not in {ROSTER_CSV.name}")
                continue
            missing.append(by_id[source_id])
    else:
        missing = find_missing(roster_rows, existing)
        if not args.no_eval:
            unevaluated = find_unevaluated_builds(
                roster_rows, args.log_dir
            )

    if args.limit:
        missing = missing[: args.limit]

    if args.list:
        for row in missing:
            print(f"{row['source_id']}\t{row.get('name', '')}\t{row.get('start_url', '')}")
        for row in unevaluated:
            print(f"{row['source_id']}\t{row.get('name', '')}\tbuilt, not evaluated")
        log(f"{len(missing)} sources without a spider; {len(unevaluated)} built but not evaluated")
        return 0

    if not missing and not unevaluated:
        log("No missing sources to dispatch and no unevaluated builds to evaluate.")
        return 0

    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    prompts = {row["source_id"].strip(): build_prompt(skill_text, row) for row in missing + unevaluated}
    unevaluated_ids = {row["source_id"].strip() for row in unevaluated}

    if args.dry_run:
        for source_id, prompt in prompts.items():
            if source_id in unevaluated_ids:
                print(f"===== {source_id} (built, not evaluated: eval only, repair on retry) =====")
                continue
            print(f"===== {source_id} =====")
            print(prompt)
        return 0

    args.log_dir.mkdir(parents=True, exist_ok=True)
    created = ensure_sources(missing + unevaluated)
    if created:
        log(f"Ensured {len(created)} source row(s) in the database: {', '.join(sorted(created))}")
    start = time.monotonic()
    total = len(missing) + len(unevaluated)
    log(
        f"Dispatching {total} sources ({len(missing)} builds, {len(unevaluated)} catch-up evals) "
        f"with {args.workers} workers (model={args.model}, max_retries={args.max_retries}); "
        f"logs in {args.log_dir}/muse-spider-<ID>.log"
    )
    log(HOTKEYS_HELP)
    esc_state = _start_esc_watcher()
    failures: list[str] = []
    built: list[str] = []
    eval_started: set[str] = set()
    eval_records: list[dict] = []
    builds_done = 0
    evals_done = 0
    # Catch-up evals credit the earlier build that produced the spider file,
    # so the shared --max-retries budget behaves the same as for fresh builds.
    attempts: dict[str, int] = {row["source_id"].strip(): 0 for row in missing}
    attempts.update({row["source_id"].strip(): 1 for row in unevaluated})
    last_records: dict[str, dict] = {}
    pending: deque[tuple[str, str]] = deque(
        [("build", row["source_id"].strip()) for row in missing]
        + [("eval", row["source_id"].strip()) for row in unevaluated]
    )
    active: dict = {}
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        with pool:

            def submit_available() -> None:
                while pending and len(active) < args.workers and not stop_after_running_requested():
                    task, source_id = pending.popleft()
                    if task == "eval":
                        submit_eval(source_id)
                        continue
                    if task == "repair":
                        submit_repair(source_id)
                        continue
                    attempts[source_id] = attempts.get(source_id, 0) + 1
                    active[
                        pool.submit(run_worker, args.muse_bin, source_id, prompts[source_id], args.log_dir, args.model)
                    ] = ("build", source_id)

            def submit_eval(source_id: str) -> None:
                active[
                    pool.submit(
                        evaluate_source,
                        source_id,
                        args.log_dir,
                        args.muse_bin,
                        roster_by_id,
                        apply=not args.no_apply,
                        model=args.model,
                    )
                ] = ("eval", source_id)
                eval_started.add(source_id)

            def submit_repair(source_id: str) -> None:
                attempts[source_id] = attempts.get(source_id, 0) + 1
                active[
                    pool.submit(
                        repair_source,
                        source_id,
                        last_records[source_id],
                        args.log_dir,
                        args.muse_bin,
                        roster_by_id,
                        model=args.model,
                    )
                ] = ("repair", source_id)

            def requeue_build(source_id: str) -> None:
                pending.append(("build", source_id))
                log(
                    f"[retry {attempts[source_id]}/{args.max_retries}] {source_id}: "
                    f"requeued build"
                )

            def requeue_repair(source_id: str) -> None:
                pending.append(("repair", source_id))
                log(
                    f"[retry {attempts[source_id]}/{args.max_retries}] {source_id}: "
                    "requeued repair"
                )

            submit_available()
            while active:
                done, _ = wait(list(active), return_when=FIRST_COMPLETED)
                for future in done:
                    kind, source_id = active.pop(future)
                    if kind == "build":
                        builds_done += 1
                        _, returncode, err_tail = future.result()
                        built.append(source_id)
                        if returncode == 0:
                            log(f"[{builds_done}/{len(missing)} {elapsed(start)}] [done] {source_id}")
                        elif attempts[source_id] <= args.max_retries and not stop_after_running_requested():
                            requeue_build(source_id)
                            submit_available()
                            continue
                        else:
                            failures.append(source_id)
                            log(
                                f"[{builds_done}/{len(missing)} {elapsed(start)}] [FAILED ({returncode})] {source_id} "
                                f"(log: {args.log_dir / f'muse-spider-{source_id}.log'})"
                            )
                            if err_tail:
                                log(f"--- {source_id} error tail ---\n{err_tail}")
                        if not args.no_eval and not stop_after_running_requested():
                            submit_eval(source_id)
                    elif kind == "eval":
                        evals_done += 1
                        record = future.result()
                        last_records[source_id] = record
                        verdict = {"verdict": record["verdict"]} if record["verdict"] else None
                        if (
                            not record["applied"]
                            and is_retryable(verdict)
                            and attempts[source_id] <= args.max_retries
                            and not stop_after_running_requested()
                        ):
                            requeue_repair(source_id)
                        else:
                            eval_records.append(record)
                            flag = "ENABLED" if record["applied"] else record["verdict"] or "no-verdict"
                            log(
                                f"[eval {evals_done}/{total} {elapsed(start)}] [{flag}] {record['source_id']}: "
                                f"{record['detail']} (notes: {record['transcript']})"
                            )
                    else:
                        result = future.result()
                        if result["returncode"] != 0:
                            log(
                                f"[repair exit {result['returncode']}] {source_id} "
                                f"(log: {args.log_dir / f'muse-repair-{source_id}.log'})"
                            )
                            if result["err_tail"]:
                                log(f"--- {source_id} repair error tail ---\n{result['err_tail']}")
                        if not stop_after_running_requested():
                            submit_eval(source_id)
                    submit_available()
    except KeyboardInterrupt:
        kill_all()
        pool.shutdown(wait=False, cancel_futures=True)
        log(f"Interrupted: killed worker processes, {live_count()} still tracked; partial logs kept in {args.log_dir}.")
        return 130
    finally:
        _stop_esc_watcher(esc_state)
    if pending:
        skipped = sorted(source_id for _task, source_id in pending)
        log(f"ESC stop: {len(skipped)} sources never started: {', '.join(skipped)}")
        log(f"Redispatch them later:\n  python3 ops/dispatch_sources.py --only {','.join(skipped)}")
        awaiting_eval = sorted(set(built) - eval_started)
        log(
            "ESC stop: skipping further evaluation; evaluate the finished ones later with:\n"
            f"  python3 ops/dispatch_sources.py --evaluate --only {','.join(awaiting_eval)}"
            if awaiting_eval
            else "ESC stop: skipping further evaluation; nothing finished, so nothing to evaluate."
        )
        return 1 if failures else 0
    if failures:
        log(f"Finished in {elapsed(start)}: {len(failures)} builds failed: {', '.join(sorted(failures))}")
    else:
        log(f"Finished in {elapsed(start)}: all {len(missing)} builds completed successfully.")
    if args.no_eval:
        return 1 if failures else 0
    enabled = sorted(r["source_id"] for r in eval_records if r["applied"])
    disabled = sorted(r["source_id"] for r in eval_records if not r["applied"])
    log(f"Finished in {elapsed(start)}: {len(enabled)} enabled, {len(disabled)} left disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
