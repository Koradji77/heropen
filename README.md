# heropen

> 给你的 AI agent 长期记忆。数据留在你本机，检索零 token 成本。

## 名字由来

名字来自两处：**her** 取自 Hermes（你正在与之对话的这个 agent），**open** 取自 OpenClaw（开放）。her + open 拼在一起就是 heropen。

拆开来看，heropen 以 **hero** 开头——呼应漫威超级英雄的意象。它是一个记忆层：记住你，并替你的 agent 把事情记下来。

## 安装

```bash
pip install heropen
```

重启你的 agent，完成。

首次启动会自动探测你的 agent（Claude Code、Cursor、Windsurf 或任意 MCP 客户端），建好数据库并注册记忆工具。你的 agent 会注意到这次新安装，并引导你完成设置。

## 30 秒快速上手

```bash
# 存一条记忆
heropen add "项目使用 FastAPI + SQLAlchemy，测试用 pytest"

# 搜索记忆
heropen search "项目技术栈"

# 查看状态
heropen status

# 诊断问题
heropen diagnose
```

## 接入你的 agent（MCP）

兼容任意支持 MCP 的 agent。v1.8+ 自动探测并配置，无需手动步骤。

也可手动加到 agent 配置：

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

重启 agent，它就有了记忆。把一条 bug 修复存一次，跨会话永久记住。

## 隐私承诺

**数据留在你本机。无遥测。无心跳 ping。** 所有记忆存于本地 SQLite 数据库。向量检索默认使用**本地 embedding 模型**（fastembed，执行 `pip install heropen[embedding]` 即可）——完全离线、零成本。你也可以把 `EMBEDDING_ENDPOINT` 和 `EMBEDDING_API_KEY` 环境变量指向**你自托管的 embedding endpoint**（兼容 OpenAI 的 `/v1/embeddings`），这样永远不会向任何第三方云付费。记忆文本仅用于生成向量，绝不上报。

如果本地 embedding 模型与自托管 endpoint 都没配置，搜索会自动降级为快速全文（FTS）匹配——依然完全离线、零成本。所以 `pip install heropen` 可**零配置**直接使用；embedding 只是*提升*检索质量，绝不会卡住基础使用。

**本地能力不上收（当前）**：当前版本的本地记忆读写、检索与面板能力不会被迁移为必须联网的云端服务；免费层已提供的能力不会在后续版本中被削减。可选的联网加速项（例如你自托管的 embedding endpoint）不属于此承诺范围——它们默认关闭，启用与否完全由你决定。

## 记忆属于你

记忆存在你本机的 `~/.heropen/` 目录下，每个 agent 一个 SQLite 文件。你可以随时：
- **备份 / 导出**：`heropen export` 把全部记忆导出为本地 JSON（`heropen import` 可再导入）；
- **删除 / 清空**：`heropen delete <id>` 删单条，`rm ~/.heropen/*.db` 清空整个库——不需要经过我们，也不需要联网。

更友好的 **Markdown + YAML 导出**（人类可读、无损往返、可再导入）已在路线图中规划。目标是「可读、可带走、可回来」，而不是「与谁互通」。

## 合规声明

heropen 是本地优先的记忆工具：数据始终在你的机器上，不拟人、不陪聊、不充当「伙伴」。我们遵循《人工智能拟人化互动服务管理暂行办法》的相关要求——本地优先的架构天然满足其中关于用户数据控制权与透明度的条款；heropen 不会以人格化方式与用户互动。

## 开源范围

免费版完全开源（Apache-2.0）。商业层（Plus / 企业版）闭源。

## 为什么选 heropen

| | heropen（免费） | 其他方案 |
|---|---|---|
| 存储 | 不限量 | 通常有限额 |
| 检索 | 不限次 | 按次计费 |
| 需要联网 | 否 | 是 |
| 数据归属 | 你的机器 | 他们的服务器 |
| 安装 | 一行 `pip install` | 服务器 + 配置 |

免费 = 完整核心功能，无功能阉割。

## heropen 的差异点

本地优先不是 heropen 独有的卖点——已经有十几个同类项目走 SQLite + MCP。heropen 真正占住、且别人还没占的位置是这三件事：

- **多 agent 的私有域 / 共享域分层**。免费层给 2 个完全私有的 agent（Hermes / WorkBuddy 这类高频常驻 agent 各占一个隔离库），再加一个可选的共享域（`_shared`）让 agent 之间按需交换知识。大多数竞品只有一个扁平的命名空间。
- **对话前的时间感知**（v1.8.7 起）。agent 每次开场会自动拿到本地时间、时段词、距上次对话间隔、是否跨睡眠周期——交互侧的时间基准，目前没人做。
- **零依赖的安装面**。`pip install heropen` 一行即可，不需要 Docker、不需要 Postgres、不需要 Ollama。同类里少有能做到「无前置依赖就能跑」的。

## 链接

- 官网：[heropen.net](https://heropen.net)
- 文档：[heropen.net/heropen/docs](https://heropen.net/heropen/docs)
- GitHub：[github.com/Koradji77/heropen](https://github.com/Koradji77/heropen)

## 路线图

- **当前聚焦桌面本地使用**。移动端 / 跨设备同步不在当前范围内——它与「数据不出本机」的默认承诺冲突，我们不会为了「表态」而做半成品。若未来做，也必然是用户自有存储 + 端到端加密 + 默认关闭的形式。
- Markdown + YAML 记忆导出（无损往返）。
- 更多 agent 私有 / 共享域的产品化能力。

## 许可证

Apache-2.0
