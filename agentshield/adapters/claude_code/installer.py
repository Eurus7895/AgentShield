"""Claude Code adapter installer.

Responsibilities:
  1. Create ~/.agentshield/ and drop a default policy.yaml.
  2. Copy the stdlib-only hook scripts into ~/.agentshield/ so Claude Code
     can invoke them by absolute path.
  3. Merge AgentShield's hook config into ~/.claude/settings.json without
     clobbering existing user hooks.
  4. Install the daemon service (systemd --user / launchd / PID-file fallback).

The installer can safely be re-run; it is idempotent.
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

from agentshield.daemon.server import DEFAULT_HOME
from agentshield.daemon.startup import install_service
from agentshield.policy.defaults import write_default_policy

logger = logging.getLogger(__name__)

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# Hook script deployment
# ---------------------------------------------------------------------------


def _adapter_dir() -> Path:
    return Path(__file__).parent


def copy_hook_scripts(home: Path = DEFAULT_HOME) -> tuple[Path, Path]:
    """Copy pre_tool.py and post_tool.py into the AgentShield home."""
    home.mkdir(parents=True, exist_ok=True)
    pre_src = _adapter_dir() / "pre_tool.py"
    post_src = _adapter_dir() / "post_tool.py"
    pre_dst = home / "pre_tool.py"
    post_dst = home / "post_tool.py"
    shutil.copyfile(pre_src, pre_dst)
    shutil.copyfile(post_src, post_dst)
    return pre_dst, post_dst


# ---------------------------------------------------------------------------
# settings.json merge
# ---------------------------------------------------------------------------


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("existing settings.json is not valid JSON; not merging")
        return {}


def _inject_hook(hooks: list, command: str) -> list:
    """Idempotently insert AgentShield's hook entry into the list of hook
    groups for a specific event (e.g. PreToolUse)."""
    if not isinstance(hooks, list):
        hooks = []

    for group in hooks:
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []) or []:
            if isinstance(h, dict) and h.get("command") == command:
                return hooks  # already installed

    hooks.append(
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": command}],
        }
    )
    return hooks


def merge_settings(
    pre_script: Path,
    post_script: Path,
    settings_path: Path = CLAUDE_SETTINGS_PATH,
) -> Path:
    """Merge AgentShield hook commands into Claude Code settings.json."""
    settings = _load_settings(settings_path)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}

    pre_cmd = f"{sys.executable} {pre_script}"
    post_cmd = f"{sys.executable} {post_script}"

    hooks["PreToolUse"] = _inject_hook(hooks.get("PreToolUse", []) or [], pre_cmd)
    hooks["PostToolUse"] = _inject_hook(hooks.get("PostToolUse", []) or [], post_cmd)
    settings["hooks"] = hooks

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return settings_path


# ---------------------------------------------------------------------------
# Top-level install
# ---------------------------------------------------------------------------


def install(
    home: Path = DEFAULT_HOME,
    settings_path: Path = CLAUDE_SETTINGS_PATH,
) -> dict:
    """Run all install steps and return a report dict."""
    home.mkdir(parents=True, exist_ok=True)
    policy_path = write_default_policy(home / "policy.yaml")
    pre_dst, post_dst = copy_hook_scripts(home)
    merged = merge_settings(pre_dst, post_dst, settings_path)
    service_path = install_service()
    return {
        "home": str(home),
        "policy": str(policy_path),
        "pre_tool": str(pre_dst),
        "post_tool": str(post_dst),
        "settings": str(merged),
        "service": str(service_path) if service_path else None,
    }
