# heropen 升级指南

## v2.0.7 → v2.0.8

> 说明：本版为**数据完整性与诊断准确性修复版**，由一台 macOS (15.7 / arm64 / Python 3.12) 实机安装反馈驱动。无记忆数据格式变更；**已被历史缺陷破坏的库会在首次连接时自动补建 schema（自愈），无需手工修库**。

### 改动摘要

- 🔴 **修复跨租户数据错位（P0）**：`_connect()` 为跨 agent 搜索 ATTACH 了 `_shared.db`，而 `init_db()` 用未限定 schema 的 `PRAGMA table_info(entries)` 判断是否新库 —— 该探测会解析到 `shared.entries`，误判「已有表」从而跳过 `CREATE TABLE entries`。结果是 agent 自己的库永远缺 `entries` 主表，所有读写靠未限定表名静默落到 `_shared.db`（`entries.agent` 标着该 agent，数据却在共享库）。更严重的是同一分支里的 `ALTER TABLE entries` 会**跨租户改写 `_shared.db` 的结构**。
  - 修法（三层）：① `init_db()` 走 `_connect(..., attach_shared=False)`，schema 探测/DDL 路径一律不挂载 shared；② 所有探测与 DDL 限定到 `main.`（`PRAGMA main.table_info`、`main._meta`、`ALTER TABLE main.entities`）；③ 判定「是否已初始化」改为按真实标准 —— `main.entries` 是否存在，而不是文件大小（WAL pragma 会把空库撑到 4096 字节，导致建表失败过的库永远看起来「已初始化」、再无自愈机会）。
  - 附带说明：ATTACH 的 `shared` schema 在本包内**从未被任何 SQL 引用**（跨 agent 读取走 `search_shared()`，它自开独立连接），故保留仅为兼容外部调用方，且不再出现在 schema 路径上。
- **`health` 不再因缺少 PyYAML 而误报 degraded**：`_mcp_registered()` 是三态（True/False/None），但 `health()` 把「无法判定」（如存在 Hermes 的 `config.yaml` 而 PyYAML 不可用）当成「未注册」处理，于是配置完全正确的安装也报 `status: degraded`。现在 `None` 只记为提示信息，`status` 仅在**数据库断连**或**确认未注册**时降级。该工具通常正是通过一个可用的 MCP 注册被调用的，此前等于在 MCP 调用内部宣称「MCP 未注册」。
- **PyYAML 升级为基础依赖**：它用于读写 YAML 客户端配置（Hermes 等）。此前不在依赖中，导致 `auto_mcp` 的 `_inject_into_yaml_config()` 在缺它时 `return "skipped"` —— 而调用方不处理 `skipped`，于是 YAML 客户端被**静默跳过**，且 `print_setup_summary()` 因计数为 0 反过来告诉用户「没找到任何配置文件」。现在：依赖入基础包（全平台有 wheel + sdist 兜底，不影响 ARM64 可装）；`skipped` 进入结果并在摘要里可见，附修复提示。
- **`AGENTS` 快照导致的结论不一致**：`AGENTS` 是 import 期快照，长驻的 MCP 进程看不到启动后才创建的 `agent-config.json`，于是 `list`/`search` 与 `health` 对同一状态给出不同答案（list 有 agent、health 报 0）。新增 `core.current_agents()` 做每次调用的新鲜读取，`health` 改用它；`AGENTS` 保持快照语义不变（避免改动全局对象在长驻进程里产生并发竞态）。
- **向量检索的回退不再静默**：`search_vector` 的最高相似度低于阈值时直接返回 `None` 触发 FTS/LIKE 回退，调用方只看到 `method: "like"`，会误判「向量没工作」。现在阈值可由 `HEROPEN_VECTOR_MIN_SIM` 配置（默认 0.5，非法值回退默认、越界钳制到 [0,1]），且因低于阈值而回退时，`search_memory` 返回值附带 `vector_fallback: {best_sim, floor}`，可区分「向量没匹配到」与「向量弱匹配被主动丢弃」。
- **`heropen diagnose` 缺 `agent-config.json` 时不再中止**：此前直接 `_fail` + `return`，跳过后续全部检查（数据库、MCP 模块、版本），而此时 MCP 完全可以工作。改为 warning 并继续。
- **ARM64 embedding 提示按平台区分**：Apple Silicon 上 onnxruntime **有官方 wheel**，此前却提示「ARM64 上常见：无匹配 wheel 或 Python 版本过新」，与同函数返回的正确 hint 自相矛盾，把人引向不存在的平台问题。现在 macOS arm64 提示「通常是尚未安装」，Linux aarch64 保留原提示。
- **新增回归门禁**：`scripts/assert_regressions.py`（11 条规则，每条带注入锚点），接入 CI —— `build-test` 跑 `--once`，独立 `gate-bidir` job 跑 `--bidir`（把历史缺陷逐条重新注入实现、要求门禁失败，再还原要求通过）并在最后 `git diff --exit-code` 确认注入/还原未改动工作树。

