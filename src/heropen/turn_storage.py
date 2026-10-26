"""按完整话轮持久化；不读取、迁移或删除旧 JSON 缓冲。

每个 agent 的 (conversation_id, turn_id) 唯一；空 conversation_id 是独立的
默认会话，flush(agent) 只刷新它，不代表刷新全部会话。调用方须在完整回复后
调用 save_turn，并在重试时复用 turn_id。省略 ID 会生成 UUID，内容相同不去重。

返回值：received_turns/pending_turns 是当前会话累计接收/待存轮数；saved 和
parts 是本次新建 entries 数，flushed 是本次关联到 entries 的轮数，buffered
表示当前轮仍待存（flush 时表示该会话仍有待存）。duplicate 不会重新写入，
entry_id 可用于确认该轮当前关联。所有计数均在同一事务快照中计算。

批量按连续相同的日期、section、原始 tags 分组，每条最多十轮，不截断内容。
直接 INSERT entries 以保留现有 FTS 触发器；embedding 和图谱不实时生成。
新库的 core schema 初始化沿用 core.conn；多进程首次使用前应先 core.init_db。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from . import core

SOURCE = "auto_turn"
MAX_TURN_CHARS = 1_000_000
_IMPORTANT = re.compile(
    r"决定|记住|记得|约定|修复完成|修复成功|已修复|问题已解决|定下|最终方案"
    r"|\b(?:remember|decided|decision|fixed|resolved)\b",
    re.IGNORECASE,
)
# 不从每个字符起贪婪扫描整段单词，避免百万字符长文触发平方级回溯。
_MEDIUM = re.compile(
    r"\d|[/\\]|\.(?:py|js|ts|json|md|sql|sh|yaml|yml)\b"
    r"|`|\b(?:def |class |function |return |SELECT |INSERT )|[{}()]",
    re.IGNORECASE,
)


def _agent(agent):
    if agent is None or agent == "":
        agent = core.get_default_agent()
    if not isinstance(agent, str) or not re.fullmatch(r"[\w.-]+", agent) or agent in (".", ".."):
        raise ValueError("agent 必须是无路径分隔符的名称，禁止路径穿越")
    root = Path(core.HERO_PEN_DIR).resolve()
    if Path(core.db_path(agent)).resolve().parent != root:
        raise ValueError("agent 数据库不得通过路径或符号链接指向存储目录外")
    return agent


def _strings(**values):
    for name, value in values.items():
        if not isinstance(value, str):
            raise TypeError(f"{name} 必须是字符串")


def _mode(agent):
    mode = core.get_storage_mode(agent)
    if mode not in ("auto", "ten_turns", "every_turn"):
        raise ValueError(f"未知存储模式：{mode}")
    return mode


@contextmanager
def _transaction(agent):
    c = core.conn(agent)
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("BEGIN IMMEDIATE")
        # 不用 executescript：它可能隐式提交，破坏接收、写入和关联的原子性。
        c.execute("""
            CREATE TABLE IF NOT EXISTS turn_receipts (
                id INTEGER PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                user TEXT NOT NULL,
                assistant TEXT NOT NULL,
                date TEXT NOT NULL,
                section TEXT NOT NULL,
                tags TEXT NOT NULL,
                entry_id INTEGER REFERENCES entries(id),
                status TEXT NOT NULL CHECK (status IN ('pending', 'saved')),
                metadata TEXT NOT NULL,
                received_at TEXT NOT NULL,
                saved_at TEXT,
                UNIQUE (conversation_id, turn_id),
                CHECK ((status = 'pending' AND entry_id IS NULL AND saved_at IS NULL)
                    OR (status = 'saved' AND entry_id IS NOT NULL AND saved_at IS NOT NULL))
            )
        """)
        c.execute("""CREATE INDEX IF NOT EXISTS turn_receipts_pending
                     ON turn_receipts (conversation_id, status, id)""")
        yield c
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _counts(c, conversation_id):
    row = c.execute(
        """SELECT COUNT(*) AS received_turns,
                  COALESCE(SUM(status = 'pending'), 0) AS pending_turns,
                  MAX(received_at) AS last_received_at
           FROM turn_receipts WHERE conversation_id = ?""", (conversation_id,)
    ).fetchone()
    return dict(row)


def _result(configured, mode, reason, counts, **extra):
    result = {
        "configured_mode": configured, "mode": mode, "reason": reason,
        "received_turns": counts.get("received_turns", 0),
        "pending_turns": counts.get("pending_turns", 0),
        "last_received_at": counts.get("last_received_at"),
        "saved": 0, "buffered": False, "flushed": 0, "parts": 0,
        "source": SOURCE, "duplicate": False, "entry_ids": [],
        "embedding": "deferred", "graph": "deferred",
    }
    result.update(extra)
    return result


def _pending(c, conversation_id):
    return c.execute(
        """SELECT * FROM turn_receipts
           WHERE conversation_id = ? AND status = 'pending' ORDER BY id""",
        (conversation_id,),
    ).fetchall()


def _decision(configured, rows):
    if configured == "every_turn":
        return True, "every_turn", "每轮完成立即保存，并保存本会话已有待存轮"
    if configured == "auto":
        metadata = [json.loads(row["metadata"]) for row in rows]
        important = next((m["important"] for m in metadata if m["important"]), "")
        if important:
            return True, "every_turn", f"命中重要内容「{important}」，立即保存本会话全部待存轮"
        chars = sum(m["chars"] for m in metadata)
        medium = sum(m["medium"] for m in metadata)
        if chars >= 6000:
            return True, "ten_turns", f"本会话待存内容累计 {chars} 字符，达到 6000 字符"
        if medium >= 4:
            return True, "ten_turns", f"本会话累计 {medium} 轮中等信息（数字/路径/代码），达到 4 轮"
    if len(rows) >= 10:
        return True, "ten_turns", f"本会话累计 {len(rows)} 轮，达到 10 轮"
    return False, "ten_turns", f"本会话待存 {len(rows)} 轮，尚未达到保存条件"


def _write_entries(c, agent, rows, reason, per_turn=False):
    groups = []
    for row in rows:
        key = (row["date"], row["section"], row["tags"])
        if per_turn or not groups or groups[-1][0] != key or len(groups[-1][1]) == 10:
            groups.append((key, []))
        groups[-1][1].append(row)
    entry_ids = []
    for (entry_date, section, tags), group in groups:
        text = "\n\n".join(
            f"第 {index} 轮\n用户：{row['user']}\n助手：{row['assistant']}"
            for index, row in enumerate(group, 1)
        )
        cursor = c.execute(
            """INSERT INTO entries (entry_date, section, content, tags, source, agent)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_date, section, text, tags, SOURCE, agent),
        )
        entry_id = cursor.lastrowid
        entry_ids.append(entry_id)
        saved_at = _now()
        for row in group:
            metadata = json.loads(row["metadata"])
            metadata["save_reason"] = reason
            c.execute(
                """UPDATE turn_receipts SET entry_id = ?, status = 'saved',
                          saved_at = ?, metadata = ? WHERE id = ? AND status = 'pending'""",
                (entry_id, saved_at, json.dumps(metadata, ensure_ascii=False), row["id"]),
            )
    return entry_ids


