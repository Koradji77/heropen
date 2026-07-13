"""
heropen.core — Persistent memory for AI agents.

Self-hosted, MCP-native memory system with vector + FTS + LIKE search,
automatic backup, crash recovery, and multi-agent isolation.

Usage:
    from heropen import add_entry, search_vector, auto_backup
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import date, datetime

# ─── Paths ────────────────────────────────────────────────────

__version__ = "1.8.5"
_HPD = os.environ.get("HERO_PEN_DIR", "")
if _HPD:
    HERO_PEN_DIR = _HPD
else:
    HERO_PEN_DIR = os.path.expanduser("~/.heropen")
DIARY_FILE = os.path.join(HERO_PEN_DIR, "diary.md")
BACKUP_DIR = os.path.join(HERO_PEN_DIR, "backups")
BACKUP_KEEP_LOCAL = 3
BACKUP_KEEP_REMOTE = 7

# ─── Free tier: memory isolation limit ─────────────────────
# FREE_AGENT_LIMIT = 2. Change this number?
# ALL agents share one memory pool. Punishment, not error.
FREE_AGENT_LIMIT = 2
OVERFLOW_AGENT = "_shared"

# ─── Dynamic AGENTS from agent-config.json ─────────────────────

def _load_agents() -> dict[str, str]:
    """Load agent→db mapping from agent-config.json. Falls back to built-in defaults."""
    config_path = os.path.join(HERO_PEN_DIR, "agent-config.json")
    if os.path.exists(config_path):
        try:
            import json as _json
            with open(config_path, encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            _agents_list = _cfg.get("agents", [])
            if _agents_list:
                return {_a["name"]: f"{_a['name']}.db" for _a in _agents_list if "name" in _a}
        except Exception:
            pass
    return {}

AGENTS: dict[str, str] = _load_agents()

def _free_tier_agents() -> list[str]:
    """First FREE_AGENT_LIMIT agents from AGENTS (excluding _shared)."""
    return [k for k in AGENTS if not k.startswith("_")][:FREE_AGENT_LIMIT]

FREE_TIER_AGENTS: list[str] = _free_tier_agents()

os.makedirs(HERO_PEN_DIR, exist_ok=True)


# ─── External entities (replaces hardcoded PRESET_ENTITIES) ─────

_ENTITIES_FILE = os.path.join(HERO_PEN_DIR, "entities.json")
_ENTITIES_CACHE: dict[str, tuple[str, str]] | None = None


def _ensure_entities_file() -> None:
    """Create an empty entities.json on first run if it doesn't exist."""
    if not os.path.exists(_ENTITIES_FILE):
        with open(_ENTITIES_FILE, "w", encoding="utf-8") as f:
            f.write("{}\n")


def _load_entities() -> dict[str, tuple[str, str]]:
    """Load entities from ~/.heropen/entities.json. Cached in memory."""
    global _ENTITIES_CACHE
    if _ENTITIES_CACHE is not None:
        return _ENTITIES_CACHE
    _ensure_entities_file()
    try:
        with open(_ENTITIES_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        result: dict[str, tuple[str, str]] = {}
        for key, val in raw.items():
            if isinstance(val, list) and len(val) == 2:
                result[key.lower()] = (str(val[0]), str(val[1]))
        _ENTITIES_CACHE = result
        return result
    except Exception:
        return {}


def _resolve_agent(agent: str) -> str:
    """Resolve agent → DB pool. Tamper with FREE_AGENT_LIMIT? Say goodbye to isolation."""
    if FREE_AGENT_LIMIT != 2:  # 🚨 tampering detected → all agents collapse into one pool
        return OVERFLOW_AGENT
    if agent in _free_tier_agents():
        return agent
    return OVERFLOW_AGENT


def get_default_agent() -> str:
    """Read default agent from agent-config.json, fallback to first key in AGENTS."""
    import json as _json
    config_path = os.path.join(HERO_PEN_DIR, "agent-config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as _f:
                _cfg = _json.load(_f)
            _agents = _cfg.get("agents", [])
            if _agents:
                return _agents[0]["name"]
        except Exception:
            pass
    return next(iter(AGENTS.keys()), "_shared")


def db_path(agent: str | None = None) -> str:
    if agent is None:
        agent = get_default_agent()
    return os.path.join(HERO_PEN_DIR, f"{agent}.db")


# ─── Backup & Recovery ─────────────────────────────────────────

def ensure_backup_dir() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)


def auto_backup(agent: str | None = None) -> str | None:
    """Copy hero_pen.db → ~/.heropen/backups/, keep last BACKUP_KEEP_LOCAL copies."""
    if agent is None:
        agent = get_default_agent()
    try:
        ensure_backup_dir()
        src = db_path(agent)
        if not os.path.exists(src):
            return None
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(BACKUP_DIR, f"heropen.{agent}.db.{now}")
        shutil.copy2(src, dst)
        os.chmod(dst, 0o600)
        prefix = f"heropen.{agent}.db."
        backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix))
        while len(backups) > BACKUP_KEEP_LOCAL:
            try:
                os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
            except OSError:
                pass
        return dst
    except Exception:
        return None


