"""
heropen.panel — HeroPen 可视化面板（tkinter GUI + TUI 后备）

通过 ``heropen panel`` 启动。
跨平台（Windows / macOS / Linux），tkinter 是 Python 内置依赖。

功能：
  - 查看版本/可用更新
  - 一键检查更新 + 升级（升级前自动备份 DB）
  - Agent 列表 + 记忆统计
  - 无 tkinter 时自动降级为 TUI
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from heropen.core import (
    AGENTS,
    HERO_PEN_DIR,
    __version__ as CORE_VERSION,
    conn,
)
_HOME = Path.home()
AGENT_CONFIG_PATH = os.path.join(HERO_PEN_DIR, "agent-config.json")
BACKUP_DIR = os.path.join(HERO_PEN_DIR, "backups")
DB_DIR = HERO_PEN_DIR  # Directory containing all .db files

# ═══════════════════════════════════════════════════════════════
# Core logic — no GUI dependency
# ═══════════════════════════════════════════════════════════════


def get_version() -> str:
    """Return the installed heropen version."""
    return CORE_VERSION


def check_pypi_version() -> Optional[str]:
    """Check PyPI for latest heropen version. Returns None on failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "index", "versions", "heropen"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        # Parse: "heropen (1.4.0)\nAvailable versions: 1.4.0, 1.3.1, ..."
        for line in result.stdout.splitlines():
            if line.startswith("heropen"):
                # First token in parens is the latest
                if "(" in line:
                    return line.split("(")[1].split(")")[0]
        return None
    except Exception:
        return None


def has_update(latest: Optional[str]) -> bool:
    """Check if there's a newer version available."""
    if not latest:
        return False
    try:
        current_parts = [int(x) for x in CORE_VERSION.split(".")]
        latest_parts = [int(x) for x in latest.split(".")]
        return latest_parts > current_parts
    except (ValueError, IndexError):
        return False


def backup_database() -> tuple[bool, str]:
    """
    Backup all agent databases to backups/ and create .bak copies.
    Returns (success, message).
    """
    # Find all .db files in HERO_PEN_DIR
    db_files = sorted(Path(HERO_PEN_DIR).glob("*.db"))
    if not db_files:
        return False, "未找到数据库文件（.db），跳过备份。"

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    total_size = 0
    count = 0
    errors = []

    for db_path in db_files:
        try:
            # Timestamped backup
            backup_name = f"{db_path.stem}_{ts}.db.bak"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            shutil.copy2(str(db_path), backup_path)

            # Simple .bak alongside the DB
            simple_bak = db_path.with_suffix(".db.bak")
            shutil.copy2(str(db_path), str(simple_bak))

            total_size += db_path.stat().st_size
            count += 1
        except OSError as e:
            errors.append(f"{db_path.name}: {e}")

    total_mb = total_size / (1024 * 1024)
    msg = f"✅ 已备份 {count} 个数据库 ({total_mb:.1f} MB) → {BACKUP_DIR}/"
    if errors:
        msg += "\n⚠️  " + "；".join(errors)
        return False, msg
    return True, msg


def do_upgrade() -> tuple[bool, str]:
    """
    Run ``pip install --upgrade heropen`` (with --user fallback).
    Returns (success, message).
    """
    commands = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "heropen"],
        [sys.executable, "-m", "pip", "install", "--upgrade", "--user", "heropen"],
    ]
    errors = []
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                return True, "✅ 升级成功！请重启 AI 助手使新版本生效。"
            errors.append(result.stderr.strip() or result.stdout.strip())
        except subprocess.TimeoutExpired:
            errors.append("安装超时（pip 超过 2 分钟）")
            break
        except Exception as e:
            errors.append(str(e))
            break
    return False, f"❌ 升级失败：{errors[0][:200]}"


