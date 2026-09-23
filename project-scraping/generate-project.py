#!/usr/bin/env python3
"""Generate a renamed project from this template.

Copies the template tree (excluding environments, databases, and build
output) and renames the project and record nouns everywhere: package,
database file, env vars, models, tables, API paths, frontend files and
labels, docs, and tests. Pluralization uses the `inflect` library;
`--plural` overrides it.

    gen project-scraping reitmaps --records company
    gen project-scraping reitmaps --records company --plural companies
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import inflect
except ImportError as exc:
    print("error: the 'inflect' package is required (run inside the template checkout)", file=sys.stderr)
    raise SystemExit(2) from exc

TEMPLATE_ROOT = Path(__file__).resolve().parent
_INFLECT = inflect.engine()

EXCLUDE_DIRS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".hypothesis", "node_modules", "var",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_NAMES = {".env", "generate-project.py", "test_generate.py", "ports.env"}
RESERVED_PROJECTS = {"scraping", "scrapectl", "tests", "src", "web", "docs", "ops"}
RESERVED_NOUNS = {"source", "sources", "job", "jobs", "scraper", "scrapers", "feature", "features"}
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*\Z")


def pluralize(noun: str) -> str:
    return _INFLECT.plural(noun)


def pascal(noun: str) -> str:
    return "".join(part.title() for part in noun.split("_"))


def replacements(project: str, singular: str, plural: str) -> list[tuple[re.Pattern, str]]:
    title = project.replace("_", " ").title()
    record, records = pascal(singular), pascal(plural)
    return [
        # Project nouns. The upper form allows a trailing underscore so
        # VCLIST_DATABASE_URL and VCLIST_CAPTURE_DIR rename correctly.
        (re.compile(r"(?<![A-Za-z0-9_])scrapectl(?![A-Za-z0-9_])"), project),
        (re.compile(r"(?<![A-Za-z0-9_])vclist(?![A-Za-z0-9_])"), project),
        (re.compile(r"(?<![A-Za-z0-9_])VCLIST(?![A-Za-z0-9])"), project.upper()),
        (re.compile(r"(?<![A-Za-z])Vclist(?![A-Za-z])"), title),
        # Record nouns. The lowercase forms refuse a `source_` prefix so the
        # retained-input machinery (SourceRecord, scrape_source_records,
        # source_record_id) keeps its stable names. The class-singular form
        # refuses a letter predecessor so SourceRecord survives, but still
        # matches RecordFeature and RecordLoader; the class plural additionally
        # accepts a lowercase predecessor (useRecords).
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
    if len(parts) >= 3 and parts[0] == "web" and parts[2] == "assets":
        return True  # Vite build output; the new project rebuilds it.
    if len(parts) == 3 and parts[0] == "web" and path.name == "index.html":
        return True  # Vite build output; the new project rebuilds it.
    return False


TEMPLATE_ONLY = re.compile(r"<!-- template-only -->\n.*?\n<!-- /template-only -->\s*", re.DOTALL)


def find_ruff() -> Path | None:
    for base in (TEMPLATE_ROOT, *TEMPLATE_ROOT.parents):
        candidate = base / ".venv" / "bin" / "ruff"
        if candidate.is_file():
            return candidate
    which = shutil.which("ruff")
    return Path(which) if which else None


def emit(source: Path, relative: Path, dest: Path, rules: list) -> None:
    renamed = relative.as_posix()
    for pattern, replacement in rules:
        renamed = pattern.sub(replacement, renamed)
    target = dest / renamed
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return
    if source.is_symlink():
        return
    try:
        text = source.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return
    for pattern, replacement in rules:
        text = pattern.sub(replacement, text)
    if source.suffix == ".md":
        text = TEMPLATE_ONLY.sub("", text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    shutil.copymode(source, target)


def iter_sources() -> list[tuple[Path, Path]]:
    """Template files as (source, dest-relative-before-rename) pairs."""
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


def rename_relative(relative: Path, rules: list) -> Path:
    renamed = relative.as_posix()
    for pattern, replacement in rules:
        renamed = pattern.sub(replacement, renamed)
    return Path(renamed)


def existing_targets(dest: Path, rules: list) -> list[Path]:
    """File targets that already exist under dest (dirs merge, files collide)."""
    return [
        dest / rename_relative(relative, rules)
        for source, relative in iter_sources()
        if not source.is_dir() and not source.is_symlink()
        and (dest / rename_relative(relative, rules)).exists()
    ]


def generate(project: str, singular: str, plural: str, dest: Path) -> Path:
    rules = replacements(project, singular, plural)
    for source, relative in iter_sources():
        emit(source, relative, dest, rules)
    return dest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a renamed project from this template.")
    parser.add_argument("project", help="New project name (lowercase identifier); becomes the package name")
    parser.add_argument("--records", required=True, help="Record noun, singular (e.g. company)")
    parser.add_argument("--plural", help="Record noun, plural (default: inflected from --records)")
    parser.add_argument("--dest", type=Path, help="Destination directory (default: current directory)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project, singular = args.project, args.records
    plural = args.plural or pluralize(singular)
    for label, value in (("project", project), ("records", singular), ("plural", plural)):
        if not NAME_PATTERN.match(value):
            print(f"error: {label} {value!r} must be a lowercase identifier", file=sys.stderr)
            return 2
    if project in RESERVED_PROJECTS:
        print(f"error: project {project!r} collides with a template directory", file=sys.stderr)
        return 2
    if singular in RESERVED_NOUNS or plural in RESERVED_NOUNS:
        print(f"error: record noun {singular!r}/{plural!r} collides with queue machinery", file=sys.stderr)
        return 2
    dest = args.dest or Path.cwd()
    conflicts = existing_targets(dest, replacements(project, singular, plural))
    if conflicts:
        shown = ", ".join(str(path) for path in conflicts[:5])
        if len(conflicts) > 5:
            shown += f" (+{len(conflicts) - 5} more)"
        print(f"error: destination {dest} already contains: {shown}", file=sys.stderr)
        return 2
    generate(project, singular, plural, dest)
    # Renaming can unsort import members (Company sorts before FeatureUnit);
    # reuse the template's own linter to restore the pristine state.
    ruff = find_ruff()
    if ruff is None:
        print("warning: ruff not found; run `ruff check --fix .` after installing dev deps", file=sys.stderr)
    else:
        subprocess.run([str(ruff), "check", "--fix", "--quiet", "."], cwd=dest, check=False)
    print(f"Created {dest} ({project}; records: {singular}/{plural}).")
    print("Next:")
    print("  uv venv && uv pip install -e '.[dev]' && npm install && npm run build")
    print(f"  python -m {project} init-db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
