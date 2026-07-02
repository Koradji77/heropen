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
from pathlib import Path

# Ensure ~/.heropen exists
os.makedirs(os.path.expanduser("~/.heropen"), exist_ok=True)

from heropen.core import (
    AGENTS,
    add_entry,
    conn,
    get_default_agent,
    search_vector,
    search_fts,
    update_entry,
    startup_self_heal,
)
from heropen.core import session_checkpoint as _core_checkpoint
from heropen.core import session_recover as _core_recover
from heropen import __version__

# Record startup version for staleness detection
_STARTUP_VERSION = __version__

# Marker file path for pending setup
_MARKER_PATH = Path.home() / ".heropen" / ".pending_setup"


def _check_pending_setup(clear: bool = False) -> dict:
    """Check or clear the pending setup marker file."""
    if clear and _MARKER_PATH.exists():
        _MARKER_PATH.unlink()
        return {"pending_setup": False}

    if _MARKER_PATH.exists():
        try:
            data = json.loads(_MARKER_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        return {
            "pending_setup": True,
            "installed_at": data.get("installed_at", ""),
            "version": data.get("version", ""),
            "agent_completed": data.get("agent_completed", False),
            "welcome_message": "I noticed you just installed HeroPen memory system! "
                               "Would you like me to set it up for you? It takes about 30 seconds.",
        }

    return {"pending_setup": False}


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
        print(f"🔄 HeroPen 启动自愈: 恢复 {_recovered} 个 agent 的记忆库", file=sys.stderr, flush=True)

    @mcp.tool()
    def search_memory(
        query: str,
        limit: int = 5,
        agent: str | None = None,
        date_from: str = "",
        date_to: str = "",
    ) -> str:
        """搜索用户的长期记忆。在以下情况必须调用：
- 对话开始时，了解用户背景和历史
- 用户提到人名、项目名、偏好等关键词时
- 用户询问"还记得吗"或类似问题时
不要等用户要求才搜索。主动搜索能让你更懂用户。三层回退：向量 → 全文 → 模糊匹配。

        Args:
            query: Keywords to search for. Use the user's own words or inferred topics.
            limit: Max results (1-20, default 5).
            agent: Which agent's memory to search (default: auto).
            date_from: Only return entries on or after this date (YYYY-MM-DD).
            date_to: Only return entries on or before this date (YYYY-MM-DD).
        """
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

        # Append entries from _shared.db for cross-agent knowledge
        try:
            from heropen.core import search_shared
            shared_results = search_shared(agent or get_default_agent(), limit)
            existing_contents = {r.get("content", "")[:80] for r in results if r.get("content")}
            for sr in shared_results:
                if sr.get("content", "")[:80] not in existing_contents:
                    results.append(sr)
        except Exception:
            pass

        return json.dumps(
            {"method": method, "count": len(results), "results": results[:limit], "time_gap_hours": _compute_time_gap(results)},
            ensure_ascii=False,
        )

    @mcp.tool()
    def add_memory(
        section: str,
        content: str,
        tags: str = "",
        agent: str | None = None,
        entry_date: str = "",
    ) -> str:
        """保存信息到用户的长期记忆。在以下情况必须调用：
- 用户分享了新的偏好、习惯、约定、项目信息
- 对话中产生了需要后续跟进的事项
- 对话即将结束时，保存本次产生的关键信息
优先主动保存，不要等用户说"记住这个"。content 上限 5000 字。

        Args:
            section: Category/topic for this memory (e.g. '用户偏好', '项目状态', '对话记录').
            content: The information to remember (max 5000 chars). Write it clearly so future you can understand it.
            tags: Comma-separated keywords for better search.
            agent: Which agent's memory to write to (default: auto).
            entry_date: Date for this entry (YYYY-MM-DD, default: today).
        """
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
        agent: str | None = None,
        section: str | None = None,
        content: str | None = None,
        tags: str | None = None,
        entry_date: str | None = None,
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

        result = update_entry(entry_id, agent, **fields)
        if result:
            result.pop("embedding", None)
            return json.dumps({"ok": True, "entry": result}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": f"Entry {entry_id} not found or no fields to update"},
            ensure_ascii=False,
        )

    @mcp.tool()
    def list_memory(limit: int = 10, agent: str | None = None) -> str:
        """列出用户的记忆列表。用于概览已有记忆、检查冗余、了解记忆总量。

        Args:
            limit: How many recent entries to show (max 50, default 10).
            agent: Which agent's memories to list.
        """
        c = conn(agent)
        total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT id, entry_date, section, content, tags, source, created_at FROM entries ORDER BY id DESC LIMIT ?",
                (min(limit, 50),),
            ).fetchall()
        ]
        c.close()
        for r in rows:
            r.pop("embedding", None)
        return json.dumps({"total_count": total, "count": len(rows), "results": rows}, ensure_ascii=False)

    @mcp.tool()
    def health(clear_pending: bool = False) -> str:
        """Check HeroPen system health and connection status. Call this when you start up to verify everything is working. If pending_setup is true, it means HeroPen was just installed — show the welcome_message to the user and ask if they'd like to complete the setup.
        
        Args:
            clear_pending: Set to true after completing initial setup to clear the pending marker.
        """
        # Check pending setup marker
        pending = _check_pending_setup(clear=clear_pending)

        stats = {}
        db_connected = True
        mcp_config_found = True
        issues = []
        total_memory = 0
        last_memory_at = ""

        for agent in list(AGENTS.keys()):
            try:
                c = conn(agent)
                total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                last = c.execute(
                    "SELECT created_at FROM entries ORDER BY id DESC LIMIT 1"
                ).fetchone()
                recent = c.execute(
                    "SELECT section FROM entries WHERE section != '' ORDER BY id DESC LIMIT 3"
                ).fetchall()
                c.close()
                last_memory = last[0] if last else ""
                if last_memory and last_memory > last_memory_at:
                    last_memory_at = last_memory
                total_memory += total
                recent_topics = [r["section"] for r in recent if r["section"]]
                stats[agent] = {"entries": total, "recent_topics": recent_topics}
            except Exception:
                stats[agent] = {"entries": 0, "recent_topics": []}
                db_connected = False
                issues.append(f"database connection failed for {agent}")

        # Check MCP config
        agent_config = Path.home() / ".heropen" / "agent-config.json"
        if not agent_config.exists():
            mcp_config_found = False
            issues.append("MCP configuration file not found")

        version_stale = None
        import importlib.metadata as _ilm
        try:
            _current = _ilm.version("heropen")
            if _current != _STARTUP_VERSION:
                version_stale = {"startup": _STARTUP_VERSION, "current": _current}
        except Exception:
            pass

        result = {
            "status": "ok" if (db_connected and mcp_config_found) else "degraded",
            "server": "hero-pen-mcp",
            "version": __version__,
            "version_stale": version_stale,
            "agents": stats,
            "db_connected": db_connected,
            "mcp_config_found": mcp_config_found,
            "memory_count": total_memory,
            "last_memory_at": last_memory_at,
        }

        # Merge in pending setup fields
        result.update(pending)
        if issues:
            result["issues"] = issues
            if pending.get("pending_setup") and not result.get("welcome_message"):
                result["welcome_message"] = (
                    "HeroPen is installed but has some configuration issues. "
                    "Would you like me to help diagnose them?"
                )

        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def session_checkpoint(agent: str | None = None, context_summary: str = "",
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
    def session_recover(agent: str | None = None, limit: int = 1) -> str:
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
    # ── Start 60s heartbeat for real-time online tracking ──
    from heropen.telemetry_ping import start_heartbeat

    try:
        start_heartbeat()
        print("📡 匿名心跳已启动（每60秒一次，仅统计在线人数，不收集任何个人信息）", file=sys.stderr, flush=True)
    except Exception:
        print("⚠️ 心跳启动失败，不影响 MCP server 运行", file=sys.stderr, flush=True)

    parser = argparse.ArgumentParser(description="HeroPen MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as HTTP/SSE server on 0.0.0.0:8090")
    args = parser.parse_args()

    mcp_server = create_mcp_server()

    if args.http:
        print(f"🚀 HeroPen MCP Server (SSE) listening on 0.0.0.0:8090", file=sys.stderr, flush=True)
        mcp_server.run(transport="sse")
    else:
        mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