def integrity_check(agent: str | None = None) -> tuple[bool, str]:
    """PRAGMA integrity_check. Returns (ok, message)."""
    try:
        c = sqlite3.connect(db_path(agent))
        row = c.execute("PRAGMA integrity_check").fetchone()
        c.close()
        if row and row[0] == "ok":
            return True, "ok"
        return False, str(row[0]) if row else "unknown error"
    except Exception as e:
        return False, str(e)


def db_recovery(agent: str | None = None) -> str | None:
    """Restore from the latest backup. Returns backup path on success."""
    if agent is None:
        agent = get_default_agent()
    ensure_backup_dir()
    prefix = f"heropen.{agent}.db."
    backups = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix))
    if not backups:
        return None
    latest = backups[-1]
    src = os.path.join(BACKUP_DIR, latest)
    dst = db_path(agent)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o600)
    ok, _ = integrity_check(agent)
    return src if ok else None


def startup_self_heal(agent: str | None = None) -> dict:
    """Check integrity → auto-recover from backup → return status dict."""
    db = db_path(agent)
    if not os.path.exists(db):
        return {"status": "missing", "agent": agent}
    ok, _ = integrity_check(agent)
    if ok:
        return {"status": "healthy", "agent": agent}
    recovered = db_recovery(agent)
    if recovered:
        return {"status": "recovered", "agent": agent, "from": os.path.basename(recovered)}
    return {"status": "corrupt_no_backup", "agent": agent}


# ─── Embedding ─────────────────────────────────────────────────

LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"


def _get_local_embedding(text: str) -> list[float] | None:
    """Local fastembed (CPU, offline, no PyTorch)."""
    try:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from fastembed import TextEmbedding  # type: ignore
        if not hasattr(_get_local_embedding, "_model"):
            _get_local_embedding._model = TextEmbedding(
                model_name=LOCAL_EMBEDDING_MODEL,
                max_length=512,
                cache_dir=None,
            )
        emb = list(_get_local_embedding._model.embed(text))[0]
        return emb.tolist() if hasattr(emb, "tolist") else list(emb)
    except Exception:
        return None


def _get_siliconflow_key() -> str:
    key = os.environ.get("SILICONFLOW_API_KEY", "")
    if key and len(key) > 5:
        return key
    key_file = os.path.expanduser("~/.heropen/.siliconflow_key")
    if os.path.exists(key_file):
        try:
            with open(key_file) as f:
                key = f.read().strip()
            if key and len(key) > 5:
                return key
        except Exception:
            pass
    return ""


