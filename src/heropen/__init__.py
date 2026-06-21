"""heropen — Persistent memory for AI agents.

Self-hosted, MCP-native memory system with vector + FTS + LIKE search,
automatic backup, crash recovery, and multi-agent isolation.
"""

__version__ = "1.5.0"
__all__ = [
    "add_entry", "update_entry",
    "search_vector", "search_fts", "search_graph",
    "search_by_date", "search_by_tag", "search_recent",
    "search_with_date_filter",
    "auto_backup", "integrity_check", "startup_self_heal",
    "init_db", "conn", "db_path",
    "get_embedding", "cosine_similarity",
    "capture_session_content",
    "AGENTS",
]

from heropen.core import (
    add_entry,
    update_entry,
    search_vector,
    search_fts,
    search_graph,
    search_by_date,
    search_by_tag,
    search_recent,
    search_with_date_filter,
    auto_backup,
    integrity_check,
    startup_self_heal,
    init_db,
    conn,
    db_path,
    get_embedding,
    cosine_similarity,
    capture_session_content,
    AGENTS,
)