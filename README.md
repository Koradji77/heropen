# heropen

> Give your AI agent long-term memory. Data stays on your machine; search costs zero tokens.

## Why "heropen"

The name comes from two places: **her** from Hermes (the agent you are reading this with), **open** from OpenClaw (openness). her + open put together is heropen.

Written out, heropen starts with **hero** — evoking the Marvel superhero trope. It is also a companion that remembers you and writes things down for you.

## Install

```bash
pip install heropen
```

Restart your agent. That's it.

On first launch it auto-detects your agent (Claude Code, Cursor, Windsurf, or any MCP client), sets up the database, and registers the memory tools. Your agent will notice the new install and walk you through setup.

## 30-second quickstart

```bash
# Save a memory
heropen add "Project uses FastAPI + SQLAlchemy, tests with pytest"

# Search memories
heropen search "project tech stack"

# Check status
heropen status

# Diagnose issues
heropen diagnose
```

## Connect your agent (MCP)

Works with any MCP-compatible agent. v1.8+ auto-detects and configures — no manual steps.

Or add it manually to your agent config:

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

Restart your agent and it has memory. Store a bug fix once, remember it permanently across sessions.

## Privacy promise

**Data stays on your machine. No telemetry. No heartbeat pings.** All memory is stored in a local SQLite database. Vector search uses a **local embedding model by default** (fastembed, `pip install heropen[embedding]`) — fully offline, zero cost. Optionally, you can point it at **your own self-hosted embedding endpoint** via the `HEROPEN_EMBEDDING_URL` environment variable (OpenAI-compatible `/embeddings`), so no third-party cloud is ever billed. The SiliconFlow cloud API is only used if you explicitly set `SILICONFLOW_API_KEY` with your own key. Memory text is only used to generate vectors and is never reported.

## Open-source scope

The free edition is fully open source (Apache-2.0). The commercial layer (Plus / Enterprise) is closed source.

## Why heropen

| | heropen (free) | other solutions |
|---|---|---|
| Storage | unlimited | usually capped |
| Searches | unlimited | pay per query |
| Needs network | no | yes |
| Data ownership | your machine | their servers |
| Install | one `pip install` | server + config |

Free = full core features. No crippled functionality.

## Links

- Home: [heropen.net](https://heropen.net)
- Docs: [heropen.net/heropen/docs](https://heropen.net/heropen/docs)
- GitHub: [github.com/Koradji77/heropen](https://github.com/Koradji77/heropen)

## License

Apache-2.0
