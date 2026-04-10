"""Claude Code PreToolUse hook — stdlib only.

Read hook JSON from stdin, forward to the AgentShield daemon over a Unix
socket, exit 0 (allow) or 2 (block). Fail-open on any error.

CRITICAL CONSTRAINT: this file is executed on every single tool call. It
MUST import only from the Python standard library — no pyyaml, no typer,
no rich, no agentshield.* imports either (the daemon is where those live).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (mirror of agentshield.daemon.server, duplicated to keep this
# file free of AgentShield imports)
# ---------------------------------------------------------------------------

_HOME = Path(os.environ.get("AGENTSHIELD_HOME", str(Path.home() / ".agentshield")))
_SOCKET_PATH = Path(
    os.environ.get("AGENTSHIELD_SOCKET", str(_HOME / "agentshield.sock"))
)
_ERROR_LOG = _HOME / "errors.log"
_CONNECT_TIMEOUT = 0.1  # 100ms fail-open cutoff
_READ_TIMEOUT = 0.1

EXIT_ALLOW = 0
EXIT_BLOCK = 2
EXIT_HOOK_ERROR = 1


def _log_error(message: str) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        with _ERROR_LOG.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"{ts} pre_tool {message}\n")
    except Exception:
        pass


def _read_stdin() -> dict | None:
    try:
        raw = sys.stdin.read()
    except Exception as e:
        _log_error(f"stdin_read_failed: {e}")
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        _log_error(f"stdin_json_decode: {e}")
        return None


def _build_event(hook_data: dict) -> dict:
    """Translate Claude Code hook payload → AgentShield ToolEvent dict.

    Claude Code hook fields vary by version; we map the common ones and fill
    sensible defaults so older versions still produce a valid event.
    """
    return {
        "tool_name": hook_data.get("tool_name", "unknown"),
        "tool_input": hook_data.get("tool_input", {}),
        "session_id": hook_data.get("session_id", "unknown"),
        "agent_id": hook_data.get("agent_id", "main"),
        "agent_type": hook_data.get("agent_type", "main"),
        "framework": "claude_code",
    }


def _call_daemon(event: dict) -> dict | None:
    """Send a pre_tool request to the daemon and return the decision dict.

    Returns None on any failure (caller falls open).
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(_CONNECT_TIMEOUT)
        s.connect(str(_SOCKET_PATH))
    except Exception as e:
        _log_error(f"connect_failed: {e}")
        return None

    try:
        payload = (json.dumps({"kind": "pre_tool", "event": event}) + "\n").encode(
            "utf-8"
        )
        s.sendall(payload)
        s.settimeout(_READ_TIMEOUT)
        buf = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in chunk:
                break
        line = bytes(buf).split(b"\n", 1)[0]
        if not line:
            return None
        return json.loads(line.decode("utf-8"))
    except Exception as e:
        _log_error(f"rpc_failed: {e}")
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def main() -> int:
    hook_data = _read_stdin()
    if hook_data is None:
        # No input or parse failure — don't break the agent.
        return EXIT_ALLOW

    event = _build_event(hook_data)
    decision = _call_daemon(event)
    if decision is None:
        return EXIT_ALLOW  # fail-open

    if decision.get("action") == "block":
        message = decision.get("message") or decision.get("reason") or "blocked"
        print(f"AgentShield: {message}", file=sys.stderr)
        return EXIT_BLOCK
    return EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
