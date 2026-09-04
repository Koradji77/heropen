"""
heropen.cli_commands — Command implementations for heropen CLI.

Each function takes a list of string args (like sys.argv[1:]).
Lazy-imported by heropen.cli for fast startup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime

from heropen.core import (
    AGENTS,
    add_entry,
    auto_tag,
    capture_session_content,
    conn,
    db_path,
    get_embedding,
    init_db,
    integrity_check,
    parse_diary,
    search_by_date,
    search_by_tag,
    search_fts,
    search_graph,
    search_recent,
    search_vector,
    startup_self_heal,
    sync_to_db,
    format_recall,
    HERO_PEN_DIR,
    __version__,
)


# ─── Helpers ─────────────────────────────────────────────────────

def _build_parser(action: str) -> argparse.ArgumentParser:
    """Build a per-command argument parser."""
    p = argparse.ArgumentParser(prog=f"heropen {action}")
    p.add_argument("--agent", default=None, help="Agent name (default: agent)")
    return p


def _resolve_agent(args: list[str]) -> str:
    """Extract --agent from args list. Returns the agent name or 'agent' for default."""
    for i, a in enumerate(args):
        if a in ("--agent",) and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return "agent"


def _tokenize(text: str) -> list[str]:
    """Split a natural-language query into search tokens.

    Latin/digit runs are kept whole; CJK runs are kept as phrases.
    Used for the keyword (FTS) fallback so that e.g. "Eddie 产品经理"
    matches a memory whose content is "Eddie 是 heropen 的 PM".
    """
    if not text:
        return []
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    tokens += re.findall(r"[\u4e00-\u9fff]+", text)
    return [t for t in tokens if t.strip()]


def _has_help(args: list[str]) -> bool:
    return any(a in ("-h", "--help") for a in args)


# ─── Bootstrap / Self-heal ──────────────────────────────────────

def _format_time_gap(last_created_at: str) -> str:
    if not last_created_at:
        return ""
    try:
        last = datetime.fromisoformat(last_created_at)
        now = datetime.now()
        delta = now - last
        hours = delta.total_seconds() / 3600
        if hours < 2:
            return ""
        if hours < 72:
            return f"⏰ 距离上次对话已过 {round(hours)} 小时"
        return f"⏰ 距离上次对话已过 {round(hours / 24)} 天"
    except Exception:
        return ""


def cmd_bootstrap(args: list[str]) -> None:
    agent = _resolve_agent(args)
    heal = startup_self_heal(agent)
    if heal["status"] == "recovered":
        print(f"🔄 记忆已从备份恢复 [{agent}]（来自 {heal.get('from', '?')}）", flush=True)
    elif heal["status"] == "corrupt_no_backup":
        print(f"⚠️ 记忆库损坏且无可用备份 [{agent}]", flush=True)
        return
    ok, msg = integrity_check(agent)
    if not ok:
        print(f"⚠️ 数据库异常: {msg} [{agent}]", flush=True)
        return
    c = conn(agent)
    total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    recent = c.execute(
        "SELECT section FROM entries WHERE section != '' ORDER BY id DESC LIMIT 3"
    ).fetchall()
    last = c.execute(
        "SELECT created_at FROM entries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    c.close()
    topics = [r["section"] for r in recent if r["section"]]
    topic_str = "、".join(topics) if topics else "（暂无分类）"
    gap = _format_time_gap(last["created_at"] if last else "")
    if gap:
        print(gap, flush=True)
        print("💡 建议：回顾近期记忆后再继续对话，以保持上下文连贯性。", flush=True)
    print(f"✅ heropen 已就绪 [{agent}] —— {total} 条记忆。最近涉及：{topic_str}", flush=True)


def cmd_init_all(args: list[str]) -> None:
    results = {}
    for agent in AGENTS:
        results[agent] = startup_self_heal(agent)
    healthy = sum(1 for r in results.values() if r["status"] == "healthy")
    recovered = sum(1 for r in results.values() if r["status"] == "recovered")
    missing = sum(1 for r in results.values() if r["status"] == "missing")
    failed = sum(1 for r in results.values() if r["status"] == "corrupt_no_backup")
    print(f"📊 heropen 启动自愈完成: 健康={healthy}, 已恢复={recovered}, 缺失={missing}, 失败={failed}")
    for agent, r in results.items():
        if r["status"] == "recovered":
            print(f"  🔄 {agent}: 已从 {r.get('from', '?')} 恢复")
        elif r["status"] == "corrupt_no_backup":
            print(f"  ❌ {agent}: 损坏且无备份")
        elif r["status"] == "missing":
            print(f"  ➖ {agent}: 数据库不存在（首次部署）")


# ─── CRUD commands ──────────────────────────────────────────────

def cmd_init(args: list[str]) -> None:
    agent = _resolve_agent(args)
    init_db(agent)
    print(f"✅ heropen 数据库已初始化 [agent: {agent}]")


def cmd_auto_setup(args: list[str]) -> None:
    """heropen auto-setup — init DB + auto-configure MCP for all detected agents."""
    from heropen.auto_mcp import auto_setup_mcp, print_setup_summary

    agent = _resolve_agent(args)

    print(f"🔧 heropen 自动配置中...\n")
    
    # Step 1: Init DB
    init_db(agent)
    print(f"✅ 数据库已初始化 [agent: {agent}]")
    
    # Step 2: Auto-inject MCP into detected agents
    print(f"🔍 扫描本机 agent 配置文件...")
    result = auto_setup_mcp(agent)
    print_setup_summary(result)


def cmd_sync(args: list[str]) -> None:
    agent = _resolve_agent(args)
    n = sync_to_db(agent)
    print(f"✅ 同步完成，新增 {n} 条记录")


def cmd_recall(args: list[str]) -> None:
    if _has_help(args):
        print("用法: heropen recall \"查询词\" [--agent 名称] [--limit 数量] [--fts] [--graph] [--date YYYY-MM-DD] [--tag 标签] [--last N] [--today]")
        return
    # Simple arg parsing for recall
    agent = "agent"
    query_parts: list[str] = []
    opts = {"fts": False, "graph": False, "date": None, "tag": None, "last": None, "today": False, "limit": 10}

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
            continue
        if a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--fts":
            opts["fts"] = True
        elif a == "--graph":
            opts["graph"] = True
        elif a == "--today":
            opts["today"] = True
        elif a == "--date" and i + 1 < len(args):
            opts["date"] = args[i + 1]
            i += 2
            continue
        elif a == "--tag" and i + 1 < len(args):
            opts["tag"] = args[i + 1]
            i += 2
            continue
        elif a == "--last" and i + 1 < len(args):
            try:
                opts["last"] = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        elif a == "--limit" and i + 1 < len(args):
            try:
                opts["limit"] = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        else:
            query_parts.append(a)
        i += 1

    query = " ".join(query_parts) if query_parts else ""

    if opts["date"]:
        results = search_by_date(opts["date"], opts["limit"], agent)
    elif opts["tag"]:
        results = search_by_tag(opts["tag"], opts["limit"], agent)
    elif opts["today"]:
        results = search_by_date(date.today().isoformat(), opts["limit"], agent)
    elif opts["last"] is not None:
        results = search_recent(opts["last"], agent)
    elif query:
        if opts["fts"]:
            results = search_fts(_tokenize(query), opts["limit"], agent)
        elif opts["graph"]:
            results = search_graph(query, opts["limit"], agent)
            if results is None:
                results = search_fts(_tokenize(query), opts["limit"], agent)
        else:
            results = search_vector(query, opts["limit"], agent)
            if results is None:
                results = search_graph(query, opts["limit"], agent)
            if results is None:
                results = search_fts(_tokenize(query), opts["limit"], agent)
    else:
        results = search_recent(opts["limit"], agent)

    format_recall(results)


def cmd_add(args: list[str]) -> None:
    if _has_help(args):
        print("用法: heropen add --content \"记忆内容\" [--section 分类] [--tags 标签] [--agent 名称]")
        return
    agent = "agent"
    section = ""
    content = ""
    tags = ""

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
            continue
        if a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--section" and i + 1 < len(args):
            section = args[i + 1]
            i += 2
            continue
        if a == "--content" and i + 1 < len(args):
            content = args[i + 1]
            i += 2
            continue
        if a == "--tags" and i + 1 < len(args):
            tags = args[i + 1]
            i += 2
            continue
        i += 1

    if not content:
        print("❌ --content is required")
        return

    today = date.today().isoformat()
    text = f"{section} {content} {tags}"
    emb = get_embedding(text)
    emb_bytes = json.dumps(emb).encode("utf-8") if emb else None
    entry_id = add_entry(today, content, section, tags, agent, "manual", emb_bytes)
    print(f"✅ 已添加记忆 [{entry_id}] [{tags}] {today}")
    if emb:
        print("   💡 embedding已生成，语义检索已就绪")
    else:
        print("   ⚠️ 本地向量引擎不可用，该记忆暂不支持语义检索。")
        print("      可运行 `heropen embed` 或设置 EMBEDDING_ENDPOINT 后重试。")


def cmd_capture(args: list[str]) -> None:
    agent = _resolve_agent(args)
    text = sys.stdin.read()
    if not text:
        print("❌ 没有输入内容（请通过管道传入）")
        return
    count = capture_session_content(text, agent)
    if count > 0:
        print(f"✅ 自动捕获完成：{count} 条关键句已存入 [{agent}]")
    else:
        print("ℹ️ 没有捕获到关键信息")


# ─── Status ─────────────────────────────────────────────────────

def cmd_status(args: list[str]) -> None:
    agent = _resolve_agent(args)
    c = conn(agent)
    total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    with_emb = c.execute("SELECT COUNT(*) FROM entries WHERE embedding IS NOT NULL").fetchone()[0]
    by_date = c.execute(
        "SELECT entry_date,COUNT(*) FROM entries GROUP BY entry_date ORDER BY entry_date DESC LIMIT 10"
    ).fetchall()
    by_tag = c.execute(
        "SELECT tags,COUNT(*) FROM entries GROUP BY tags ORDER BY COUNT(*) DESC LIMIT 10"
    ).fetchall()
    by_source = c.execute("SELECT source,COUNT(*) FROM entries GROUP BY source").fetchall()
    ent_count = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    rel_count = c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
    c.close()

    print(f"📊 heropen 数据库 [{agent}] 共{total}条 (有embedding: {with_emb}/{total})")
    print(f"   🧠 知识图谱: {ent_count} 实体, {rel_count} 关系")
    print(f"\n   最近日期:")
    for d, n in by_date:
        print(f"     {d}: {n}条")
    print(f"\n   标签分布:")
    for t, n in by_tag:
        print(f"     [{t}]: {n}条")
    print(f"\n   来源分布:")
    for s, n in by_source:
        print(f"     {s}: {n}条")


def cmd_entities(args: list[str]) -> None:
    agent = _resolve_agent(args)
    c = conn(agent)
    ents = c.execute(
        "SELECT name, entity_type, description, count FROM entities ORDER BY count DESC LIMIT 50"
    ).fetchall()
    rels = c.execute(
        "SELECT entity_a, entity_b, strength FROM relations ORDER BY strength DESC LIMIT 30"
    ).fetchall()
    c.close()

    if not ents:
        print("📭 知识图谱为空")
        return

    print(f"🧠 实体 ({len(ents)} 个):")
    for e in ents[:20]:
        print(f"   [{e['entity_type']}] {e['name']} ({e['count']}次)")
    if len(ents) > 20:
        print(f"   ... 还有 {len(ents) - 20} 个")
    print(f"\n🔗 最强关系 (Top 10):")
    for r in rels[:10]:
        bar = "█" * min(int(r["strength"]), 20)
        print(f"   {r['entity_a']} ═══ {r['entity_b']}  {bar} ({r['strength']:.1f})")


# ─── Export / Import / Delete / Embed ───────────────────────────

def cmd_export(args: list[str]) -> None:
    agent = _resolve_agent(args)
    out_path = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--output", "-o") and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
            continue
        if a.startswith("--output="):
            out_path = a.split("=", 1)[1]
        i += 1

    out_path = out_path or os.path.join(HERO_PEN_DIR, f"export-{date.today()}.json")
    os.makedirs(os.path.dirname(out_path) or HERO_PEN_DIR, exist_ok=True)

    c = conn(agent)
    rows = [
        dict(r)
        for r in c.execute(
            "SELECT id, entry_date, section, content, tags, source, agent, created_at FROM entries ORDER BY id"
        ).fetchall()
    ]
    entities = [dict(r) for r in c.execute("SELECT * FROM entities ORDER BY count DESC").fetchall()]
    relations = [
        dict(r) for r in c.execute("SELECT * FROM relations ORDER BY strength DESC").fetchall()
    ]
    c.close()

    backup = {
        "version": 1.1,
        "agent": agent,
        "exported_at": datetime.now().isoformat(),
        "total_entries": len(rows),
        "entities": entities,
        "relations": relations,
        "entries": rows,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False, indent=2)
    print(f"✅ 已导出 {len(rows)} 条记忆 + {len(entities)} 实体 + {len(relations)} 关系到: {out_path}")


def cmd_import(args: list[str]) -> None:
    agent = "agent"
    file_path = ""

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
            continue
        if a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            i += 1
            continue
        if not a.startswith("-"):
            file_path = a
        i += 1

    if not file_path:
        print("❌ 需要指定导入文件路径")
        return

    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", [])
    if not entries:
        print("❌ 备份文件中没有条目")
        return

    c = conn(agent)
    existing = set()
    for r in c.execute("SELECT substr(content,1,100) as prefix FROM entries"):
        existing.add(r[0])

    count = 0
    for e in entries:
        content = e.get("content", "")
        if content[:100] in existing:
            continue
        text = f"{e.get('section', '')} {content} {e.get('tags', '')}"
        emb = get_embedding(text)
        emb_bytes = json.dumps(emb).encode("utf-8") if emb else None
        c.execute(
            "INSERT INTO entries (entry_date, section, content, tags, source, agent, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (e.get("entry_date", date.today().isoformat()), e.get("section", ""), content,
             e.get("tags", ""), f"import:{data.get('exported_at', 'unknown')}", agent, emb_bytes,
             e.get("created_at", datetime.now().isoformat())),
        )
        count += 1
    c.commit()

    for ent in data.get("entities", []):
        c.execute(
            "INSERT INTO entities (name, entity_type, description, count) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET count = count + excluded.count",
            (ent["name"], ent.get("entity_type", "other"), ent.get("description", ""), ent.get("count", 1)),
        )
    for rel in data.get("relations", []):
        a_name, b_name = rel["entity_a"], rel["entity_b"]
        if a_name > b_name:
            a_name, b_name = b_name, a_name
        c.execute(
            "INSERT INTO relations (entity_a, entity_b, strength) VALUES (?, ?, ?) "
            "ON CONFLICT(entity_a, entity_b) DO UPDATE SET strength = strength + excluded.strength",
            (a_name, b_name, rel.get("strength", 1.0)),
        )
    c.commit()
    c.close()
    print(f"✅ 已导入 {count} 条新记录 [{agent}]")


def cmd_delete(args: list[str]) -> None:
    agent = "agent"
    entry_id: int | None = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
            continue
        if a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            i += 1
            continue
        if not a.startswith("-"):
            try:
                entry_id = int(a)
            except ValueError:
                pass
        i += 1

    if entry_id is None:
        print("❌ 需要指定条目 ID")
        return

    c = conn(agent)
    row = c.execute(
        "SELECT id, entry_date, section, substr(content,1,80) as preview FROM entries WHERE id=?",
        (entry_id,),
    ).fetchone()
    if not row:
        print(f"❌ 未找到 ID={entry_id}")
        c.close()
        return
    print(f"即将删除 [{row['id']}] {row['entry_date']} | {row['section']}")
    print(f"  {row['preview']}")
    ans = input("确认删除？[y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        print("已取消。")
        c.close()
        return
    c.execute("DELETE FROM entries WHERE id=?", (entry_id,))
    c.commit()
    c.close()
    print(f"✅ 已删除 [{entry_id}]")


def cmd_embed(args: list[str]) -> None:
    agent = "agent"
    force = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
            continue
        if a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            i += 1
            continue
        if a in ("--force", "-f"):
            force = True
        i += 1

    c = conn(agent)
    if force:
        cur = c.execute("SELECT id, content, tags, section FROM entries")
    else:
        cur = c.execute("SELECT id, content, tags, section FROM entries WHERE embedding IS NULL")
    rows = cur.fetchall()
    c.close()

    if not rows:
        print("✅ 所有记录已有embedding，无需生成")
        return

    print(f"🔄 正在为 {len(rows)} 条记录生成embedding...")
    count = 0
    for row in rows:
        text = f"{row['section']} {row['content']} {row['tags']}"
        emb = get_embedding(text)
        if emb:
            emb_bytes = json.dumps(emb).encode("utf-8")
            cc = conn(agent)
            cc.execute("UPDATE entries SET embedding=? WHERE id=?", (emb_bytes, row["id"]))
            cc.commit()
            cc.close()
            count += 1
            if count % 5 == 0:
                print(f"  ...已处理 {count}/{len(rows)}")
        else:
            print(f"  ⚠️ 跳过 [{row['id']}] (embedding失败)")
    print(f"✅ 完成：为 {count} 条记录生成了embedding")


# ─── Session Checkpoint & Recovery ──────────────────────────────

def cmd_session(args: list[str]) -> None:
    """heropen session check --context "..."
       heropen session recover [--limit 3]
    """
    agent = "agent"
    action = ""
    context_summary = ""
    active_task = ""
    key_decisions = ""
    limit = 1

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
            continue
        if a.startswith("--agent="):
            agent = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--context" and i + 1 < len(args):
            context_summary = args[i + 1]
            i += 2
            continue
        if a == "--task" and i + 1 < len(args):
            active_task = args[i + 1]
            i += 2
            continue
        if a == "--decisions" and i + 1 < len(args):
            key_decisions = args[i + 1]
            i += 2
            continue
        if a == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                pass
            i += 2
            continue
        if a in ("check", "save"):
            action = "check"
        elif a in ("recover", "get", "load"):
            action = "recover"
        i += 1

    if action == "recover":
        from heropen.core import session_recover
        results = session_recover(agent=agent, limit=limit)
        if not results:
            print(f"📭 没有找到会话断点 [{agent}]")
            return
        print(f"📋 恢复 {len(results)} 个会话断点 [{agent}]:\n")
        for r in results:
            data = r.get("checkpoint_data", {})
            ts = data.get("timestamp", r.get("created_at", "?"))
            ctx = data.get("context", "")
            task = data.get("active_task", "")
            decisions = data.get("key_decisions", [])
            print(f"  ⏱  {ts}")
            if ctx:
                print(f"     📝 上下文: {ctx}")
            if task:
                print(f"     🎯 当前任务: {task}")
            if decisions:
                print(f"     🔑 关键决策: {'; '.join(decisions)}")
            print()
    elif action == "check":
        from heropen.core import session_checkpoint
        decisions_list = [d.strip() for d in key_decisions.split(",") if d.strip()] if key_decisions else []
        eid = session_checkpoint(
            agent=agent,
            context_summary=context_summary,
            active_task=active_task,
            key_decisions=decisions_list
        )
        if eid:
            print(f"✅ 会话断点已保存 [{agent}] (ID={eid})")
        else:
            print("❌ 保存失败")
    else:
        print("用法:")
        print("  heropen session check   --context \"...\" --task \"...\" [--decisions \"a,b,c\"] [--agent agent]")
        print("  heropen session recover [--limit 3] [--agent agent]")


# ─── Diagnostics ──────────────────────────────────────────────────

def cmd_diagnose(args: list[str]) -> None:
    """heropen diagnose — run system diagnostics.

    Checks config, database, connectivity, version, and pending setup.
    Supports --test-agent to simulate a memory search, and --clear-pending
    to clear the pending_setup marker file.
    """
    test_agent = False
    clear_pending = False

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--test-agent":
            test_agent = True
        elif a == "--clear-pending":
            clear_pending = True
        i += 1

    # ANSI color helpers
    _G = "\033[92m"  # green
    _R = "\033[91m"  # red
    _Y = "\033[93m"  # yellow
    _B = "\033[94m"  # blue
    _N = "\033[0m"   # reset
    _BOLD = "\033[1m"

    def _ok(msg: str) -> None:
        print(f"  {_G}✅{_N} {msg}")

    def _fail(msg: str) -> None:
        print(f"  {_R}❌{_N} {msg}")

    def _warn(msg: str) -> None:
        print(f"  {_Y}⚠️{_N} {msg}")

    print(f"\n{_BOLD}🔍 heropen 系统诊断{_N}\n")
    print(f"{_B}── 配置检查 ──{_N}")

    # ── 1. MCP config ────────────────────────────────────────────
    from pathlib import Path
    import json as _json
    config_path = Path.home() / ".heropen" / "agent-config.json"
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as _f:
                cfg = _json.load(_f)
            agents_list = cfg.get("agents", [])
            agent_names = [a["name"] for a in agents_list if "name" in a]
            _ok(f"agent-config.json 存在且有效 — {len(agent_names)} 个 agent: {', '.join(agent_names)}")
        except (_json.JSONDecodeError, KeyError, Exception) as e:
            _fail(f"agent-config.json 格式无效: {e}")
            return
    else:
        _fail("agent-config.json 不存在")
        _warn("请先运行 heropen install 或 heropen auto-setup")
        return

    # ── 2. Database connection ───────────────────────────────────
    print(f"\n{_B}── 数据库连接 ──{_N}")
    from heropen.core import AGENTS, conn as _conn, db_path
    import os as _os

    db_ok = False
    for agent_name in list(AGENTS.keys()):
        dbp = db_path(agent_name)
        if not _os.path.exists(dbp):
            _fail(f"[{agent_name}] 数据库文件不存在: {dbp}")
            continue
        try:
            c = _conn(agent_name)
            total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            c.close()
            _ok(f"[{agent_name}] {total} 条记录 — {dbp}")
            db_ok = True
        except Exception as e:
            _fail(f"[{agent_name}] 连接失败: {e}")

    if not db_ok:
        _warn("没有任何数据库可用 — 请运行 heropen init <agent>")

    # ── 3. Tool connectivity (MCP server module check) ─────────────
    print(f"\n{_B}── 工具连通性 ──{_N}")
    try:
        # Just check that the module can be imported and parsed
        import importlib as _il
        _il.import_module("heropen.mcp_server")
        _ok("MCP server 模块加载正常 — heropen.mcp_server 可导入")
    except Exception as e:
        _fail(f"MCP server 模块加载失败: {e}")
        _warn("通常意味着缺少依赖 (mcp 包)。运行: pip install mcp")

    # ── 4. Version check ─────────────────────────────────────────
    print(f"\n{_B}── 版本检查 ──{_N}")
    current = __version__
    print(f"    当前版本: {current}")
    try:
        import urllib.request as _ur
        import json as _json
        # Use PyPI JSON API with a timeout
        req = _ur.Request(
            "https://pypi.org/pypi/heropen/json",
            headers={"User-Agent": "heropen-diagnose/1.0"},
        )
        with _ur.urlopen(req, timeout=5) as resp:
            pypi_data = _json.loads(resp.read().decode("utf-8"))
        latest = pypi_data["info"]["version"]
        if latest == current:
            _ok(f"heropen {current} — 已是最新版本")
        else:
            # Compare version tuples
            def _parse_ver(v: str) -> tuple:
                try:
                    return tuple(int(x) for x in v.split("."))
                except Exception:
                    return (0,)
            if _parse_ver(current) > _parse_ver(latest):
                _ok(f"PyPI: {latest} | 本地: {current} — 本地版本更新于 PyPI")
            else:
                _warn(f"PyPI: {latest} | 本地: {current} — 有可用更新。运行: pip install --upgrade heropen")
    except Exception as e:
        _warn(f"无法检查 PyPI 版本: {e}")

    # ── 5. Pending setup marker ──────────────────────────────────
    print(f"\n{_B}── 待办标记 ──{_N}")
    from heropen.mcp_server import _check_pending_setup
    pending = _check_pending_setup(clear=clear_pending)

    if pending.get("pending_setup"):
        installed_at = pending.get("installed_at", "")
        version = pending.get("version", "")
        agent_done = pending.get("agent_completed", False)
        _warn("pending_setup 标记存在")
        if installed_at:
            print(f"    安装时间: {installed_at}")
        if version:
            print(f"    安装版本: {version}")
        if not agent_done:
            print(f"    agent 配置: 未完成")
        if clear_pending:
            _ok("已清除 pending_setup 标记")
        else:
            print(f"    提示: 使用 --clear-pending 可清除此标记")
    else:
        _ok("无 pending_setup 标记")

    # ── 6. --test-agent: simulate memory search ──────────────────
    if test_agent:
        print(f"\n{_B}── Agent 模拟测试 --test-agent ──{_N}")
        try:
            from heropen.core import search_recent, get_default_agent
            agent = get_default_agent()
            results = search_recent(3)
            if results:
                _ok(f"[{agent}] 最近3条记忆查询成功 ({len(results)} 条)")
                for r in results[:3]:
                    sec = r.get("section", "") or ""
                    content = (r.get("content", "") or "")[:60]
                    print(f"      [{sec}] {content}")
            else:
                _warn(f"[{agent}] 数据库为空，无记忆可查")
        except Exception as e:
            _fail(f"记忆搜索测试失败: {e}")

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{_BOLD}诊断完成{_N}")


def cmd_doctor(args: list[str]) -> None:
    """heropen doctor — 工程税自检（映射 AWS 五大工程税）。

    只读本地数据库与环境变量，零联网、零外部副作用、失败静默。
    五项自检，每项输出「通过 / 提示 / 告警」三态：
      1. 写入纪律与失效
      2. Prompt Cache 冲突
      3. 跨模型容量上限
      4. Embedding 迁移数据税
      5. 端口安全
    """
    # 解析 --agent
    agent = "agent"
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--agent",) and i + 1 < len(args):
            agent = args[i + 1]
        elif a.startswith("--agent="):
            agent = a.split("=", 1)[1]
        i += 1

    # ANSI 三态
    _G = "\033[92m"; _R = "\033[91m"; _Y = "\033[93m"; _B = "\033[94m"; _N = "\033[0m"; _BOLD = "\033[1m"
    def _ok(m): print(f"  {_G}✅{_N} {m}")
    def _warn(m): print(f"  {_Y}⚠️{_N} {m}")
    def _fail(m): print(f"  {_R}❌{_N} {m}")

    print(f"\n{_BOLD}🩺 heropen 工程税自检{_N}\n")

    from heropen.core import AGENTS, conn as _conn, db_path as _db_path, HERO_PEN_DIR
    import os as _os
    from datetime import datetime
    agents = list(AGENTS.keys()) if AGENTS else [agent]
    if agent not in agents and agent != "agent":
        agents = [agent]

    total_entries = 0
    total_chars = 0
    stale_count = 0
    latest_dt = None
    for ag in agents:
        dbp = _db_path(ag)
        if not _os.path.exists(dbp):
            continue
        try:
            c = _conn(ag)
            rows = c.execute("SELECT id, content, created_at FROM entries").fetchall()
            c.close()
            for r in rows:
                total_entries += 1
                total_chars += len(r[1] or "")
                try:
                    dt = datetime.fromisoformat(r[2])
                    if latest_dt is None or dt > latest_dt:
                        latest_dt = dt
                    if (datetime.now() - dt).days > 365:
                        stale_count += 1
                except Exception:
                    pass
        except Exception:
            pass

    # ── 1. 写入纪律与失效 ──
    print(f"{_B}── 1. 写入纪律与失效 ──{_N}")
    if total_entries == 0:
        _warn("暂无记忆条目 — 无法评估写入纪律；先 add 几条记忆")
    else:
        if stale_count > 0:
            ratio = stale_count / total_entries
            if ratio > 0.3:
                _warn(f"{total_entries} 条中 {stale_count} 条超过 365 天未更新（占比 {ratio:.0%}）— 建议运行记忆卫生复查")
            else:
                _ok(f"{total_entries} 条记忆，{stale_count} 条陈旧（<30%）— 写入纪律良好")
        else:
            _ok(f"{total_entries} 条记忆，无超过 365 天的陈旧条目")
        if latest_dt:
            days = (datetime.now() - latest_dt).days
            if days > 30:
                _warn(f"最近一次写入在 {days} 天前 — 记忆可能已偏离当前上下文")
            else:
                _ok(f"最近写入 {days} 天前 — 记忆较新鲜")

    # ── 2. Prompt Cache 冲突 ──
    print(f"\n{_B}── 2. Prompt Cache 冲突 ──{_N}")
    BUDGET = 4000
    if total_entries == 0:
        _warn("无记忆数据 — 跳过 Prompt Cache 评估")
    else:
        avg = total_chars / total_entries
        if avg > BUDGET:
            _warn(f"平均每条记忆 {avg:.0f} 字符（> {BUDGET} 建议预算）— 过长记忆会稀释 Prompt Cache 命中；建议裁剪单条长度")
        else:
            _ok(f"平均每条记忆 {avg:.0f} 字符（≤ {BUDGET} 预算）— 对 Prompt Cache 友好")

    # ── 3. 跨模型容量上限 ──
    print(f"\n{_B}── 3. 跨模型容量上限 ──{_N}")
    CAPS = [("8K", 8000 * 4), ("32K", 32000 * 4), ("128K", 128000 * 4)]
    if total_chars == 0:
        _warn("无记忆数据 — 跳过容量评估")
    else:
        exceeded = [name for name, cap in CAPS if total_chars > cap]
        if not exceeded:
            _ok(f"记忆总量 {total_chars:,} 字符 — 在 8K/32K/128K 模型容量内")
        else:
            _warn(f"记忆总量 {total_chars:,} 字符 — 超出 {', '.join(exceeded)} 模型容量上限；跨模型切换时需注意上下文截断")

    # ── 4. Embedding 迁移数据税 ──
    print(f"\n{_B}── 4. Embedding 迁移数据税 ──{_N}")
    ep = _os.environ.get("EMBEDDING_ENDPOINT")
    if not ep:
        _ok("未配置 EMBEDDING_ENDPOINT — 使用本地 fastembed，无迁移数据税")
    else:
        if total_entries == 0:
            _ok(f"已配置自托管 endpoint（{ep}）— 当前无记忆，切换无成本")
        else:
            _warn(f"已配置自托管 endpoint（{ep}）— 若切换端点，需对 {total_entries} 条记忆重新嵌入（数据税）；迁移前请评估收益")

    # ── 5. 端口安全 ──
    print(f"\n{_B}── 5. 端口安全 ──{_N}")
    _warn("若以 `heropen mcp --http` 启动，MCP 服务默认绑定 0.0.0.0:8090（暴露到局域网/公网）；仅本机使用请加防火墙或绑定 127.0.0.1")
    _ok("viewer（heropen viewer）硬编码仅绑定 loopback（127.0.0.1）— 已满足 C2 安全约束")

    print(f"\n{_BOLD}自检完成{_N}")
    print(f"  记忆目录: {HERO_PEN_DIR}")

