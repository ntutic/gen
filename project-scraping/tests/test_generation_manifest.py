"""Render the real template; do not copy this generator-only test to consumers."""
import hashlib
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def generator():
    spec = importlib.util.spec_from_file_location("template_generator", ROOT / "generate-project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_records_names_hashes_and_ownership(tmp_path):
    tool = generator()
    destination = tmp_path / "rendered"
    tool.generate("sample", "fact", "facts", destination)
    manifest = json.loads((destination / ".gen-manifest.json").read_text())
    assert manifest["names"] == {"project": "sample", "singular": "fact", "plural": "facts"}
    assert len(manifest["source_tree_sha256"]) == 64
    assert len(manifest["generator_sha256"]) == 64
    assert manifest["files"]["sample/worker.py"]["ownership"] == "shared"
    assert manifest["files"]["sample/project_contract.py"]["ownership"] == "extension"
    assert manifest["files"]["docs/project-contract.md"]["ownership"] == "extension"
    for name, metadata in manifest["files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == metadata["sha256"]
    assert not (destination / "tests/test_generation_manifest.py").exists()
    assert not (destination / "generate-project.py").exists()
    assert not (destination / ".agents/plans").exists()


def test_consumer_edits_are_detectable_without_regeneration(tmp_path):
    tool = generator()
    destination = tmp_path / "consumer"
    tool.generate("sample", "fact", "facts", destination)
    manifest = json.loads((destination / ".gen-manifest.json").read_text())
    path = destination / "sample/project_contract.py"
    path.write_text(path.read_text() + "\n# Consumer-specific rule\n")
    assert hashlib.sha256(path.read_bytes()).hexdigest() != manifest["files"]["sample/project_contract.py"]["sha256"]


def test_generator_protects_an_existing_manifest(tmp_path):
    (tmp_path / ".gen-manifest.json").write_text("owned")
    assert generator().main(["sample", "--records", "fact", "--plural", "facts", "--dest", str(tmp_path)]) == 2
    assert (tmp_path / ".gen-manifest.json").read_text() == "owned"


@pytest.mark.parametrize("name", ["class", "def", "return"])
def test_python_keywords_are_rejected(name, tmp_path):
    assert generator().main([name, "--records", "fact", "--plural", "facts", "--dest", str(tmp_path)]) == 2


@pytest.mark.parametrize("project,singular,plural", [
    ("alpha", "company", "companies"), ("beta", "analysis", "analyses"),
    ("gamma", "observation", "observations"),
])
def test_rendered_python_compiles_and_core_imports(project, singular, plural, tmp_path):
    destination = tmp_path / project
    generator().generate(project, singular, plural, destination)
    for path in destination.rglob("*.py"):
        py_compile.compile(str(path), doraise=True)
    env = {**os.environ, "PYTHONPATH": str(destination)}
    result = subprocess.run([sys.executable, "-c",
                             f"import {project}.acceptance; import {project}.project_contract; "
                             "from scraping.crawler.collection import CollectionProgress; "
                             "assert CollectionProgress(expected_total=1).page('first', ['a'], next_cursor=None)"],
                            cwd=destination, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_generated_project_pipeline(tmp_path):
    """Requires the normal project dev dependencies, including Scrapy."""
    destination = tmp_path / "pipeline"
    generator().generate("sample", "fact", "facts", destination)
    env = {**os.environ, "PYTHONPATH": str(destination),
           "SAMPLE_DATABASE_URL": f"sqlite:///{tmp_path / 'sample.db'}",
           "SAMPLE_CAPTURE_DIR": str(tmp_path / "captures"),
           "SAMPLE_RATE_LIMIT_DIR": str(tmp_path / "pacing")}
    initialized = subprocess.run([sys.executable, "-m", "sample", "init-db"], cwd=destination,
                                 env=env, capture_output=True, text=True, timeout=30)
    assert initialized.returncode == 0, initialized.stderr
    tested = subprocess.run([sys.executable, "-m", "pytest", "tests/test_EXAMPLE.py", "tests/test_project_contract.py", "-q"],
                            cwd=destination, env=env, capture_output=True, text=True, timeout=120)
    assert tested.returncode == 0, tested.stdout + tested.stderr
