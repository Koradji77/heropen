"""
heropen Web Viewer — Lightweight HTTP API server.
Serves the viewer HTML and exposes REST endpoints backed by heropen.core.
Run: heropen viewer
"""
from __future__ import annotations

import json
import os
import mimetypes
from datetime import date
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

from heropen.core import AGENTS, HERO_PEN_DIR, conn, get_agent_limit
from heropen import __version__ as HP_VERSION
from heropen.mac_workbench import dashboard, set_mode

# 后台摄取：延迟导入，避免缺少可选依赖时阻断面板启动。
try:
    from heropen.turn_ingest_scheduler import (
        read_result as _ingest_read_result,
        run_once as _ingest_run_once,
        running as _ingest_running,
        set_settings as _ingest_set_settings,
        settings as _ingest_settings,
        start_auto as _ingest_start_auto,
        stop_auto as _ingest_stop_auto,
    )
except Exception:  # noqa: BLE001
    _ingest_read_result = _ingest_run_once = None
    _ingest_running = _ingest_settings = None
    _ingest_set_settings = _ingest_start_auto = _ingest_stop_auto = None

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 9020
FREE_AGENT_LIMIT = 8  # fallback; prefer get_agent_limit() at runtime

# ── AI assistant scanner ──

ASSISTANT_DEFS = [
    {
        "id": "workbuddy",
        "name": "WorkBuddy",
        "icon": "WB",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json"),
            os.path.join(os.path.expanduser("~"), ".mcp.json"),
        ],
        "mcp_key": "mcpServers.heropen",
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "icon": "CS",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json"),
        ],
        "mcp_key": "mcpServers.heropen",
    },
    {
        "id": "claude",
        "name": "Claude Code",
        "icon": "CC",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".claude", "mcp.json"),
            os.path.join(os.path.expanduser("~"), ".claude.json"),
        ],
        "mcp_key": "mcpServers.heropen",
    },
    {
        "id": "vscode",
        "name": "VS Code (Copilot)",
        "icon": "VC",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".vscode", "mcp.json"),
        ],
        "mcp_key": "mcpServers.heropen",
    },
]

heropen_mcp_config = {
    "command": "heropen-mcp",
    "args": [],
}


def _visible_agents() -> list[str]:
    """Include existing local databases even without agent-config.json."""
    configured = list(AGENTS.keys())
    data_dir = Path(HERO_PEN_DIR)
    if data_dir.is_dir():
        for db in sorted(data_dir.glob("*.db")):
            name = db.stem
            if name not in configured:
                configured.append(name)
    return configured


