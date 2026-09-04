# heropen

> **专业解决记不住的问题，越用越懂你。**

本地优先的 AI 记忆系统。记忆存在你自己的机器上，不上传、不同步、不收走；它自动整理与裁剪，让你越用越准、越用越懂你。

⭐ **这个工具帮到你了？点个 star 支持一下** → 右上角 [★ Star](https://github.com/Koradji77/heropen)，让更多 agent 用户少走弯路。

## 30 秒看懂

- **一行装好，零配置**：`pip install heropen`，重启 agent 即用，不用 Docker / Postgres / API key。
- **你的 agent 从此有长期记忆**：跨会话记得人、记得事、记得上下文——不再「聊完就忘」。
- **数据 100% 留本机**：无遥测、无云端同步、无账单。记忆只在你机器上，只有你拿得走。

## 安装

```bash
pip install heropen
```

重启你的 agent，完成。首次启动会自动探测你的 agent（Claude Code、Cursor、Windsurf 或任意 MCP 客户端），建好数据库并注册记忆工具。

## 30 秒快速上手

```bash
heropen add "项目使用 FastAPI + SQLAlchemy，测试用 pytest"   # 存一条记忆
heropen search "项目技术栈"                                   # 搜记忆
heropen status                                                # 看状态
heropen diagnose                                              # 诊断问题
```

## 接入你的 agent（MCP）

兼容任意支持 MCP 的 agent。v1.8+ 自动探测并配置，无需手动步骤。

```json
{
  "mcpServers": {
    "heropen": { "command": "heropen", "args": ["mcp"] }
  }
}
```

重启 agent，它就有了记忆。把一条 bug 修复存一次，跨会话永久记住。

## 为什么选 heropen

| | heropen（免费） | 其他方案 |
|---|---|---|
| 存储 | 不限量 | 通常有限额 |
| 检索 | 不限次 | 按次计费 |
| 需要联网 | 否 | 是 |
| 数据归属 | 你的机器 | 他们的服务器 |
| 安装 | 一行 `pip install` | 服务器 + 配置 |

免费 = 完整核心功能，无功能阉割。

## heropen 占住的位置

本地优先不是 heropen 独有的卖点——已经有十几个同类项目走 SQLite + MCP。heropen 真正占住、且别人还没占的位置是这三件事：

- **多 agent 的私有域 / 共享域分层**。免费层给 2 个完全私有的 agent（Hermes / WorkBuddy 这类高频常驻 agent 各占一个隔离库），再加一个可选的共享域（`_shared`）让 agent 之间按需交换知识。大多数竞品只有一个扁平的命名空间。
- **对话前的时间感知**（v1.8.7 起）。agent 每次开场会自动拿到本地时间、时段词、距上次对话间隔、是否跨睡眠周期——交互侧的时间基准，目前没人做。
- **零依赖的安装面**。`pip install heropen` 一行即可，不需要 Docker、不需要 Postgres、不需要 Ollama。

## 隐私承诺

**数据留在你本机。无遥测。无心跳 ping。** 所有记忆存于本地 SQLite 数据库。向量检索默认使用**本地 embedding 模型**（fastembed，执行 `pip install heropen[embedding]` 即可）——完全离线、零成本。你也可以把 `EMBEDDING_ENDPOINT` 和 `EMBEDDING_API_KEY` 环境变量指向**你自托管的 embedding endpoint**（兼容 OpenAI 的 `/v1/embeddings`），这样永远不会向任何第三方云付费。

如果本地 embedding 与自托管 endpoint 都没配置，搜索会自动降级为快速全文（FTS）匹配——依然完全离线、零成本。`pip install heropen` 可**零配置**直接使用；embedding 只是*提升*检索质量，绝不会卡住基础使用。

**本地能力不上收（当前）**：当前版本的本地记忆读写、检索与面板能力不会被迁移为必须联网的云端服务；免费层已提供的能力不会在后续版本中被削减。

## 为什么是本地

当记忆被做成云端原语，你的对话历史、工作上下文就被存在别人的服务器上。heropen 的选择相反：记忆写在 `~/.heropen/`，一个 agent 一个 SQLite 文件，数据物理不出本机。

**云能给你的，是方便；本地给你的，是只有你拿得走。**

## 记忆属于你

记忆存在你本机的 `~/.heropen/` 目录下，每个 agent 一个 SQLite 文件。你可以随时：
- **备份 / 导出**：`heropen export` 把全部记忆导出为本地 JSON（`heropen import` 可再导入）；
- **删除 / 清空**：`heropen delete <id>` 删单条，`rm ~/.heropen/*.db` 清空整个库——不需要经过我们，也不需要联网。

更友好的 **Markdown + YAML 导出**（人类可读、无损往返、可再导入）已在路线图中规划。

## 如何选型

选记忆方案，不只看召回率，看五件事：

1. **部署门槛**：`pip install` 一行就能跑，还是要 Docker / Postgres / Ollama / API key？
2. **依赖与成本**：写入路径烧不烧 LLM token？是否零外部依赖？
3. **可移植性**：记忆能不能导出成可读文件、带走、再导入？
4. **安全默认值**：监听端口默认绑 loopback 还是 `0.0.0.0`？是否零遥测？
5. **工程税**：写入纪律与失效机制是否清晰？与 Prompt Cache 是否冲突？跨模型容量上限如何处理？

跑一句 `heropen doctor`，上面五项会逐项给你「通过 / 提示 / 告警」三态自检。

## 版本与定位

| 版本 | 定位 | 适合谁 |
|---|---|---|
| **免费** | 专业解决记不住的问题，越用越懂你 | 任何想给 agent 装长期记忆的人，本机即用 |
| **Plus** | 收费档 = 真功能开发档（如多端备份） | 一人公司 / 独立开发者，愿意为持续新功能付费 |
| **企业版** | 多员工 × 多客户的记忆底座（自托管 + 隔离 + 审计） | 多员工服务多客户的机构 |

免费版完全开源（Apache-2.0）；Plus / 企业版闭源。

## 合规声明

heropen 是本地优先的记忆与上下文工具，你的数据始终保存在本机、由你完全掌控。本地优先的架构天然满足关于用户数据控制权与透明度的要求，运行过程对你透明、可控。

## 开源范围

免费版完全开源（Apache-2.0）。商业层（Plus / 企业版）闭源。

## 链接 & 支持

- 官网：[heropen.net](https://heropen.net)
- 文档：[heropen.net/heropen/docs](https://heropen.net/heropen/docs)
- GitHub：[github.com/Koradji77/heropen](https://github.com/Koradji77/heropen)

⭐ **喜欢就 star**，这是对我们最大的支持：点 [★ Star](https://github.com/Koradji77/heropen)。

## 路线图

- **当前聚焦桌面本地使用**。移动端 / 跨设备同步不在当前范围内——它与「数据不出本机」的默认承诺冲突。若未来做，也必然是用户自有存储 + 端到端加密 + 默认关闭的形式。
- Markdown + YAML 记忆导出（无损往返）。
- 更多 agent 私有 / 共享域的产品化能力。

## 许可证

Apache-2.0
