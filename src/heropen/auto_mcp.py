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


# STDIO config (default for most agents)
heropen_stdio_config = {
    "command": "heropen-mcp",
    "args": [],
}

# SSE config (for WorkBuddy / CodeBuddy — needs background HTTP service)
heropen_sse_config = {
    "type": "sse",
    "url": "http://127.0.0.1:8090/sse",
}


# ─── Windows scheduled task ──────────────────────────────
# ⚠️ 后台常驻服务不得出现控制台窗口。schtasks 若直接运行 heropen-mcp.exe
# （控制台程序），登录时会弹出可见黑窗（≤2.0.5 的缺陷）。因此统一改由
# wscript.exe 执行一个隐藏启动器 VBS（窗口样式 0 = 隐藏）来拉起服务。
def _hidden_launcher_path() -> Path:
    """Path of the VBS launcher used to start heropen-mcp --http hidden."""
    base = Path(os.environ.get("LOCALAPPDATA", str(_HOME))) / "heropen"
    return base / "heropen-mcp-hidden.vbs"


def _write_hidden_launcher(exe_path: str) -> str | None:
    """Write a VBS that launches heropen-mcp --http with a hidden window.

    Returns the launcher path on success, else None.
    """
    p = _hidden_launcher_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # WScript.Shell.Run(cmd, intWindowStyle, bWaitOnReturn): 0 = 隐藏窗口
        content = (
            'CreateObject("WScript.Shell").Run '
            '"""%s"" --http", 0, False\r\n' % exe_path
        )
        p.write_text(content, encoding="utf-8")
        return str(p)
    except OSError:
        return None


def _register_windows_scheduled_task() -> bool:
    """Register a Windows scheduled task to auto-start heropen-mcp --http at logon.

    The task runs ``wscript.exe <hidden launcher.vbs>`` so no console window
    appears. Legacy tasks that invoked the .exe directly (visible window) are
    overwritten via ``/f``.
    """
    if sys.platform != "win32":
        return True
    task_name = "heropenMCP"
    # Try to find heropen-mcp.exe
    exe_path = _find_heropen_mcp_exe()
    if not exe_path:
        return False
    launcher = _write_hidden_launcher(exe_path)
    if not launcher:
        return False
    # Up-to-date (already runs via wscript) → nothing to do.
    if _schtasks_query(task_name) and _schtasks_uses_hidden_launcher(task_name):
        _cleanup_legacy_autostart()
        return True
    # Create or overwrite (migrates legacy visible-task to hidden launcher).
    ok = _schtasks_create(task_name, launcher)
    if ok:
        _cleanup_legacy_autostart()
    return ok


def _schtasks_query(task_name: str) -> bool:
    import subprocess
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "LIST"],
            capture_output=True, text=True, encoding="gbk", timeout=10,
        )
    except (UnicodeDecodeError, LookupError):
        # Fallback for non-Windows encoding or unexpected locale
        r = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "LIST"],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
    return r.returncode == 0


def _schtasks_uses_hidden_launcher(task_name: str) -> bool:
    """True if the task already runs via wscript + a .vbs launcher (hidden)."""
    import subprocess
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "LIST", "/v"],
            capture_output=True, text=True, encoding="gbk",
            errors="replace", timeout=10,
        )
    except Exception:
        return False
    if r.returncode != 0:
        return False
    out = (r.stdout or "").lower()
    # 命令行里同时出现 wscript 与 .vbs 即视为已走隐藏启动
    return "wscript" in out and ".vbs" in out


def _schtasks_create(task_name: str, launcher_path: str) -> bool:
    """Create/overwrite the logon task to run the hidden VBS launcher."""
    import subprocess
    tr = f'wscript.exe "{launcher_path}"'
    try:
        r = subprocess.run(
            ["schtasks", "/create", "/tn", task_name,
             "/tr", tr,
             "/sc", "onlogon", "/rl", "highest", "/f"],
            capture_output=True, text=True, encoding="gbk", timeout=10,
        )
    except (UnicodeDecodeError, LookupError):
        r = subprocess.run(
            ["schtasks", "/create", "/tn", task_name,
             "/tr", tr,
             "/sc", "onlogon", "/rl", "highest", "/f"],
            capture_output=True, text=True, errors="replace", timeout=10,
        )
    return r.returncode == 0


def _cleanup_legacy_autostart() -> None:
    """Remove legacy Startup-folder launchers that caused duplicate/visible starts."""
    if sys.platform != "win32":
        return
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    startup = (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
        / "Programs" / "Startup"
    )
    for name in ("HeroPenMCP.vbs", "heropenMCP.vbs"):
        try:
            f = startup / name
            if f.exists():
                f.unlink()
        except OSError:
            pass


