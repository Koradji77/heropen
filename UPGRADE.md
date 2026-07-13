# heropen 升级指南

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
