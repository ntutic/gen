#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

command -v npm >/dev/null
npm ci
npm run build
test -f web/web/index.html
test -f web/admin/index.html
