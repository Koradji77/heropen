<!-- mcp-name: net.heropen/heropen -->
<!-- Official MCP Registry ownership marker. Do not remove. -->

# heropen

[![M8ven Verified](https://m8ven.ai/badge/mcp/koradji77-heropen-1juqae?v=64f16afe3a683bd0739d73c9289cf695&variant=verified)](https://m8ven.ai/mcp/koradji77-heropen-1juqae?s=readme)

**English** · [简体中文](README.zh-CN.md)

> **Built to solve forgetting. The more you use it, the better it knows you.**

A local-first memory system for AI agents. Memories live on your own machine — never uploaded, never synced, never taken away. heropen organizes and prunes them automatically, so retrieval gets sharper and more personal over time.

⭐ **Did this help you? Give us a star** → [★ Star](https://github.com/Koradji77/heropen) in the top right, so more agent users find it.

## In 30 seconds

- **One-line install**: `pip install heropen`. No Docker, no Postgres, no API key. Common clients get their MCP config written automatically; Hermes and similar require wiring up `heropen-mcp` by hand.
- **Your agent gains long-term memory**: it remembers people, events, and context across sessions — no more starting from zero every conversation.
- **Data stays 100% on your machine**: no telemetry, no cloud sync, no bills. Memories live on your disk, and only you can take them away.

## Install

```bash
pip install heropen                 # Core: MCP + full-text search, pure-Python deps, any architecture (incl. ARM64)
pip install 'heropen[embedding]'    # Optional: local embeddings (pulls in onnxruntime / fastembed)
pip install 'heropen[tray]'         # Optional: Windows system tray badge (always-visible presence + workbench)
```

### ARM64 notes

| Platform | Recommendation |
|------|------|
| Apple Silicon (macOS arm64) | Usually `pip install 'heropen[embedding]'` just works |
| Windows ARM64 | Same; make sure Python is an ARM64 build |
| Linux aarch64 | Install core first, then try embedding. If no `onnxruntime` wheel matches, point `EMBEDDING_ENDPOINT` at a self-hosted endpoint or use FTS only |

Without local embeddings, `search` / `recall` degrade gracefully to full-text search — **memory storage and retrieval keep working**.

Restart your agent — done. On first launch heropen probes the known config paths of common clients (Claude Code, Cursor, Windsurf, WorkBuddy, etc.) and registers the memory tools. **Custom `mcp_servers` setups like Hermes are not written automatically**; follow "Connect your agent" below to configure them by hand.

## Quick start in 30 seconds

```bash
heropen add "Project uses FastAPI + SQLAlchemy, tests run on pytest"   # store a memory
heropen search "project tech stack"                                    # search memories
heropen status                                                         # check status
heropen diagnose                                                       # diagnose problems
```

**How to make it actually stick**: read the official usage guide (single source of truth) — [docs/usage-ssot-zh.md](docs/usage-ssot-zh.md) *(Chinese)*.
The short version: only facts go into heropen; the hot layer holds iron rules, high-frequency items, and pointers; start every session with `prime`, then `search`; free-form tags are the primary mechanism.

The first `add` does **not** download the ~95 MB embedding model — writes go straight to full-text search. When you want local semantic retrieval, run it explicitly:

```bash
pip install 'heropen[embedding]'
heropen embed          # download the model and generate embeddings for existing entries
```

## Connect your agent (MCP)

Use the dedicated `heropen-mcp` entry point (stdio). `heropen mcp` also works, but prefer the form below in MCP client config:

```json
{
  "mcpServers": {
    "heropen": { "command": "heropen-mcp", "args": [] }
  }
}
```

If `heropen-mcp` is not on your PATH (e.g. `uv tool install` into an isolated environment), replace `command` with the absolute path inside that environment.

For Claude Code / Cursor / Windsurf / WorkBuddy: `heropen auto-setup` attempts to write the known config paths. For **Hermes** and other custom config formats, add the block above to your own `mcp_servers` and restart the agent.

Restart your agent and it has memory. Store a bug fix once, and it is remembered permanently across sessions.

## Tray badge (Windows system tray, `heropen[tray]`)

With `heropen[tray]` installed, run `heropen tray` to pin a native heropen icon in the taskbar tray (same area as WeChat / Bluetooth / IME) — **it covers no window content**. It solves the problem of "a memory system with no presence, easily mistaken for junkware and uninstalled."

```bash
pip install 'heropen[tray]'   # Windows only
heropen tray                  # start the badge (auto-starts on boot by default)
```

Right-click menu:

| Item | What it does |
|---|---|
| Status line | Running · Free / Plus · N agents · N memories |
| Version vX.Y.Z | Opens the changelog |
| Storage mode (per agent) | Each agent independently picks bulk store / per-turn store / auto-detect |
| Open workbench | **Left-clicking the icon does the same** — opens the live-data workbench page |
| Open homepage | Opens heropen.net |
| Check for updates | `pip install -U heropen` |
| Start on boot | Toggle (on by default; writes to the registry Run key) |
| Restart / Quit | Restart to load the latest code / exit completely |

Hovering the icon shows four pieces of self-explanatory status: identity (heropen agent memory system), storage mode, entries stored, and days in use.

> **Real data, no fabrication**: the badge and workbench read the actual `entries` table from your local `~/.heropen/*.db`. Nothing is hardcoded. When no database is readable it shows "no data / 0" and never invents numbers (`--mock` is for demos only). It reflects only the local database — memories from another machine (say, another agent on a different computer) do not appear here, which is expected (each machine's badge is independent).
>
> **Storage modes (wired into the core)**: the three modes control how often and at what granularity memories are committed to heropen — bulk store batches them, per-turn store writes each turn, and auto-detect picks intelligently based on trigger words such as "remember / decided / path / preference". Set per agent. The mode **only governs commits — not retrieval, not what the tray displays**; what gets written is still the local fact store. See [`docs/2.0-开场签名与存储内核.md`](docs/2.0-开场签名与存储内核.md) *(Chinese)*.
>
> **Interface languages (since 2.0.4)**: the tray menu's "Language" item switches between 简体中文 / English / 日本語 / 한국어 / Español / Français / Deutsch / Português / Русский. Menus, hover tooltips, notifications, and the workbench page switch instantly, and the choice persists locally (`~/.heropen/_tray_state.json`).
>
> **No delete feature**: the badge, the workbench, and the CLI expose no deletion entry point whatsoever.

### Opening signature

Before every conversation starts — before the agent actually begins working — heropen drops a single status line into context. **You don't type a command, and you don't open a panel**:

```
heropen ready · 187 local memories · ~3 hours since last conversation · agent=xiaokai
```

It is attached to the **beginning** of the MCP `prime_conversation` return value (the CLI `bootstrap` prints it too).

**Presence in 2.0 = opening signature + tray**: the signature covers "you see it while chatting", the tray covers "it's in your taskbar even when you're not."

## Why heropen

| | heropen (free) | Other options |
|---|---|---|
| Storage | Unlimited | Usually capped |
| Retrieval | Unlimited | Metered per call |
| Network required | No | Yes |
| Data ownership | Your machine | Their servers |
| Install | One `pip install` | Server + configuration |

Free = the complete core feature set, with nothing crippled.

## The position heropen holds

Local-first is not unique to heropen — a dozen similar projects run SQLite + MCP. What heropen genuinely holds, and others have not yet taken, is three things:

- **Private-domain / shared-domain layering across multiple agents**. The free tier gives you 8 fully private agents (high-frequency always-on agents like Hermes and WorkBuddy each get an isolated database), plus an optional shared domain (`_shared`) for exchanging knowledge between agents on demand. Free and Plus differ in capability, not count (Plus adds real features such as skill collection and sharing). Most competitors offer only a single flat namespace.
- **Time awareness before a conversation** (since v1.8.7). At the start of each session the agent automatically receives local time, the time-of-day word, the interval since the last conversation, and whether a sleep cycle was crossed — a temporal baseline on the interaction side that nobody else provides.
- **A zero-dependency install surface**. One line of `pip install heropen`, with no Docker, no Postgres, and no Ollama.

## Privacy commitment

**Your data stays on your machine. No telemetry. No heartbeat ping.** All memories live in a local SQLite database. Vector retrieval uses a **local embedding model by default** (fastembed; `pip install heropen[embedding]`) — fully offline and free. You can also point the `EMBEDDING_ENDPOINT` and `EMBEDDING_API_KEY` environment variables at **your own self-hosted embedding endpoint** (OpenAI-compatible `/v1/embeddings`), so you never pay a third-party cloud.

If neither local embeddings nor a self-hosted endpoint is configured, search falls back to fast full-text (FTS) matching — still fully offline and free. `pip install heropen` works with **zero configuration**; embeddings only *improve* retrieval quality and never block basic use.

**Local capabilities stay local (currently)**: in the current version, local memory read/write, retrieval, and panel capabilities will not be migrated into services that require network access; capabilities already available in the free tier will not be reduced in later versions.

## Why local

When memory is turned into a cloud primitive, your conversation history and working context end up on someone else's servers. heropen chooses the opposite: memories are written to `~/.heropen/`, one SQLite file per agent, and the data never leaves your machine.

**What the cloud gives you is convenience; what local gives you is that only you can take it away.**

## The memories are yours

Memories live in your local `~/.heropen/` directory, one SQLite file per agent. At any time you can:
- **Back up / export**: `heropen export` dumps all memories to a local JSON file (`heropen import` reads it back in);
- **No delete feature**: heropen provides no delete / clear entry point — memories are locally under your control and exportable, but there is deliberately no "delete" button anywhere, to prevent unrecoverable mistakes (there is no cloud copy; deleted means gone). If you really need to clean up, simply remove the corresponding SQLite file under `~/.heropen/` — no need to go through us, and no network required.

A friendlier **Markdown + YAML export** (human-readable, lossless round-trip, re-importable) is on the roadmap.

## How to choose

When choosing a memory solution, don't look only at recall — look at five things:

1. **Deployment cost**: does one `pip install` get it running, or do you need Docker / Postgres / Ollama / an API key?
2. **Dependencies and cost**: does the write path burn LLM tokens? Are there zero external dependencies?
3. **Portability**: can memories be exported as readable files, carried elsewhere, and re-imported?
4. **Secure defaults**: does the listening port bind to loopback or `0.0.0.0` by default? Is it telemetry-free?
5. **Engineering tax**: are write discipline and invalidation mechanisms clear? Is it compatible with prompt caching? How are cross-model capacity limits handled?

Run `heropen doctor` and it self-checks all five, giving each a pass / notice / warning verdict.

## Versions and positioning

| Version | Positioning | Who it's for |
|---|---|---|
| **Free** | Does the one thing — memory — well: local, private, better the more you use it | Anyone who wants long-term memory for their agent, running on their own machine |
| **Plus** | Great memory + serving your work: the first feature is skill collection and sharing, so what you've remembered is reused across agents | Solo founders / indie developers willing to pay for continuous new features |
| **Enterprise** | A memory foundation for many employees × many customers (self-hosted + isolation + audit) | Organizations where many employees serve many customers |

The free version is fully open source (Apache-2.0); Plus / Enterprise are closed source.

## Compliance statement

heropen is a local-first memory and context tool: your data always stays on your machine, fully under your control. The local-first architecture inherently satisfies requirements around user data control and transparency — its operation is transparent and controllable to you.

## Open-source scope

The free version is fully open source (Apache-2.0). The commercial layer (Plus / Enterprise) is closed source.

## Links & support

- Homepage: [heropen.net](https://heropen.net)
- Docs: [heropen.net/docs](https://heropen.net/docs/)
- GitHub: [github.com/Koradji77/heropen](https://github.com/Koradji77/heropen)

⭐ **If you like it, star it** — that's the biggest support you can give us: click [★ Star](https://github.com/Koradji77/heropen).

## Roadmap

- **Currently focused on desktop, local use**. Mobile / cross-device sync is out of scope for now — it conflicts with the default promise that "data never leaves your machine". If it is ever built, it will necessarily take the form of user-owned storage + end-to-end encryption + off by default.
- Markdown + YAML memory export (lossless round-trip).
- More productized private-domain / shared-domain capabilities across agents.

## License

Apache-2.0