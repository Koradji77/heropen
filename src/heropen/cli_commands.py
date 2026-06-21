"""
heropen.cli_commands — Command implementations for heropen CLI.

Each function takes a list of string args (like sys.argv[1:]).
Lazy-imported by heropen.cli for fast startup.
"""

from __future__ import annotations

import argparse
import json
import os
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
    p.add_argument("--agent", default=None, help="Agent name (default: xiaoman)")
    return p


def _resolve_agent(args: list[str]) -> str:
    """Extract --agent from args list. Returns the agent name or empty for default."""
    for i, a in enumerate(args):
        if a in ("--agent",) and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--agent="):
            return a.split("=", 1)[1]
    return "xiaoman"


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
    print(f"✅ HeroPen 已就绪 [{agent}] —— {total} 条记忆。最近涉及：{topic_str}", flush=True)


def cmd_init_all(args: list[str]) -> None:
    results = {}
    for agent in AGENTS:
        results[agent] = startup_self_heal(agent)
    healthy = sum(1 for r in results.values() if r["status"] == "healthy")
    recovered = sum(1 for r in results.values() if r["status"] == "recovered")
    missing = sum(1 for r in results.values() if r["status"] == "missing")
    failed = sum(1 for r in results.values() if r["status"] == "corrupt_no_backup")
    print(f"📊 HeroPen 启动自愈完成: 健康={healthy}, 已恢复={recovered}, 缺失={missing}, 失败={failed}")
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
    print(f"✅ HeroPen 数据库已初始化 [agent: {agent}]")


def cmd_sync(args: list[str]) -> None:
    agent = _resolve_agent(args)
    n = sync_to_db(agent)
    print(f"✅ 同步完成，新增 {n} 条记录")


def cmd_recall(args: list[str]) -> None:
    # Simple arg parsing for recall
    agent = "xiaoman"
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
            results = search_fts([query], opts["limit"], agent)
        elif opts["graph"]:
            results = search_graph(query, opts["limit"], agent)
            if results is None:
                results = search_fts([query], opts["limit"], agent)
        else:
            results = search_vector(query, opts["limit"], agent)
            if results is None:
                results = search_graph(query, opts["limit"], agent)
            if results is None:
                results = search_fts([query], opts["limit"], agent)
    else:
        results = search_recent(opts["limit"], agent)

    format_recall(results)


def cmd_add(args: list[str]) -> None:
    agent = "xiaoman"
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
        print("   💡 embedding已生成，知识图谱已更新")
    else:
        print("   💡 知识图谱已更新")


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

    print(f"📊 HeroPen 数据库 [{agent}] 共{total}条 (有embedding: {with_emb}/{total})")
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
    agent = "xiaoman"
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
    agent = "xiaoman"
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
    agent = "xiaoman"
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
    """heropen session check --context "..." --task "..." --decisions "a,b,c"
       heropen session recover [--limit 3]
    """
    agent = "xiaoman"
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
        print("  heropen session check   --context \"...\" --task \"...\" [--decisions \"a,b,c\"] [--agent xiaoman]")
        print("  heropen session recover [--limit 3] [--agent xiaoman]")
