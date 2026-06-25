"""
HeroPen Web Viewer — Lightweight HTTP API server.
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

from heropen.core import AGENTS, conn
from heropen import __version__ as HP_VERSION

# viewer.html lives next to this file
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 9020


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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

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

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # ── API routes ──
        if path == "/api/health":
            agents_stats = {}
            for agent_name in list(AGENTS.keys()):
                agents_stats[agent_name] = self._agent_stats(agent_name)
            return self._send_json({
                "status": "ok",
                "version": HP_VERSION,
                "agents": agents_stats,
            })

        if path.startswith("/api/memory/"):
            agent_name = path.split("/api/memory/")[1]
            if agent_name not in AGENTS:
                return self._send_json({"error": "agent not found"}, 404)

            params = parse_qs(parsed.query)
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
                    r["content_preview"] = raw[:80] + ("…" if len(raw) > 80 else "")
                    r["content_truncated"] = len(raw) > 80
                return self._send_json({
                    "agent": agent_name,
                    "count": len(rows),
                    "today": self._agent_stats(agent_name)["today"],
                    "results": rows,
                })
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        # ── Static files ──
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    httpd = HTTPServer((HOST, PORT), ViewerHandler)
    print(f"  HeroPen Web Viewer", flush=True)
    print(f"  http://127.0.0.1:{PORT}", flush=True)
    print(f"  Ctrl+C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.", flush=True)


if __name__ == "__main__":
    main()