### 升级

```bash
pip install --upgrade heropen
```

### 行为兼容

- 已有记忆数据无需迁移，无数据格式变更。
- **受上述 P0 影响过的库会在首次 `conn()` 时自动补建 `entries` 等表（自愈）**；已写入 `_shared.db` 的历史条目**不会**被自动搬迁（heropen 只标不删），如需归位请显式导出/导入。
- `health` 的 `mcp_config_found` 新增 `null`（无法判定）取值，与 `true`/`false` 并列；`status` 判定相应放宽。依赖该字段的下游若做过严格布尔判断，请注意 `null` 分支。
- `search_memory` 返回值可能多出 `vector_fallback` 字段（仅在弱匹配回退时出现）。
- MCP 工具集合（12 个）与免费额度（8 个 agent）不变。

---

## v2.0.6 → v2.0.7

> 说明：本版为**能力回流版**——把 macOS 桌面版已验证的「话轮摄取」能力并入通用版，修复「宿主未接钩子时自动存档空转」的缺口。无记忆数据格式变更，无行为破坏。

### 改动摘要

- **新增话轮摄取能力（回流自 macOS 版，性质为修补缺口）**：此前 heropen 是被动 MCP server，依赖宿主每轮调用 `auto_save_turn`；**当宿主未接钩子时整条自动累计链路空转（接收数恒为 0）**。本版合入 `turn_ingest.py` / `turn_storage.py` / `turn_ingest_scheduler.py`，不依赖宿主配合，直接读取客户端已落盘的会话记录补交存储。三个模块经实测为平台中立（0 处 macOS 专属 API）。
- **MCP 工具 9 → 12**：新增 `ingest_local_turns`（主动读本机会话记录补交存储）、`turn_storage_status`（只读查询事务式话轮存储状态）、`flush_turns`（显式保存指定会话的待存尾批）。
- **新增连接状态判定模块**：`connection_status.py`（回流），其中 macOS 专属的 `proc_pidinfo` / `proc_pidpath` 已补 **Windows 分支**（kernel32 Toolhelp 快照 / `QueryFullProcessImageNameW`，纯 ctypes，无新依赖）。
- **工作台与内核差异合并**：`viewer_server.py`（+110 行）、`core.py`（+16 行）经三方合并（base=2.0.5）合入，冲突仅版本号一处，已保留主线值。
- CLI 子命令与免费额度（8 个 agent）不变。

### 升级

```bash
pip install --upgrade heropen
```

### 行为兼容

- 已有记忆数据无需迁移。
- MCP 工具集合由 9 个增至 **12 个**（只增不减，旧工具签名不变）；免费额度（8 个 agent）不变。
- 宿主已接 `auto_save_turn` 钩子的用户无感知；未接钩子的用户升级后「自动记住」开始真正生效。

---

## v2.0.5 → v2.0.6

> 说明：本版为**安装体验修复版**，无记忆数据格式变更，无行为破坏。

### 改动摘要