def get_agent_list() -> list[dict]:
    """
    Return list of configured agents from agent-config.json.
    Each entry: {name, db, entries, size_mb, edition}
    """
    agents: list[dict] = []

    # Read agent-config.json for names and editions
    config_agents = []
    try:
        with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            config_agents = cfg.get("agents", [])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Build name → edition mapping
    edition_map: dict[str, str] = {}
    for a in config_agents:
        edition_map[a.get("name", "")] = a.get("edition", "basic")

    # Collect all known agents (from AGENTS dict + config)
    seen_names: set[str] = set()
    for name in list(AGENTS.keys()) + [a.get("name", "") for a in config_agents]:
        if not name or name.startswith("_") or name in seen_names:
            continue
        seen_names.add(name)
        db_name = AGENTS.get(name, f"{name}.db")
        db_path = os.path.join(HERO_PEN_DIR, db_name)

        entries = 0
        try:
            c = conn(name)
            entries = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            c.close()
        except Exception:
            pass

        size_mb = 0.0
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)

        agents.append({
            "name": name,
            "entries": entries,
            "size_mb": round(size_mb, 2),
            "edition": edition_map.get(name, "basic"),
            "db": db_name,
        })

    return agents


def get_memory_stats() -> dict:
    """Return overall memory stats."""
    total_entries = 0
    total_size = 0.0
    agents = get_agent_list()
    for a in agents:
        total_entries += a["entries"]
        total_size += a["size_mb"]
    return {
        "total_entries": total_entries,
        "total_size_mb": round(total_size, 2),
        "agent_count": len(agents),
        "db_path": DB_DIR,
    }


# ═══════════════════════════════════════════════════════════════
# TUI (terminal UI) fallback
# ═══════════════════════════════════════════════════════════════


def run_tui() -> None:
    """Terminal-based panel when tkinter is unavailable."""
    import shutil as shutil_mod

    term_width = shutil_mod.get_terminal_size().columns if hasattr(shutil_mod, "get_terminal_size") else 80

    def hr():
        print("─" * min(term_width, 60))

    def show_header():
        hr()
        version = get_version()
        print(f"  🖊  HeroPen 控制面板  v{version}")
        hr()

    def show_status():
        stats = get_memory_stats()
        agents = get_agent_list()
        latest = check_pypi_version()
        outdated = has_update(latest)

        print()
        print(f"  📊 记忆库：{stats['total_entries']} 条  |  {stats['total_size_mb']:.1f} MB  |  {stats['agent_count']} 个 Agent")
        print(f"  📁 数据库：{stats['db_path']}")
        print()
        if outdated:
            print(f"  🔄 有新版本！当前：v{get_version()} → 最新：v{latest}")
        elif latest:
            print(f"  ✅ 已是最新版本 v{get_version()}")
        else:
            print(f"  ℹ️  版本：v{get_version()}（无法检查更新）")
        print()

        if agents:
            print(f"  🤖 已配置的 Agent：")
            print(f"     {'名称':<16} {'记忆条数':<10} {'大小':<8} {'版型':<6}")
            print(f"     {'─'*16} {'─'*10} {'─'*8} {'─'*6}")
            for a in agents:
                size_str = f"{a['size_mb']:.1f} MB" if a['size_mb'] > 0 else "-"
                edition_str = "Plus" if a['edition'] == "plus" else "Free"
                print(f"     {a['name']:<16} {a['entries']:<10} {size_str:<8} {edition_str:<6}")
        print()

    def show_menu():
        while True:
            hr()
            print("  [1] 检查更新")
            print("  [2] 一键升级（自动备份）")
            print("  [3] 查看 Agent 详情")
            print("  [4] 刷新状态")
            print("  [q] 退出")
            hr()
            choice = input("  > ").strip().lower()

            if choice == "1":
                print("\n  🔍 正在检查更新...")
                latest = check_pypi_version()
                if latest:
                    if has_update(latest):
                        print(f"  🔄 发现新版本：v{get_version()} → v{latest}")
                    else:
                        print(f"  ✅ 已是最新版本 v{get_version()}")
                else:
                    print("  ❌ 无法检查更新（网络问题？）")
                print()

            elif choice == "2":
                latest = check_pypi_version()
                if latest and not has_update(latest):
                    print(f"\n  ✅ 已是最新版本 v{get_version()}，无需升级。")
                    continue
                print("\n  💾 正在备份数据库...")
                bak_ok, bak_msg = backup_database()
                print(f"  {bak_msg}")
                if not bak_ok:
                    print("  ⚠️  备份失败，是否继续升级？[y/N] ", end="")
                    if input().strip().lower() != "y":
                        print("  已取消。")
                        continue
                print("  ⬆️  正在升级...")
                up_ok, up_msg = do_upgrade()
                print(f"  {up_msg}")

            elif choice == "3":
                agents = get_agent_list()
                if not agents:
                    print("\n  ℹ️  尚未配置 Agent。")
                else:
                    for a in agents:
                        print(f"\n  🤖 {a['name']}")
                        print(f"     版型：{'Plus' if a['edition'] == 'plus' else 'Free'}")
                        print(f"     记忆：{a['entries']} 条")
                        print(f"     大小：{a['size_mb']:.1f} MB" if a['size_mb'] > 0 else "     大小：-")
                        print(f"     数据库：{a['db']}")

            elif choice == "4":
                show_status()

            elif choice in ("q", "quit", "exit", ""):
                print("\n  再见 👋")
                break

            else:
                print("  ❓ 请输入 1-4 或 q")

    show_header()
    show_status()
    show_menu()


