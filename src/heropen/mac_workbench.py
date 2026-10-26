"""macOS desktop workbench: real local statistics and read-only assistant discovery."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from heropen import __version__
from heropen.core import HERO_PEN_DIR, get_agent_limit, get_storage_mode
from heropen.connection_status import identify_client, read_status

DATA_DIR = Path(HERO_PEN_DIR).expanduser().absolute()
STATE_FILE = DATA_DIR / "_tray_state.json"
MODES = ("auto", "ten_turns", "every_turn")


def _database_stats(path: Path) -> dict:
    try:
        connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'").fetchone():
                return {"name": path.stem, "path": str(path), "error": "缺少 entries 表"}
            count, earliest, latest = connection.execute(
                "SELECT COUNT(*), MIN(entry_date), MAX(entry_date) FROM entries"
            ).fetchone()
            since = (date.today() - timedelta(days=6)).isoformat()
            rows = connection.execute(
                "SELECT substr(entry_date,1,10), COUNT(*) FROM entries "
                "WHERE substr(entry_date,1,10) >= ? GROUP BY substr(entry_date,1,10)", (since,)
            ).fetchall()
            return {"name": path.stem, "path": str(path), "count": int(count), "first": earliest, "last": latest,
                    "week": {str(day): int(n) for day, n in rows}, "mode": get_storage_mode(path.stem),
                    "turns": _turn_status(path.stem)}
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        return {"name": path.stem, "path": str(path), "error": str(exc)}


def _turn_status(agent: str) -> dict:
    from heropen.turn_storage import status
    try:
        return status(agent)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {"error": str(exc), "received_turns": 0, "pending_turns": 0,
                "last_received_at": None}


def _client_config(path: Path, *, is_yaml: bool = False,
                   server_keys: tuple[str, ...] | None = None,
                   allow_named_list: bool = False) -> dict:
    # 仅返回发现状态、配置来源及两个白名单环境变量，不暴露其他配置。
    result = {"config_found": None, "configured": None, "binding": None,
              "data_directory": None, "connection_status": "not_observed", "note": "",
              "config_source": str(path), "config_exists": None}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        result.update(config_exists=False, config_found=False, configured=False,
                      note="未发现配置文件；调用观测独立展示")
        return result
    except (OSError, UnicodeError):
        result["note"] = "配置不可读取，未解析；接入未知"
        return result
    result["config_exists"] = True
    if is_yaml and yaml is None:
        result["note"] = "发现配置文件，但未安装 PyYAML，未解析；接入未知"
        return result
    try:
        config = yaml.safe_load(text) if is_yaml else json.loads(text)
    except (ValueError, yaml.YAMLError if yaml is not None else ValueError):
        result["note"] = "发现配置文件，但解析失败；接入未知"
        return result
    if not isinstance(config, dict):
        result["note"] = "发现配置文件，但结构无法识别；接入未知"
        return result
    servers = config
    for key in server_keys or (("mcp_servers",) if is_yaml else ("mcpServers",)):
        if not isinstance(servers, dict):
            result["note"] = "发现配置文件，但 MCP 配置结构无法识别；接入未知"
            return result
        servers = servers.get(key, {})
    if allow_named_list and isinstance(servers, list):
        named_servers = {}
        for server in servers:
            name = server.get("name") if isinstance(server, dict) else None
            if not isinstance(name, str) or not name.strip() or name in named_servers:
                result["note"] = "发现配置文件，但 MCP 列表缺少有效的显式 name 或名称重复；接入未知"
                return result
            named_servers[name] = server
        servers = named_servers
    if not isinstance(servers, dict):
        result["note"] = "发现配置文件，但 MCP 配置结构无法识别；接入未知"
        return result
    found = "heropen" in servers
    if found and not isinstance(servers["heropen"], dict):
        result["note"] = "发现 heropen 配置项，但结构无法识别；接入未知"
        return result
    # configured 保留兼容字段，与 config_found 同义；None 表示无法判断。
    result.update(config_found=found, configured=found)
    if not found:
        result["note"] = "已解析配置文件，未发现 heropen 配置；调用观测独立展示"
        return result
    result["note"] = "发现 heropen 配置；仅配置不能证明已接入或自动存档已接通"
    server = servers["heropen"]
    env = server.get("env", {})
    if isinstance(env, dict):
        for field, key in (("binding", "HEROPEN_AGENT"), ("data_directory", "HERO_PEN_DIR")):
            value = env.get(key)
            if isinstance(value, str) and value.strip():
                result[field] = value
    return result


def assistants(home: Path | str | None = None) -> list[dict]:
    home = Path.home() if home is None else Path(home)
    zcode = _client_config(home / ".zcode/cli/config.json",
                           server_keys=("mcp", "servers"), allow_named_list=True)
    # 仅主配置文件不存在时回退，避免用共享配置掩盖主配置的解析错误。
    if zcode["config_exists"] is False:
        zcode = _client_config(home / ".agents/mcp.json", allow_named_list=True)
        zcode["note"] = "ZCode 用户配置不存在，回退检查 agents 共享配置；" + zcode["note"]
    clients = [
        {"name": "WorkBuddy", "installed": (Path("/Applications/WorkBuddy.app").exists()
          or (home / "Applications/WorkBuddy.app").exists() or (home / ".workbuddy").exists()),
         **_client_config(home / ".workbuddy/mcp.json")},
        {"name": "Hermes", "installed": ((home / ".hermes").exists()
          or (home / ".local/bin/hermes").exists()),
         **_client_config(home / ".hermes/config.yaml", is_yaml=True)},
        {"name": "ZCode", "installed": (Path("/Applications/ZCode.app").exists()
          or (home / "Applications/ZCode.app").exists() or (home / ".zcode").exists()),
         **zcode},
    ]
    for client in clients:
        configured_directory = client["data_directory"]
        directory = DATA_DIR
        if configured_directory:
            if configured_directory == "~" or configured_directory.startswith("~/"):
                directory = home / configured_directory[2:] if configured_directory != "~" else home
            else:
                directory = Path(configured_directory).expanduser()
                if not directory.is_absolute():
                    directory = home / directory
        directory = directory.absolute()
        agent = client["binding"] or client["name"].lower()
        observation = read_status(agent, directory)
        # 共享绑定库中的另一个客户端调用，不能当作当前客户端的验证。
        identified = identify_client(observation.get("client_name"))
        if identified and identified != client["name"].lower():
            observation = {**observation, "status": "observed_mismatch", "recent_verified": False}
        client.update(connection_status=observation["status"], connection=observation,
                      status_data_directory=str(directory))
    return clients


def storage_settings(agents: list[dict], clients: list[dict]) -> list[dict]:
    """Per-agent storage mode table: one independent row per agent.

    Includes clients that are bound to an agent name but have no database yet,
    so each client can be configured separately before it writes anything.
    Takes already-collected data to avoid recursive dashboard() calls.
    """
    databases = {a["name"]: a for a in agents}
    rows = []
    for client in clients:
        binding = client.get("binding")
        if not (isinstance(binding, str) and binding):
            rows.append({"agent": None, "client": client["name"], "mode": None,
                         "bindable": False, "has_database": False,
                         "note": f"{client['name']} 未声明绑定 Agent，无法单独设置"})
            continue
        rows.append({"agent": binding, "client": client["name"],
                     "mode": get_storage_mode(binding),
                     "bindable": True, "has_database": binding in databases,
                     "note": ""})
    # Databases without a matching client still get their own independent row.
    bound = {r["agent"] for r in rows if r["agent"]}
    for name in sorted(databases):
        if name not in bound:
            rows.append({"agent": name, "client": None,
                         "mode": get_storage_mode(name), "bindable": True,
                         "has_database": True, "note": "未对应已发现客户端"})
    return rows


def _ingest_view() -> dict:
    """Auto-ingest settings plus the last run, for the workbench panel.

    Read straight from the scheduler's state/result files; this must stay
    read-only and must never fail the dashboard if the module is unavailable.
    """
    try:
        from heropen.turn_ingest_scheduler import read_result, running, settings
        current = settings()
        last = read_result()
        return {
            "available": True,
            "enabled": current["enabled"],
            "apply": current["apply"],
            "interval_seconds": current["interval_seconds"],
            "source": current["source"],
            "running": running(),
            "last_ok": last.get("ok"),
            "last_status": last.get("status"),
            "last_at": last.get("finished_at") or last.get("started_at"),
            "last_success_at": last.get("last_success_at"),
            "last_error": last.get("error") or "",
            "last_failed_sources": last.get("failed_sources") or [],
            "last_problem_sources": last.get("problem_sources") or [],
            "last_conflicts": last.get("conflicts"),
            "last_failed": last.get("failed"),
            "last_submitted": last.get("submitted"),
            "last_saved": last.get("saved"),
            "last_duplicate": last.get("duplicate"),
            "last_considered": last.get("considered"),
            "last_already_stored": last.get("already_stored"),
            "last_deferred": last.get("deferred"),
            "last_apply": last.get("apply"),
        }
    except Exception:  # noqa: BLE001 - the panel must still render
        return {"available": False,
                "error": "摄取状态不可用"}


def dashboard() -> dict:
    agents, errors = [], []
    try:
        # scandir 会报告无权限/目录不存在；glob 在部分 Python 版本会吞掉这些错误。
        with os.scandir(DATA_DIR) as entries:
            databases = sorted(DATA_DIR / entry.name for entry in entries
                               if entry.name.endswith(".db") and not entry.name.startswith("._"))
        for db in databases:
            result = _database_stats(db)
            (errors if "error" in result else agents).append(result)
    except OSError as exc:
        errors.append({"path": str(DATA_DIR), "error": str(exc)})
    named = [a for a in agents if not a["name"].startswith("_")]
    all_dates = []
    for agent in agents:
        if agent["first"]:
            try:
                all_dates.append(date.fromisoformat(agent["first"][:10]))
            except (TypeError, ValueError):
                errors.append({"path": agent["path"], "error": "最早记录日期无效"})
    oldest = min(all_dates) if all_dates else None
    days = max(0, (date.today() - oldest).days + 1) if oldest else 0
    clients = assistants()
    return {"data_directory": str(DATA_DIR), "errors": errors, "read_ok": not errors,
            "version": __version__, "free_limit": get_agent_limit(), "agent_count": len(named),
            "database_count": len(agents), "entries": sum(a["count"] for a in agents),
            "days": days, "first": oldest.isoformat() if oldest else None, "agents": agents,
            "assistants": clients,
            "storage_settings": storage_settings(agents, clients),
            "ingest": _ingest_view(),
            "language": _read_state().get("lang", "zh")}


def _read_state() -> dict:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _known_agents() -> set[str]:
    """Agents the workbench may configure: databases plus bound clients."""
    names = {a["name"] for a in dashboard()["agents"]}
    for client in assistants():
        binding = client.get("binding")
        if isinstance(binding, str) and binding:
            names.add(binding)
    return names


def set_mode(agent: str, mode: str) -> bool:
    """Set one agent's storage mode; never touches any other agent.

    Allowed agents are existing memory databases plus clients that are already
    bound to an agent name, so a client can be configured before its database
    exists. A legacy global "default_mode" is removed so no agent inherits it.
    """
    if mode not in MODES or agent not in _known_agents():
        return False
    state = _read_state()
    modes = state.get("modes")
    if not isinstance(modes, dict):
        modes = {}
    modes[agent] = mode
    state["modes"] = modes
    # Drop the legacy global lever: it applied one agent's choice to all others.
    state.pop("default_mode", None)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, STATE_FILE)
    return True