- **Windows 托盘角标改为默认安装**：此前托盘依赖（`pystray` / `Pillow`）放在可选扩展 `heropen[tray]` 里，而官网与安装脚本给的都是裸 `pip install heropen` —— 结果**装完看不到角标**，而角标正是图形工作台与「存储模式」的唯一入口。现在这两个依赖已按 `platform_system == "Windows"` 并入基础依赖，**Windows 上 `pip install heropen` 即带角标**；macOS / Linux 保持精简，不受影响。
- **安装完自动拉起角标**：新增 `heropen.auto_tray`。装完后自动以 `pythonw` 静默启动角标（不弹控制台黑窗），并沿用既有的开机自启（`HKCU` Run 键）。覆盖两条路径：安装脚本收尾、以及 `.pth` 首次启动钩子。
- **安装脚本改用 `heropen[tray]`**：作为双保险，老版本基础包不含该依赖时也能装上；并在收尾以隐藏方式拉起角标。
- **文档数字勘误**：`UPGRADE.md` 中「MCP 工具集合（8 个）与免费额度（6 个 agent）」更正为**（9 个）与（8 个 agent）**，与代码 `FREE_AGENT_LIMIT = 8` 及实际注册的 9 个 MCP 工具一致。
- **注册表元数据一致性**：`server.json` 顶层 `version` 与 `packages[0].version` 对齐（此前分别为 2.0.5 与 2.0.2，会让 MCP 注册表指向错误的包版本）。

### 升级

```bash
pip install --upgrade heropen
```

### 行为兼容

- 已有记忆数据无需迁移。
- CLI 子命令、MCP 工具集合（9 个）与免费额度（8 个 agent）均不变。
- 角标若此前已手动装过 `heropen[tray]`，本版无变化；未装过的用户升级后即自动获得。

---

## v1.9.5 → v1.9.6

> 说明：本版为**元数据 / 分发版**，无记忆数据格式变更，无行为破坏。核心目的是把 heropen 发布到 **MCP 官方注册表**（MCP Registry），让 MCP 客户端与各目录能自动发现它。

### 改动摘要

- **接入官方 MCP 注册表**：新增 `server.json`（`net.heropen/heropen`，PyPI 包 + `uvx heropen mcp` 启动）。README 增加 `mcp-name:` 归属标记——注册表用它校验「这个 PyPI 包确实属于这个 server 名」。
- **修复 `initialize` 握手中的版本号**：此前 MCP 客户端看到的 `serverInfo.version` 是 **mcp SDK 的版本**（例：`1.30.0`），而不是 heropen 的版本。现在正确上报 heropen 自身版本。这会影响客户端展示与目录收录信息。
- **文档链接修正**：`pyproject.toml` 的 `Documentation`、README、安装引导文案统一指向 `https://heropen.net/docs/`（旧路径 `heropen.net/heropen/docs/` 已 301，但不再写死）。
- **许可声明修正**：代码内面板文案由 `MIT License` 改为实际的 `Apache-2.0 License`。

### 升级

```bash
pip install --upgrade heropen
```

### 行为兼容

- 已有记忆数据无需迁移。
- CLI 子命令、MCP 工具集合（9 个）与免费额度（8 个 agent）均不变。

---

## v1.9.3 → v1.9.5

> 说明：**1.9.4 已于 2026-09-11 发布**（GitHub tag `v1.9.4` + PyPI）。1.9.5 在 1.9.4 基础上修复真实 agent 安装反馈，并**补回** 1.9.4 分支遗漏的免费额度改动（当时另一条开发线从 1.9.3 分出，未包含 1.9.4 提交）。发版由 AK 负责（GitHub + PyPI），开发侧不再自行 push tag / 上传。

### 改动摘要

**A. 免费额度与分档口径（1.9.4 内容，本版合并进来）**

- **免费版 agent 数量统一为 6**：`FREE_AGENT_LIMIT` 由 2 改为 6，与 Plus/Pro 一致（各档均为 6 个隔离 agent）。消费版定价已从「按数量分」转为「按功能分」（Plus 提供 skill 收集与共享），数量不再作为分档边界。额度仍可由 `agent-config.json` 的 `edition` 或环境变量 `HEROPEN_AGENT_LIMIT` 覆盖。
- **升级引导文案同步**：面板「升级 Plus」提示改为功能向（skill 收集与共享），不再以「多显示 Agent」为卖点。

