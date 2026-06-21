"""
heropen.install — auto MCP config injection.

Scans user's machine for agent config files (Claude, Cursor, etc.)
and injects heropen MCP server configuration automatically.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

_HOME = Path.home()


def _read_json_safe(path: Path) -> Optional[dict]:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _write_json_safe(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


HEOPEN_MCP_CONFIG = {
    "heropen": {
        "command": "heropen",
        "args": ["mcp"],
    }
}


# ─── Scanner: find config files ──────────────────────────


def _find_claude_configs() -> list[Path]:
    """Find Claude config files on this machine."""
    candidates = []
    if sys.platform == "darwin":
        candidates.extend([
            _HOME / "Library" / "Application Support" / "Claude" / "claude.json",
            _HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        ])
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "Claude" / "claude.json")
    # Linux / cross-platform
    candidates.append(_HOME / ".claude.json")
    return [p for p in candidates if p.exists()]


def _find_cursor_configs() -> list[Path]:
    """Find Cursor MCP config files."""
    candidates = [_HOME / ".cursor" / "mcp.json"]
    return [p for p in candidates if p.exists()]


def _find_windsurf_configs() -> list[Path]:
    """Find Windsurf MCP config files."""
    candidates = [_HOME / ".codeium" / "windsurf" / "mcp.json"]
    return [p for p in candidates if p.exists()]


def _find_vscode_cline_configs() -> list[Path]:
    """Find VS Code Cline settings."""
    candidates = []
    if sys.platform == "darwin":
        base = _HOME / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", ""))
    else:
        base = _HOME / ".config"
    for pattern in ["Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                      "Code - Insiders/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"]:
        p = base / pattern
        if p.exists():
            candidates.append(p)
    return candidates


def _find_openclaw_configs() -> list[Path]:
    """Find OpenClaw config files."""
    candidates = [
        _HOME / ".openclaw" / "openclaw.json",
        _HOME / ".config" / "openclaw" / "openclaw.json",
    ]
    return [p for p in candidates if p.exists()]


def _find_generic_mcp_configs() -> list[Path]:
    """Scan ~/.config/ for any mcp.json files not already covered."""
    configs = []
    config_dir = _HOME / ".config"
    if config_dir.is_dir():
        for mcp_path in config_dir.rglob("mcp.json"):
            # Skip ones we already handle explicitly
            skip_parents = {"cursor", "windsurf", "openclaw"}
            if mcp_path.parent.name.lower() not in skip_parents:
                configs.append(mcp_path)
    return configs


# ─── Injector ────────────────────────────────────────────


def _inject_into_config(path: Path, label: str) -> str:
    """
    Inject heropen MCP config into a JSON config file.
    Returns a status string: 'added', 'exists', 'skipped', 'error'.
    """
    cfg = _read_json_safe(path)
    if cfg is None:
        return "error"

    servers = cfg.get("mcpServers", cfg.get("mcp_servers", {}))
    if not isinstance(servers, dict):
        cfg["mcpServers"] = HEOPEN_MCP_CONFIG
        ok = _write_json_safe(path, cfg)
        return "added" if ok else "error"

    # Already has heropen? Don't overwrite
    for key in servers:
        if "hero" in key.lower():
            return "exists"

    # Inject
    servers["heropen"] = HEOPEN_MCP_CONFIG["heropen"]
    cfg["mcpServers"] = servers
    ok = _write_json_safe(path, cfg)
    return "added" if ok else "error"


# ─── Main entry point ────────────────────────────────────


def auto_setup_mcp(agent: str = "agent") -> dict:
    """
    Scan for agent config files and inject heropen MCP.
    Returns a dict with summary of what happened.
    """
    scanners = [
        ("Claude", _find_claude_configs),
        ("Cursor", _find_cursor_configs),
        ("Windsurf", _find_windsurf_configs),
        ("VS Code Cline", _find_vscode_cline_configs),
        ("OpenClaw", _find_openclaw_configs),
        ("Other MCP", _find_generic_mcp_configs),
    ]

    result = {"configured": [], "already_had": [], "errors": []}

    for label, scanner in scanners:
        try:
            paths = scanner()
        except Exception:
            continue
        for p in paths:
            try:
                status = _inject_into_config(p, label)
                if status == "added":
                    result["configured"].append(f"{label} ({p})")
                elif status == "exists":
                    result["already_had"].append(f"{label} ({p})")
                elif status == "error":
                    result["errors"].append(f"{label} ({p})")
            except Exception:
                result["errors"].append(f"{label} ({p})")

    return result


def print_setup_summary(result: dict) -> None:
    """Print a human-readable summary of the auto-setup."""
    total = len(result["configured"]) + len(result["already_had"]) + len(result["errors"])
    if total == 0:
        print("⚠️  未检测到支持的 agent 配置文件。")
        print("   手动添加 MCP 配置：https://ksmn.cc/heropen/docs")
        return

    if result["configured"]:
        print(f"✅ 已自动配置 {len(result['configured'])} 个 agent：")
        for item in result["configured"]:
            print(f"   + {item}")

    if result["already_had"]:
        print(f"   {len(result['already_had'])} 个已配置 heropen，跳过：")
        for item in result["already_had"]:
            print(f"   · {item}")

    if result["errors"]:
        print(f"⚠️  {len(result['errors'])} 个配置失败：")
        for item in result["errors"]:
            print(f"   ✗ {item}")

    if result["configured"] or result["already_had"]:
        print("\n💡 重启你的 agent，它就有记忆了。")
    if not result["configured"] and not result["already_had"]:
        print("\n   或手动添加：https://ksmn.cc/heropen/docs")
