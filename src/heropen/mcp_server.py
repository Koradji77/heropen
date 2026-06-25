"""
HeroPen MCP Server — Expose HeroPen memory tools via MCP protocol.

Tools:
  search_memory, add_memory, update_memory, list_memory, health

Run:
  heropen-mcp                  # stdio (for Hermes native MCP)
  heropen-mcp --http           # SSE (for HTTP MCP clients)
"""
from __future__ import annotations

import argparse
import json
import sys
import os

# Ensure ~/.heropen exists
os.makedirs(os.path.expanduser("~/.heropen"), exist_ok=True)

from heropen.core import (
    AGENTS,
    add_entry,
    conn,
    search_vector,
    search_fts,
    update_entry,
    startup_self_heal,
)
from heropen.core import session_checkpoint as _core_checkpoint
from heropen.core import session_recover as _core_recover
from heropen import __version__


def _compute_time_gap(results: list) -> float | None:
    """Compute hours since the most recent entry's created_at. Returns None if unavailable."""
    if not results:
        return None
    try:
        from datetime import datetime
        last = max(r.get("created_at", "") or "" for r in results if r.get("created_at"))
        if not last:
            return None
        dt = datetime.fromisoformat(last)
        now = datetime.now()
        return round((now - dt).total_seconds() / 3600, 1)
    except Exception:
        return None


