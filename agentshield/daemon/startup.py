"""AgentShield daemon lifecycle helpers.

Generates systemd --user units (Linux) and launchd plists (macOS) for
managed daemon lifecycle, plus PID-file-based start/stop/status fallbacks
for other environments (dev machines, CI, WSL without systemd).

Scope: just enough to satisfy the Week 1 acceptance scorecard. Real install
flows run from `adapters/claude_code/installer.py` which imports from here.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agentshield.daemon.server import DEFAULT_HOME, DEFAULT_PID_FILE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# ---------------------------------------------------------------------------
# systemd --user unit (Linux)
# ---------------------------------------------------------------------------


SYSTEMD_UNIT_TEMPLATE = """\
[Unit]
Description=AgentShield daemon (integrity + audit slice of the agent harness)
After=default.target

[Service]
Type=simple
ExecStart={python} -m agentshield.daemon.server
Restart=on-failure
RestartSec=1
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "agentshield.service"


def write_systemd_unit() -> Path:
    path = systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = SYSTEMD_UNIT_TEMPLATE.format(python=sys.executable)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# launchd plist (macOS)
# ---------------------------------------------------------------------------


LAUNCHD_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.agentshield.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>agentshield.daemon.server</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{home}/daemon.out.log</string>
  <key>StandardErrorPath</key><string>{home}/daemon.err.log</string>
</dict>
</plist>
"""


def launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "dev.agentshield.daemon.plist"


def write_launchd_plist() -> Path:
    path = launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = LAUNCHD_PLIST_TEMPLATE.format(
        python=sys.executable, home=str(DEFAULT_HOME)
    )
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# PID-file fallback
# ---------------------------------------------------------------------------


@dataclass
class DaemonStatus:
    running: bool
    pid: int | None
    detail: str


def read_pid(pid_file: Path = DEFAULT_PID_FILE) -> int | None:
    try:
        text = pid_file.read_text(encoding="utf-8").strip()
        return int(text) if text else None
    except (OSError, ValueError):
        return None


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def status(pid_file: Path = DEFAULT_PID_FILE) -> DaemonStatus:
    pid = read_pid(pid_file)
    if pid is None:
        return DaemonStatus(running=False, pid=None, detail="no pid file")
    if is_process_alive(pid):
        return DaemonStatus(running=True, pid=pid, detail="running")
    return DaemonStatus(running=False, pid=pid, detail="stale pid file")


def start_background() -> int:
    """Fork a background daemon process and return its PID.

    This is the generic fallback used when systemd/launchd aren't available.
    """
    existing = status()
    if existing.running:
        assert existing.pid is not None
        return existing.pid

    proc = subprocess.Popen(
        [sys.executable, "-m", "agentshield.daemon.server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def stop(pid_file: Path = DEFAULT_PID_FILE) -> bool:
    pid = read_pid(pid_file)
    if pid is None or not is_process_alive(pid):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


# ---------------------------------------------------------------------------
# Public install entry point
# ---------------------------------------------------------------------------


def install_service() -> Path | None:
    """Install the daemon as a managed system service.

    Returns the path of the generated unit/plist, or None if the platform
    falls back to the PID-file approach.
    """
    DEFAULT_HOME.mkdir(parents=True, exist_ok=True)
    if _is_macos():
        return write_launchd_plist()
    if _is_linux():
        return write_systemd_unit()
    return None
