# Shared port-block selector for gen project deploys.
#
# Vendored into generated projects at ci/shared/ports.sh by
# generate-project.py; edit here, not in the copies.
#
# Usage: eval "$(select_ports)"  ->  sets WEB_PORT and ADMIN_PORT.
# Scans ~/code/*/ci for 40xxx ports already taken and picks the lowest free
# 40N00/40N02 block (N = 1..9).
select_ports() {
  local used n web_port admin_port
  used=$(grep -rhoE '\b40[0-9]{3}\b' ~/code/*/ci/ports.env ~/code/*/ci/*.sh ~/code/*/ops/systemd/* 2>/dev/null | sort -u || true)
  n=1
  while (( n <= 9 )); do
    web_port=$((40000 + n * 100))
    admin_port=$((web_port + 2))
    if ! grep -qx -e "$web_port" -e "$admin_port" <<<"${used:-}"; then
      printf 'WEB_PORT=%s\nADMIN_PORT=%s\n' "$web_port" "$admin_port"
      return 0
    fi
    ((n++))
  done
  echo "error: no free 40N00/40N02 block; pin WEB_PORT/ADMIN_PORT in ci/ports.env" >&2
  return 1
}
