"""Tests for agentshield.adapters.claude_code.

Three concerns:
  1. pre_tool.py / post_tool.py are stdlib-only (enforced via AST scan).
  2. The hook scripts talk to a mock Unix-socket server and exit with the
     correct code (0 allow, 2 block).
  3. The installer merges settings.json without clobbering user config.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ADAPTER_DIR = Path(__file__).resolve().parents[1] / "agentshield" / "adapters" / "claude_code"
PRE_TOOL = ADAPTER_DIR / "pre_tool.py"
POST_TOOL = ADAPTER_DIR / "post_tool.py"


# ---------------------------------------------------------------------------
# Static check: stdlib-only
# ---------------------------------------------------------------------------


STDLIB_MODULES = {
    "json",
    "os",
    "socket",
    "sys",
    "time",
    "pathlib",
    "__future__",
    "typing",
}


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                modules.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


class TestStdlibOnly:
    def test_pre_tool_stdlib_only(self):
        mods = _collect_imports(PRE_TOOL)
        non_stdlib = mods - STDLIB_MODULES
        assert not non_stdlib, f"pre_tool.py imports non-stdlib: {non_stdlib}"

    def test_post_tool_stdlib_only(self):
        mods = _collect_imports(POST_TOOL)
        non_stdlib = mods - STDLIB_MODULES
        assert not non_stdlib, f"post_tool.py imports non-stdlib: {non_stdlib}"

    def test_no_agentshield_import(self):
        """The hook scripts must never import from the agentshield package."""
        for path in (PRE_TOOL, POST_TOOL):
            mods = _collect_imports(path)
            assert "agentshield" not in mods


# ---------------------------------------------------------------------------
# Mock socket server fixtures
# ---------------------------------------------------------------------------


class MockDaemon:
    """A minimal blocking Unix-socket server that returns a fixed response."""

    def __init__(self, socket_path: Path, response: dict) -> None:
        self.socket_path = socket_path
        self.response = response
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.received: list[dict] = []

    def start(self) -> None:
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.socket_path))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.settimeout(1.0)
                buf = bytearray()
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)
                line = bytes(buf).split(b"\n", 1)[0]
                if line:
                    try:
                        self.received.append(json.loads(line.decode("utf-8")))
                    except json.JSONDecodeError:
                        pass
                    conn.sendall((json.dumps(self.response) + "\n").encode("utf-8"))
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
        except OSError:
            pass


@pytest.fixture
def mock_home(tmp_path: Path) -> Path:
    home = tmp_path / "ash_home"
    home.mkdir()
    return home


def _run_hook(
    script: Path,
    hook_input: dict,
    home: Path,
    socket_path: Path,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AGENTSHIELD_HOME"] = str(home)
    env["AGENTSHIELD_SOCKET"] = str(socket_path)
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


HOOK_INPUT_BASIC = {
    "tool_name": "bash",
    "tool_input": {"command": "ls"},
    "session_id": "sess-1",
    "agent_id": "main",
    "agent_type": "main",
}


# ---------------------------------------------------------------------------
# pre_tool.py behaviour
# ---------------------------------------------------------------------------


class TestPreToolHook:
    def test_allow_exit_0(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "daemon.sock"
        daemon = MockDaemon(sock, {"action": "allow", "reason": None, "message": None})
        daemon.start()
        try:
            result = _run_hook(PRE_TOOL, HOOK_INPUT_BASIC, mock_home, sock)
        finally:
            daemon.stop()
        assert result.returncode == 0
        assert result.stderr == ""
        assert len(daemon.received) == 1
        assert daemon.received[0]["kind"] == "pre_tool"
        assert daemon.received[0]["event"]["tool_name"] == "bash"

    def test_block_exit_2_with_stderr(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "daemon.sock"
        daemon = MockDaemon(
            sock,
            {
                "action": "block",
                "reason": "block_rm_rf",
                "message": "Dangerous deletion blocked",
            },
        )
        daemon.start()
        try:
            result = _run_hook(PRE_TOOL, HOOK_INPUT_BASIC, mock_home, sock)
        finally:
            daemon.stop()
        assert result.returncode == 2
        assert "AgentShield" in result.stderr
        assert "Dangerous deletion blocked" in result.stderr

    def test_fail_open_when_socket_missing(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "no_socket.sock"  # never created
        result = _run_hook(PRE_TOOL, HOOK_INPUT_BASIC, mock_home, sock)
        assert result.returncode == 0
        assert (mock_home / "errors.log").exists()

    def test_empty_stdin_exits_allow(self, mock_home: Path, tmp_path: Path):
        env = os.environ.copy()
        env["AGENTSHIELD_HOME"] = str(mock_home)
        env["AGENTSHIELD_SOCKET"] = str(tmp_path / "none.sock")
        result = subprocess.run(
            [sys.executable, str(PRE_TOOL)],
            input="",
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# post_tool.py behaviour
# ---------------------------------------------------------------------------


POST_HOOK_INPUT = {
    **HOOK_INPUT_BASIC,
    "tool_response": {"stdout": "hello world"},
}


class TestPostToolHook:
    def test_no_findings_exit_0(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "daemon.sock"
        daemon = MockDaemon(sock, {"findings": []})
        daemon.start()
        try:
            result = _run_hook(POST_TOOL, POST_HOOK_INPUT, mock_home, sock)
        finally:
            daemon.stop()
        assert result.returncode == 0
        assert len(daemon.received) == 1
        assert daemon.received[0]["kind"] == "post_tool"
        assert daemon.received[0]["output"] == "hello world"

    def test_credential_finding_exit_2(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "daemon.sock"
        daemon = MockDaemon(sock, {"findings": ["credential:aws_key"]})
        daemon.start()
        try:
            result = _run_hook(POST_TOOL, POST_HOOK_INPUT, mock_home, sock)
        finally:
            daemon.stop()
        assert result.returncode == 2
        assert "credential:aws_key" in result.stderr

    def test_imperative_finding_exit_2(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "daemon.sock"
        daemon = MockDaemon(sock, {"findings": ["imperative:memory_update"]})
        daemon.start()
        try:
            result = _run_hook(POST_TOOL, POST_HOOK_INPUT, mock_home, sock)
        finally:
            daemon.stop()
        assert result.returncode == 2
        assert "imperative:memory_update" in result.stderr

    def test_fail_open_when_socket_missing(self, mock_home: Path, tmp_path: Path):
        sock = tmp_path / "no_socket.sock"
        result = _run_hook(POST_TOOL, POST_HOOK_INPUT, mock_home, sock)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


class TestInstaller:
    def test_merge_settings_creates_hooks(self, tmp_path: Path):
        from agentshield.adapters.claude_code.installer import merge_settings

        pre = tmp_path / "pre_tool.py"
        post = tmp_path / "post_tool.py"
        pre.write_text("# stub\n")
        post.write_text("# stub\n")
        settings = tmp_path / "settings.json"
        merge_settings(pre, post, settings)
        data = json.loads(settings.read_text())
        assert "PreToolUse" in data["hooks"]
        assert "PostToolUse" in data["hooks"]
        pre_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert str(pre) in pre_cmd

    def test_merge_settings_preserves_existing(self, tmp_path: Path):
        from agentshield.adapters.claude_code.installer import merge_settings

        pre = tmp_path / "pre_tool.py"
        post = tmp_path / "post_tool.py"
        pre.write_text("# stub\n")
        post.write_text("# stub\n")
        settings = tmp_path / "settings.json"
        existing = {
            "theme": "dark",
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo existing"}
                        ],
                    }
                ]
            },
        }
        settings.write_text(json.dumps(existing))
        merge_settings(pre, post, settings)
        data = json.loads(settings.read_text())
        assert data["theme"] == "dark"
        pre_groups = data["hooks"]["PreToolUse"]
        # Existing command still there; AgentShield command appended.
        all_commands = [
            h["command"]
            for g in pre_groups
            for h in g.get("hooks", [])
        ]
        assert "echo existing" in all_commands
        assert any(str(pre) in c for c in all_commands)

    def test_merge_settings_idempotent(self, tmp_path: Path):
        from agentshield.adapters.claude_code.installer import merge_settings

        pre = tmp_path / "pre_tool.py"
        post = tmp_path / "post_tool.py"
        pre.write_text("# stub\n")
        post.write_text("# stub\n")
        settings = tmp_path / "settings.json"
        merge_settings(pre, post, settings)
        merge_settings(pre, post, settings)
        merge_settings(pre, post, settings)
        data = json.loads(settings.read_text())
        # Exactly one AgentShield hook group per event.
        assert len(data["hooks"]["PreToolUse"]) == 1
        assert len(data["hooks"]["PostToolUse"]) == 1

    def test_install_end_to_end(self, tmp_path: Path):
        from agentshield.adapters.claude_code.installer import install

        home = tmp_path / "ash_home"
        settings = tmp_path / "settings.json"
        report = install(home=home, settings_path=settings)
        assert (home / "policy.yaml").exists()
        assert (home / "pre_tool.py").exists()
        assert (home / "post_tool.py").exists()
        assert settings.exists()
        assert report["policy"].endswith("policy.yaml")