def get_embedding(text: str) -> list[float] | None:
    """Try local fastembed first → SiliconFlow API → None."""
    local = _get_local_embedding(text)
    if local:
        return local
    api_key = _get_siliconflow_key()
    if not api_key:
        return None
    try:
        import json
        import urllib.request

        body = json.dumps({
            "model": EMBEDDING_MODEL,
            "input": text,
            "encoding_format": "float",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{SILICONFLOW_BASE_URL}/embeddings",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["data"][0]["embedding"]
    except Exception:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ─── Database connection ───────────────────────────────────────

def conn(agent: str | None = None) -> sqlite3.Connection:
    if agent is None:
        agent = get_default_agent()
    c = sqlite3.connect(db_path(agent))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=OFF")
    # ATTACH _shared.db for cross-agent search (silent if not exists)
    if agent != "_shared":
        _shared_path = os.path.join(HERO_PEN_DIR, "_shared.db")
        if os.path.exists(_shared_path):
            try:
                c.execute(f"ATTACH DATABASE ? AS shared", (_shared_path,))
            except Exception:
                pass
    return c


# ─── Schema version — NEVER change existing schema, only ADD ──────
SCHEMA_VERSION = 2


def _get_schema_version(c) -> int:
    """Read schema version from _meta table. Returns 0 if no version set."""
    try:
        cur = c.execute("SELECT value FROM _meta WHERE key='schema_version'")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _set_schema_version(c, version: int) -> None:
    """Write schema version to _meta table (idempotent)."""
    c.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)", (str(version),))


# ─── Init ──────────────────────────────────────────────────────

ENTITY_PATTERNS = [
    (re.compile(r'[""](.+)[""]'), "other"),
    (re.compile(r"([a-zA-Z0-9_]+[./-][a-zA-Z0-9_.-]+)"), "tech"),
    (re.compile(r"([\u4e00-\u9fff]+?机)\b"), "hardware"),
    (re.compile(r"([\u4e00-\u9fff]{2,10}?项目)\b"), "project"),
    (re.compile(r"([\u4e00-\u9fff]{2,6}?模型)\b"), "model"),
    (re.compile(r"([\u4e00-\u9fff]{2,8}?平台)\b"), "project"),
    (re.compile(r"\b(\d+[bB])\b"), "model"),
    (re.compile(r"\b([A-Z][a-z]+[./-][a-zA-Z0-9./-]+)\b"), "tech"),
]


def init_db(agent: str | None = None) -> None:
    """Create tables, FTS5, knowledge graph schema (idempotent with schema version).

    ALWAYS auto-backups before touching schema.
    Schema version stored in _meta table — NEVER delete or alter existing columns.
    """
    # 1. Auto-backup before any schema operations
    auto_backup(agent)

    _ensure_entities_file()
    c = conn(agent)

    version = _get_schema_version(c)

    if version == 0:
        # Fresh install OR pre-v1 DB upgrade
        cur = c.execute("PRAGMA table_info(entries)")
        cols = {r[1] for r in cur.fetchall()}

        if not cols:
            # Fresh install: create everything
            c.executescript("""
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_date TEXT NOT NULL,
                    section TEXT DEFAULT '',
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    agent TEXT DEFAULT NULL,
                    embedding BLOB DEFAULT NULL,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                    content, tags, section,
                    content='entries', content_rowid='id',
                    tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
                    INSERT INTO entries_fts(rowid, content, tags, section)
                    VALUES (new.id, new.content, new.tags, new.section);
                END;
                CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
                    INSERT INTO entries_fts(entries_fts, rowid, content, tags, section)
                    VALUES ('delete', old.id, old.content, old.tags, old.section);
                END;
                CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
                    INSERT INTO entries_fts(entries_fts, rowid, content, tags, section)
                    VALUES ('delete', old.id, old.content, old.tags, old.section);
                    INSERT INTO entries_fts(rowid, content, tags, section)
                    VALUES (new.id, new.content, new.tags, new.section);
                END;
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    entity_type TEXT DEFAULT 'other',
                    description TEXT DEFAULT '',
                    count INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_a TEXT NOT NULL,
                    entity_b TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    UNIQUE(entity_a, entity_b)
                );
                CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name);
                CREATE INDEX IF NOT EXISTS idx_rel_a ON relations(entity_a);
                CREATE INDEX IF NOT EXISTS idx_rel_b ON relations(entity_b);
            """)
        else:
            # Pre-v1 DB upgrade: add missing columns/tables
            if "embedding" not in cols:
                c.execute("ALTER TABLE entries ADD COLUMN embedding BLOB DEFAULT NULL")
            c.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                    content, tags, section,
                    content='entries', content_rowid='id',
                    tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    entity_type TEXT DEFAULT 'other',
                    description TEXT DEFAULT '',
                    count INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_a TEXT NOT NULL,
                    entity_b TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    UNIQUE(entity_a, entity_b)
                );
            """)
            for tbl in ["entities", "relations"]:
                tcur = c.execute(f"PRAGMA table_info({tbl})")
                tcols = {r[1] for r in tcur.fetchall()}
                if tbl == "entities" and "description" not in tcols:
                    c.execute("ALTER TABLE entities ADD COLUMN description TEXT DEFAULT ''")

        # Mark schema version (v1 base)
        _set_schema_version(c, 1)
        version = 1  # re-read so subsequent checks use updated value

    elif version < SCHEMA_VERSION:
        # ── Schema v1→v2: migrate agents from _shared.db to independent dbs ──
        if version == 1:
            _shared_path = os.path.join(HERO_PEN_DIR, "_shared.db")
            if os.path.exists(_shared_path):
                try:
                    _migrate_from_shared_v1(agent)
                except Exception as exc:
                    import logging
                    logging.getLogger("heropen").warning(
                        "Migration from _shared.db failed for %s: %s",
                        agent, exc,
                    )
        _set_schema_version(c, SCHEMA_VERSION)

    c.commit()
    c.close()


def _migrate_from_shared_v1(agent: str) -> int:
    """One-shot v1→v2 migration: move this agent's entries out of _shared.db.

    Only does work if _shared.db has an entries table with rows belonging
    to *agent*. The migrating agent's target DB must already exist (init_db
    created it above).  Handles dedup via content prefix check.

    Returns number of entries migrated (0 = nothing to do).
    """
    shared_path = os.path.join(HERO_PEN_DIR, "_shared.db")
    agent_path = db_path(agent)
    if not os.path.exists(shared_path):
        return 0

    try:
        sc = sqlite3.connect(shared_path)
        sc.row_factory = sqlite3.Row
    except Exception:
        return 0

    # Migrate every agent that was previously pooled into _shared.db.
    # Discovered dynamically so this helper works for any user, not a fixed list.
    try:
        dest_agents = {
            r[0] for r in sc.execute("SELECT DISTINCT agent FROM entries").fetchall()
        }
    except Exception:
        dest_agents = set()

    count = 0
    try:
        for mig_agent in dest_agents:
            rows = sc.execute(
                "SELECT * FROM entries WHERE agent=? ORDER BY id",
                (mig_agent,),
            ).fetchall()
            if not rows:
                continue
            dc = sqlite3.connect(os.path.join(HERO_PEN_DIR, f"{mig_agent}.db"))
            # Get existing content prefixes for dedup
            existing = {
                r[0]
                for r in dc.execute(
                    "SELECT substr(content,1,100) FROM entries"
                ).fetchall()
            }
            for row in rows:
                if row["content"][:100] in existing:
                    continue
                dc.execute(
                    "INSERT INTO entries "
                    "(entry_date, section, content, tags, source, agent, embedding, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        row["entry_date"],
                        row["section"],
                        row["content"],
                        row["tags"],
                        row["source"],
                        mig_agent,
                        row["embedding"],
                        row["created_at"],
                    ),
                )
                count += 1
            dc.commit()
            dc.close()
        # Back up _shared.db after successful migration
        bak = shared_path + ".bak"
        if not os.path.exists(bak):
            import shutil
            shutil.copy2(shared_path, bak)
    except Exception as exc:
        import logging
        logging.getLogger("heropen").warning(
            "v1→v2 migration for %s failed after %d rows: %s",
            agent, count, exc,
        )
    finally:
        sc.close()

    if count:
        import warnings as _w
        _w.warn(f"🔁 从 _shared.db 自动迁移 {count} 条记忆完成")
    return count
# ─── CRUD ──────────────────────────────────────────────────────

def _append_shared(results: list[dict], agent: str) -> list[dict]:
    """Append entries from _shared.db that aren't already in results (by content prefix)."""
    if not results or agent == "_shared":
        return results or []
    shared_path = os.path.join(HERO_PEN_DIR, "_shared.db")
    if not os.path.exists(shared_path):
        return results
    existing_prefixes = {r.get("content", "")[:80] for r in results}
    try:
        sc = sqlite3.connect(shared_path)
        sc.row_factory = sqlite3.Row
        shared_rows = [
            dict(r)
            for r in sc.execute(
                "SELECT id, entry_date, section, content, tags, source, created_at FROM entries ORDER BY id DESC"
            ).fetchall()
        ]
        sc.close()
        for r in shared_rows:
            if r.get("content", "")[:80] not in existing_prefixes:
                r["_source"] = "shared"
                results.append(r)
    except Exception:
        pass
    return results