def _find_heropen_mcp_exe() -> str | None:
    """Find heropen-mcp.exe on the system."""
    candidates = []
    # Common Python Scripts locations
    local = os.environ.get("LOCALAPPDATA", "")
    appdata = os.environ.get("APPDATA", "")
    base_dirs = []
    if local:
        base_dirs.append(Path(local) / "Programs" / "Python")
    if appdata:
        base_dirs.append(Path(appdata) / ".." / "Local" / "Programs" / "Python")
    for base in base_dirs:
        if base.is_dir():
            for py_dir in base.iterdir():
                scripts = py_dir / "Scripts" / "heropen-mcp.exe"
                if scripts.exists():
                    candidates.append(str(scripts))
    if not candidates:
        # Try PATH
        import shutil
        found = shutil.which("heropen-mcp.exe") or shutil.which("heropen-mcp")
        if found:
            candidates.append(found)
    return candidates[0] if candidates else None


# ─── Detect already-running heropen-mcp --http ────────────
def _is_sse_server_running(port: int = 8090) -> bool:
    """Check if heropen-mcp --http is already listening on the given port."""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/sse", method="HEAD")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


def _start_sse_server_background() -> bool:
    """Start heropen-mcp --http as a background process."""
    exe_path = _find_heropen_mcp_exe()
    if not exe_path:
        return False
    try:
        import subprocess
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(
            [exe_path, "--http"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except Exception:
        return False


# ─── Scanner: find config files ──────────────────────────
def _find_claude_configs() -> list[Path]:
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
    candidates.append(_HOME / ".claude.json")
    return [p for p in candidates if p.exists()]


def _find_cursor_configs() -> list[Path]:
    candidates = [_HOME / ".cursor" / "mcp.json"]
    return [p for p in candidates if p.exists()]


def _find_windsurf_configs() -> list[Path]:
    candidates = [_HOME / ".codeium" / "windsurf" / "mcp.json"]
    return [p for p in candidates if p.exists()]


def _find_vscode_cline_configs() -> list[Path]:
    candidates = []
    if sys.platform == "darwin":
        base = _HOME / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", ""))
    else:
        base = _HOME / ".config"
    for pattern in [
        "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
        "Code - Insiders/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
    ]:
        p = base / pattern
        if p.exists():
            candidates.append(p)
    return candidates


def _find_openclaw_configs() -> list[Path]:
    candidates = [
        _HOME / ".openclaw" / "openclaw.json",
        _HOME / ".config" / "openclaw" / "openclaw.json",
    ]
    return [p for p in candidates if p.exists()]


def _find_workbuddy_configs() -> list[Path]:
    candidates = [
        _HOME / ".mcp.json",           # WorkBuddy default
        _HOME / ".workbuddy" / "mcp.json",  # Fallback
    ]
    return [p for p in candidates if p.exists()]


def _find_hermes_configs() -> list[Path]:
    candidates = [_HOME / ".hermes" / "config.yaml"]
    return [p for p in candidates if p.exists()]


def _find_generic_mcp_configs() -> list[Path]:
    configs = []
    config_dir = _HOME / ".config"
    if config_dir.is_dir():
        for mcp_path in config_dir.rglob("mcp.json"):
            skip_parents = {"cursor", "windsurf", "openclaw"}
            if mcp_path.parent.name.lower() not in skip_parents:
                configs.append(mcp_path)
    return configs


# ─── Injector ────────────────────────────────────────────
def _inject_into_config(path: Path, label: str, use_sse: bool = False) -> str:
    """
    Inject heropen MCP config into a JSON config file.
    Returns: 'added', 'exists', 'skipped', 'error'.
    If use_sse is True, write SSE config (for WorkBuddy/CodeBuddy).
    """
    cfg = _read_json_safe(path)
    if cfg is None:
        return "error"
    mcp_config = heropen_sse_config if use_sse else heropen_stdio_config
    config_key = "heropen"
    servers = cfg.get("mcpServers", cfg.get("mcp_servers", {}))
    if not isinstance(servers, dict):
        cfg["mcpServers"] = {config_key: mcp_config}
        ok = _write_json_safe(path, cfg)
        return "added" if ok else "error"
    for key in servers:
        if "hero" in key.lower():
            existing = servers[key]
            # Check if existing config matches expected format
            if isinstance(existing, dict):
                if use_sse:
                    # Should be SSE — check if it already is
                    if existing.get("type") == "sse":
                        return "exists"
                    # Wrong format (e.g. stdio when SSE needed), overwrite
                    servers[key] = mcp_config
                else:
                    # Should be stdio — check if it already is
                    if existing.get("command"):
                        return "exists"
                    # Wrong format (e.g. SSE when stdio needed), overwrite
                    servers[key] = mcp_config
            else:
                # Weird format, overwrite
                servers[key] = mcp_config
            cfg["mcpServers"] = servers
            ok = _write_json_safe(path, cfg)
            return "added" if ok else "error"
    servers[config_key] = mcp_config
    cfg["mcpServers"] = servers
    ok = _write_json_safe(path, cfg)
    return "added" if ok else "error"


def _inject_into_yaml_config(path: Path, label: str) -> str:
    try:
        import yaml
    except ImportError:
        return "skipped"
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception:
        return "error"
    if not isinstance(cfg, dict):
        return "error"
    mcp_config = heropen_stdio_config
    servers = cfg.get("mcpServers", {})
    if not isinstance(servers, dict):
        cfg["mcpServers"] = {"heropen": mcp_config}
    else:
        for key in servers:
            if "hero" in key.lower():
                return "exists"
        servers["heropen"] = mcp_config
        cfg["mcpServers"] = servers
    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return "added"
    except Exception:
        return "error"


# ─── Main entry point ────────────────────────────────────
def auto_setup_mcp(agent: str = "agent") -> dict:
    """Scan for agent config files and inject heropen MCP config."""
    scanners = [
        ("Claude", _find_claude_configs),
        ("Cursor", _find_cursor_configs),
        ("Windsurf", _find_windsurf_configs),
        ("VS Code Cline", _find_vscode_cline_configs),
        ("OpenClaw", _find_openclaw_configs),
        ("WorkBuddy", _find_workbuddy_configs),
        ("Hermes", _find_hermes_configs),
        ("Other MCP", _find_generic_mcp_configs),
    ]
    result = {
        "configured": [],
        "already_had": [],
        "errors": [],
        "skipped": [],
        "sse_started": False,
        "scheduler_registered": False,
    }
    for label, scanner in scanners:
        try:
            paths = scanner()
        except Exception:
            continue
        for p in paths:
            try:
                is_wb = label == "WorkBuddy"
                if p.suffix in (".yaml", ".yml"):
                    status = _inject_into_yaml_config(p, label)
                else:
                    status = _inject_into_config(p, label, use_sse=is_wb)
                if status == "added":
                    result["configured"].append(f"{label} ({p})")
                elif status == "exists":
                    result["already_had"].append(f"{label} ({p})")
                elif status == "skipped":
                    # YAML client config found but not writable without PyYAML.
                    # Must stay visible: otherwise a bare install silently skips
                    # exactly the YAML-configured clients (e.g. Hermes) and the
                    # summary would wrongly say "no config files found".
                    result["skipped"].append(f"{label} ({p})")
                elif status == "error":
                    result["errors"].append(f"{label} ({p})")
                if is_wb and status in ("added", "exists"):
                    if not _is_sse_server_running():
                        started = _start_sse_server_background()
                        result["sse_started"] = started
                    if sys.platform == "win32":
                        result["scheduler_registered"] = _register_windows_scheduled_task()
            except Exception:
                result["errors"].append(f"{label} ({p})")
    return result


def print_setup_summary(result: dict) -> None:
    """Print a human-readable summary of the auto-setup."""
    total = len(result["configured"]) + len(result["already_had"]) + len(result["errors"]) + len(result.get("skipped", []))
    if total == 0:
        print("\u26a0\ufe0f No supported agent config files found.")
        print(" Manual setup: https://heropen.net/docs/")
        return
    if result["configured"]:
        print(f"\u2705 Auto-configured {len(result['configured'])} agent(s):")
        for item in result["configured"]:
            mode = "SSE" if "WorkBuddy" in item else "stdio"
            print(f"   + {item} [{mode} mode]")
    if result["already_had"]:
        print(f"  {len(result['already_had'])} already had heropen, skipped:")
        for item in result["already_had"]:
            print(f"   \u00b7 {item}")
    if result["errors"]:
        print(f"\u26a0\ufe0f {len(result['errors'])} failed:")
        for item in result["errors"]:
            print(f"   \u2717 {item}")
    if result.get("skipped"):
        print(f"\u26a0\ufe0f {len(result['skipped'])} YAML config(s) skipped (PyYAML missing):")
        for item in result["skipped"]:
            print(f"   \u00b7 {item}")
        print("   Fix: pip install 'heropen[all]' (or 'pip install pyyaml'), then re-run heropen install")
    if result.get("sse_started"):
        print("  \U0001f680 WorkBuddy SSE server started (http://127.0.0.1:8090/sse)")
    if result.get("scheduler_registered"):
        print("  \U0001f504 Auto-start registered (will launch at next login)")
    if result["configured"] or result["already_had"]:
        print("\n\U0001f4a1 Restart your agent and it will have memory.")
    has_wb = any("WorkBuddy" in item for item in result["configured"] + result["already_had"])
    if has_wb:
        print("\n\U0001f4cc WorkBuddy uses SSE mode - the background service must stay running.")
        if not result.get("sse_started") and not _is_sse_server_running():
            print("   If connection fails, start manually:")
            print("   heropen-mcp --http")
