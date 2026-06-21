# heropen

> 给本地大模型 Agent 装上长期记忆。一次 `pip install`，不用每次从头教。

你的 agent 每次开新会话都要重新交代一遍——项目怎么组织的、习惯用什么工具、之前踩过的坑、写好的配置和 skill。heropen 就是解决这个的：装完以后，agent 自己记得住。

**数据 100% 在你自己的机器上**，SQLite + 向量搜索，不联网、不上云。

## 安装

```bash
pip install heropen
heropen auto-setup
```

两行搞定。会装好数据库、自动检测你的 agent（Claude Code / Cursor / Windsurf 等），配好 MCP。

装完**重启 agent**，它就有记忆了。

如果你用 `heropen install` 可以走交互式向导手动选择。

## 快速开始

```bash
# 存一条记忆
heropen add "项目用 FastAPI + SQLAlchemy，测试走 pytest"

# 搜索之前记过的
heropen search "项目技术栈"

# 查看状态
heropen status
```

## 接入 Agent（MCP 协议）

主流 agent 工具都能接。`heropen auto-setup` 会自动检测并配置。

也可以手动配，在 agent 的配置里加上：

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

重启 agent，它就有记忆了。

接上 MCP 之后，agent 甚至可以自己在干活过程中自动存和查——遇到一个坑记下来，下次另一个会话里遇到同样问题，自己翻出来。

## 为什么是 heropen

| | heropen 免费版 | 其他方案 |
|---|---|---|
| 记忆条数 | 不限 | 通常有限制 |
| 搜索次数 | 不限 | 按量收费 |
| 联网 | 不需要 | 必须联网 |
| 数据在哪 | 你的机器 | 别人的服务器 |
| 安装 | `pip install` 一行 | 搭服务、配环境 |

免费版就是全部核心，不阉割。

## 支持

- 官网：[ksmn.cc/heropen](https://ksmn.cc/heropen)
- 文档：[ksmn.cc/heropen/docs](https://ksmn.cc/heropen/docs)
- 问题反馈：[GitHub Issues](https://github.com/Koradji77/heropen/issues)

## 协议

MIT License
