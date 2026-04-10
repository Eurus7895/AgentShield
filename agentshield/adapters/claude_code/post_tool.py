"""Claude Code PostToolUse hook — stdlib only.

Read hook JSON from stdin (includes tool output), forward to the
AgentShield daemon over a Unix socket, and emit findings to stderr.

Exit semantics:
  0 — no findings or only informational findings → allow
  2 — at least one credential:* or imperative:* finding → warn/block

Because PostToolUse runs *after* the tool has already executed, exit 2
is a warning to Claude Code, not a prevention. The daemon has already
logged the event to SQLite.

CRITICAL CONSTRAINT: stdlib only. See pre_tool.py for the rationale.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

_HOME = Path(os.environ.get("AGENTSHIELD_HOME", str(Path.home() / ".agentshield")))
_SOCKET_PATH = Path(
    os.environ.get("AGENTSHIELD_SOCKET", str(_HOME / "agentshield.sock"))
)
_ERROR_LOG = _HOME / "errors.log"
_CONNECT_TIMEOUT = 0.1
_READ_TIMEOUT = 0.2  # scanning can be slightly slower than policy eval

EXIT_ALLOW = 0
EXIT_BLOCK = 2


def _log_error(message: str) -> None:
    try:
        _HOME.mkdir(parents=True, exist_ok=True)
        with _ERROR_LOG.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"{ts} post_tool {message}\n")
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


def _extract_output(hook_data: dict) -> str:
    """Claude Code hook may nest output as tool_response.{stdout, content, ...}."""
    resp = hook_data.get("tool_response")
    if isinstance(resp, dict):
        for key in ("stdout", "content", "output", "text"):
            value = resp.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(resp, default=str)
    if isinstance(resp, str):
        return resp
    return hook_data.get("output", "") or ""


def _build_event(hook_data: dict) -> dict:
    return {
        "tool_name": hook_data.get("tool_name", "unknown"),
        "tool_input": hook_data.get("tool_input", {}),
        "session_id": hook_data.get("session_id", "unknown"),
        "agent_id": hook_data.get("agent_id", "main"),
        "agent_type": hook_data.get("agent_type", "main"),
        "framework": "claude_code",
    }


def _call_daemon(event: dict, output: str) -> list[str] | None:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(_CONNECT_TIMEOUT)
        s.connect(str(_SOCKET_PATH))
    except Exception as e:
        _log_error(f"connect_failed: {e}")
        return None

    try:
        payload = (
            json.dumps({"kind": "post_tool", "event": event, "output": output})
            + "\n"
        ).encode("utf-8")
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
        parsed = json.loads(line.decode("utf-8"))
        findings = parsed.get("findings")
        if isinstance(findings, list):
            return [str(f) for f in findings]
        return []
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
        return EXIT_ALLOW

    event = _build_event(hook_data)
    output = _extract_output(hook_data)

    findings = _call_daemon(event, output)
    if findings is None:
        return EXIT_ALLOW  # fail-open

    bad = [f for f in findings if f.startswith("credential:") or f.startswith("imperative:")]
    if bad:
        print(
            "AgentShield: findings in tool output: " + ", ".join(bad),
            file=sys.stderr,
        )
        return EXIT_BLOCK
    return EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
