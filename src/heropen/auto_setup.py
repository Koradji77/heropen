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

def _get_hero_pen_dir() -> str:
    """Match core.py exactly: $HERO_PEN_DIR env or ~/.heropen."""
    env = os.environ.get("HERO_PEN_DIR", "")
    if env:
        return env
    return os.path.join(str(Path.home()), ".heropen")


def _log_path() -> str:
    return os.path.join(_get_hero_pen_dir(), "setup.log")


_LOG_PATH = _log_path()


def _safe_print(msg: str) -> None:
    """Print without crashing non-UTF-8 Windows consoles during interpreter startup."""
    try:
        stream = sys.stdout
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
        print(msg, flush=True)
    except Exception:
        # A .pth hook must never break the user's Python interpreter.
        try:
            sys.stdout.buffer.write(msg.encode("utf-8", errors="replace") + b"\n")
            sys.stdout.buffer.flush()
        except Exception:
            _log(f"stdout unavailable: {msg}")


def _log(msg: str) -> None:
    """Write a timestamped log entry to setup.log."""
    try:
        os.makedirs(os.path.dirname(_log_path()), exist_ok=True)
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        pass


def _should_run() -> bool:
    """Only run if no sentinel file exists and no agent-config.json."""
    # Pure queries must not trigger side-effecting first-run setup.
    argv = [a.lower() for a in sys.argv[1:]]
    skip = {"--version", "-v", "version", "--help", "-h", "help"}
    if any(a in skip for a in argv):
        _log(f"auto_setup skipped (argv={sys.argv!r})")
        return False
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
    _safe_print("🖊  heropen 正在自动配置...")

    # Step 1: Agent detection and MCP configuration
    _log("step 1: agent detection start")
    try:
        from heropen.install import _install_with_detect as detect
        detect()
        _log("step 1: agent detection OK")
    except Exception as e:
        _log(f"step 1 FAILED: {e}")
        _safe_print(f"  ❌ 自动检测 Agent 失败：{e}")
        _safe_print(f"     查看日志：{_log_path()}")
        _safe_print("     或运行 heropen diagnose 排查问题。")
        _mark_done()
        _create_pending_marker()
        return

    # Step 1.5: System prompt injection (P0-5 v1.8)
    _log("step 1.5: system prompt injection start")
    try:
        from heropen.install import inject_memory_usage_rules
        results = inject_memory_usage_rules(log_fn=_log)
        for r in results:
            _log(f"  inject: {r}")
        _log("step 1.5: injection done")
    except Exception as e:
        _log(f"step 1.5 FAILED: {e}")

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
    _safe_print("  ✅ heropen 配置成功！重启你的 AI 助手后它就会拥有长期记忆。")
    _safe_print("     工具已注册：search_memory / add_memory / list_memory / health")
    _safe_print(f"     日志文件：{_log_path()}")
    _log("auto_setup completed successfully")
