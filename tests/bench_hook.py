"""End-to-end hook → daemon round-trip benchmark.

This script is **not** a pytest test — it's run manually (or from CI) to
validate the Week 1 exit gate:

    p95 < 20ms, p99 < 40ms on a warm daemon

It spins up a real DaemonServer on a temp Unix socket in a background
thread (so blocking socket/subprocess calls on the main thread don't
starve the daemon's asyncio loop), then measures two things:

  1. Pure daemon round-trip — what Claude Code observes *after* the hook
     process is already running. This is the R1 gate (p95 < 20ms,
     p99 < 40ms).

  2. End-to-end subprocess — what Claude Code observes including Python
     interpreter startup. Reported for transparency; not part of R1.

Exit code:
    0 — R1 thresholds met
    1 — R1 threshold missed

Usage:
    python tests/bench_hook.py            # default: 500 iterations
    python tests/bench_hook.py --n 2000   # custom iteration count
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket as _socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentshield.daemon.server import DaemonConfig, DaemonServer  # noqa: E402
from agentshield.policy.defaults import write_default_policy  # noqa: E402

PRE_TOOL = REPO_ROOT / "agentshield" / "adapters" / "claude_code" / "pre_tool.py"

# R1 latency gate — per the plan, p95 < 20ms, p99 < 40ms on warm daemon.
R1_P95_LIMIT_MS = 20.0
R1_P99_LIMIT_MS = 40.0


HOOK_INPUT = {
    "tool_name": "bash",
    "tool_input": {"command": "ls"},
    "session_id": "bench",
    "agent_id": "main",
    "agent_type": "main",
}


# ---------------------------------------------------------------------------
# Daemon thread wrapper
# ---------------------------------------------------------------------------


class DaemonThread:
    """Run a DaemonServer on its own asyncio loop in a background thread.

    Decoupling the loop from the main thread is essential: the bench makes
    blocking socket.recv() and subprocess.run() calls that would otherwise
    starve the daemon's loop.
    """

    def __init__(self, config: DaemonConfig) -> None:
        self._config = config
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: DaemonServer | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("daemon thread failed to start within 5s")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._server = DaemonServer(config=self._config)
        loop.run_until_complete(self._server.start())
        self._ready.set()
        try:
            loop.run_until_complete(self._server.serve_forever())
        finally:
            try:
                loop.run_until_complete(self._server.stop())
            except Exception:
                pass
            loop.close()

    def stop(self) -> None:
        if self._server is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._server._stopping.set)
        if self._thread is not None:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Measurement primitives
# ---------------------------------------------------------------------------


def _socket_rt_ms(socket_path: Path, payload: str) -> float:
    t0 = time.perf_counter()
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        s.connect(str(socket_path))
        s.sendall((payload + "\n").encode("utf-8"))
        buf = bytearray()
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in chunk:
                break
    finally:
        s.close()
    return (time.perf_counter() - t0) * 1000.0


def _percentiles(samples_ms: list[float]) -> tuple[float, float, float]:
    s = sorted(samples_ms)
    p50 = statistics.median(s)
    p95 = s[int(0.95 * len(s))]
    p99 = s[int(0.99 * len(s))]
    return p50, p95, p99


def _report(name: str, samples_ms: list[float]) -> tuple[float, float, float]:
    p50, p95, p99 = _percentiles(samples_ms)
    print(f"[{name}]")
    print(f"  n   = {len(samples_ms)}")
    print(f"  p50 = {p50:7.2f} ms")
    print(f"  p95 = {p95:7.2f} ms")
    print(f"  p99 = {p99:7.2f} ms")
    return p50, p95, p99


# ---------------------------------------------------------------------------
# Main bench
# ---------------------------------------------------------------------------


def run_bench(
    n: int,
    spawn_p95_limit_ms: float,
    spawn_p99_limit_ms: float,
    skip_subprocess: bool,
) -> int:
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        config = DaemonConfig(
            socket_path=home / "agentshield.sock",
            policy_path=home / "policy.yaml",
            db_path=home / "logs.db",
            error_log_path=home / "errors.log",
            pid_file=home / "daemon.pid",
            reload_interval_seconds=5.0,
        )
        write_default_policy(config.policy_path)

        daemon = DaemonThread(config)
        daemon.start()

        env = os.environ.copy()
        env["AGENTSHIELD_HOME"] = str(home)
        env["AGENTSHIELD_SOCKET"] = str(config.socket_path)
        hook_payload = json.dumps(HOOK_INPUT)
        socket_payload = json.dumps({"kind": "pre_tool", "event": HOOK_INPUT})

        socket_samples: list[float] = []
        spawn_samples: list[float] = []

        try:
            # --- 1. Pure daemon round-trip (R1 gate) ---
            for _ in range(50):
                _socket_rt_ms(config.socket_path, socket_payload)
            for _ in range(max(n, 500)):
                socket_samples.append(
                    _socket_rt_ms(config.socket_path, socket_payload)
                )

            # --- 2. End-to-end via subprocess (informational) ---
            if not skip_subprocess:
                for _ in range(10):
                    subprocess.run(
                        [sys.executable, str(PRE_TOOL)],
                        input=hook_payload,
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=5,
                    )
                for _ in range(n):
                    t0 = time.perf_counter()
                    subprocess.run(
                        [sys.executable, str(PRE_TOOL)],
                        input=hook_payload,
                        capture_output=True,
                        text=True,
                        env=env,
                        timeout=5,
                    )
                    spawn_samples.append((time.perf_counter() - t0) * 1000.0)
        finally:
            daemon.stop()

    print("=" * 60)
    print("AgentShield hook → daemon → decision benchmark")
    print("=" * 60)
    _, r1_p95, r1_p99 = _report("daemon round-trip (R1 gate)", socket_samples)
    print(f"  target p95 < {R1_P95_LIMIT_MS:.0f} ms, p99 < {R1_P99_LIMIT_MS:.0f} ms")
    print()
    if spawn_samples:
        _, sp_p95, sp_p99 = _report(
            "subprocess end-to-end (informational)", spawn_samples
        )
        print(
            f"  target p95 < {spawn_p95_limit_ms:.0f} ms, "
            f"p99 < {spawn_p99_limit_ms:.0f} ms"
        )
        print(
            "  NOTE: subprocess numbers include Python interpreter startup "
            "(30–100ms on\n  most machines) and are not part of the R1 budget."
        )
        print()
    else:
        sp_p95 = sp_p99 = 0.0

    ok = True
    if r1_p95 >= R1_P95_LIMIT_MS:
        print(f"FAIL: R1 p95 {r1_p95:.2f} >= {R1_P95_LIMIT_MS:.0f}")
        ok = False
    if r1_p99 >= R1_P99_LIMIT_MS:
        print(f"FAIL: R1 p99 {r1_p99:.2f} >= {R1_P99_LIMIT_MS:.0f}")
        ok = False
    if spawn_samples:
        if sp_p95 >= spawn_p95_limit_ms:
            print(f"WARN: spawn p95 {sp_p95:.2f} >= {spawn_p95_limit_ms:.0f}")
        if sp_p99 >= spawn_p99_limit_ms:
            print(f"WARN: spawn p99 {sp_p99:.2f} >= {spawn_p99_limit_ms:.0f}")
    if ok:
        print("PASS: R1 latency gate met")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument(
        "--spawn-p95",
        type=float,
        default=250.0,
        help="informational spawn p95 ceiling",
    )
    parser.add_argument(
        "--spawn-p99",
        type=float,
        default=400.0,
        help="informational spawn p99 ceiling",
    )
    parser.add_argument(
        "--skip-subprocess",
        action="store_true",
        help="skip the subprocess end-to-end pass (R1 gate only)",
    )
    args = parser.parse_args()
    return run_bench(args.n, args.spawn_p95, args.spawn_p99, args.skip_subprocess)


if __name__ == "__main__":
    sys.exit(main())