**B. 真实 agent 安装反馈修复**

- **钉死 mcp 上界**：依赖改为 `mcp>=1.6,<2`。mcp 2.x 把 `FastMCP` 改名为 `MCPServer` 并搬迁模块，裸 `mcp>=1.6` 会装到 2.x，导致 `ModuleNotFoundError: mcp.server.fastmcp`、MCP 起不来。导入失败时给出明确降级提示。
- **修复 `heropen mcp`**：此前把字面量 `mcp` 漏进 argparse，报 `unrecognized arguments: mcp`。现在 CLI 与 `heropen-mcp` 都能启动；文档/安装引导统一推荐 `{"command":"heropen-mcp","args":[]}`。
- **首次 add 不再卡下载**：本地向量模型未标记就绪时，`add`/`recall` 直接走 FTS，不阻塞下载约 95MB 模型。显式 `heropen embed` 或 `HEROPEN_ALLOW_MODEL_DOWNLOAD=1` 才下载。离线包可从 GitHub Release 获取。
- **`--version` / `--help` 不触发 auto-setup**：`.pth` 钩子遇到纯查询参数直接跳过，避免副作用太积极。
- **宣传口径收窄**：自动探测仅覆盖 Claude/Cursor/Windsurf/WorkBuddy 等固定路径；Hermes 等自定义 `mcp_servers` 需手动配置（README 已写清；自动写入 Hermes 仍待后续版本）。

### 升级

```bash
pip install --upgrade 'heropen'
# 若已被 mcp 2.x 卡住：
pip install --upgrade 'heropen' 'mcp>=1.6,<2'
```

### 行为兼容

- 已有记忆数据无需迁移。
- 已缓存的向量模型行为不变；未就绪时写入仍成功，仅暂无 embedding 列。
- MCP 客户端若仍写 `{"command":"heropen","args":["mcp"]}`，1.9.5 起可工作；仍建议改为 `heropen-mcp`。
- 免费版可见的 agent 数量由 2 增至 6（1.9.4 起生效）；已存在的 agent 库路径不变，无需迁移。

---

## v1.9.2 → v1.9.3

### 改动摘要

- **Windows CLI**：启动时把 stdout/stderr 设为 UTF-8（`errors=replace`），修复 GBK 控制台打印 emoji 导致的 `UnicodeEncodeError`（如 `heropen status`）；同样加固安装后由 `.pth` 触发的自配置流程与零外联断言脚本，避免解释器启动阶段因编码中断。
- **全新库懒初始化**：装完无需先 `heropen init`，直接 `heropen status` / `heropen recall` 或 MCP 工具调用会自动建表（修复全新空库 `no such table: entries` 崩溃）。
- **默认 agent 统一**：所有 CLI 子命令未传 `--agent` 时都读取 `agent-config.json` 的默认 agent（此前 `recall`/`add`/`doctor` 等仍写死 `agent`）。
- **去掉「改常量就惩罚」逻辑**：`FREE_AGENT_LIMIT` 不再暗中把所有 agent 并进 `_shared`；额度改由 `agent-config.json` 的 `edition`（basic=6 / plus|pro=6，各档 agent 数量统一，按功能分档）或环境变量 `HEROPEN_AGENT_LIMIT` 控制。
- **SQLite**：`PRAGMA synchronous` 从 `OFF` 改为 `NORMAL`（WAL 下更安全）。
- **MCP HTTP**：默认绑定 `127.0.0.1:8090`（不再默认 `0.0.0.0`）；可用 `--host` / `--port` 或 `HEROPEN_MCP_HOST` / `HEROPEN_MCP_PORT` 覆盖；非本机绑定时开启 DNS rebinding 防护。`doctor` 端口提示同步更新。
- **ARM64 可安装**：`fastembed` 改回**可选依赖**（`heropen[embedding]`）；MCP 运行时（`mcp`，纯 Python）保留为核心依赖，确保裸装即可 `heropen-mcp`。`pip install heropen` 在 Linux aarch64 / Windows ARM64 / Apple Silicon 上均可装；无 onnx wheel 时自动走 FTS 或 `EMBEDDING_ENDPOINT`。新增 `get_embedding_status()` / `heropen doctor` 架构检测与安装提示。

