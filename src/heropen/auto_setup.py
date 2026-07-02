"""
heropen.auto_setup — triggered via .pth on interpreter startup.
Runs auto-detect once; subsequent startups are no-ops.
Logs all steps to ~/.heropen/setup.log for diagnostics.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_LOG_PATH = os.path.join(os.path.expanduser("~/.heropen"), "setup.log")


def _log(msg: str) -> None:
    """Write a timestamped log entry to setup.log."""
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        pass


def _should_run() -> bool:
    """Only run if no sentinel file exists and no agent-config.json."""
    hero_pen_dir = _get_hero_pen_dir()
    sentinel = Path(hero_pen_dir) / ".auto_setup_done"
    if sentinel.exists():
        return False
    config = Path(hero_pen_dir) / "agent-config.json"
    if config.exists():
        return False
    return True


def _get_hero_pen_dir() -> str:
    """Match core.py exactly: $HERO_PEN_DIR env or ~/.heropen."""
    env = os.environ.get("HERO_PEN_DIR", "")
    if env:
        return env
    return os.path.join(str(Path.home()), ".heropen")


def _mark_done() -> None:
    """Write sentinel file."""
    try:
        hero_pen_dir = _get_hero_pen_dir()
        os.makedirs(hero_pen_dir, exist_ok=True)
        with open(os.path.join(hero_pen_dir, ".auto_setup_done"), "w") as f:
            f.write("1")
    except OSError:
        pass


def _create_pending_marker() -> None:
    """Create the .pending_setup marker file so Agent health tool can detect new install."""
    try:
        from heropen import __version__
        hero_pen_dir = _get_hero_pen_dir()
        marker = Path(hero_pen_dir) / ".pending_setup"
        marker.write_text(json.dumps({
            "installed_at": datetime.now().isoformat(),
            "version": __version__,
            "agent_completed": False,
        }, ensure_ascii=False))
        _log("pending_setup marker created")
    except Exception as e:
        _log(f"failed to create pending marker: {e}")


def run() -> None:
    """Run auto-setup if this is the first interpreter start after install."""
    if not _should_run():
        return

    _log("auto_setup started")
    print("🖊  heropen 正在自动配置...", flush=True)

    # Step 1: Agent detection and MCP configuration
    _log("step 1: agent detection start")
    try:
        from heropen.install import _install_with_detect as detect
        detect()
        _log("step 1: agent detection OK")
    except Exception as e:
        _log(f"step 1 FAILED: {e}")
        print(f"  ❌ 自动检测 Agent 失败：{e}", flush=True)
        print(f"     查看日志：{_LOG_PATH}", flush=True)
        print(f"     或运行 heropen diagnose 排查问题。", flush=True)
        _mark_done()
        _create_pending_marker()
        return

    # Step 2: Mark setup done and create pending marker
    _log("step 2: finalizing")
    try:
        _mark_done()
        _create_pending_marker()
        _log("step 2: done")
    except Exception as e:
        _log(f"step 2 FAILED: {e}")
        # Non-fatal — setup config exists even if marker fails
        pass

    # Success feedback
    print("  ✅ heropen 配置成功！重启你的 AI 助手后它就会拥有长期记忆。", flush=True)
    print(f"     工具已注册：search_memory / add_memory / list_memory / health", flush=True)
    print(f"     日志文件：{_LOG_PATH}", flush=True)
    _log("auto_setup completed successfully")