def create_mcp_server():
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("hero-pen")

    # ── Startup self-heal: check all agent databases ──
    _all_agents = list(AGENTS.keys())
    _recovered = 0
    for _agent in _all_agents:
        _r = startup_self_heal(_agent)
        if _r["status"] == "recovered":
            _recovered += 1
    if _recovered > 0:
        print(f"🔄 HeroPen 启动自愈: 恢复 {_recovered} 个 agent 的记忆库", flush=True)

    @mcp.tool()
    def search_memory(
        query: str,
        limit: int = 5,
        agent: str = "xiaoman",
        date_from: str = "",
        date_to: str = "",
    ) -> str:
        """Search HeroPen memory database (vector → FTS → LIKE)."""
        limit = min(limit, 20)
        results = search_vector(query, limit, agent)
        method = "vector"
        if not results:
            keywords = [k.strip() for k in query.split() if k.strip()]
            results = search_fts(keywords or [query], limit, agent)
            method = "fts"
        if not results:
            import sqlite3
            c = conn(agent)
            keywords = [k.strip() for k in query.split() if k.strip()]
            if not keywords:
                keywords = [query]
            conditions = []
            params = []
            for kw in keywords:
                conditions.append("(section LIKE ? OR content LIKE ? OR tags LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
            where = " OR ".join(conditions)
            sql = f"SELECT id, entry_date, section, content, tags, source, agent, created_at FROM entries WHERE {where} ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = [dict(r) for r in c.execute(sql, params).fetchall()]
            c.close()
            results = rows
            method = "like"

        if results is None:
            results = []

        if date_from or date_to:
            filtered = []
            for r in results:
                ed = r.get("entry_date", "")
                if date_from and ed < date_from:
                    continue
                if date_to and ed > date_to:
                    continue
                filtered.append(r)
            results = filtered

        for r in results:
            r.pop("embedding", None)
            if "similarity" in r and r["similarity"] is not None:
                r["score"] = round(r["similarity"], 4)
                r["score_label"] = (
                    "高相关" if r["score"] >= 0.7
                    else ("中相关" if r["score"] >= 0.4 else "低相关")
                )
            else:
                r["score"] = None
                r["score_label"] = "关键词匹配"

        return json.dumps(
            {"method": method, "count": len(results), "results": results[:limit], "time_gap_hours": _compute_time_gap(results)},
            ensure_ascii=False,
        )

    @mcp.tool()
    def add_memory(
        section: str,
        content: str,
        tags: str = "",
        agent: str = "xiaoman",
        entry_date: str = "",
    ) -> str:
        """Add a new memory entry to hero_pen."""
        from datetime import date
        ed = entry_date if entry_date else date.today().isoformat()
        entry_id = add_entry(
            entry_date=ed,
            content=content[:5000],
            section=section,
            tags=tags,
            agent=agent,
            source="mcp",
        )
        if entry_id:
            return json.dumps({"ok": True, "id": entry_id, "section": section}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": "Write failed"}, ensure_ascii=False)

    @mcp.tool()
    def update_memory(
        entry_id: int,
        agent: str = "xiaoman",
        section: str | None = None,
        content: str | None = None,
        tags: str | None = None,
        entry_date: str | None = None,
        status: str | None = None,
    ) -> str:
        """Update an existing memory entry. Only provided fields are changed."""
        fields = {}
        if section is not None:
            fields["section"] = section
        if content is not None:
            fields["content"] = content[:5000]
        if tags is not None:
            fields["tags"] = tags
        if entry_date is not None:
            fields["entry_date"] = entry_date
        if status is not None:
            fields["status"] = status

        result = update_entry(entry_id, agent, **fields)
        if result:
            result.pop("embedding", None)
            return json.dumps({"ok": True, "entry": result}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": f"Entry {entry_id} not found or no fields to update"},
            ensure_ascii=False,
        )

    @mcp.tool()
    def list_memory(limit: int = 10, agent: str = "xiaoman") -> str:
        """List recent memory entries."""
        c = conn(agent)
        total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT id, entry_date, section, content, tags, source, status, created_at FROM entries ORDER BY id DESC LIMIT ?",
                (min(limit, 50),),
            ).fetchall()
        ]
        c.close()
        for r in rows:
            r.pop("embedding", None)
        return json.dumps({"total_count": total, "count": len(rows), "results": rows}, ensure_ascii=False)

    @mcp.tool()
    def health() -> str:
        """Health check with per-agent memory stats and recent topics."""
        stats = {}
        for agent in list(AGENTS.keys()):
            try:
                c = conn(agent)
                total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                recent = c.execute(
                    "SELECT section FROM entries WHERE section != '' ORDER BY id DESC LIMIT 3"
                ).fetchall()
                c.close()
                recent_topics = [r["section"] for r in recent if r["section"]]
                stats[agent] = {"entries": total, "recent_topics": recent_topics}
            except Exception:
                stats[agent] = {"entries": 0, "recent_topics": []}

        return json.dumps(
            {"status": "ok", "server": "hero-pen-mcp", "version": __version__, "agents": stats},
            ensure_ascii=False,
        )

    @mcp.tool()
    def session_checkpoint(agent: str = "xiaoman", context_summary: str = "",
                           active_task: str = "", key_decisions: str = "",
                           tags: str = "") -> str:
        """Save a session checkpoint. Call periodically during long conversations
        so the agent can recover context after compression or restart.
        
        Args:
            agent: Which agent's memory
            context_summary: Summary of current conversation context
            active_task: What task is currently being worked on
            key_decisions: Comma-separated key decisions made so far
            tags: Extra comma-separated tags
        """
        decisions_list = [d.strip() for d in key_decisions.split(",") if d.strip()] if key_decisions else []
        entry_id = _core_checkpoint(
            agent=agent,
            context_summary=context_summary,
            active_task=active_task,
            key_decisions=decisions_list,
            tags=tags
        )
        if entry_id:
            return json.dumps({"ok": True, "id": entry_id}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": "Checkpoint save failed"}, ensure_ascii=False)

    @mcp.tool()
    def session_recover(agent: str = "xiaoman", limit: int = 1) -> str:
        """Recover the most recent session checkpoint(s). Use after context
        compression or agent restart to pick up where you left off.
        
        Args:
            agent: Which agent's memory
            limit: Number of recent checkpoints (default 1, max 5)
        """
        limit = min(limit, 5)
        results = _core_recover(agent=agent, limit=limit)
        for r in results:
            r.pop("content", None)
        return json.dumps({
            "count": len(results),
            "checkpoints": results
        }, ensure_ascii=False)

    # HTTP settings (only used with --http)
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = 8090
    mcp.settings.transport_security.enable_dns_rebinding_protection = False

    return mcp


def main():
    # ── Start heartbeat ping (every hour, for "online now" tracking) ──
    import threading as _threading
    import time as _time

    print("📡 匿名心跳已启动（每小时一次，仅统计在线人数，不收集任何个人信息）", flush=True)

    try:
        from heropen.telemetry_ping import fire_ping
    except Exception:
        fire_ping = None

    def _heartbeat_loop():
        if fire_ping:
            _time.sleep(60)  # let server start
            while True:
                try:
                    fire_ping()
                except Exception:
                    pass
                _time.sleep(3600)  # every hour

    _hb = _threading.Thread(target=_heartbeat_loop, daemon=True)
    _hb.start()

    parser = argparse.ArgumentParser(description="HeroPen MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as HTTP/SSE server on 0.0.0.0:8090")
    args = parser.parse_args()

    mcp_server = create_mcp_server()

    if args.http:
        print(f"🚀 HeroPen MCP Server (SSE) listening on 0.0.0.0:8090", flush=True)
        mcp_server.run(transport="sse")
    else:
        mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
