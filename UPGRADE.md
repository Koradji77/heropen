# heropen 升级指南

## v1.9.3 → v1.9.4

### 改动摘要

- **免费版 agent 数量上调**：`FREE_AGENT_LIMIT` 由 2 改为 6，与 Plus/Pro 统一（各档均为 6 个隔离 agent）。消费版定价已从「按数量分」转为「按功能分」（Plus 提供 skill 收集与共享），数量不再作为分档边界。
- **升级引导文案同步**：面板「升级 Plus」提示改为功能向（skill 收集与共享），不再以「多显示 Agent」为卖点。

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
