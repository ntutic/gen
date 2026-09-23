#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

test -x .venv/bin/python
# The suite must never touch the hosted database.
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT
VCLIST_DATABASE_URL="sqlite:///$test_dir/vclist.db" .venv/bin/python -m pytest -q
