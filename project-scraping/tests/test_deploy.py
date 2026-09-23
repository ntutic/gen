from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent


def deploy_port_block() -> str:
    """The port-resolution stanza of ci/005_deploy.sh, verbatim."""
    text = (TEMPLATE_ROOT / "ci" / "005_deploy.sh").read_text()
    start = text.index("if [[ -z $web_port")
    end = text.index("\nfi", start) + len("\nfi")
    return text[start:end]


def run_port_block(home: Path, ports_file: Path) -> subprocess.CompletedProcess:
    shared = TEMPLATE_ROOT.parent / "shared" / "ports.sh"
    script = (
        "web_port=''\nadmin_port=''\n"
        f"ports_file={shlex.quote(str(ports_file))}\n"
        f"source {shlex.quote(str(shared))}\n"
        f"{deploy_port_block()}\n"
    )
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_deploy_pins_selected_ports(tmp_path):
    """First deploy (no pins) must write the selected numeric ports.

    Regression: select_ports emits uppercase WEB_PORT/ADMIN_PORT while the
    deploy script consumes lowercase $web_port/$admin_port; without bridging
    the two it wrote empty pins and later crashed on int('').
    """
    home = tmp_path / "home"
    home.mkdir()
    ports_file = tmp_path / "ports.env"
    result = run_port_block(home, ports_file)
    assert result.returncode == 0, result.stderr
    pinned = ports_file.read_text()
    assert re.fullmatch(r"WEB_PORT=40\d00\nADMIN_PORT=40\d02\n", pinned), pinned