def save_turn(agent, content, user_content='', section='', tags='', entry_date='',
              conversation_id='', turn_id=''):
    """接收完整的一轮；总用户+助手字符超过 1,000,000 时拒绝，不截断。

    同 ID 同请求返回 duplicate；同 ID 不同正文或标签等元数据拒绝，避免静默丢失。
    重试时 entry_date 留空沿用原轮日期，因此跨午夜重试仍幂等。
    """
    agent = _agent(agent)
    _strings(content=content, user_content=user_content, section=section, tags=tags,
             entry_date=entry_date, conversation_id=conversation_id, turn_id=turn_id)
    configured = _mode(agent)
    generated = not turn_id
    turn_id = turn_id or uuid4().hex
    identity = {
        "conversation_id": conversation_id, "turn_id": turn_id,
        "generated_id": generated, "idempotency_warning": (
            "未提供 turn_id，已生成 ID；重试必须复用返回的 turn_id，无 ID 重试无法幂等"
            if generated else ""
        ),
    }
    if not content.strip():
        counts = status(agent)["conversations"].get(conversation_id, {})
        return _result(configured, configured, "助手回复为空，不计为完整轮", counts,
                       skipped=True, entry_id=None, **identity)
    if len(content) + len(user_content) > MAX_TURN_CHARS:
        raise ValueError("单轮用户与助手内容合计超过 1,000,000 字符，拒绝保存且不截断")
    with _transaction(agent) as c:
        existing = c.execute(
            "SELECT * FROM turn_receipts WHERE conversation_id = ? AND turn_id = ?",
            (conversation_id, turn_id),
        ).fetchone()
        if existing:
            expected = (user_content, content, entry_date or existing["date"], section, tags)
            actual = tuple(existing[key] for key in ("user", "assistant", "date", "section", "tags"))
            if expected != actual:
                raise ValueError("同一 conversation_id/turn_id 已存在，但内容或元数据不同")
            metadata = json.loads(existing["metadata"])
            return _result(
                configured, metadata["mode"], "重复 turn_id，未重复接收或写入 entries",
                _counts(c, conversation_id), duplicate=True,
                buffered=existing["status"] == "pending", entry_id=existing["entry_id"],
                **identity,
            )
        text = user_content + "\n" + content
        important = _IMPORTANT.search(text)
        metadata = {
            "configured_mode": configured, "mode": configured,
            "chars": len(user_content) + len(content),
            "medium": bool(_MEDIUM.search(text)),
            "important": important.group(0) if important else "",
            "generated_id": generated,
        }
        cursor = c.execute(
            """INSERT INTO turn_receipts
               (conversation_id, turn_id, user, assistant, date, section, tags,
                status, metadata, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (conversation_id, turn_id, user_content, content, entry_date or date.today().isoformat(),
             section, tags, json.dumps(metadata, ensure_ascii=False), _now()),
        )
        receipt_id = cursor.lastrowid
        rows = _pending(c, conversation_id)
        should_save, mode, reason = _decision(configured, rows)
        metadata.update(mode=mode, reason=reason)
        c.execute("UPDATE turn_receipts SET metadata = ? WHERE id = ?",
                  (json.dumps(metadata, ensure_ascii=False), receipt_id))
        # 刷新当前轮的 metadata，避免后续关联更新覆盖刚记录的判定。
        rows = _pending(c, conversation_id)
        entry_ids = _write_entries(c, agent, rows, reason, configured == "every_turn") if should_save else []
        entry_id = c.execute("SELECT entry_id FROM turn_receipts WHERE id = ?", (receipt_id,)).fetchone()[0]
        return _result(
            configured, mode, reason, _counts(c, conversation_id),
            saved=len(entry_ids), parts=len(entry_ids), flushed=len(rows) if should_save else 0,
            buffered=not should_save, entry_ids=entry_ids, entry_id=entry_id, **identity,
        )


def flush(agent, conversation_id=''):
    """原子保存指定会话的全部待存轮；不修改其他会话，不触碰旧 JSON。"""
    agent = _agent(agent)
    _strings(conversation_id=conversation_id)
    configured = _mode(agent)
    with _transaction(agent) as c:
        rows = _pending(c, conversation_id)
        reason = "显式保存本会话全部待存轮" if rows else "本会话没有待存轮"
        ids = _write_entries(c, agent, rows, reason, configured == "every_turn")
        return _result(
            configured, configured, reason, _counts(c, conversation_id),
            conversation_id=conversation_id, saved=len(ids), parts=len(ids),
            flushed=len(rows), entry_ids=ids,
        )


def status(agent):
    """只读快照，不调用 core.conn、不初始化 schema、不创建不存在的数据库。"""
    agent = _agent(agent)
    configured = _mode(agent)
    result = _result(configured, configured, "只读轮存储状态", {},
                     agent=agent, conversations={}, saved_turns=0, database_exists=False)
    path = Path(core.db_path(agent)).absolute()
    if not path.exists():
        return result
    result["database_exists"] = True
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_receipts'").fetchone():
            return result
        for row in c.execute(
            """SELECT conversation_id, COUNT(*) AS received_turns,
                      SUM(status = 'pending') AS pending_turns,
                      SUM(status = 'saved') AS saved_turns,
                      MAX(received_at) AS last_received_at
               FROM turn_receipts GROUP BY conversation_id ORDER BY conversation_id"""
        ):
            counts = dict(row)
            conversation_id = counts.pop("conversation_id")
            result["conversations"][conversation_id] = counts
            for key in ("received_turns", "pending_turns", "saved_turns"):
                result[key] += counts[key]
            if result["last_received_at"] is None or counts["last_received_at"] > result["last_received_at"]:
                result["last_received_at"] = counts["last_received_at"]
        result["buffered"] = result["pending_turns"] > 0
        return result
    finally:
        c.close()


def turn_digest(user: str, assistant: str) -> str:
    """校验用摘要：只比对内容是否一致，不能从中还原正文。

    采集端用它判断“已知 ID 的正文是否被改过”。不用原文比对是为了避免把
    整库正文读进内存；SHA-256 足以发现任何差异。
    """
    import hashlib

    return hashlib.sha256(
        (user + "\x00" + assistant).encode("utf-8", "surrogatepass")
    ).hexdigest()


def stored_turn_digests(agent) -> dict:
    """只读返回 {(conversation_id, turn_id): 内容摘要}，供采集端做一致性校验。

    不创建数据库、不初始化 schema；数据库不存在时返回空字典。
    """
    agent = _agent(agent)
    path = Path(core.db_path(agent)).absolute()
    if not path.exists():
        return {}
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='turn_receipts'").fetchone():
            return {}
        result = {}
        for conversation_id, turn_id, user, assistant in c.execute(
                "SELECT conversation_id, turn_id, user, assistant FROM turn_receipts"):
            result[(conversation_id, turn_id)] = turn_digest(user or "", assistant or "")
        return result
    finally:
        c.close()


def stored_turn_ids(agent) -> set:
    """只读返回该 agent 已接收的 (conversation_id, turn_id) 集合。

    供增量采集使用：已接收的轮次不必再提交，也不必占用每次提交额度。
    不创建数据库、不初始化 schema；数据库不存在时返回空集合。
    """
    agent = _agent(agent)
    path = Path(core.db_path(agent)).absolute()
    if not path.exists():
        return set()
    c = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        c.execute("PRAGMA query_only=ON")
        c.execute("BEGIN")
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='turn_receipts'").fetchone():
            return set()
        return {(row[0], row[1]) for row in c.execute(
            "SELECT conversation_id, turn_id FROM turn_receipts")}
    finally:
        c.close()
