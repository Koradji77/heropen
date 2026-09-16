# heropen 官方使用方法（SSOT · 消费版）

> 版本：与 heropen **1.9.5+** 对齐  
> 受众：把 heropen 接到自己 agent 上的用户，以及负责写 system prompt / 铁律的 agent  
> 一句话：**事实只进 heropen；热层只留铁律、高频条目和检索指针。召回靠开场主动搜，不靠模型「想起来」。**

---

## 1. 为什么要这样用

多数 agent 有两层「记忆」：

| 层 | 典型形态 | 问题 |
|----|----------|------|
| **热层** | 宿主自带 memory / notes / 长期提示 | 占上下文、容易双写、一满就乱删 |
| **冷层** | heropen（本机 SQLite） | 不搜就等于不存在 |

若两边都当「事实库」，会出现：同一事实两处各写一版、一处更新另一处过期、上下文满了靠模型自觉搬运——**必然偶发遗漏**。

官方推荐的体系是 **SSOT（Single Source of Truth）**：

```
一个事实库（heropen）
  + 一个自动缓存层（宿主热记忆）
  + 纪律主动召回（开场必搜）
```

把「记不记得住」从模型自觉，尽量变成可执行的使用纪律（后续版本会把开场召回 / 压缩下沉做成内核机制；在那之前，请按本文配置铁律）。

---

## 2. 两层怎么分工

### 2.1 heropen = 唯一事实库

适合写入 heropen 的内容：

- 稳定偏好、约定、身份与关系
- 项目事实、技术选型、路径与账号偏好（不含密钥正文）
- 决策与结论（含日期 / 原因）
- 需要跨会话、跨 agent 复用的任何事实

写入原则：

- **一条一事**，写清楚到「以后的自己不看聊天也能懂」
- **主打自由标签 `tags`**，少纠结固定分类
- `section` 只做可选大桶（如 `用户偏好` / `项目` / `约定`），允许空或粗分
- 更新事实时优先 `update_memory`，避免平行再插一条导致漂移

### 2.2 宿主热记忆 = 缓存，不是第二事实库

热层只保留三类东西：

1. **铁律**（本文件的精简版，见 §6）——怎么用 heropen  
2. **高频热记忆**——最近反复用到、短、值得常驻上下文的几条  
3. **检索指针**——指向 heropen 的 `entry_id` / 关键词 / 标签，而不是事实全文双写

不要：把同一段事实既写进热层全文、又写进 heropen，然后只改其中一处。

### 2.3 下沉（热 → 冷）

当热层变满或整理时：

- **优先搬走低频、很长、已过时效的内容** → `add_memory` 进 heropen  
- 热层留下一行指针即可，例如：`项目栈 → heropen tags:tech-stack` 或 `entry_id=42`  
- 不要按「字符占用率到了就整页清空」一刀切；先搬最不常碰的

> 内核尚未提供压缩钩子自动下沉（需求 B）。在钩子上线前，靠铁律 + 定期整理完成。

---

## 3. 开场协议（主动召回）

**每一次新对话开场**，在寒暄或办事之前：

1. 调用 `prime_conversation`（拿本地时间、距上次间隔、时段词）  
2. **再按本轮主题关键词调用 `search_memory`**（或宿主等价检索），把 top 结果读进上下文  
3. 再开始回答用户

说明：

- 只调 `prime_conversation` 而不搜，仍等于「库里有、你没用」  
- `search_memory` 返回里若已带 `time_context`，可视为已满足时间感知；仍建议对关键主题再搜一轮  
- 条数建议：默认 3～5 条；可按任务加大

> 内核尚未把「开场自动召回」做进 `prime_conversation`（需求 A）。在机制上线前，**必须把本协议写进 agent 铁律**。

---

## 4. 写入与标签

### 4.1 什么时候写

主动写，不要等用户说「记住这个」：

- 用户陈述偏好、习惯、约束  
- 达成约定、拍板决策  
- 出现稳定的项目 / 人名 / 路径信息  
- 对话结束前，把本轮新增的稳定事实落盘  

### 4.2 怎么打标签

| 推荐 | 不推荐 |
|------|--------|
| 多打自由 tags：`偏好,咖啡,工作流` | 为选一个完美 section 卡住 |
| 英文短词或中英均可，保持前后一致 | 每次换一套同义词标签 |
| section 粗分或省略 | 把整段聊天塞进一条 |

CLI 示例：