def _scan_assistants() -> list[dict]:
    """Scan the machine for AI assistants and their config state."""
    results = []
    for ad in ASSISTANT_DEFS:
        found_path = None
        for p in ad["config_paths"]:
            if os.path.isfile(p):
                found_path = p
                break
        if found_path is None:
            continue

        configured = False
        try:
            with open(found_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            servers = cfg.get("mcpServers", {})
            configured = "heropen" in servers
        except Exception:
            pass

        results.append({
            "id": ad["id"],
            "name": ad["name"],
            "icon": ad["icon"],
            "path": found_path,
            "configured": configured,
        })
    return results


def _write_heropen_config(config_path: str) -> bool:
    """Merge heropen MCP config into an assistant's mcp.json."""
    try:
        cfg = {}
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg.setdefault("mcpServers", {})
        cfg["mcpServers"]["heropen"] = heropen_mcp_config
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


class ViewerHandler(SimpleHTTPRequestHandler):
    """Serve viewer HTML and REST API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SERVER_DIR, **kwargs)

    def log_message(self, fmt, *args):
        """Minimal logging."""
        print(f"[viewer] {args[0]}", flush=True)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", f"http://{HOST}:{PORT}" if os.environ.get("HEROPEN_DESKTOP") == "1" else "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        referer = self.headers.get("Referer", "")
        local_url = f"http://{HOST}:{PORT}/"
        return origin == local_url[:-1] or referer.startswith(local_url)

    def _agent_stats(self, agent_name: str) -> dict:
        """Get stats and today's count for one agent."""
        try:
            c = conn(agent_name)
            total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            today_str = date.today().isoformat()
            today_count = c.execute(
                "SELECT COUNT(*) FROM entries WHERE entry_date = ?", (today_str,)
            ).fetchone()[0]
            c.close()
            return {
                "name": agent_name,
                "total": total,
                "today": today_count,
                "online": True,
            }
        except Exception:
            return {"name": agent_name, "total": 0, "today": 0, "online": False}

    # ── API routes (GET) ──

    def _handle_health(self):
        agents_stats = {}
        for agent_name in _visible_agents():
            agents_stats[agent_name] = self._agent_stats(agent_name)
        return self._send_json({
            "status": "ok",
            "version": HP_VERSION,
            "agents": agents_stats,
            "free_limit": get_agent_limit(),
            "setup_done": bool(agents_stats),
        })

    def _handle_memory(self, agent_name: str, query: str):
        if agent_name not in _visible_agents():
            return self._send_json({"error": "agent not found"}, 404)

        params = parse_qs(query)
        limit = min(int(params.get("limit", [20])[0]), 50)
        date_filter = params.get("date", [None])[0]

        try:
            c = conn(agent_name)
            if date_filter:
                rows = [
                    dict(r) for r in c.execute(
                        "SELECT id, entry_date, section, content, tags, source, created_at "
                        "FROM entries WHERE entry_date = ? ORDER BY id DESC LIMIT ?",
                        (date_filter, limit),
                    ).fetchall()
                ]
            else:
                rows = [
                    dict(r) for r in c.execute(
                        "SELECT id, entry_date, section, content, tags, source, created_at "
                        "FROM entries ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                ]
            c.close()
            for r in rows:
                r.pop("embedding", None)
                raw = r.get("content", "") or ""
                r["content_preview"] = raw[:80] + ("..." if len(raw) > 80 else "")
                r["content_truncated"] = len(raw) > 80
            return self._send_json({
                "agent": agent_name,
                "count": len(rows),
                "today": self._agent_stats(agent_name)["today"],
                "results": rows,
            })
        except Exception as e:
            return self._send_json({"error": str(e)}, 500)

    def _handle_ingest_status(self):
        """Read-only view of the auto-ingest scheduler and its last run."""
        if _ingest_settings is None:
            return self._send_json({"available": False,
                                    "note": "摄取模块不可用"})
        return self._send_json({
            "available": True,
            "settings": _ingest_settings(),
            "running": _ingest_running(),
            "last": _ingest_read_result(),
        })

    def _handle_setup_status(self):
        return self._send_json({
            "configured": bool(_visible_agents()),
            "agent_count": len(_visible_agents()),
            "free_limit": get_agent_limit(),
        })

    def _handle_setup_scan(self):
        return self._send_json({
            "assistants": _scan_assistants(),
            "free_limit": get_agent_limit(),
        })

    def _handle_setup_configure(self, body: dict):
        assistant_ids = body.get("assistants", [])
        if not assistant_ids:
            return self._send_json({"error": "no assistants selected"}, 400)

        results = {}
        for a in _scan_assistants():
            if a["id"] in assistant_ids:
                ok = _write_heropen_config(a["path"])
                results[a["id"]] = {"ok": ok, "path": a["path"]}

        return self._send_json({
            "results": results,
            "message": "MCP 配置已写入。请重启对应的 AI 助手使配置生效。",
        })

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/health":
            return self._handle_health()

        if path == "/api/workbench":
            return self._send_json(dashboard())

        if path.startswith("/api/memory/"):
            agent_name = path.split("/api/memory/")[1]
            return self._handle_memory(agent_name, parsed.query)

        if path == "/api/ingest/status":
            return self._handle_ingest_status()

        if path == "/api/setup/status":
            return self._handle_setup_status()

        if path == "/api/setup/scan":
            return self._handle_setup_scan()

        # ── Static files ──
        if path == "/" or path == "":
            path = "/mac_workbench.html" if os.environ.get("HEROPEN_DESKTOP") == "1" else "/viewer.html"

        if os.environ.get("HEROPEN_DESKTOP") == "1" and path not in ("/mac_workbench.html", "/viewer.html"):
            return self._send_json({"error": "not found"}, 404)
        file_path = os.path.realpath(os.path.join(SERVER_DIR, path.lstrip("/")))
        if os.path.commonpath((SERVER_DIR, file_path)) != SERVER_DIR:
            return self._send_json({"error": "not found"}, 404)
        if os.path.isfile(file_path):
            content_type, _ = mimetypes.guess_type(file_path)
            if content_type is None:
                content_type = "application/octet-stream"

            with open(file_path, "rb") as f:
                body = f.read()

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError:
                return self._send_json({"error": "invalid JSON"}, 400)

        if path == "/api/ingest/run" and os.environ.get("HEROPEN_DESKTOP") == "1":
            if not self._same_origin():
                return self._send_json({"error": "forbidden origin"}, 403)
            apply = body.get("apply")
            return self._send_json({"ok": True, "result": _ingest_run_once(
                apply=apply if isinstance(apply, bool) else None)})

        if path == "/api/ingest/settings" and os.environ.get("HEROPEN_DESKTOP") == "1":
            if not self._same_origin():
                return self._send_json({"error": "forbidden origin"}, 403)
            kwargs = {}
            for key in ("enabled", "apply"):
                if isinstance(body.get(key), bool):
                    kwargs[key] = body[key]
            if "interval_seconds" in body:
                kwargs["interval_seconds"] = body["interval_seconds"]
            try:
                updated = _ingest_set_settings(**kwargs)
            except ValueError as exc:
                return self._send_json({"error": str(exc)}, 400)
            # Apply the new state to the timer immediately.
            if updated["enabled"]:
                _ingest_start_auto()
            else:
                _ingest_stop_auto()
            return self._send_json({"ok": True, "settings": updated,
                                    "running": _ingest_running()})

        if path == "/api/workbench/mode" and os.environ.get("HEROPEN_DESKTOP") == "1":
            if not self._same_origin():
                return self._send_json({"error": "forbidden origin"}, 403)
            agent, mode = body.get("agent"), body.get("mode")
            if not isinstance(agent, str) or not isinstance(mode, str) or not set_mode(agent, mode):
                return self._send_json({"error": "invalid agent or mode"}, 400)
            return self._send_json({"ok": True, "agent": agent, "mode": mode})

        if path == "/api/setup/configure":
            if os.environ.get("HEROPEN_DESKTOP") == "1":
                return self._send_json({"error": "desktop configuration is disabled"}, 403)
            return self._handle_setup_configure(body)

        return self._send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", f"http://{HOST}:{PORT}" if os.environ.get("HEROPEN_DESKTOP") == "1" else "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    # C2 安全自查：viewer 仅绑 loopback，绝不暴露到局域网/公网。
    # 即便 HOST 常量被误改，也拒绝以非 loopback 地址启动。
    _host = (HOST or "").lower()
    if _host not in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"):
        raise RuntimeError(
            f"[heropen viewer] 安全约束：viewer 只能绑定 loopback（127.0.0.1 / localhost / ::1），"
            f"当前 HOST={HOST} 会把面板暴露到网络，已拒绝启动。"
            f"如需远程访问请自行通过 SSH 隧道转发，不要直接改 HOST。"
        )
    httpd = HTTPServer((HOST, PORT), ViewerHandler)
    # 后台定时摄取：让「主动读取会话记录」不必手动调用。默认关闭，
    # 且默认只试算不写入；任何异常都被限制在定时线程内，不影响面板。
    try:
        from heropen.turn_ingest_scheduler import settings as _ingest_settings
        from heropen.turn_ingest_scheduler import start_auto as _start_ingest
        if _start_ingest():
            _cfg = _ingest_settings()
            _mode = "写入" if _cfg["apply"] else "仅试算"
            print(f"  [摄取] 已启用后台定时读取（每 {_cfg['interval_seconds']} 秒 · {_mode}）",
                  flush=True)
        else:
            print("  [摄取] 后台定时读取未启用（可在工作台开启）", flush=True)
    except Exception as exc:  # noqa: BLE001 - 摄取故障不得阻断面板启动
        print(f"  [摄取] 未能启动后台定时读取：{exc}", flush=True)
    print(f"  heropen Web Viewer", flush=True)
    print(f"  http://127.0.0.1:{PORT}", flush=True)
    print(f"  API: http://127.0.0.1:{PORT}/api/health", flush=True)
    print(f"  [安全] 仅绑定本机 loopback（{HOST}），不暴露到局域网/公网", flush=True)
    print(f"  Ctrl+C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.", flush=True)


if __name__ == "__main__":
    main()
