from __future__ import annotations

import importlib.util
import os
import py_compile
import subprocess
import sys
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_project", TEMPLATE_ROOT / "generate-project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pluralize():
    generate = load_generator()
    assert generate.pluralize("company") == "companies"
    assert generate.pluralize("box") == "boxes"
    assert generate.pluralize("fund") == "funds"
    assert generate.pluralize("analysis") == "analyses"
    assert generate.pluralize("child") == "children"


def test_generate_renames_project_and_records(tmp_path):
    generate = load_generator()
    dest = tmp_path / "acme"
    result = subprocess.run(
        [sys.executable, "generate-project.py", "acme", "--records", "company", "--dest", str(dest)],
        cwd=TEMPLATE_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    assert (dest / "acme" / "api.py").is_file()
    assert (dest / "acme" / "admin.py").is_file()
    assert not (dest / "scrapectl").exists()
    assert (dest / "src" / "web" / "api" / "queries" / "useCompanies.ts").is_file()
    assert (dest / "src" / "web" / "views" / "CompaniesBrowser.vue").is_file()
    assert (dest / "scraping" / "crawler" / "loaders" / "company_loader.py").is_file()
    assert (dest / "web" / "admin" / "styles.css").is_file()
    assert not (dest / "web" / "admin" / "index.html").exists()  # build output; npm run build recreates it
    assert not (dest / "web" / "admin" / "assets").exists()
    for excluded in (".venv", "node_modules", "generate-project.py"):
        assert not (dest / excluded).exists()
    assert not (dest / "tests" / "test_generate.py").exists()
    assert (dest / "ci" / "ports.env.example").is_file()
    assert (dest / "ci" / "shared" / "ports.sh").is_file()
    assert not (dest / "ci" / "ports.env").exists()
    assert generate.excluded(Path("ci/ports.env"))
    assert os.access(dest / "ci" / "005_deploy.sh", os.X_OK)

    models = (dest / "acme" / "models.py").read_text()
    assert "class Company(Base):" in models
    assert "class CompanyFeature(Base):" in models
    assert '__tablename__ = "companies"' in models
    assert "SourceRecord" in models  # retained-input machinery keeps stable names
    assert (dest / "acme" / "settings.py").read_text().count("acme.db") == 1
    assert 'DATABASE_URL_ENV = "ACME_DATABASE_URL"' in (dest / "acme" / "settings.py").read_text()
    assert "/api/companies" in (dest / "acme" / "api.py").read_text()
    assert "<title>Acme companies</title>" in (dest / "src" / "web" / "index.html").read_text()
    assert "<title>Acme administration</title>" in (dest / "src" / "admin" / "index.html").read_text()
    assert 'name = "acme"' in (dest / "pyproject.toml").read_text()
    assert '"name": "acme-frontends"' in (dest / "package.json").read_text()
    assert "python -m acme " in (dest / "README.md").read_text()
    assert "generate-project.py" not in (dest / "README.md").read_text()
    assert "template-only" not in (dest / "README.md").read_text()

    # No template token survives anywhere: crude substrings for project nouns
    # (nothing may keep them), the generator's own patterns for record nouns
    # (fixpoint: nothing it claims to rename is left behind).
    rules = generate.replacements("acme", "company", "companies")
    offenders = []
    for path in sorted(dest.rglob("*")):
        if path.is_dir() or path.is_symlink():
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        for token in ("scrapectl", "vclist", "VCLIST", "Vclist"):
            if token in text or token in path.name:
                offenders.append(f"{path}: {token}")
        for pattern, _ in rules:
            if pattern.search(text) or pattern.search(path.name):
                offenders.append(f"{path}: {pattern.pattern}")
    assert offenders == []

    for path in dest.rglob("*.py"):
        py_compile.compile(str(path), doraise=True)

    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."], cwd=dest, capture_output=True, text=True,
    )
    assert lint.returncode == 0, lint.stdout


def test_generate_accepts_template_default_project_name(tmp_path):
    result = subprocess.run(
        [sys.executable, "generate-project.py", "vclist", "--records", "company",
         "--dest", str(tmp_path / "vclist")],
        cwd=TEMPLATE_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "vclist" / "vclist" / "api.py").is_file()


def test_generate_defaults_to_cwd(tmp_path):
    dest = tmp_path / "work"
    dest.mkdir()
    script = str(TEMPLATE_ROOT / "generate-project.py")
    result = subprocess.run(
        [sys.executable, script, "acme", "--records", "company"],
        cwd=dest, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (dest / "acme" / "api.py").is_file()
    assert not (dest / "acme" / "acme").exists()  # no nested container
    assert "cd" not in result.stdout.split("Next:")[1].splitlines()[1]

    rerun = subprocess.run(
        [sys.executable, script, "acme", "--records", "company"],
        cwd=dest, capture_output=True, text=True,
    )
    assert rerun.returncode == 2


def test_generate_rejects_bad_names_and_conflicting_dest(tmp_path):
    for argv in (["Acme", "--records", "company"],
                ["acme", "--records", "Company"],
                ["acme", "--records", "source"],
                ["scraping", "--records", "company"]):
        result = subprocess.run(
            [sys.executable, "generate-project.py", *argv, "--dest", str(tmp_path / "out")],
            cwd=TEMPLATE_ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 2, argv
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "pyproject.toml").write_text('[project]\nname = "taken"\n')
    result = subprocess.run(
        [sys.executable, "generate-project.py", "acme", "--records", "company",
         "--dest", str(taken)],
        cwd=TEMPLATE_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    result = subprocess.run(
        [sys.executable, "generate-project.py", "acme", "--records", "company",
         "--dest", str(empty)],
        cwd=TEMPLATE_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (empty / "acme" / "api.py").is_file()
