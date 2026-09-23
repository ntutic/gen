#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

# No internally hosted dependencies; do not automatically upgrade third parties.
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_root/var/uv-cache}"
if [[ ! -x .venv/bin/python ]]; then
  uv venv .venv
fi
uv pip install --python .venv/bin/python -e '.[dev]'
