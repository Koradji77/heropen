# -*- coding: utf-8 -*-
"""heropen.auto_tray — 安装后自动拉起托盘角标（并登记开机自启）。

为什么需要这个模块
------------------
托盘角标是「图形工作台」与「一步一存 / 存储模式」的唯一入口。若安装脚本
只做 ``pip install heropen`` 而不拉起角标，用户装完看不到任何界面，会误以为
没装成功——这是 2.0.5 及以前的实际缺陷。

本模块只做两件事，且都在 Windows 上：
1. ``ensure_tray_running()``：若角标未在运行，用 pythonw 静默拉起一个。
2. 角标自身启动时会写 ``HKCU\\...\\Run``（见 tray.set_autostart_reg），
   所以这里只需拉起一次，之后开机由注册表接管。

设计约束（与项目铁律一致）
------------------------
- **绝不弹黑窗**：一律用 ``pythonw.exe``（无控制台），并有 CREATE_NO_WINDOW 兜底。
- **幂等**：已在运行则直接返回，不重复拉起（复用 tray.PID_FILE + _owns_process）。
- **非 Windows 安全跳过**：macOS/Linux 返回 skipped，不报错、不影响安装流程。
- **失败不阻断安装**：任何异常都吞掉并记日志，绝不因为角标拉不起来就让安装回滚。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HEROPEN_HOME = Path.home() / ".heropen"
LOG_PATH = HEROPEN_HOME / "tray_autostart.log"


def _log(msg: str) -> None:
    try:
        HEROPEN_HOME.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        pass


def _pythonw() -> str:
    """优先 pythonw.exe（无控制台窗口），缺失时回退当前解释器。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.exists() else str(exe)


def is_tray_running() -> bool:
    """角标是否已在运行：读 PID 文件并校验命令行确为托盘实例。"""
    pid_file = HEROPEN_HOME / "_tray.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    try:
        ps = (
            "$c=(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CommandLine; "
            "if ($c -like '*heropen.tray*') { 'YES' } else { 'NO' }" % pid
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
        return "YES" in (r.stdout or "")
    except Exception:
        return False


def _tray_deps_available() -> bool:
    """托盘依赖是否已就绪（pystray / PIL）。

    时序风险：``.pth`` 首启钩子可能在 pip 把 pystray 装完之前就触发。
    此时直接拉起只会得到一个立刻退出的进程。先探测依赖，缺了就干净返回，
    等下一次触发（或安装脚本收尾）再拉——不写任何「已尝试」标记，保证可重试。
    """
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"tray deps not ready yet: {e}")
        return False


def ensure_tray_running(verbose: bool = True) -> str:
    """确保角标在运行；返回状态字符串（供安装脚本/CLI 打印）。

    可能返回值：
        "started"   已拉起新实例
        "running"   已经有一个在跑，未重复拉起
        "skipped"   非 Windows，主动跳过
        "no_deps"   托盘依赖尚未就绪（稍后重试即可）
        "failed"    拉起失败（不影响安装，仅提示手动执行 heropen tray）
    """
    if sys.platform != "win32":
        _log("skipped: not win32")
        return "skipped"

    if not _tray_deps_available():
        return "no_deps"

    if is_tray_running():
        _log("already running; skip")
        return "running"

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    # DETACHED_PROCESS 让角标脱离父进程，安装脚本退出后角标仍常驻
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    try:
        subprocess.Popen(
            [_pythonw(), "-m", "heropen.tray"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        _log("launched pythonw -m heropen.tray")
        return "started"
    except Exception as e:  # noqa: BLE001 - 安装流程绝不能被角标拖垮
        _log(f"launch failed: {e}")
        return "failed"