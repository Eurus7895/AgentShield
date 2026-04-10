"""AgentShield daemon — asyncio Unix-socket server hosting the engine.

Protocol: line-delimited JSON. One request per connection:

  client → server:
    {"kind": "pre_tool",  "event": {...}}
    {"kind": "post_tool", "event": {...}, "output": "..."}

  server → client:
    pre_tool  → {"action": "allow"|"block", "reason": ..., "message": ...}
    post_tool → {"findings": [...]}

Design principles:
  * **Fail-open.** Every handler is wrapped; on any exception we return allow
    (for pre_tool) or empty findings (for post_tool), and append a line to the
    error log.
  * **Thread-safe engine.** `AgentShieldEngine` already holds its own lock; the
    daemon just routes messages.
  * **Policy hot-reload.** A background asyncio task polls `policy.yaml`'s
    mtime every `reload_interval_seconds` (default 0.5s). On change, it calls
    `policy.reload_from_path()`, which atomically swaps the rule set.
  * **Single request per connection.** Matches the stdlib-only hook client,
    which opens a new connection per tool call.

This daemon is wired by `startup.py` into systemd (Linux) or launchd (macOS);
for other environments a PID file in `~/.agentshield/daemon.pid` is written so
`agentshield daemon stop` can terminate it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from agentshield.engine.core import (
    AgentShieldEngine,
    EngineDecision,
    ToolEvent,
)
from agentshield.engine.monitor import SessionMonitor
from agentshield.engine.policy import PolicyEngine
from agentshield.engine.scanner import OutputScanner
from agentshield.storage.db import AuditLogger

logger = logging.getLogger(__name__)


DEFAULT_HOME = Path.home() / ".agentshield"
DEFAULT_SOCKET = DEFAULT_HOME / "agentshield.sock"
DEFAULT_POLICY = DEFAULT_HOME / "policy.yaml"
DEFAULT_DB = DEFAULT_HOME / "logs.db"
DEFAULT_ERROR_LOG = DEFAULT_HOME / "errors.log"
DEFAULT_PID_FILE = DEFAULT_HOME / "daemon.pid"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class DaemonConfig:
    socket_path: Path = DEFAULT_SOCKET
    policy_path: Path = DEFAULT_POLICY
    db_path: Path = DEFAULT_DB
    error_log_path: Path = DEFAULT_ERROR_LOG
    pid_file: Path = DEFAULT_PID_FILE
    reload_interval_seconds: float = 0.5

    def ensure_home(self) -> None:
        for p in (
            self.socket_path.parent,
            self.policy_path.parent,
            self.db_path.parent,
            self.error_log_path.parent,
            self.pid_file.parent,
        ):
            p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Error log
# ---------------------------------------------------------------------------


def _append_error(error_log_path: Path, message: str) -> None:
    try:
        error_log_path.parent.mkdir(parents=True, exist_ok=True)
        with error_log_path.open("a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        logger.exception("failed to append to error log %s", error_log_path)


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------


RequestHandler = Callable[[dict], Awaitable[dict]]


class DaemonServer:
    """Asyncio Unix-socket server wrapping AgentShieldEngine."""

    def __init__(
        self,
        config: DaemonConfig | None = None,
        engine: AgentShieldEngine | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self._config = config or DaemonConfig()
        self._config.ensure_home()

        self._policy = policy or self._build_policy(self._config.policy_path)
        self._engine = engine or AgentShieldEngine(
            policy=self._policy,
            logger_=AuditLogger(self._config.db_path),
            scanner=OutputScanner(),
            monitor=SessionMonitor(),
        )

        self._server: asyncio.base_events.Server | None = None
        self._reload_task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_policy_mtime: float | None = self._current_mtime()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind the Unix socket and start serving. Returns when the server
        has begun accepting connections."""
        sock_path = self._config.socket_path
        if sock_path.exists():
            try:
                sock_path.unlink()
            except OSError:
                logger.warning("could not remove stale socket %s", sock_path)

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(sock_path)
        )
        try:
            os.chmod(sock_path, 0o600)
        except OSError:
            pass

        self._reload_task = asyncio.create_task(self._policy_reload_loop())
        self._write_pid_file()

    async def serve_forever(self) -> None:
        """Block until `stop()` is called (or a SIGTERM/SIGINT is received)."""
        assert self._server is not None, "start() must be called first"
        async with self._server:
            await self._stopping.wait()

    async def stop(self) -> None:
        """Close the server, cancel the reload task, and remove the PID file."""
        self._stopping.set()
        if self._reload_task is not None:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
        try:
            if self._config.socket_path.exists():
                self._config.socket_path.unlink()
        except OSError:
            pass
        try:
            if self._config.pid_file.exists():
                self._config.pid_file.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Client handling
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw:
                return
            try:
                request = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                response = {"error": f"invalid json: {e}"}
                _append_error(
                    self._config.error_log_path,
                    f"bad_request json_decode: {e}",
                )
            else:
                response = await self._dispatch(request)
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception as e:  # pragma: no cover — defence in depth
            logger.exception("daemon client handler crashed")
            _append_error(
                self._config.error_log_path, f"client_handler_crash: {e}"
            )
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, request: dict) -> dict:
        kind = request.get("kind")
        if kind == "pre_tool":
            return self._handle_pre_tool(request)
        if kind == "post_tool":
            return self._handle_post_tool(request)
        if kind == "ping":
            return {"ok": True}
        return {"error": f"unknown request kind: {kind!r}"}

    def _handle_pre_tool(self, request: dict) -> dict:
        try:
            event = ToolEvent.from_dict(request["event"])
        except Exception as e:
            _append_error(
                self._config.error_log_path,
                f"pre_tool_bad_event: {e}",
            )
            return EngineDecision.allow().to_dict()
        try:
            decision = self._engine.process(event)
        except Exception as e:
            _append_error(
                self._config.error_log_path, f"pre_tool_engine_crash: {e}"
            )
            return EngineDecision.allow().to_dict()
        return decision.to_dict()

    def _handle_post_tool(self, request: dict) -> dict:
        try:
            event = ToolEvent.from_dict(request["event"])
            output = request.get("output", "")
        except Exception as e:
            _append_error(
                self._config.error_log_path, f"post_tool_bad_event: {e}"
            )
            return {"findings": []}
        try:
            findings = self._engine.process_post_tool(event, output)
        except Exception as e:
            _append_error(
                self._config.error_log_path, f"post_tool_scan_crash: {e}"
            )
            findings = []
        return {"findings": findings}

    # ------------------------------------------------------------------
    # Policy hot-reload
    # ------------------------------------------------------------------

    async def _policy_reload_loop(self) -> None:
        interval = self._config.reload_interval_seconds
        while not self._stopping.is_set():
            try:
                await asyncio.sleep(interval)
                mtime = self._current_mtime()
                if mtime is None:
                    continue
                if self._last_policy_mtime != mtime:
                    self._last_policy_mtime = mtime
                    try:
                        self._policy.reload_from_path(self._config.policy_path)
                        logger.info(
                            "policy reloaded from %s", self._config.policy_path
                        )
                    except Exception as e:
                        _append_error(
                            self._config.error_log_path,
                            f"policy_reload_failed: {e}",
                        )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover
                _append_error(
                    self._config.error_log_path, f"reload_loop_error: {e}"
                )

    def _current_mtime(self) -> float | None:
        try:
            return self._config.policy_path.stat().st_mtime
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_policy(self, policy_path: Path) -> PolicyEngine:
        """Construct a PolicyEngine from the given path, falling back to an
        empty rule set if the file is missing."""
        if policy_path.exists():
            try:
                return PolicyEngine.from_path(policy_path)
            except Exception as e:
                _append_error(
                    self._config.error_log_path,
                    f"policy_initial_load_failed: {e}",
                )
        return PolicyEngine(rules=())

    def _write_pid_file(self) -> None:
        try:
            self._config.pid_file.write_text(str(os.getpid()))
        except OSError:
            logger.warning("could not write PID file %s", self._config.pid_file)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(config: DaemonConfig | None = None) -> None:
    """Run the daemon until SIGTERM/SIGINT."""
    server = DaemonServer(config=config)

    loop = asyncio.get_running_loop()

    def _signal_stop() -> None:
        asyncio.create_task(server.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_stop)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    await server.start()
    await server.serve_forever()


def main() -> None:  # pragma: no cover — exercised via CLI
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
