#!/usr/bin/env python3
"""Generate a standalone renamed project, with a checksummed upstream manifest.

    gen project-scraping reitmaps --records company
    gen project-scraping reitmaps --records company --plural companies

The manifest records lineage and ownership; it never updates a consumer in place.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import keyword
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parent
MANIFEST_NAME = ".gen-manifest.json"
EXCLUDE_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".hypothesis", "node_modules", "var",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_NAMES = {".env", "generate-project.py", "test_generate.py",
                 "test_generation_manifest.py", "ports.env", MANIFEST_NAME}
RESERVED_PROJECTS = {"scraping", "scrapectl", "tests", "src", "web", "docs", "ops"}
RESERVED_NOUNS = {"source", "sources", "job", "jobs", "scraper", "scrapers", "feature", "features"}
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*\Z")
TEMPLATE_ONLY = re.compile(r"<!-- template-only -->\n.*?\n<!-- /template-only -->\s*", re.DOTALL)


def pluralize(noun: str) -> str:
    # Explicit --plural and programmatic rendering need only the standard library.
    try:
        import inflect
    except ImportError as exc:
        print("error: install 'inflect' or supply --plural explicitly", file=sys.stderr)
        raise SystemExit(2) from exc
    return inflect.engine().plural(noun)


def pascal(noun: str) -> str:
    return "".join(part.title() for part in noun.split("_"))


def replacements(project: str, singular: str, plural: str) -> list[tuple[re.Pattern, str]]:
    title = project.replace("_", " ").title()
    record, records = pascal(singular), pascal(plural)
    return [
        (re.compile(r"(?<![A-Za-z0-9_])scrapectl(?![A-Za-z0-9_])"), project),
        (re.compile(r"(?<![A-Za-z0-9_])vclist(?![A-Za-z0-9_])"), project),
        (re.compile(r"(?<![A-Za-z0-9_])VCLIST(?![A-Za-z0-9])"), project.upper()),
        (re.compile(r"(?<![A-Za-z])Vclist(?![A-Za-z])"), title),
        # Keep SourceRecord, scrape_source_records and source_record_id stable.
        (re.compile(r"(?<![A-Za-z])(?<!source_)records(?![a-z])"), plural),
        (re.compile(r"(?<![A-Za-z])(?<!source_)record(?![a-z])"), singular),
        (re.compile(r"(?<![A-Z])Records(?![a-z])"), records),
        (re.compile(r"(?<![A-Za-z])Record(?![a-z])"), record),
        (re.compile(r"(?<![A-Za-z0-9_])RECORD(?![A-Za-z0-9])"), singular.upper()),
    ]


def excluded(path: Path) -> bool:
    parts = path.parts
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return True
    if path.suffix == ".db" or path.name.endswith((".db-shm", ".db-wal", ".upgrade.lock")):
        return True
    if any(part in EXCLUDE_DIRS or part.endswith(".egg-info") for part in parts):
        return True
    # Extraction plans belong to the generator, not each new product's backlog.
    if parts[:2] == (".agents", "plans"):
        return True
    if len(parts) >= 3 and parts[0] == "web" and parts[2] == "assets":
        return True
    if len(parts) == 3 and parts[0] == "web" and path.name == "index.html":
        return True
    return False


def find_ruff() -> Path | None:
    for base in (TEMPLATE_ROOT, *TEMPLATE_ROOT.parents):
        candidate = base / ".venv" / "bin" / "ruff"
        if candidate.is_file():
            return candidate
    which = shutil.which("ruff")
    return Path(which) if which else None


def rename_relative(relative: Path, rules: list) -> Path:
    renamed = relative.as_posix()
    for pattern, replacement in rules:
        renamed = pattern.sub(replacement, renamed)
    return Path(renamed)


def emit(source: Path, relative: Path, dest: Path, rules: list) -> None:
    target = dest / rename_relative(relative, rules)
    if source.is_symlink():
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = source.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        shutil.copy2(source, target)
        return
    for pattern, replacement in rules:
        text = pattern.sub(replacement, text)
    if source.suffix == ".md":
        text = TEMPLATE_ONLY.sub("", text)
    target.write_text(text, encoding="utf-8")
    shutil.copymode(source, target)


def iter_sources() -> list[tuple[Path, Path]]:
    pairs = [
        (source, source.relative_to(TEMPLATE_ROOT))
        for source in sorted(TEMPLATE_ROOT.rglob("*"))
        if not excluded(source.relative_to(TEMPLATE_ROOT))
    ]
    shared = TEMPLATE_ROOT.parent / "shared"
    if shared.is_dir():
        for source in sorted(shared.rglob("*")):
            relative = Path("ci/shared") / source.relative_to(shared)
            if not excluded(relative):
                pairs.append((source, relative))
    return pairs


def existing_targets(dest: Path, rules: list) -> list[Path]:
    targets = [dest / rename_relative(relative, rules) for source, relative in iter_sources()
               if not source.is_dir() and not source.is_symlink()]
    targets.append(dest / MANIFEST_NAME)
    return [target for target in targets if target.exists() or target.is_symlink()]


def ownership(relative: Path) -> str:
    name = relative.as_posix()
    if name in {"scrapectl/project_contract.py", "docs/project-contract.md", "pyproject.toml", ".env.example", "README.md"}:
        return "extension"
    if (name == "docs/sources.csv" or name == "tests/test_EXAMPLE.py"
            or name.startswith(("scraping/crawler/spiders/", "src/web/", "tests/fixtures/EXAMPLE-"))):
        return "project"
    return "shared"


def source_revision() -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True, timeout=5)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def write_manifest(dest: Path, project: str, singular: str, plural: str, rules: list) -> None:
    files = {}
    source_digest = hashlib.sha256()
    for source, relative in iter_sources():
        if source.is_symlink() or not source.is_file():
            continue
        body = source.read_bytes()
        name = relative.as_posix().encode()
        source_digest.update(len(name).to_bytes(8, "big") + name)
        source_digest.update(len(body).to_bytes(8, "big") + body)
        generated = rename_relative(relative, rules)
        target = dest / generated
        files[generated.as_posix()] = {
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "ownership": ownership(relative),
        }
    manifest = {
        "schema_version": 1, "generator": "project-scraping", "source_repo": "ntutic/gen",
        "source_rev": source_revision(), "source_tree_sha256": source_digest.hexdigest(),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "names": {"project": project, "singular": singular, "plural": plural},
        "files": dict(sorted(files.items())),
    }
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def generate(project: str, singular: str, plural: str, dest: Path) -> Path:
    rules = replacements(project, singular, plural)
    for source, relative in iter_sources():
        emit(source, relative, dest, rules)
    write_manifest(dest, project, singular, plural, rules)
    return dest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", help="New project name (lowercase Python identifier)")
    parser.add_argument("--records", required=True, help="Record noun, singular (e.g. company)")
    parser.add_argument("--plural", help="Plural override (otherwise use inflect)")
    parser.add_argument("--dest", type=Path, help="Destination (default: current directory)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project, singular = args.project, args.records
    plural = args.plural or pluralize(singular)
    for label, value in (("project", project), ("records", singular), ("plural", plural)):
        if not NAME_PATTERN.fullmatch(value) or keyword.iskeyword(value):
            print(f"error: {label} {value!r} must be a non-keyword lowercase identifier", file=sys.stderr)
            return 2
    if project in RESERVED_PROJECTS:
        print(f"error: project {project!r} collides with a template directory", file=sys.stderr)
        return 2
    if singular in RESERVED_NOUNS or plural in RESERVED_NOUNS:
        print(f"error: record noun {singular!r}/{plural!r} collides with queue machinery", file=sys.stderr)
        return 2
    dest = args.dest or Path.cwd()
    if dest.resolve().is_relative_to(TEMPLATE_ROOT):
        print("error: destination must be outside the generator template", file=sys.stderr)
        return 2
    rules = replacements(project, singular, plural)
    conflicts = existing_targets(dest, rules)
    if conflicts:
        shown = ", ".join(str(path) for path in conflicts[:5])
        if len(conflicts) > 5:
            shown += f" (+{len(conflicts) - 5} more)"
        print(f"error: destination {dest} already contains: {shown}", file=sys.stderr)
        return 2
    generate(project, singular, plural, dest)
    ruff = find_ruff()
    if ruff is None:
        print("warning: ruff not found; run `ruff check --fix .` after installing dev deps", file=sys.stderr)
    else:
        # Do not lint/fix unrelated pre-existing files in a merged destination.
        python_paths = [str(rename_relative(relative, rules)) for source, relative in iter_sources()
                        if source.is_file() and not source.is_symlink() and source.suffix == ".py"]
        subprocess.run([str(ruff), "check", "--fix", "--quiet", *python_paths], cwd=dest, check=False)
        write_manifest(dest, project, singular, plural, rules)
    print(f"Created {dest} ({project}; records: {singular}/{plural}).")
    print("Next:")
    print("  uv venv && uv pip install -e '.[dev]' && npm install && npm run build")
    print(f"  python -m {project} init-db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
