# heropen

> 让 AI Agent 拥有长期记忆。数据不出本机，搜索不用 token。

## 为什么叫 heropen

名字来自两个地方：**her** 取自 Hermes（你阅读这里用的 Agent），**pen** 取自 Open（OpenClaw，开放）。合起来就是 heropen。

hero 也撞上了漫威的超级英雄——一个记住你、替你写下的伙伴。

## 安装

```bash
pip install heropen
```

重启你的 Agent。仅此而已。

首次启动自动检测 Agent（Claude Code, Cursor, Windsurf 或任何 MCP 客户端），配置数据库并注册记忆工具。你的 Agent 会注意到新安装并引导你完成设置。

## 30 秒开箱

```bash
# 保存一条记忆
heropen add "项目使用 FastAPI + SQLAlchemy，测试用 pytest"

# 搜索记忆
heropen search "项目技术栈"

# 查看状态
heropen status

# 诊断问题
heropen diagnose
```

## 连接你的 Agent（MCP）

支持任何兼容 MCP 的 Agent。v1.8+ 自动检测并配置——无需手动操作。

或者手动加到你的 Agent 配置：

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

重启 Agent，它就有记忆了。遇到 bug 存一次，跨会话永久记住。

## 隐私承诺

**数据不出本机。无遥测。无心跳上报。** 所有记忆存储在本地 SQLite 数据库中。向量检索默认调用 SiliconFlow embedding API（使用你的密钥），记忆文本仅用于生成向量，不上报。也支持纯本地 embedding（fastembed 可选依赖，`pip install heropen[embedding]`）。

## 开源范围

免费版全量开源（Apache-2.0），商业层（Plus/Enterprise）闭源。

## 为什么选 heropen

| | heropen（免费） | 其他方案 |
|---|---|---|
| 存储空间 | 无限制 | 通常有上限 |
| 搜索次数 | 无限制 | 按次计费 |
| 需要联网 | 否 | 是 |
| 数据归属 | 你的机器 | 他们的服务器 |
| 安装 | `pip install` 一条命令 | 服务器 + 配置 |

免费 = 完整核心功能。没有功能阉割。

## 链接

- 主页: [ksmn.cc/heropen](https://ksmn.cc/heropen)
- 文档: [ksmn.cc/heropen/docs](https://ksmn.cc/heropen/docs)
- GitHub: [github.com/Koradji77/heropen](https://github.com/Koradji77/heropen)

## License

Apache-2.0
