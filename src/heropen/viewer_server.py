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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from heropen.core import AGENTS, conn, add_entry
from heropen import __version__ as HP_VERSION

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 9020
FREE_AGENT_LIMIT = 2

# Ordered list of free-tier agent names (first 2 are unlocked)
agent_order = sorted(k for k in AGENTS if not k.startswith("_"))


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
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "icon": "CS",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json"),
        ],
    },
    {
        "id": "claude",
        "name": "Claude Code",
        "icon": "CC",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".claude", "mcp.json"),
            os.path.join(os.path.expanduser("~"), ".claude.json"),
        ],
    },
    {
        "id": "vscode",
        "name": "VS Code (Copilot)",
        "icon": "VC",
        "config_paths": [
            os.path.join(os.path.expanduser("~"), ".vscode", "mcp.json"),
        ],
    },
]

HEROPEN_MCP_CONFIG = {
    "command": "heropen-mcp",
    "args": [],
}


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
        cfg["mcpServers"]["heropen"] = HEROPEN_MCP_CONFIG
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
        print(f"[viewer] {args[0]}", flush=True)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _agent_stats(self, agent_name: str) -> dict:
        try:
            c = conn(agent_name)
            # Check if entries table exists and has data
            has_table = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='entries'"
            ).fetchone()
            if not has_table:
                c.close()
                return {"name": agent_name, "total": 0, "today": 0, "online": False}
            total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            if total == 0:
                c.close()
                return {"name": agent_name, "total": 0, "today": 0, "online": False}
            today_str = date.today().isoformat()
            today_count = c.execute(
                "SELECT COUNT(*) FROM entries WHERE entry_date = ?", (today_str,)
            ).fetchone()[0]
            c.close()
            return {"name": agent_name, "total": total, "today": today_count, "online": True}
        except Exception:
            return {"name": agent_name, "total": 0, "today": 0, "online": False}

    def _handle_health(self):
        agents_stats = {}
        for agent_name in agent_order:
            agents_stats[agent_name] = self._agent_stats(agent_name)
        # Check if agent-config.json exists (configured agents)
        config_detected = False
        try:
            from heropen.install import AGENT_CONFIG_PATH
            config_detected = os.path.isfile(AGENT_CONFIG_PATH)
        except Exception:
            pass
        # Check if any agents are actually running
        any_online = any(s.get("online") for s in agents_stats.values())
        return self._send_json({
            "status": "ok",
            "version": HP_VERSION,
            "agents": agents_stats,
            "free_limit": FREE_AGENT_LIMIT,
            "setup_done": bool(AGENTS),
            "config_detected": config_detected,
            "any_online": any_online,
            "hint": None if any_online else (
                "已检测到配置，但 AI 助手未运行。请先启动你的 AI 助手。"
                if config_detected else None
            ),
        })

    def _handle_memory(self, agent_name: str, query: str):
        # Backend lock: only first 2 agents
        if agent_name not in agent_order[:FREE_AGENT_LIMIT]:
            return self._send_json({"error": "upgrade to Plus to unlock"}, 403)
        if agent_name not in AGENTS:
            return self._send_json({"error": "agent not found"}, 404)

        params = parse_qs(query)
        limit = min(int(params.get("limit", [20])[0]), 50)
        date_filter = params.get("date", [None])[0]

        try:
            c = conn(agent_name)
            if date_filter:
                rows = [dict(r) for r in c.execute(
                    "SELECT id, entry_date, section, content, tags, source, created_at "
                    "FROM entries WHERE entry_date = ? ORDER BY id DESC LIMIT ?",
                    (date_filter, limit),
                ).fetchall()]
            else:
                rows = [dict(r) for r in c.execute(
                    "SELECT id, entry_date, section, content, tags, source, created_at "
                    "FROM entries ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()]
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

    def _handle_setup_status(self):
        return self._send_json({
            "configured": bool(AGENTS),
            "agent_count": len(AGENTS),
            "free_limit": FREE_AGENT_LIMIT,
        })

    def _handle_setup_scan(self):
        return self._send_json({"assistants": _scan_assistants(), "free_limit": FREE_AGENT_LIMIT})

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

    def _handle_write_instruction(self, body: dict):
        """Write the identity instruction directly into the first agent's memory DB."""
        # Read agent-config.json to find the first real agent
        import json
        from datetime import date, datetime
        from heropen.core import init_db
        try:
            from heropen.install import AGENT_CONFIG_PATH
            with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return self._send_json({"error": "agent config not found"}, 404)

        agents = cfg.get("agents", [])
        if not agents:
            return self._send_json({"error": "no agents configured"}, 404)

        results = []
        for agent_cfg in agents:
            name = agent_cfg["name"]
            try:
                # Ensure DB is initialized (creates tables if needed)
                init_db(name)
                # Build the identity instruction
                instruction = (
                    f"你叫 {name}，现在拥有 HeroPen 记忆系统。\n\n"
                    "每次对话开始时，先调用 search_memory 工具（MCP 工具，不是命令行）：\n"
                    f'  query: "最近的对话"\n'
                    f'  agent: "{name}"\n\n'
                    "如果返回结果里出现「⏰ 距离上次对话已过 X 小时」，\n"
                    "说明对话间隔太久——主动问问用户最近发生了什么，要不要回顾。\n\n"
                    "每次对话结束前，调用 add_memory 工具（MCP 工具）保存本次对话：\n"
                    '  section: "对话记录"\n'
                    "  content: （本次对话的摘要——你自己总结，抓住重点）\n"
                    f'  agent: "{name}"\n\n'
                    "以后回答时，看到跟记忆里已有信息相关的话题，\n"
                    "先调 search_memory 看看之前说过什么，避免重复问用户。"
                )

                entry_id = add_entry(
                    entry_date=date.today().isoformat(),
                    content=instruction,
                    section="🔧 HeroPen 身份配置",
                    tags="heropen,setup,identity",
                    agent=name,
                    source="setup_wizard",
                )
                results.append({
                    "agent": name,
                    "entry_id": entry_id,
                    "ok": True,
                })
            except Exception as e:
                results.append({
                    "agent": name,
                    "ok": False,
                    "error": str(e),
                })

        return self._send_json({
            "results": results,
            "message": f"已向 {len([r for r in results if r.get('ok')])} 个 AI 写入身份指令。"
        })

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/health":
            return self._handle_health()
        if path.startswith("/api/memory/"):
            agent_name = path.split("/api/memory/")[1]
            return self._handle_memory(agent_name, parsed.query)
        if path == "/api/setup/status":
            return self._handle_setup_status()
        if path == "/api/setup/scan":
            return self._handle_setup_scan()

        if path == "/" or path == "":
            path = "/viewer.html"
        file_path = os.path.join(SERVER_DIR, path.lstrip("/"))
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
        if path == "/api/setup/configure":
            return self._handle_setup_configure(body)
        if path == "/api/setup/write-instruction":
            return self._handle_write_instruction(body)
        return self._send_json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), ViewerHandler)
    print(f"  heropen Web Viewer", flush=True)
    print(f"  http://127.0.0.1:{PORT}", flush=True)
    print(f"  API: http://127.0.0.1:{PORT}/api/health", flush=True)
    print(f"  Ctrl+C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.", flush=True)


if __name__ == "__main__":
    main()
