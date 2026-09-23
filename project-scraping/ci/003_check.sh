#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

for script in ci/*.sh ci/shared/*.sh; do
  [[ -f $script ]] || continue
  bash -n "$script"
done
test -x .venv/bin/python
# Explicit exclusions: outside a git checkout ruff would otherwise lint the
# environment and the project-local uv cache too.
.venv/bin/ruff check . --exclude .venv,var