### 本地安装本仓库 1.9.3（发版前）

```bash
pip install --force-reinstall -e .
# 需要本地向量时再装：
pip install -e '.[embedding]'
```

### 行为兼容

- 各 agent 的独立 `.db` 路径不变。
- 已有记忆数据无需迁移。
- 若你曾依赖 MCP HTTP 对局域网开放，升级后需显式：`heropen-mcp --http --host 0.0.0.0`
- 若需要本地语义检索，请显式安装：`pip install 'heropen[embedding]'`（1.9.2 曾把 fastembed 设为硬依赖，1.9.3 改回可选以免 ARM 装失败）。

---

## 如何确认当前版本

heropen 可能安装在多个 Python 环境中，升级前先确认升级的是哪个：

```bash
# 检查当前终端默认的 Python 环境中的版本
python -c "import heropen; print(heropen.__version__)"

# 如果是 Hermes Agent 环境（uv 安装），需要指定 Hermes venv
~/.local/share/uv/tools/hermes-agent/bin/python -c "import heropen; print(heropen.__version__)"

# 系统 Python（例如 Windows 下的 Python 3.12）
/usr/bin/python3 -c "import heropen; print(heropen.__version__)"
```

升级前先确认你**想升级的是哪个环境**，然后针对那个环境执行升级命令。

## 如何升级

```bash
# 标准升级（适用于 pip 安装的 heropen）
pip install --upgrade heropen

# 如果标准升级报卸载错误（常因旧版卸载脚本有残留文件冲突），
# 加 --force-reinstall 强制重装：
pip install --upgrade --force-reinstall heropen
```

> ⚠️ `--force-reinstall` 在 `pip install --upgrade` 卸载旧版时如果报错（如"文件不存在"等），加上此参数即可解决。

### Hermes Agent 环境中的升级

如果 heropen 装在 Hermes Agent 的 venv 中（uv 管理），需要这样升：

```bash
# 先确认 Hermes 的 uv 位置
which uv

# 升级 Hermes venv 中的 heropen
uv pip install --upgrade --force-reinstall heropen
```

或者找到 Hermes venv 的 pip 直接升：

```bash
~/.local/share/uv/tools/hermes-agent/bin/pip install --upgrade --force-reinstall heropen
```

## `--agent` 参数的正确用法

`--agent` 必须跟在**子命令后面**，不能放在 `heropen` 之后：

```bash
# ✅ 正确
heropen recall --last 10 --agent work
heropen status --agent work
heropen add --agent work --content "..."

# ❌ 错误（会报错）
heropen --agent work recall --last 10
```

这是 argparse 解析顺序决定的——`--agent` 是子命令级别的参数，不是全局参数。

## v1.7.21 → v1.7.22 升级注意事项

### 自动迁移

v1.7.22 修复了之前多个 agent 被硬编码映射到 `_shared.db` 的问题。
升级后调用 `init_db()` 时会**自动执行一次迁移**：
1. 读取 `_shared.db` 中属于这些 agent 的记录
2. 写入各自独立的 agent 数据库文件
3. 对 `_shared.db` 创建备份 `_shared.db.bak`
4. 迁移过程自动去重（按内容前 100 字判断）

如果你在升级**之前**已经手动迁移过数据（如从 `_shared.db` 导出再导入 agent 数据库），
自动迁移不会重复写入（已有内容前缀的会跳过）。

### 迁移后的清理

`_shared.db.bak` 确认数据无误后可以删除：

```bash
rm ~/.heropen/_shared.db.bak
```

如果 `_shared.db` 中所有 agent 的数据都已迁移完，也可以清理它：

```bash
rm ~/.heropen/_shared.db
# 如果仍有其他 agent 使用 _shared.db，可以先确认
sqlite3 ~/.heropen/_shared.db "SELECT DISTINCT agent, COUNT(*) FROM entries GROUP BY agent"
```