# ═══════════════════════════════════════════════════════════════
# tkinter GUI
# ═══════════════════════════════════════════════════════════════


def _try_import_tk() -> bool:
    """Check if tkinter is available without importing it globally."""
    try:
        import tkinter
        return True
    except ImportError:
        return False


def run_gui() -> None:
    """Launch the tkinter HeroPen panel."""
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext

    root = tk.Tk()
    root.title(f"HeroPen v{get_version()} — 控制面板")
    root.geometry("680x520")
    root.minsize(580, 420)
    root.resizable(True, True)

    try:
        root.iconbitmap(default="")  # No icon needed
    except Exception:
        pass

    # ── Style ────────────────────────────────────────────────
    style = ttk.Style()
    style.theme_use("clam")
    bg_color = "#f5f5f5"
    fg_color = "#333333"
    accent = "#4a90d9"
    style.configure(".", background=bg_color, foreground=fg_color)
    style.configure("TFrame", background=bg_color)
    style.configure("TLabel", background=bg_color, foreground=fg_color)
    style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"))
    style.configure("Title.TLabel", font=("Segoe UI", 11))
    style.configure("Small.TLabel", font=("Segoe UI", 9), foreground="#666")
    style.configure("Accent.TButton", font=("Segoe UI", 10), background=accent)

    # ── Main container ──────────────────────────────────────
    main_frame = ttk.Frame(root, padding=16)
    main_frame.pack(fill=tk.BOTH, expand=True)

    # ── Header ──────────────────────────────────────────────
    header_frame = ttk.Frame(main_frame)
    header_frame.pack(fill=tk.X, pady=(0, 12))

    ttk.Label(header_frame, text="🖊  HeroPen 控制面板", style="Header.TLabel").pack(side=tk.LEFT)
    version_label = ttk.Label(header_frame, text=f"v{get_version()}", style="Small.TLabel")
    version_label.pack(side=tk.LEFT, padx=(8, 0))

    # ── Notebook (tabs) ─────────────────────────────────────
    notebook = ttk.Notebook(main_frame)
    notebook.pack(fill=tk.BOTH, expand=True)

    # ── Tab 1: Status ───────────────────────────────────────
    tab_status = ttk.Frame(notebook, padding=12)
    notebook.add(tab_status, text="📊 状态")

    # Stats frame
    stats_frame = ttk.LabelFrame(tab_status, text="记忆概览", padding=10)
    stats_frame.pack(fill=tk.X, pady=(0, 10))

    stats_text = tk.StringVar()
    stats_label = ttk.Label(stats_frame, textvariable=stats_text, style="Title.TLabel",
                            wraplength=600)
    stats_label.pack(anchor=tk.W)

    # Agent list
    agent_frame = ttk.LabelFrame(tab_status, text="已配置 Agent", padding=10)
    agent_frame.pack(fill=tk.BOTH, expand=True)

    agent_cols = ("name", "entries", "size", "edition")
    agent_tree = ttk.Treeview(agent_frame, columns=agent_cols, show="headings",
                              height=6)
    agent_tree.heading("name", text="名称")
    agent_tree.heading("entries", text="记忆条数")
    agent_tree.heading("size", text="大小")
    agent_tree.heading("edition", text="版型")
    agent_tree.column("name", width=160)
    agent_tree.column("entries", width=100, anchor=tk.CENTER)
    agent_tree.column("size", width=100, anchor=tk.CENTER)
    agent_tree.column("edition", width=80, anchor=tk.CENTER)

    scrollbar = ttk.Scrollbar(agent_frame, orient=tk.VERTICAL, command=agent_tree.yview)
    agent_tree.configure(yscrollcommand=scrollbar.set)
    agent_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ── Tab 2: Update ───────────────────────────────────────
    tab_update = ttk.Frame(notebook, padding=12)
    notebook.add(tab_update, text="🔄 更新")

    update_status = tk.StringVar(value="点击「检查更新」查看最新版本")
    check_btn = ttk.Button(tab_update, text="🔍 检查更新",
                           style="Accent.TButton")
    upgrade_btn = ttk.Button(tab_update, text="⬆️  一键升级（自动备份）",
                             style="Accent.TButton")

    ttk.Label(tab_update, textvariable=update_status, style="Title.TLabel",
              wraplength=600).pack(anchor=tk.W, pady=(0, 16))

    btn_frame = ttk.Frame(tab_update)
    btn_frame.pack(fill=tk.X)
    check_btn.pack(side=tk.LEFT, padx=(0, 8), ipadx=8, ipady=4)
    upgrade_btn.pack(side=tk.LEFT, ipadx=8, ipady=4)

    # ── Tab 3: About ────────────────────────────────────────
    tab_about = ttk.Frame(notebook, padding=12)
    notebook.add(tab_about, text="ℹ️ 关于")

    about_text = (
        "HeroPen — AI Agent 长期记忆系统\n"
        f"版本：v{get_version()}\n"
        f"数据目录：{HERO_PEN_DIR}\n\n"
        "MIT License\n"
        "© KSMN Studio — hello@ksmn.cc\n"
        "https://ksmn.cc/heropen"
    )
    ttk.Label(tab_about, text=about_text, style="Title.TLabel",
              wraplength=600, justify=tk.LEFT).pack(anchor=tk.W)

    # ── Footer bar ──────────────────────────────────────────
    footer_frame = ttk.Frame(main_frame)
    footer_frame.pack(fill=tk.X, pady=(12, 0))

    # Progress bar (hidden by default)
    progress = ttk.Progressbar(footer_frame, mode="indeterminate", length=200)
    status_msg = tk.StringVar(value="就绪")

    ttk.Label(footer_frame, textvariable=status_msg,
              style="Small.TLabel").pack(side=tk.LEFT)
    db_path_label = ttk.Label(
        footer_frame,
        text=f"📁 {DB_DIR}",
        style="Small.TLabel",
        wraplength=400,
    )
    db_path_label.pack(side=tk.RIGHT)

    # ── Functions ──────────────────────────────────────────

    def refresh_stats():
        """Refresh the stats tab data."""
        stats = get_memory_stats()
        agents = get_agent_list()
        stats_text.set(
            f"📊 {stats['total_entries']} 条记忆  |  "
            f"{stats['total_size_mb']:.1f} MB  |  "
            f"{stats['agent_count']} 个 Agent"
        )
        # Clear and repopulate tree
        for item in agent_tree.get_children():
            agent_tree.delete(item)
        for a in agents:
            size_str = f"{a['size_mb']:.1f} MB" if a['size_mb'] > 0 else "-"
            edition_str = "Plus" if a['edition'] == 'plus' else "Free"
            agent_tree.insert("", tk.END, values=(
                a["name"], a["entries"], size_str, edition_str
            ))

    def do_check_update():
        """Background check for updates."""
        def _work():
            root.after(0, lambda: progress.pack(side=tk.LEFT, padx=(8, 8)))
            root.after(0, lambda: progress.start(10))
            root.after(0, lambda: status_msg.set("正在检查更新..."))
            latest = check_pypi_version()
            root.after(0, lambda: progress.stop())
            root.after(0, lambda: progress.pack_forget())
            if latest:
                if has_update(latest):
                    root.after(0, lambda: update_status.set(
                        f"🔴 新版本可用：v{get_version()} → v{latest}"))
                    root.after(0, lambda: status_msg.set(
                        f"发现 v{latest}，点击升级"))
                else:
                    root.after(0, lambda: update_status.set(
                        f"✅ 已是最新版本 v{get_version()}"))
                    root.after(0, lambda: status_msg.set("已是最新"))
            else:
                root.after(0, lambda: update_status.set(
                    "❌ 无法检查更新（网络问题？）"))
                root.after(0, lambda: status_msg.set("检查更新失败"))

        threading.Thread(target=_work, daemon=True).start()

    def do_upgrade_with_backup():
        """Backup + upgrade in background."""
        def _work():
            root.after(0, lambda: progress.pack(side=tk.LEFT, padx=(8, 8)))
            root.after(0, lambda: progress.start(10))
            root.after(0, lambda: status_msg.set("正在备份..."))
            bak_ok, bak_msg = backup_database()
            if not bak_ok:
                root.after(0, lambda: progress.stop())
                root.after(0, lambda: progress.pack_forget())
                root.after(0, lambda: messagebox.showwarning(
                    "备份失败", bak_msg + "\n\n是否继续升级？"))
                return
            root.after(0, lambda: status_msg.set("正在升级..."))
            up_ok, up_msg = do_upgrade()
            root.after(0, lambda: progress.stop())
            root.after(0, lambda: progress.pack_forget())
            if up_ok:
                root.after(0, lambda: messagebox.showinfo(
                    "升级成功", up_msg))
                root.after(0, lambda: update_status.set(f"✅ 已升级"))
                # Update version display
                new_ver = get_version()
                root.after(0, lambda: version_label.configure(text=f"v{new_ver}"))
                root.after(0, lambda: root.title(f"HeroPen v{new_ver} — 控制面板"))
            else:
                root.after(0, lambda: messagebox.showerror(
                    "升级失败", up_msg))
            root.after(0, lambda: refresh_stats())

        threading.Thread(target=_work, daemon=True).start()

    # Bind buttons
    check_btn.configure(command=do_check_update)
    upgrade_btn.configure(command=do_upgrade_with_backup)

    # ── Init ────────────────────────────────────────────────
    refresh_stats()
    root.mainloop()


# ═══════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════


def cmd_panel(args: list[str]) -> None:
    """
    ``heropen panel`` — launch the control panel.

    Uses tkinter GUI if available, falls back to TUI.
    """
    # Parse --tui flag to force terminal mode
    force_tui = any(a in ("--tui", "--terminal") for a in args)

    if not force_tui and _try_import_tk():
        try:
            run_gui()
            return
        except Exception:
            print("⚠️  GUI 面板异常，降级到终端模式：")
            run_tui()
    else:
        run_tui()
