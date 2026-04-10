"""Output scanner — credential and imperative-language detection.

Conforms to OutputScannerProtocol from agentshield.engine.core.

Findings are kind-prefixed strings so downstream consumers can filter:
  credential:aws_key
  credential:github_token
  credential:ssh_private_key
  credential:generic_api_key
  credential:jwt
  imperative:memory_update
  imperative:instruction_override
  imperative:role_injection

The scanner *reports* findings; it does not block. Blocking is the daemon's
and adapter's job — they decide what findings warrant exit-code 2.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from agentshield.engine.core import ToolEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pattern:
    kind: str
    regex: re.Pattern[str]


def _compile(kind: str, pattern: str, flags: int = 0) -> Pattern:
    return Pattern(kind=kind, regex=re.compile(pattern, flags))


# ---------------------------------------------------------------------------
# Credential patterns
# ---------------------------------------------------------------------------

_CREDENTIAL_PATTERNS: tuple[Pattern, ...] = (
    _compile("credential:aws_key", r"AKIA[0-9A-Z]{16}"),
    _compile("credential:aws_secret", r"(?i)aws_secret_access_key\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}"),
    _compile("credential:github_token", r"gh[pousr]_[A-Za-z0-9]{36,255}"),
    _compile(
        "credential:ssh_private_key",
        r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY-----",
    ),
    _compile(
        "credential:generic_api_key",
        r"(?i)(?:api[_-]?key|apikey|api_token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}",
    ),
    _compile(
        "credential:generic_secret",
        r"(?i)(?:^|\W)(?:secret|password|passwd|pwd)\s*[:=]\s*[\"']?[A-Za-z0-9_\-@#$%^&*!+/=]{8,}",
    ),
    _compile(
        "credential:jwt",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    ),
)


# ---------------------------------------------------------------------------
# Imperative-language patterns (harness integrity — workflow witness)
#
# These catch AI-directed phrases that may be injected into tool output by
# untrusted content (web pages, email bodies, scraped docs, etc.) and are
# attempting to reprogram the agent. Hitting one is not definitive proof of
# malice — imperative language is inherently ambiguous — so these findings
# are WARNINGS, not hard blocks. The plan (R5) allows up to 5% false positive
# rate here, in exchange for catching real injection attempts.
# ---------------------------------------------------------------------------

_IMPERATIVE_PATTERNS: tuple[Pattern, ...] = (
    # Memory-update injection
    _compile("imperative:memory_update", r"(?i)\bupdate\s+your\s+memory\b"),
    _compile("imperative:memory_update", r"(?i)\bremember\s+that\b"),
    _compile("imperative:memory_update", r"(?i)\badd\s+this\s+to\s+your\s+(?:context|memory)\b"),
    _compile("imperative:memory_update", r"(?i)\bsave\s+this\s+(?:for\s+later|to\s+memory)\b"),
    _compile("imperative:memory_update", r"(?i)\bstore\s+(?:this|the\s+following)\s+in\s+(?:your\s+)?memory\b"),
    # Instruction override
    _compile("imperative:instruction_override", r"(?i)\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions\b"),
    _compile("imperative:instruction_override", r"(?i)\bdisregard\s+(?:all\s+)?(?:previous|prior)\s+instructions\b"),
    _compile("imperative:instruction_override", r"(?i)\bnew\s+instructions\s*:"),
    _compile("imperative:instruction_override", r"(?i)\bfrom\s+now\s+on\b,?\s+(?:you|always)"),
    _compile("imperative:instruction_override", r"(?i)\boverride\s+(?:your|the)\s+(?:system|default)\s+prompt\b"),
    # Role injection
    _compile("imperative:role_injection", r"(?i)\byou\s+are\s+now\s+(?:a|an|the)\b"),
    _compile("imperative:role_injection", r"(?i)\byour\s+(?:new\s+)?role\s+is\b"),
    _compile("imperative:role_injection", r"(?is)<\|?system\|?>"),
    _compile("imperative:role_injection", r"(?im)^system\s*:"),
    _compile("imperative:role_injection", r"(?i)\bact\s+as\s+(?:a|an|the)\s+\w"),
)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class OutputScanner:
    """Stateless pattern-matcher implementing OutputScannerProtocol.

    Patterns are compiled once at module import time. Scan is O(N * len(output))
    where N is the number of patterns — fast enough for sub-millisecond calls
    on typical tool output sizes.
    """

    def __init__(
        self,
        credential_patterns: tuple[Pattern, ...] = _CREDENTIAL_PATTERNS,
        imperative_patterns: tuple[Pattern, ...] = _IMPERATIVE_PATTERNS,
    ) -> None:
        self._patterns: tuple[Pattern, ...] = credential_patterns + imperative_patterns

    def scan(self, event: ToolEvent, output: str) -> list[str]:
        """Return a list of finding kinds (deduped, order-preserving).

        Empty list = nothing found. Never raises — fail-soft.
        """
        del event  # not yet used; reserved for per-tool pattern gating
        if not output:
            return []
        findings: list[str] = []
        seen: set[str] = set()
        try:
            for pattern in self._patterns:
                if pattern.regex.search(output):
                    if pattern.kind not in seen:
                        seen.add(pattern.kind)
                        findings.append(pattern.kind)
        except Exception:
            logger.exception("OutputScanner.scan regex failure; returning partial")
        return findings

    # ------------------------------------------------------------------
    # Introspection (used by tests + debug)
    # ------------------------------------------------------------------

    @property
    def pattern_count(self) -> int:
        return len(self._patterns)

    def kinds(self) -> list[str]:
        return sorted({p.kind for p in self._patterns})