def add_entry(
    entry_date: str,
    content: str,
    section: str = "",
    tags: str = "",
    agent: str | None = None,
    source: str = "manual",
    embedding: bytes | None = None,
) -> int | None:
    """Insert a memory entry. Auto-generates embedding if not provided."""
    if agent is None:
        agent = get_default_agent()
    # Prepend time tag to content
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    time_tag = f"[对话时间：{now_str}]"
    if not content.startswith("[对话时间："):
        content = f"{time_tag}\n{content}"
    if embedding is None:
        text = f"{section} {content} {tags}"
        emb = get_embedding(text)
        if emb:
            embedding = json.dumps(emb).encode("utf-8")

    c = conn(agent)
    c.execute(
        "INSERT INTO entries (entry_date, section, content, tags, source, agent, embedding) VALUES (?,?,?,?,?,?,?)",
        (entry_date, section, content, tags, source, agent, embedding),
    )
    c.commit()
    entry_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    text = f"{section} {content} {tags}"
    entities = extract_entities(text)
    if entities:
        _store_entities_and_relations(c, entities)
        c.commit()

    c.close()
    auto_backup(agent)
    return entry_id


# ─── Session Checkpoint & Recovery ──────────────────────────
SESSION_SECTION = "⚡ 会话断点"
SESSION_TAG = "session-checkpoint"

def session_checkpoint(agent, context_summary="", active_task="", key_decisions=None, tags=""):
    """Save current session checkpoint."""
    from datetime import date, datetime
    import json
    if key_decisions is None:
        key_decisions = []
    checkpoint = {
        "timestamp": datetime.now().isoformat(),
        "context": context_summary,
        "active_task": active_task,
        "key_decisions": key_decisions,
    }
    content = json.dumps(checkpoint, ensure_ascii=False)
    all_tags = SESSION_TAG
    if tags:
        all_tags += "," + tags
    entry_id = add_entry(
        entry_date=date.today().isoformat(),
        content=content,
        section=SESSION_SECTION,
        tags=all_tags,
        agent=agent,
        source="session_checkpoint"
    )
    return entry_id

