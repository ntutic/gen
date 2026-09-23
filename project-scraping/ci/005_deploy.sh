#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
prefix=vclist

for executable in systemctl loginctl curl; do
  command -v "$executable" >/dev/null
done
test -x .venv/bin/python
test -f web/web/index.html
test -f web/admin/index.html

# Ports come from ci/ports.env when pinned; otherwise the shared selector
# picks the lowest free 40N00/40N02 block. In this checkout the shared file
# lives at ../shared/ports.sh; generated projects carry a vendored copy at
# ci/shared/ports.sh so they stay standalone.
ports_file=ci/ports.env
shared_ports=""
for candidate in "$repo_root/../shared/ports.sh" "$repo_root/ci/shared/ports.sh"; do
  if [[ -f $candidate ]]; then
    shared_ports=$candidate
    break
  fi
done
test -n "$shared_ports"
# shellcheck disable=SC1090,SC1091
source "$shared_ports"
web_port=""
admin_port=""
if [[ -f $ports_file ]]; then
  # shellcheck disable=SC1090
  source "$ports_file"
  web_port="${WEB_PORT:-}"
  admin_port="${ADMIN_PORT:-}"
fi
if [[ -z $web_port || -z $admin_port ]]; then
  eval "$(select_ports)"
  web_port="${WEB_PORT:-}"
  admin_port="${ADMIN_PORT:-}"
  printf 'WEB_PORT=%s\nADMIN_PORT=%s\n' "$web_port" "$admin_port" > "$ports_file"
fi

# Refuse ports held by anything that is not already our services.
for site in "web:$web_port" "admin:$admin_port"; do
  IFS=: read -r name port <<< "$site"
  if ! systemctl --user is-active --quiet "$prefix-$name.service"; then
    .venv/bin/python -c '
import socket, sys
with socket.socket() as listener:
    listener.bind(("127.0.0.1", int(sys.argv[1])))
' "$port"
  fi
done

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
.venv/bin/python - "$repo_root" "$unit_dir" "$prefix" "$web_port" "$admin_port" <<'PY'
import sys
from pathlib import Path

root, destination, prefix, web_port, admin_port = sys.argv[1:]
ports = {"web": web_port, "admin": admin_port, "worker": None}
escaped = root.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
for name, port in ports.items():
    template = Path("ops/systemd", f"vclist-{name}.service").read_text()
    unit = template.replace("WorkingDirectory=@REPO_ROOT@", "WorkingDirectory=" + root.replace("%", "%%"))
    unit = unit.replace("@REPO_ROOT@", escaped.replace("$", "$$"))
    if port is not None:
        unit = unit.replace("@PORT@", port)
    Path(destination, f"{prefix}-{name}.service").write_text(unit)
PY

# Linger starts the user manager at boot, even before anyone logs in.
if [[ $(loginctl show-user "$(id -un)" -p Linger --value) != yes ]]; then
  loginctl enable-linger "$(id -un)"
fi
systemctl --user daemon-reload
# Nothing starts at boot: every service runs until reboot or a manual stop,
# and projects opt into boot enablement by hand when they want it.
for service in "$prefix-web" "$prefix-admin" "$prefix-worker"; do
  if systemctl --user is-enabled --quiet "$service.service"; then
    systemctl --user disable "$service.service"
  fi
done

for site in "web:$web_port:web" "admin:$admin_port:admin"; do
  IFS=: read -r name port directory <<< "$site"
  service_name="$prefix-$name.service"
  local_url="http://127.0.0.1:$port"
  systemctl --user restart "$service_name"
  if ! curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 1 --max-time 2 "$local_url/health"; then
    journalctl --user -u "$service_name" -n 30 --no-pager
    exit 1
  fi
  curl --fail --silent --show-error --max-time 5 "$local_url/" | cmp -s "web/$directory/index.html" -
  systemctl --user is-active --quiet "$service_name"
  printf '\n%s: %s/\n' "$directory" "$local_url"
done

# Apply the installed worker unit, including its spider concurrency, on every deployment.
systemctl --user restart "$prefix-worker.service"
if ! systemctl --user is-active --quiet "$prefix-worker.service"; then
  journalctl --user -u "$prefix-worker.service" -n 30 --no-pager
  exit 1
fi
printf '\nScraping worker: running\n'
