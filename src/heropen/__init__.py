"""heropen — Persistent memory for AI agents.

Self-hosted, MCP-native memory system with vector + FTS + LIKE search,
automatic backup, crash recovery, and multi-agent isolation.
"""

# Single source of truth for the version: heropen.core.__version__.
# (2.0.7: previously duplicated here and drifted from core during the
#  2.0.6→2.0.7 bump — stdio handshake reads *this* attribute, so the
#  wheel advertised 2.0.7 while runtime reported 2.0.6. Never hardcode.)
__all__ = [
    "add_entry", "update_entry",
    "search_vector", "search_fts", "search_graph",
    "search_by_date", "search_by_tag", "search_recent",
    "search_with_date_filter",
    "auto_backup", "integrity_check", "startup_self_heal",
    "init_db", "conn", "db_path",
    "get_embedding", "cosine_similarity",
    "get_embedding_status", "is_arm64", "embedding_install_hint",
    "AGENTS",
]

from heropen.core import (
    __version__,
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
    get_embedding_status,
    is_arm64,
    embedding_install_hint,
    AGENTS,
)