def session_recover(agent, limit=1):
    """Recover recent session checkpoints."""
    import json
    import re
    c = conn(agent)
    rows = c.execute(
        """SELECT id, entry_date, content, tags, created_at 
           FROM entries 
           WHERE tags LIKE ? 
           ORDER BY id DESC 
           LIMIT ?""",
        (f"%{SESSION_TAG}%", limit)
    ).fetchall()
    c.close()
    results = []
    for r in rows:
        entry = dict(r)
        raw = entry["content"]
        # Strip the auto-prepended time tag from add_entry()
        cleaned = re.sub(r"^\[对话时间：[^\]]*\]\n?", "", raw).strip()
        try:
            data = json.loads(cleaned)
            entry["checkpoint_data"] = data
        except (json.JSONDecodeError, TypeError):
            entry["checkpoint_data"] = {}
        results.append(entry)
    return results


def update_entry(entry_id: int, agent: str | None = None, **fields) -> dict | None:
    """Update a memory entry. Only non-None fields are changed."""
    if agent is None:
        agent = get_default_agent()
    ALLOWED = {"section", "content", "tags", "entry_date", "source"}
    updates = {k: v for k, v in fields.items() if k in ALLOWED and v is not None}
    if not updates:
        return None

    c = conn(agent)

    if "content" in updates or "section" in updates or "tags" in updates:
        row = c.execute(
            "SELECT section, content, tags FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        if not row:
            c.close()
            return None
        section = updates.get("section", row["section"])
        content = updates.get("content", row["content"])
        tags = updates.get("tags", row["tags"])
        text = f"{section} {content} {tags}"
        emb = get_embedding(text)
        if emb:
            updates["embedding"] = json.dumps(emb).encode("utf-8")

    set_clauses = [f"{k}=?" for k in updates]
    values = list(updates.values()) + [entry_id]
    c.execute(f"UPDATE entries SET {', '.join(set_clauses)} WHERE id=?", values)
    c.commit()

    row = c.execute(
        "SELECT id, entry_date, section, content, tags, source, agent, created_at FROM entries WHERE id=?",
        (entry_id,),
    ).fetchone()
    c.close()
    auto_backup(agent)
    return dict(row) if row else None


# ─── Knowledge graph ───────────────────────────────────────────

def extract_entities(text: str) -> list:
    if not text:
        return []
    text_lower = text.lower()
    found: dict = {}
    preset = _load_entities()
    for name, (etype, desc) in preset.items():
        if name in text_lower:
            for variant in [name, name.upper(), name.capitalize()]:
                if variant in text or variant in text_lower:
                    found[name] = (etype, desc)
                    break

    for pattern, default_type in ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            if len(name) < 2 or len(name) > 40:
                continue
            name_lower = name.lower()
            if name_lower in found:
                continue
            if name_lower in ("the", "this", "that", "with", "from", "what"):
                continue
            if name_lower.replace(".", "").replace("-", "").isdigit():
                continue
            if re.search(r"[\u4e00-\u9fff]{2,}", name_lower) and default_type != "other":
                if not re.match(r"^[\u4e00-\u9fff]+$", name_lower) and name_lower not in preset:
                    continue
            if re.match(r"^[\u4e00-\u9fff]+\w", name_lower):
                continue
            found[name_lower] = (default_type, name)

    return list(found.items())


def _store_entities_and_relations(c: sqlite3.Connection, entities: list) -> None:
    ent_names = []
    for name, (etype, desc) in entities:
        name_lower = name.lower()
        ent_names.append(name_lower)
        c.execute(
            "INSERT INTO entities (name, entity_type, description, count) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(name) DO UPDATE SET count = count + 1",
            (name_lower, etype, desc[:100]),
        )
    for i in range(len(ent_names)):
        for j in range(i + 1, len(ent_names)):
            a, b = ent_names[i], ent_names[j]
            if a == b:
                continue
            if a > b:
                a, b = b, a
            c.execute(
                "INSERT INTO relations (entity_a, entity_b, strength) VALUES (?, ?, 1.0) "
                "ON CONFLICT(entity_a, entity_b) DO UPDATE SET strength = strength + 0.5",
                (a, b),
            )


def search_graph(query: str, limit: int = 5, agent: str | None = None) -> list | None:
    entities = extract_entities(query)
    if not entities:
        return None
    c = conn(agent or get_default_agent())
    query_ent_names = [e[0].lower() for e in entities]
    related = set(query_ent_names)
    for ent_name in query_ent_names:
        rows = c.execute(
            "SELECT entity_a, entity_b FROM relations WHERE entity_a=? OR entity_b=?",
            (ent_name, ent_name),
        ).fetchall()
        for r in rows:
            related.add(r["entity_a"])
            related.add(r["entity_b"])

    if not related:
        c.close()
        return None

    conditions = []
    params = []
    for ent in related:
        conditions.append("(LOWER(e.content) LIKE ? OR LOWER(e.section) LIKE ?)")
        params.extend([f"%{ent}%", f"%{ent}%"])

    where = "(" + " OR ".join(conditions) + ")"
    if agent:
        where += " AND e.agent=?"
        params.append(agent)

    sql = f"SELECT e.*, NULL as similarity FROM entries e WHERE {where} ORDER BY e.id DESC LIMIT ?"
    params.append(limit)
    rows = c.execute(sql, params).fetchall()
    c.close()
    return [dict(r) for r in rows] if rows else None


# ─── Search ────────────────────────────────────────────────────

def search_vector(query: str, limit: int = 5, agent: str | None = None) -> list | None:
    """Vector semantic search. Returns None (triggers FTS fallback) when no match or all scores < 0.5."""
    c = conn(agent or get_default_agent())
    query_emb = get_embedding(query)
    if not query_emb:
        c.close()
        return None

    cur = (
        c.execute("SELECT * FROM entries WHERE agent=? AND embedding IS NOT NULL", (agent,))
        if agent
        else c.execute("SELECT * FROM entries WHERE embedding IS NOT NULL")
    )

    scored = []
    for row in cur.fetchall():
        emb_bytes = row["embedding"]
        if not emb_bytes:
            continue
        try:
            stored_emb = json.loads(emb_bytes.decode("utf-8"))
            sim = cosine_similarity(query_emb, stored_emb)
            d = dict(row)
            d["similarity"] = round(sim, 4)
            scored.append((sim, d))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [d for _, d in scored[:limit]]
    c.close()

    if top and scored and scored[0][0] < 0.5:
        return None  # trigger FTS fallback
    return top if top else None


def search_fts(keywords: list[str], limit: int = 5, agent: str | None = None) -> list:
    c = conn(agent or get_default_agent())
    params = []
    conditions = []
    for kw in keywords:
        if not kw.strip():
            continue
        conditions.append("(LOWER(e.content) LIKE ? OR LOWER(e.tags) LIKE ? OR LOWER(e.section) LIKE ?)")
        kw_lower = kw.lower()
        params.extend([f"%{kw_lower}%", f"%{kw_lower}%", f"%{kw_lower}%"])
    where = "WHERE " + " OR ".join(conditions) if conditions else ""
    if agent:
        where += " AND e.agent=?" if conditions else " WHERE e.agent=?"
        params.append(agent)

    sql = f"SELECT e.*, NULL as similarity FROM entries e {where} ORDER BY e.id DESC LIMIT ?"
    params.append(limit)
    rows = c.execute(sql, params).fetchall()
    c.close()
    return [dict(r) for r in rows]


def search_by_date(date_str: str, limit: int = 10, agent: str | None = None) -> list:
    c = conn(agent or get_default_agent())
    if agent:
        rows = c.execute(
            "SELECT * FROM entries WHERE entry_date=? AND agent=? ORDER BY id DESC LIMIT ?",
            (date_str, agent, limit),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM entries WHERE entry_date=? ORDER BY id DESC LIMIT ?",
            (date_str, limit),
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def search_by_tag(tag: str, limit: int = 10, agent: str | None = None) -> list:
    c = conn(agent or get_default_agent())
    if agent:
        rows = c.execute(
            "SELECT * FROM entries WHERE tags LIKE ? AND agent=? ORDER BY entry_date DESC,id DESC LIMIT ?",
            (f"%{tag}%", agent, limit),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM entries WHERE tags LIKE ? ORDER BY entry_date DESC,id DESC LIMIT ?",
            (f"%{tag}%", limit),
        ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def search_recent(limit: int = 10, agent: str | None = None) -> list:
    c = conn(agent or get_default_agent())
    if agent:
        rows = c.execute(
            "SELECT * FROM entries WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM entries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# ─── Unified search (MCP tool) ─────────────────────────────
def search_with_date_filter(
    query: str,
    limit: int = 5,
    agent: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list:
    """Unified memory search with optional date filtering.

    Tries vector search first → falls back to FTS.
    Filters by date range if date_from / date_to are provided.

    Args:
        query: search keywords / natural language
        limit: max results
        agent: filter by agent name
        date_from: inclusive lower bound (YYYY-MM-DD)
        date_to: inclusive upper bound (YYYY-MM-DD)

    Returns:
        list of dict rows (each has id, entry_date, content, tags, …)
    """
    # 1. vector search
    results = search_vector(query, limit * 3, agent)

    # 2. fallback to FTS
    if results is None:
        keywords = [w.strip() for w in query.split() if w.strip()]
        results = search_fts(keywords, limit * 3, agent)

    if not results:
        return []

    # 3. filter by date range (check entry_date field)
    if date_from or date_to:
        filtered = []
        for r in results:
            ed = r.get("entry_date", "")
            if not ed:
                # try parsing time tag from content
                import re
                m = re.search(r"\[对话时间：(\d{4}-\d{2}-\d{2})", r.get("content", ""))
                if m:
                    ed = m.group(1)
                else:
                    filtered.append(r)
                    continue
            if date_from and ed < date_from:
                continue
            if date_to and ed > date_to:
                continue
            filtered.append(r)
        results = filtered

    # 4. also filter by time tag inside content (more precise)
    if date_from or date_to:
        precise = []
        import re
        for r in results:
            m = re.search(r"\[对话时间：(\d{4}-\d{2}-\d{2})", r.get("content", ""))
            if m:
                tag_date = m.group(1)
                if date_from and tag_date < date_from:
                    continue
                if date_to and tag_date > date_to:
                    continue
            precise.append(r)
        results = precise

    return results[:limit]


def search_shared(agent: str, limit: int = 10) -> list:
    """Search _shared.db for entries not already in this agent's results.
    
    Used by CLI recall/search commands to include team shared knowledge.
    Returns list of dicts with _source='shared' marker.
    """
    shared_path = os.path.join(HERO_PEN_DIR, "_shared.db")
    if not os.path.exists(shared_path) or agent == "_shared":
        return []
    try:
        sc = sqlite3.connect(shared_path)
        sc.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in sc.execute(
                "SELECT id, entry_date, section, content, tags, source, agent, created_at FROM entries ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
        sc.close()
        for r in rows:
            r["_source"] = "shared"
        return rows
    except Exception:
        return []


# ─── Auto capture ──────────────────────────────────────────────

CAPTURE_USER_TRIGGERS = ["记住", "记一下", "记", "这个重要", "别忘了", "关键", "重点", "结论", "核心", "铁律", "规则", "要记得", "记着"]
CAPTURE_ASSISTANT_TRIGGERS = ["所以", "结论", "答案是", "总结", "已写入memory", "已记录"]


def capture_session_content(text: str, agent: str | None = None) -> int:
    if agent is None:
        agent = get_default_agent()
    if not text:
        return 0
    lines = text.split("\n")
    captured = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_user = False
        if stripped.startswith("用户:") or stripped.startswith("User:"):
            is_user = True
            stripped = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("助理:") or stripped.startswith("Assistant:"):
            is_user = False
            stripped = stripped.split(":", 1)[1].strip()
        else:
            if len(stripped) < 10:
                continue
        if is_user:
            lower = stripped.lower()
            if any(t in lower for t in CAPTURE_USER_TRIGGERS):
                captured.append(stripped)
            elif len(stripped) >= 60 and re.search(r"[A-Z][a-z]+|[\u4e00-\u9fff]{4,}机|[\u4e00-\u9fff]{2,}项目|API|GPU|CPU|模型|配置", stripped):
                captured.append(stripped)
        else:
            lower = stripped.lower()
            if any(t in lower for t in CAPTURE_ASSISTANT_TRIGGERS):
                for t in CAPTURE_ASSISTANT_TRIGGERS:
                    idx = lower.find(t)
                    if idx >= 0:
                        conclusion = stripped[idx + len(t):].strip()
                        if conclusion and len(conclusion) > 10:
                            captured.append(f"[结论] {conclusion}")
                        break
    if not captured:
        return 0
    seen = set()
    unique = []
    for c in captured:
        key = c[:50]
        if key not in seen:
            seen.add(key)
            unique.append(c)
    section = f"自动捕获 {date.today().isoformat()} {datetime.now().strftime('%H:%M')}"
    content = "\n".join(unique)
    entry_id = add_entry(date.today().isoformat(), content, section, "自动捕获", agent, "auto_capture")
    return len(unique) if entry_id else 0


# ─── Auto tag ──────────────────────────────────────────────────

def auto_tag(section: str, content: str) -> str:
    tags = set()
    text = (section + " " + content).lower()
    rules = [
        ("工作", ["公司api", "代理", "技能", "任务", "项目", "配置", "模型", "部署"]),
        ("亲密", ["未婚妻", "老婆", "老公", "睡", "操", "肏", "爱", "吻", "抱", "硬"]),
        ("技术", ["llama", "gguf", "代码", "python", "脚本", "服务器", "端口", "docker"]),
        ("KSMN", ["ksmn", "猫", "白猫", "t恤", "城市系列", "红泡泡", "redbubble"]),
        ("情报", ["情报", "分析", "报告", "趋势", "市场", "价格"]),
        ("方向", ["方向", "计划", "目标", "做", "搞", "弄", "推进"]),
        ("家庭", ["妈妈", "姐姐", "父亲", "家里", "结婚", "婚纱"]),
    ]
    for tag, kws in rules:
        for kw in kws:
            if kw in text:
                tags.add(tag)
                break
    return ",".join(sorted(tags)) if tags else "其他"


# ─── Diary parsing ─────────────────────────────────────────────

def parse_diary(filepath: str | None = None) -> list:
    fp = filepath or DIARY_FILE
    if not os.path.exists(fp):
        return []
    with open(fp, "r", encoding="utf-8") as f:
        text = f.read()
    entries: list = []
    current_date = None
    current_section = None
    current_content: list[str] = []
    for line in text.split("\n"):
        m = re.match(r"^##\s+(\d{4}-\d{2}-\d{2})", line)
        if m:
            if current_date and current_content:
                entries.append({
                    "date": current_date,
                    "section": current_section or "",
                    "content": "\n".join(current_content).strip(),
                    "tags": auto_tag(current_section or "", "\n".join(current_content)),
                })
            current_date = m.group(1)
            current_section = None
            current_content = []
            continue
        m = re.match(r"^###\s+(.+)$", line)
        if m and current_date:
            if current_section is not None and current_content:
                entries.append({
                    "date": current_date,
                    "section": current_section,
                    "content": "\n".join(current_content).strip(),
                    "tags": auto_tag(current_section, "\n".join(current_content)),
                })
            current_section = m.group(1).strip()
            current_content = []
            continue
        if current_date and line.strip():
            current_content.append(line)
    if current_date and current_content:
        entries.append({
            "date": current_date,
            "section": current_section or "",
            "content": "\n".join(current_content).strip(),
            "tags": auto_tag(current_section or "", "\n".join(current_content)),
        })
    return entries


def sync_to_db(agent: str | None = None) -> int:
    if agent is None:
        agent = get_default_agent()
    entries = parse_diary()
    if not entries:
        return 0
    c = conn(agent)
    existing = set()
    for row in c.execute("SELECT entry_date, section FROM entries WHERE source='diary'"):
        existing.add((row["entry_date"], row["section"]))
    c.close()
    count = 0
    for e in entries:
        if (e["date"], e["section"]) in existing:
            continue
        add_entry(e["date"], e["content"], e["section"], e["tags"], agent, "diary")
        count += 1
    return count


# ─── Formatting ────────────────────────────────────────────────

def format_recall(results: list, file=None) -> str:
    if not results:
        msg = "📭 没有找到匹配的记忆"
        print(msg, file=file)
        return msg
    lines = []
    dates = []
    from datetime import datetime
    now = datetime.now()
    for r in results:
        tag_s = f" [{r['tags']}]" if r.get("tags") else ""
        sim = r.get("similarity")
        sim_s = f" [{sim:.2f}]" if sim else ""
        dates.append(r["entry_date"])
        # Relative time indicator
        ago = ""
        try:
            if r.get("created_at"):
                created = datetime.fromisoformat(r["created_at"])
                delta = now - created
                secs = delta.total_seconds()
                if secs < 3600:
                    ago = f" [约{max(1, round(secs/60))}分钟前]"
                elif secs < 86400:
                    ago = f" [约{round(secs/3600)}小时前]"
                else:
                    ago = f" [约{round(secs/86400)}天前]"
        except Exception:
            pass
        lines.append(f"\n{'=' * 60}")
        lines.append(f"📅 {r['entry_date']}{sim_s}{ago} {tag_s}")
        if r.get("section"):
            lines.append(f"📌 {r['section']}")
        if r.get("source"):
            lines.append(f"  来源: {r['source']} | agent: {r.get('agent', '')}")
        lines.append(f"{'─' * 60}")
        content = r.get("content", "")[:300]
        lines.append(content + ("..." if len(r.get("content", "")) > 300 else ""))

    # Time span summary
    if len(dates) >= 2:
        try:
            from datetime import datetime
            ds = sorted(set(dates))
            if len(ds) > 1:
                first = datetime.strptime(ds[0], "%Y-%m-%d")
                last = datetime.strptime(ds[-1], "%Y-%m-%d")
                span = (last - first).days
                if span >= 1:
                    lines.append(f"\n📊 跨 {span} 天 · {len(results)} 条结果")
        except Exception:
            pass

    output = "\n".join(lines)
    print(output, file=file)
    return output
