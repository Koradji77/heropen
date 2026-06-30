# heropen

> Persistent memory for local AI agents. One `pip install`, never start from scratch again.

Every new session, your agent forgets — project structure, tool preferences, past pitfalls, custom skills. heropen fixes that: after install, your agent remembers everything.

**100% local.** SQLite + vector search. No cloud, no telemetry.

## Install

```bash
pip install heropen
heropen auto-setup
```

Two lines. It sets up the database, auto-detects your agent (Claude Code / Cursor / Windsurf etc.), and configures MCP.

**Restart your agent** and it has memory.

## Quick Start

```bash
# Save a memory
heropen add "Project uses FastAPI + SQLAlchemy, tests with pytest"

# Search your memories
heropen search "project tech stack"

# Check status
heropen status
```

## Connect Your Agent (MCP)

Works with any agent that supports MCP. `heropen auto-setup` detects and configures automatically.

Or add this to your agent config:

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

Restart your agent, and it has memory.

Once connected, your agent can auto-save and search memories on the fly — hit a bug once, remember it forever across sessions.

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