```bash
heropen add --content "部署目标是局域网 Ubuntu 主站，Windows 只做 Office 导出" \
  --tags "部署,Ubuntu,教研工作台" \
  --section "项目"
```

MCP 示例：调用 `add_memory`，`tags` 填逗号分隔关键词，`section` 可粗可空。

### 4.3 检索路径（已实现）

heropen 检索顺序：**向量 → 全文 → 模糊**。  
没有本地向量模型时自动走全文，**写入不会被下载模型卡住**（1.9.5+）。需要本地语义检索时：

```bash
pip install 'heropen[embedding]'
heropen embed
# 或使用官方离线包（见 Release 附件 heropen-model-offline-bundle.zip）
```

---

## 5. 接入与常用命令

### 5.1 安装

```bash
pip install heropen                 # 核心：MCP + 全文
pip install 'heropen[embedding]'    # 可选：本地向量
```

### 5.2 MCP 配置（推荐）

```json
{
  "mcpServers": {
    "heropen": {
      "command": "heropen-mcp",
      "args": []
    }
  }
}
```

- Claude / Cursor / Windsurf / WorkBuddy：可试 `heropen auto-setup`  
- **Hermes**：请手动写入 `~/.hermes/config.yaml` 的 `mcp_servers`，`command` 建议用 `heropen-mcp` 的**绝对路径**（`uv tool` 隔离安装时 PATH 里常常没有）

Hermes 示例：

```yaml
mcp_servers:
  heropen:
    command: 'C:/path/to/heropen-mcp.exe'
    args: []
    env:
      HF_HUB_OFFLINE: '1'              # 模型已就位后建议打开
      HEROPEN_NO_UPDATE_CHECK: '1'
    connect_timeout: 60
    timeout: 120
```

### 5.3 CLI

```bash
heropen add "……" --tags "a,b"     # 写入
heropen search "关键词"            # 检索
heropen status                     # 统计
heropen panel                      # 本地面板（存在感入口）
heropen export                     # 备份
```

### 5.4 MCP 工具一览

| 工具 | 用途 |
|------|------|
| `prime_conversation` | 开场时间上下文（必调） |
| `search_memory` | 检索事实（开场 + 提到人名/项目/偏好时主动调） |
| `add_memory` | 写入事实 |
| `update_memory` | 更新已有条目 |
| `list_memory` / `health` | 浏览与健康检查 |
| `session_checkpoint` / `session_recover` | 会话断点 |

---

## 6. 可贴进 agent 的铁律（精简版）

将下面整段放入宿主 system prompt / 记忆铁律（可按产品改名，语义勿删）：

```text
【heropen 铁律 v2 · SSOT】
1. 事实只存 heropen。宿主热记忆只保留：本铁律、少量高频短句、指向 heropen 的指针（entry_id/tags）。禁止同一事实双写全文。
2. 每次开场：先 prime_conversation，再按本轮主题 search_memory；不搜就当作库里没有。
3. 用户给出偏好/约定/项目事实时主动 add_memory，不要等「记住」。
4. 写入主打自由 tags；section 只是可选大桶，允许粗分或省略。
5. 热层将满时：优先把低频、长、过时内容下沉到 heropen，热层改留指针。
6. 更新已有事实用 update_memory，避免平行插入导致漂移。
7. 密钥/密码不要写入记忆正文。
```

---

## 7. 和后续内核能力的关系

| 能力 | 现状（使用纪律） | 目标（内核机制） |
|------|------------------|------------------|
| 开场自动召回 | 铁律强制 `prime` + `search` | 扩展 `prime_conversation` 自动带 top-k 历史 |
| 压缩自动下沉 | 人工 / 铁律下沉 | 压缩钩子：移出热层时原子写入 heropen + 留指针 |
| 按价值淘汰 | 先搬低频 | `last_used_at` / hit 打分，给出建议下沉列表 |
| 弱分类 | 本文已规定 tags 优先 | 写入时标签建议、去重合并 |

使用方法本文**现在就可以用**；不需要等内核 A/B 做完。

---

## 8. 隐私与定位

- 数据默认只在本机 `~/.heropen/`，无遥测。  
- heropen 负责「记」，不负责「陪」——配置人设时不要把它包装成情感伙伴。  
- 导出：`heropen export`；删除：`heropen delete <id>`。

---

*维护：KSMN / heropen。消费版（免费 + Plus）默认按本文使用；企业版 / Flex 的组织策略另文说明。*
