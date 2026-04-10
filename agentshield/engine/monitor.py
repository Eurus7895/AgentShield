"""Session monitor — loop / rapid-call detection.

Conforms to SessionMonitorProtocol from agentshield.engine.core.

Threshold defaults (override via constructor):
  * loop_threshold=30 calls of the same tool
  * loop_window_seconds=10  → 30 calls in 10s = block
  * soft_session_cap=200    → total calls per session; logged, not blocked

Thread-safe: a single SessionMonitor instance is shared across daemon threads.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Deque

from agentshield.engine.core import EngineDecision, ToolEvent

logger = logging.getLogger(__name__)


class SessionMonitor:
    """Sliding-window per-session call-count tracker."""

    def __init__(
        self,
        loop_threshold: int = 30,
        loop_window_seconds: float = 10.0,
        soft_session_cap: int = 200,
    ) -> None:
        self._loop_threshold = loop_threshold
        self._loop_window = loop_window_seconds
        self._soft_cap = soft_session_cap

        # (session_id, tool_name) -> deque of timestamps (monotonic seconds)
        self._tool_windows: dict[tuple[str, str], Deque[float]] = defaultdict(deque)
        # session_id -> total call count (for soft cap warning)
        self._session_totals: dict[str, int] = defaultdict(int)

        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # SessionMonitorProtocol
    # ------------------------------------------------------------------

    def check(self, event: ToolEvent) -> EngineDecision | None:
        """Return a block EngineDecision if the session is looping; else None."""
        key = (event.session_id, event.tool_name)
        now = time.monotonic()

        with self._lock:
            window = self._tool_windows[key]
            window.append(now)
            self._prune(window, now)

            self._session_totals[event.session_id] += 1
            total = self._session_totals[event.session_id]

            if len(window) >= self._loop_threshold:
                # Loop detected — block and reset the window for this tool.
                window.clear()
                return EngineDecision.block(
                    reason="session_loop",
                    message=(
                        f"AgentShield: loop detected — tool {event.tool_name!r} "
                        f"called {self._loop_threshold} times in "
                        f"{self._loop_window:.0f}s on session {event.session_id!r}"
                    ),
                )

            if total == self._soft_cap:
                logger.warning(
                    "session %s crossed soft cap of %d tool calls",
                    event.session_id,
                    self._soft_cap,
                )

        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close_session(self, session_id: str) -> None:
        """Forget all state for a finished session."""
        with self._lock:
            self._session_totals.pop(session_id, None)
            stale_keys = [k for k in self._tool_windows if k[0] == session_id]
            for k in stale_keys:
                self._tool_windows.pop(k, None)

    def total_for(self, session_id: str) -> int:
        with self._lock:
            return self._session_totals.get(session_id, 0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _prune(self, window: Deque[float], now: float) -> None:
        cutoff = now - self._loop_window
        while window and window[0] < cutoff:
            window.popleft()
