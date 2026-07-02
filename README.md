# heropen

> Persistent memory for local AI agents. One `pip install`, never start from scratch again.

Every new session, your agent forgets — project structure, tool preferences, past pitfalls, custom skills. heropen fixes that: after install, your agent remembers everything.

**100% local.** SQLite + vector search. No cloud, no telemetry.

## Install

```bash
pip install heropen
```

**Restart your agent.** That's it.

On first start, heropen auto-detects your agent (Claude Code, Cursor, Windsurf, or any MCP client), configures the database, and registers memory tools. You agent will notice the new install and offer to guide you through setup.

## Quick Start

```bash
# Save a memory
heropen add "Project uses FastAPI + SQLAlchemy, tests with pytest"

# Search your memories
heropen search "project tech stack"

# Check status
heropen status

# Diagnose issues (v1.8+)
heropen diagnose
```

## Connect Your Agent (MCP)

Works with any agent that supports MCP. Auto-setup (v1.8+) detects and configures automatically — no manual config needed.

Or add this to your agent config manually:

```json
{
  "mcpServers": {
    "heropen": {
      "command": "heropen",
      "args": ["mcp"]
    }
  }
}
```

Restart your agent, and it has memory. Your agent will auto-save and search memories on the fly — hit a bug once, remember it forever across sessions.

## What's New in v1.8

- **Zero-setup install** — auto-setup runs automatically on first interpreter start
- **Agent self-guidance** — tool descriptions now tell your agent *when* to use each tool, not just what it does
- **Welcome workflow** — new installs detected via health check, agent proactively offers to complete setup
- **`heropen diagnose`** — one-command troubleshooting for MCP config, DB, agent detection, and version checks
- **Auto-setup logging** — every step logged to `~/.heropen/setup.log`; success/failure shown in terminal
- **stdout fix** — MCP protocol no longer corrupted by heartbeat messages (reported by community)

## Why heropen

| | heropen (free) | Others |
|---|---|---|
| Memory storage | Unlimited | Often capped |
| Search | Unlimited | Per-query billing |
| Internet required | No | Yes |
| Your data stays | Your machine | Their servers |
| Setup | `pip install` one line | Server + config |

Free = full core. No gating.

## Support

- Homepage: [ksmn.cc/heropen](https://ksmn.cc/heropen)
- Docs: [ksmn.cc/heropen/docs](https://ksmn.cc/heropen/docs)
- Issues: [GitHub Issues](https://github.com/Koradji77/heropen/issues)

## License

MIT
