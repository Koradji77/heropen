---
name: heropen-quickstart
description: 为 agent 接入 heropen 本地长期记忆系统。当用户需要给 AI agent 添加"记住用户偏好/历史/项目信息"的长期记忆能力、或配置 heropen MCP 时使用。覆盖安装、MCP 配置、常用命令、记忆最佳实践。
---

# heropen 快速接入 skill

heropen 是本地优先的 AI agent 长期记忆系统：数据存本机 SQLite，零遥测，MCP 原生。

## 前置

- Python ≥ 3.10
- 无需 Docker / Postgres / Ollama

## 步骤

### 1. 安装

```bash
pip install heropen
```

### 2. 配置 MCP（自动）

```bash
heropen auto-setup
```

会自动探测本机 agent（Claude Code / Cursor / VS Code / WorkBuddy）并把 heropen 注入其 MCP 配置。重启 agent 即可。

手动配置（任意 MCP 客户端）：

```json
{ "mcpServers": { "heropen": { "command": "heropen", "args": ["mcp"] } } }
```

### 3. 常用命令

```bash
heropen add "项目用 FastAPI + SQLAlchemy"      # 存一条记忆
heropen search "技术栈"                         # 搜记忆
heropen status                                 # 看统计
heropen export                                 # 导出 JSON 备份
heropen panel                                  # 打开本地记忆面板
```

### 4. MCP 工具（agent 侧）

- `search_memory(query, limit, agent, date_from, date_to)` —— 三层回退：向量 → 全文 → 模糊；返回含 `time_context` 与 `hygiene_flags`
- `add_memory(section, content, tags, agent)` —— 存记忆
- `prime_conversation(agent)` —— 对话前获取本地时间上下文（**每次开场必调**）
- `list_memory` / `update_memory` / `health` / `session_checkpoint` / `session_recover`

## 记忆最佳实践

- agent 开场先调 `prime_conversation` 或 `search_memory`（二者任一即满足时间感知约定）。
- 主动存：用户分享偏好 / 约定 / 项目信息时，调 `add_memory` 而不是等用户说"记住"。
- 数据主权：记忆在本机 `~/.heropen/`；`heropen export` 可带走，`heropen delete <id>` 可删。heropen 不上传、不追踪。

## 合规提醒

heropen 是记忆工具，不拟人、不陪聊。不要在 agent 人设里把 heropen 描述为"伙伴 / 朋友"。
