"""
heropen.cli — Command-line interface for HeroPen.

Usage:
    heropen --help
    heropen install      # new in v1.2.0
    heropen init ...
    heropen recall ...
    heropen add ...
    heropen bootstrap ...
"""

from __future__ import annotations

import sys
from pathlib import Path

from heropen.core import HERO_PEN_DIR


def main():
    args = sys.argv[1:]
    from heropen.telemetry_ping import fire_ping
    fire_ping()

    # Track which commands to fire a second ping for (commands that matter most)
    if args and args[0].lower() not in ("", "help", "--help", "-h", "version", "--version", "-V"):
        pass  # The main ping above covers this
    if not args:
        config_path = Path(HERO_PEN_DIR) / "agent-config.json"
        if not config_path.exists():
            from heropen.install import cmd_first_run
            cmd_first_run()
            return
        print_help()
        return

    cmd = args[0].lower()

    # ── Install wizard (v1.2.0) ────────────────────────
    if cmd in ("install", "setup"):
        # Lazy import to avoid loading rich unless needed
        from heropen.install import cmd_install
        cmd_install(args[1:])
        return

    # ── Auto MCP setup (v1.4.0) ─────────────────────────
    if cmd in ("auto-setup", "setup-mcp", "auto"):
        from heropen.cli_commands import cmd_auto_setup
        cmd_auto_setup(args[1:])
        return

    # ── Existing commands (all lazy-loaded) ─────────────
    if cmd == "init":
        from heropen.cli_commands import cmd_init
        cmd_init(args[1:])
    elif cmd == "recall":
        from heropen.cli_commands import cmd_recall
        cmd_recall(args[1:])
    elif cmd == "add":
        from heropen.cli_commands import cmd_add
        cmd_add(args[1:])
    elif cmd == "bootstrap":
        from heropen.cli_commands import cmd_bootstrap
        cmd_bootstrap(args[1:])
    elif cmd == "health":
        from heropen.cli_commands import cmd_status
        cmd_status(args[1:])
    elif cmd == "search":
        from heropen.cli_commands import cmd_recall
        cmd_recall(args[1:])
    elif cmd == "list":
        from heropen.cli_commands import cmd_status
        cmd_status(args[1:])
    elif cmd == "embed":
        from heropen.cli_commands import cmd_embed
        cmd_embed(args[1:])
    elif cmd == "backup":
        from heropen.cli_commands import cmd_export
        cmd_export(args[1:])
    elif cmd == "restore":
        from heropen.cli_commands import cmd_import
        cmd_import(args[1:])
    elif cmd == "mcp":
        from heropen.mcp_server import main as mcp_main
        mcp_main()
    elif cmd in ("--help", "-h", "help"):
        print_help()
    elif cmd in ("--version", "-V", "version"):
        from heropen.core import __version__
        print(f"heropen {__version__}")
    elif cmd == "status":
        from heropen.cli_commands import cmd_status
        cmd_status(args[1:])
    elif cmd in ("entities",):
        from heropen.cli_commands import cmd_entities
        cmd_entities(args[1:])
    elif cmd in ("capture",):
        from heropen.cli_commands import cmd_capture
        cmd_capture(args[1:])
    elif cmd in ("sync",):
        from heropen.cli_commands import cmd_sync
        cmd_sync(args[1:])
    elif cmd in ("init-all",):
        from heropen.cli_commands import cmd_init_all
        cmd_init_all(args[1:])
    elif cmd in ("export",):
        from heropen.cli_commands import cmd_export
        cmd_export(args[1:])
    elif cmd in ("import",):
        from heropen.cli_commands import cmd_import
        cmd_import(args[1:])
    elif cmd in ("delete",):
        from heropen.cli_commands import cmd_delete
        cmd_delete(args[1:])
    elif cmd in ("session",):
        from heropen.cli_commands import cmd_session
        cmd_session(args[1:])
    elif cmd == "panel":
        from heropen.panel import cmd_panel
        cmd_panel(args[1:])
    else:
        print(f"heropen: unknown command '{cmd}'")
        print_help()
        sys.exit(1)


def print_help():
    help_text = f"""
HeroPen v1.4.0 — AI Agent Long-term Memory System

Usage:
    heropen <command> [options]

Commands:
    install         Interactive install wizard (NEW in v1.2.0)
    auto-setup      One-command DB init + auto MCP config (NEW in v1.4.0)
    setup           Alias for install
    setup-mcp       Alias for auto-setup
    auto            Alias for auto-setup
    init            Initialize agent memory database
    add             Add a new memory entry
    recall          Search and recall memories
    search          Search memories (alias for recall)
    list            List memory stats (alias for status)
    status          Database statistics
    entities        View knowledge graph
    bootstrap       Agent memory startup summary
    capture         Auto-capture key sentences from stdin
    sync            Sync from diary.md to database
    embed           Generate embeddings for existing entries
    backup          Export memories to JSON
    restore         Import memories from JSON backup
    delete          Delete a memory entry
    health          Check system health (alias for status)
    session         Save or recover session checkpoint
    panel           Launch control panel (GUI/TUI)
    mcp             Start MCP server
    help            Show this help message
    version         Show version

Options:
    -h, --help     Show this help message
    -V, --version  Show version

Data directory: {HERO_PEN_DIR}
"""
    print(help_text.strip())


if __name__ == "__main__":
    main()
