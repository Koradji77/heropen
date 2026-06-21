"""
heropen.auto_setup — triggered via .pth on interpreter startup.
Runs auto-detect once; subsequent startups are no-ops.
"""
import json
import os
import sys
from pathlib import Path


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


def run() -> None:
    """Run auto-setup if this is the first interpreter start after install."""
    if not _should_run():
        return
    print("\n🖊  HeroPen 已安装！重启你的 AI 助手即可完成配置。\n", flush=True)
    try:
        from heropen.install import _install_with_detect as detect
        detect()
        _mark_done()
    except Exception:
        pass
