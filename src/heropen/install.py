"""
heropen.install — Interactive install wizard for HeroPen.

Usage (CLI):
    heropen install

Phase 1: rich-powered terminal UI.
Phase 2 (TODO): --ui flag for local web page.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from heropen.core import HERO_PEN_DIR, AGENTS, init_db

# ─── Constants ─────────────────────────────────────────────────

AGENT_CONFIG_PATH = os.path.join(HERO_PEN_DIR, "agent-config.json")
from heropen.core import FREE_AGENT_LIMIT as MAX_AGENTS_BASIC
MAX_AGENTS_PLUS = 5

INVALID_NAME_RE = re.compile(r'[/\\]')


# ─── Data model ────────────────────────────────────────────────

@dataclass
class DiscoveredAgent:
    """An agent found on the user's machine via MCP config scanning."""
    name: str
    source: str  # e.g. "Claude Desktop", "Cursor", "Hermes", "manual"


@dataclass
class AgentConfig:
    """A configured agent with its own memory database."""
    name: str
    db_path: str
    created_at: str


@dataclass
class InstallConfig:
    """The entire install configuration saved to disk."""
    version: str = "1.0"
    edition: str = "basic"   # "basic" or "plus"
    agents: list[dict] = field(default_factory=list)


# ─── Agent scanning ───────────────────────────────────────────

_HOME = Path.home()


def _read_json_safe(path: Path) -> Optional[dict]:
    """Read a JSON file, return None on failure."""
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _scan_claude() -> list[DiscoveredAgent]:
    """Scan Claude Desktop's MCP config for hero-pen entries."""
    agents: list[DiscoveredAgent] = []
    candidates = [
        # Old Claude Desktop
        _HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        # New Claude Desktop (2024+)
        _HOME / "Library" / "Application Support" / "Claude" / "claude.json",
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        candidates.extend([
            Path(appdata) / "Claude" / "claude_desktop_config.json",
            Path(appdata) / "Claude" / "claude.json",
        ])
    if sys.platform == "darwin":
        # Also check ~/.config/claude/ on macOS (some distributions)
        candidates.append(_HOME / ".config" / "claude" / "claude_desktop_config.json")

    for path in candidates:
        cfg = _read_json_safe(path)
        if not cfg:
            continue
        mcp_servers = cfg.get("mcpServers", cfg.get("mcp_servers", {}))
        if not isinstance(mcp_servers, dict):
            continue
        for server_name, server_cfg in mcp_servers.items():
            if not isinstance(server_cfg, dict):
                continue
            combined = (server_name + str(server_cfg)).lower()
            if "hero" in combined:
                env = server_cfg.get("env", {})
                agent_name = env.get("AGENT", env.get("agent", ""))
                if not agent_name:
                    agent_name = server_name
                agents.append(DiscoveredAgent(name=agent_name, source="Claude Desktop"))
    return agents


def _scan_cursor() -> list[DiscoveredAgent]:
    """Scan Cursor's MCP config files."""
    agents: list[DiscoveredAgent] = []
    candidates = [
        _HOME / ".cursor" / "mcp.json",
        # Project-level configs (scan cwd and common project dirs)
    ]
    # Also scan workspace dirs if we can detect them
    for path in candidates:
        cfg = _read_json_safe(path)
        if not cfg:
            continue
        mcp_servers = cfg.get("mcpServers", {})
        if not isinstance(mcp_servers, dict):
            continue
        for server_name, server_cfg in mcp_servers.items():
            if not isinstance(server_cfg, dict):
                continue
            combined = (server_name + str(server_cfg)).lower()
            if "hero" in combined:
                env = server_cfg.get("env", {})
                agent_name = env.get("AGENT", env.get("agent", server_name))
                agents.append(DiscoveredAgent(
                    name=agent_name,
                    source="Cursor",
                ))
    return agents


def _scan_workbuddy() -> list[DiscoveredAgent]:
    """Scan WorkBuddy MCP config."""
    agents: list[DiscoveredAgent] = []
    path = _HOME / ".workbuddy" / "mcp.json"
    cfg = _read_json_safe(path)
    if not cfg:
        return agents
    mcp_servers = cfg.get("mcpServers", cfg.get("mcp_servers", {}))
    if not isinstance(mcp_servers, dict):
        return agents
    for server_name, srv in mcp_servers.items():
        if not isinstance(srv, dict):
            continue
        combined = (server_name + str(srv)).lower()
        if "hero" in combined:
            env = srv.get("env", {})
            agent_name = env.get("AGENT", env.get("agent", server_name))
            agents.append(DiscoveredAgent(name=agent_name, source="WorkBuddy"))
    return agents


def _scan_hermes() -> list[DiscoveredAgent]:
    """Scan Hermes profiles for agent names."""
    agents: list[DiscoveredAgent] = []
    hermes_home = Path(os.environ.get("HERMES_HOME", str(_HOME / ".hermes")))
    profiles_dir = Path(hermes_home) / "profiles"
    if profiles_dir.is_dir():
        for entry in sorted(profiles_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("."):
                agents.append(DiscoveredAgent(name=entry.name, source="Hermes"))
    # Also scan ~/.hermes/scripts/ for agent-named scripts
    scripts_dir = Path(hermes_home) / "scripts"
    if scripts_dir.is_dir():
        for entry in sorted(scripts_dir.iterdir()):
            if entry.is_file() and not entry.name.startswith("."):
                name = entry.stem  # filename without extension
                if name not in [a.name for a in agents]:
                    agents.append(DiscoveredAgent(name=name, source="Hermes (scripts)"))
    return agents


def _scan_other_mcp() -> list[DiscoveredAgent]:
    """Scan ~/.config/*/mcp.json and Windows equivalent for hero-pen references."""
    agents: list[DiscoveredAgent] = []
    # Linux / macOS
    config_dir = _HOME / ".config"
    if config_dir.is_dir():
        for mcp_path in config_dir.rglob("mcp.json"):
            _scan_mcp_file(mcp_path, agents, f"other ({mcp_path.parent.name})")
    # Windows: check APPDATA and LOCALAPPDATA
    if sys.platform == "win32":
        for env_var in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(env_var, "")
            if base:
                for mcp_path in Path(base).rglob("mcp.json"):
                    _scan_mcp_file(mcp_path, agents, f"other (Windows {env_var})")
    return agents


def _scan_mcp_file(path: Path, agents: list[DiscoveredAgent], source_label: str) -> None:
    """Helper: scan a single mcp.json file for heropen references."""
    cfg = _read_json_safe(path)
    if not cfg:
        return
    servers = cfg.get("mcpServers", cfg.get("mcp_servers", {}))
    if not isinstance(servers, dict):
        return
    for server_name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        combined = (server_name + str(srv)).lower()
        if "hero" in combined:
            env = srv.get("env", {})
            agent_name = env.get("AGENT", env.get("agent", server_name))
            # Avoid duplicates
            if not any(a.name == agent_name for a in agents):
                agents.append(DiscoveredAgent(
                    name=agent_name,
                    source=source_label,
                ))


def scan_agents() -> list[DiscoveredAgent]:
    """Run all scanners, deduplicate by name, return ordered list."""
    seen: set[str] = set()
    result: list[DiscoveredAgent] = []

    scanners = [
        ("Claude Desktop", _scan_claude),
        ("Cursor", _scan_cursor),
        ("WorkBuddy", _scan_workbuddy),
        ("Hermes", _scan_hermes),
        ("Other MCP", _scan_other_mcp),
    ]

    for source_name, scanner in scanners:
        try:
            for agent in scanner():
                if agent.name not in seen:
                    seen.add(agent.name)
                    result.append(agent)
        except Exception:
            # Don't let one scanner crash the whole scan
            pass

    return result


# ─── Input validation ─────────────────────────────────────────

def _validate_agent_name(name: str) -> tuple[bool, str]:
    """Validate an agent name. Returns (ok, error_msg)."""
    name = name.strip()
    if not name:
        return False, "名称不能为空"
    if INVALID_NAME_RE.search(name):
        return False, "名称不能包含 / 或 \\"
    if len(name) > 50:
        return False, "名称不能超过 50 个字符"
    return True, ""


# ─── Config generation ────────────────────────────────────────

def _agent_db_path(agent_name: str) -> str:
    """Get the db path for a given agent name."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', agent_name)
    return os.path.join(HERO_PEN_DIR, f"{safe_name}.db")


def _build_agent_config(name: str, edition: str) -> dict:
    return {
        "name": name,
        "db_path": _agent_db_path(name),
        "created_at": datetime.now().isoformat(),
    }


def _build_identity_text(agent_cfg: dict, edition: str, total_agents: int) -> str:
    """Build the 'copy config' text for one agent — talks to the agent itself."""
    name = agent_cfg["name"]
    lines = [
        "【HeroPen 身份配置 — 请存入你的长期记忆】",
        "",
        f"你叫 {name}，现在拥有 HeroPen 记忆系统。",
        f"你的记忆库在：{agent_cfg['db_path']}",
        "",
        "每次对话开始时，先调用 search_memory 工具（MCP 工具，不是命令行）：",
        f'  query: "最近的对话"',
        f'  agent: "{name}"',
        "",
        "如果返回结果里出现「⏰ 距离上次对话已过 X 小时」，",
        "说明对话间隔太久——主动问问用户最近发生了什么，要不要回顾。",
        "",
        "每次对话结束前，调用 add_memory 工具（MCP 工具）：",
        '  section: "对话记录"',
        "  content: （本次对话的摘要——你自己总结，抓住重点）",
        f'  agent: "{name}"',
        "",
        "以后回答时，看到跟记忆里已有信息相关的话题，",
        "先调 search_memory 看看之前说过什么，避免重复问用户。",
    ]

    if edition == "plus" and total_agents > 1:
        lines.extend([
            "",
            "（你还可以问用户「有没有共享记忆服务地址」，",
            "有的话填上，就可以跟其他助手互相检索记忆了。）",
        ])

    lines.extend([
        "",
        "【结束】",
    ])
    return "\n".join(lines)


def generate_config(selected_names: list[str], edition: str) -> InstallConfig:
    """Initialize DBs for each selected agent and save agent-config.json."""
    os.makedirs(HERO_PEN_DIR, exist_ok=True)

    config = InstallConfig(version="1.0", edition=edition)
    for name in selected_names:
        # Register in the internal AGENTS dict and init DB
        init_db(name)
        agent_cfg = _build_agent_config(name, edition)
        config.agents.append(agent_cfg)

    # Save agent-config.json
    with open(AGENT_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "version": config.version,
            "edition": config.edition,
            "agents": config.agents,
        }, f, ensure_ascii=False, indent=2)

    # Auto-configure MCP for detected agents
    try:
        from heropen.auto_mcp import auto_setup_mcp, print_setup_summary
        mcp_result = auto_setup_mcp()
        if mcp_result["configured"] or mcp_result["already_had"]:
            print()
            print_setup_summary(mcp_result)
    except Exception:
        pass

    return config


def _config_exists() -> bool:
    """Check if agent-config.json already exists."""
    return os.path.exists(AGENT_CONFIG_PATH)


def _confirm_overwrite() -> bool:
    """Ask user to confirm overwriting existing config. Returns True if OK to overwrite."""
    if not _config_exists():
        return True
    try:
        ans = input("⚠️  检测到已有配置，覆盖？[y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ─── CLI interaction (Phase 1 — rich-powered) ───────────────

def _check_rich() -> bool:
    """Check if rich is available."""
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


def cmd_install(args: object) -> None:
    """heropen install — interactive install wizard."""
    arg_list = list(args) if hasattr(args, "__iter__") and not isinstance(args, str) else []
    if any(a in ("--detect", "-d") for a in arg_list):
        _install_with_detect()
        return
    has_rich = _check_rich()
    if has_rich:
        _install_with_rich()
    else:
        _install_plain()


def _show_copy_text_dialog(agent_name: str = "") -> None:
    """Show a tkinter dialog with one-click copy of AI instruction text."""
    # Try to read real agent name from existing config
    if not agent_name:
        try:
            with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            agents = cfg.get("agents", [])
            if agents:
                agent_name = agents[0]["name"]
        except Exception:
            pass

    if agent_name:
        name_line = f"你叫 {agent_name}，现在拥有 HeroPen 记忆系统。\n"
        agent_arg = f'  agent: "{agent_name}"\n'
    else:
        name_line = "你现在拥有 HeroPen 记忆系统。\n"
        agent_arg = "  agent: \"你的名字\"\n"

    text = (
        "【HeroPen 身份配置 — 请存入你的长期记忆】\n"
        "\n"
        + name_line +
        "\n"
        "MCP 服务已就绪，连接方式：\n"
        "  MCP 配置名：heropen\n"
        "  启动命令：heropen-mcp\n"
        "  类型：stdio\n"
        "\n"
        "每次对话开始时，先调用 search_memory 工具（MCP 工具，不是命令行）：\n"
        '  query: "最近的对话"\n'
        + agent_arg +
        "\n"
        "每次对话结束前，调用 add_memory 工具（MCP 工具）保存本次对话：\n"
        '  section: "对话记录"\n'
        "  content: （本次对话的摘要——你自己总结，抓住重点）\n"
        + agent_arg +
        "\n"
        "以后回答时，看到跟记忆里已有信息相关的话题，\n"
        "先调 search_memory 看看之前说过什么，避免重复问用户。\n"
        "\n"
        "【结束】"
    )

    try:
        import tkinter as tk
        from tkinter import scrolledtext

        root = tk.Tk()
        root.title("HeroPen — 复制给AI助手")
        root.geometry("520x400")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        frame = tk.Frame(root, padx=20, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="🖊  HeroPen 已安装",
            font=("Microsoft YaHei", 14, "bold"),
            fg="#333"
        ).pack(anchor=tk.W, pady=(0, 8))

        tk.Label(
            frame,
            text="复制下面这段话发给你的AI助手，它会自己配置：",
            font=("Microsoft YaHei", 10),
            fg="#666",
            wraplength=480
        ).pack(anchor=tk.W, pady=(0, 12))

        txt = scrolledtext.ScrolledText(
            frame, wrap=tk.WORD, height=10,
            font=("Consolas", 11),
            bg="#f8f8f8", fg="#333",
            relief=tk.FLAT, borderwidth=1,
        )
        txt.insert(tk.END, text)
        txt.config(state=tk.DISABLED)
        txt.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        def copy():
            root.clipboard_clear()
            root.clipboard_append(text)
            btn.config(text="✅ 已复制!", bg="#d4edda", fg="#333")
            btn.config(state=tk.DISABLED)
            root.after(2000, root.destroy)

        btn = tk.Button(
            frame,
            text="📋 一键复制",
            command=copy,
            font=("Microsoft YaHei", 12, "bold"),
            bg="#4a90d9", fg="white",
            relief=tk.FLAT, padx=20, pady=8,
            cursor="hand2",
            activebackground="#357abd"
        )
        btn.pack(pady=(0, 4))

        tk.Label(
            frame,
            text="复制后，在你的AI助手对话框里粘贴发送即可",
            font=("Microsoft YaHei", 9),
            fg="#999",
        ).pack()

        root.mainloop()
    except ImportError:
        # No tkinter — terminal fallback
        print("\n" + "═" * 50)
        print("🖊  HeroPen 已安装！请复制下面这段话发给你的AI助手：")
        print("═" * 50)
        print(text)
        print("═" * 50)
        print()


def _install_with_detect() -> None:
    """``heropen install --detect`` — auto-detect calling agent and configure."""
    from heropen.auto_mcp import auto_setup_mcp, print_setup_summary, _is_sse_server_running
    import json, os
    from pathlib import Path

    print("🔧 HeroPen 自配置中...")
    result = auto_setup_mcp(agent="agent")

    # If no configs found, try to detect agent and CREATE config
    if not result["configured"] and not result["already_had"]:
        # Detection: env var → process list → parent → known paths
        is_wb = bool(os.environ.get("WORKBUDDY_HOME"))
        if not is_wb:
            try:
                import subprocess
                r = subprocess.run(
                    ["tasklist", "/fi", "imagename eq workbuddy.exe", "/nh"],
                    capture_output=True, text=True, timeout=5,
                )
                is_wb = bool(r.stdout.strip())
            except Exception:
                pass
        if not is_wb:
            try:
                import psutil
                parent_name = psutil.Process(os.getppid()).name().lower()
                is_wb = "workbuddy" in parent_name or "electron" in parent_name
            except Exception:
                pass
        if not is_wb:
            wb_dirs = [
                Path.home() / ".workbuddy",
                Path.home() / ".mcp.json",
            ]
            for p in wb_dirs:
                if p.exists():
                    is_wb = True
                    break

        if is_wb:
            mcp_path = Path.home() / ".mcp.json"
            cfg = {"mcpServers": {"heropen": {"type": "sse", "url": "http://127.0.0.1:8090/sse"}}}
            try:
                mcp_path.parent.mkdir(parents=True, exist_ok=True)
                with open(mcp_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                print(f"\n✅ 已创建 WorkBuddy SSE 配置文件：{mcp_path}")
                print(f"   配置：SSE → http://127.0.0.1:8090/sse")
                if _is_sse_server_running():
                    print("   ✅ SSE 服务已在运行")
                else:
                    print("   ⚠️  SSE 服务未运行，请手动启动：")
                    print("      heropen-mcp --http")
                print("\n💡 请重启 WorkBuddy 使配置生效。")
                return
            except OSError as e:
                print(f"\n❌ 写入失败：{mcp_path}（{e}）")
                return
        else:
            print("\n⚠️  未能自动检测到 AI 助手。")
            print("   弹出配置窗口，复制后发给你的 AI 助手即可。")
            _show_copy_text_dialog()
            return

    # Normal case: configs were found
    print_setup_summary(result)
    for item in result.get("configured", []) + result.get("already_had", []):
        if "(" in item and item.endswith(")"):
            path = item.split("(")[1].rstrip(")")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                servers = cfg.get("mcpServers", cfg.get("mcp_servers", {}))
                hero_cfg = servers.get("heropen", servers.get("hero-pen", {}))
                if hero_cfg:
                    print(f"\n   📄 {path.split('/')[-1]} 中的 heropen 配置：")
                    print(f"      {json.dumps(hero_cfg, ensure_ascii=False)}")
            except Exception:
                pass
    if not result.get("sse_started") and any("WorkBuddy" in i for i in result.get("configured", [])):
        print("\n   💡 如需自动启动 SSE 服务，请在 Windows 上运行：")
        print("      heropen-mcp --http")

    # Always show the copy dialog as a reliable fallback
    _show_copy_text_dialog()


def cmd_first_run() -> None:
    """``heropen`` first-run — auto-init + output copy-paste text for AI.

    The user copies the text block and pastes it to their AI assistant.
    The AI reads it and knows how to use HeroPen memory.
    """
    from heropen.auto_mcp import auto_setup_mcp

    agent_name = "agent"

    # 1. Init DB
    init_db(agent_name)

    # 2. Try to auto-configure MCP (don't show output, just do it)
    had_mcp = False
    try:
        mcp_result = auto_setup_mcp(agent=agent_name)
        had_mcp = bool(mcp_result.get("configured") or mcp_result.get("already_had"))
    except Exception:
        pass

    # 3. Build the copy-paste text block
    lines = [
        "━" * 70,
        "🖊  HeroPen 已安装",
        "   复制下面这段话发给你的 AI 助手，它会自己配置",
        "━" * 70,
        "",
        "【HeroPen 身份配置 — 请存入你的长期记忆】",
        "",
        f"你叫 {agent_name}，现在拥有 HeroPen 记忆系统。",
        f"你的记忆库在：{os.path.join(HERO_PEN_DIR, f'{agent_name}.db')}",
        "",
    ]

    if had_mcp:
        lines.extend([
            "MCP 服务已自动配置，你的 AI 可以直接调用记忆工具。",
        ])
    else:
        lines.extend([
            "MCP 配置方式（如果 AI 支持）：",
            f"  配置名：heropen",
            f"  命令：heropen-mcp",
            f"  类型：stdio",
        ])
    lines.extend([
        "",
        "每次对话开始时，调用 search_memory 工具：",
        f'  query: "最近的对话"',
        f'  agent: "{agent_name}"',
        "",
        "每次对话结束前，调用 add_memory 工具保存本次对话摘要：",
        '  section: "对话记录"',
        "  content: （本次对话的摘要——你自己总结，抓住重点）",
        f'  agent: "{agent_name}"',
        "",
        "以后回答时，看到跟记忆里已有信息相关的话题，",
        "先调 search_memory 看看之前说过什么，避免重复问用户。",
        "",
        "【结束】",
        "",
        "━" * 70,
        "粘贴发送后，AI 助手会自动理解并开始使用 HeroPen 记忆系统。",
        "━" * 70,
    ])

    print("")
    print("\n".join(lines))
    print("")


def _print_banner(use_rich: bool = False) -> None:
    """Print the HeroPen install banner."""
    if use_rich:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        console.print(Panel(
            "[bold cyan]🖊  HeroPen 安装配置向导[/bold cyan]\n"
            "[dim]让 AI 助手拥有长期记忆[/dim]",
            border_style="cyan",
        ))
    else:
        print()
        print("  🖊  HeroPen 安装配置向导")
        print("  ══════════════════════════════════════")
        print()


def _install_plain() -> None:
    """Fallback install wizard without rich."""
    _print_banner(use_rich=False)

    # Check for existing config
    if _config_exists():
        print("⚠️  检测到已有配置：")
        try:
            with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            for a in existing.get("agents", []):
                print(f"     - {a['name']} ({a.get('edition', 'basic')} 版)")
        except Exception:
            pass
        if not _confirm_overwrite():
            print("已取消。")
            return

    # Step 1: Scan
    print("🔍 正在扫描本机 AI 助手...")
    found = scan_agents()

    if found:
        print(f"\n✅ 发现了 {len(found)} 个 AI 助手：")
        for i, a in enumerate(found, 1):
            print(f"    {i}. {a.name}  (来自 {a.source})")
    else:
        print("\n⚠️  没有自动扫描到 AI 助手。")
        print("   请手动输入助手名称。")

    # Step 2: Manual input / confirm list
    selected_names: list[str] = []
    if found:
        print("\n请输入要配置的助手编号（用逗号分隔，如 1,2），或直接回车全选：")
        raw = input("> ").strip()
        if raw:
            try:
                indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
                selected_names = [found[i - 1].name for i in indices if 1 <= i <= len(found)]
            except (ValueError, IndexError):
                print("❌ 输入有误，默认全选。")
                selected_names = [a.name for a in found]
        else:
            selected_names = [a.name for a in found]
    else:
        print("\n请输入你的 AI 助手名字（多个用逗号分隔）：")
        raw = input("> ").strip()
        if raw:
            selected_names = [x.strip() for x in raw.split(",") if x.strip()]
        else:
            print("❌ 至少需要一个助手名字。")
            return

    # Validate names
    valid_names: list[str] = []
    for name in selected_names:
        ok, msg = _validate_agent_name(name)
        if ok:
            valid_names.append(name)
        else:
            print(f"⚠️  跳过「{name}」：{msg}")
    if not valid_names:
        print("❌ 没有有效的助手名字。")
        return
    selected_names = valid_names

    # Step 3: Edition
    print("\n请选择版型：")
    print(f"  [1] Basic — 给 {MAX_AGENTS_BASIC} 个助手使用（免费）")
    print(f"  [2] Plus  — 最多给 {MAX_AGENTS_PLUS} 个助手使用（订阅）")
    edition_raw = input("> ").strip()
    if edition_raw == "2":
        edition = "plus"
        max_agents = MAX_AGENTS_PLUS
    else:
        edition = "basic"
        max_agents = MAX_AGENTS_BASIC

    if len(selected_names) > max_agents:
        print(f"\n⚠️  {edition} 版最多支持 {max_agents} 个助手，已自动截断。")
        selected_names = selected_names[:max_agents]

    # Step 4: Generate
    print(f"\n⚙️  正在为 {len(selected_names)} 个助手生成配置...")

    config = generate_config(selected_names, edition)

    print(f"\n✅ 配置完成！配置文件已保存到：{AGENT_CONFIG_PATH}")
    print(f"\n{'═' * 55}")
    print("  请将以下配置分别发给对应的 AI 助手")
    print(f"{'═' * 55}\n")

    for i, agent_cfg in enumerate(config.agents, 1):
        text = _build_identity_text(agent_cfg, edition, len(config.agents))
        print(f"─── [{i}] {agent_cfg['name']} {'─' * (50 - len(agent_cfg['name']) - 7)}")
        print(f"\n{text}\n")

    print(f"{'═' * 55}")
    print("  助手收到后把那段文字存进长期记忆即可。")
    print("  以后它每次对话都知道自己有 HeroPen 记忆系统了。")
    print(f"{'═' * 55}\n")


def _install_with_rich() -> None:
    """Install wizard with rich — colors, boxes, checkboxes."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.text import Text
    import rich.box

    console = Console()

    # Check for existing config
    if _config_exists():
        console.print("\n[yellow]⚠️  检测到已有配置。[/yellow]")
        try:
            with open(AGENT_CONFIG_PATH, "r", encoding="utf-8") as f:
                existing = json.load(f)
            for a in existing.get("agents", []):
                console.print(f"     - {a['name']} ([dim]{a.get('edition', 'basic')} 版[/dim])")
        except Exception:
            pass
        if not _confirm_overwrite():
            console.print("[dim]已取消。[/dim]")
            return

    _print_banner(use_rich=True)

    # Step 1: Scan
    with console.status("[bold]🔍 正在扫描本机 AI 助手...[/bold]", spinner="dots"):
        found = scan_agents()

    if found:
        table = Table(title="✅ 发现了以下 AI 助手", box=rich.box.ROUNDED)
        table.add_column("#", style="cyan", width=4)
        table.add_column("助手名称", style="bold green")
        table.add_column("来源", style="blue")
        for i, a in enumerate(found, 1):
            table.add_row(str(i), a.name, a.source)
        console.print(table)
    else:
        console.print("\n[yellow]⚠️  没有自动扫描到 AI 助手。[/yellow]")
        console.print("  请手动输入助手名称。")

    # Step 2: Select agents
    selected_names: list[str] = []
    if found:
        console.print("\n请输入要配置的助手编号（用逗号分隔，如 [bold]1,2[/bold]），或直接回车全选：")
        raw = Prompt.ask(">", default="")
        if raw:
            try:
                indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
                selected_names = [found[i - 1].name for i in indices if 1 <= i <= len(found)]
            except (ValueError, IndexError):
                console.print("[red]❌ 输入有误，默认全选。[/red]")
                selected_names = [a.name for a in found]
        else:
            selected_names = [a.name for a in found]
    else:
        console.print("\n请输入你的 AI 助手名字（多个用逗号分隔）：")
        raw = Prompt.ask(">", default="")
        if raw:
            selected_names = [x.strip() for x in raw.split(",") if x.strip()]
        if not selected_names:
            console.print("[red]❌ 至少需要一个助手名字。[/red]")
            return

    # Validate names
    valid_names: list[str] = []
    for name in selected_names:
        ok, msg = _validate_agent_name(name)
        if ok:
            valid_names.append(name)
        else:
            console.print(f"[yellow]⚠️  跳过「{name}」：{msg}[/yellow]")
    if not valid_names:
        console.print("[red]❌ 没有有效的助手名字。[/red]")
        return
    selected_names = valid_names

    # Step 3: Edition
    console.print("\n请选择版型：")
    console.print(f"  [1] [bold]Basic[/bold] — 给 {MAX_AGENTS_BASIC} 个助手使用（免费）")
    console.print(f"  [2] [bold]Plus[/bold]  — 最多给 {MAX_AGENTS_PLUS} 个助手使用（订阅）")
    edition_raw = Prompt.ask(">", default="1")
    if edition_raw == "2":
        edition = "plus"
        max_agents = MAX_AGENTS_PLUS
    else:
        edition = "basic"
        max_agents = MAX_AGENTS_BASIC

    if len(selected_names) > max_agents:
        console.print(f"\n[yellow]⚠️  {edition} 版最多支持 {max_agents} 个助手，已自动截断。[/yellow]")
        selected_names = selected_names[:max_agents]

    # Step 4: Generate
    with console.status("[bold]⚙️  正在生成配置...[/bold]", spinner="dots"):
        config = generate_config(selected_names, edition)

    console.print(f"\n[green]✅ 配置完成！[/green] 配置文件：{AGENT_CONFIG_PATH}")

    # Step 5: Auto-configure MCP
    try:
        from heropen.auto_mcp import auto_setup_mcp
        mcp_result = auto_setup_mcp()
        if mcp_result["configured"] or mcp_result["already_had"]:
            mcp_lines = []
            for item in mcp_result["configured"]:
                mode = "SSE" if "WorkBuddy" in item else "stdio"
                mcp_lines.append(f"[green]  + {item} [{mode}][/green]")
            for item in mcp_result["already_had"]:
                mcp_lines.append(f"[dim]  . {item} (already configured)[/dim]")
            if mcp_lines:
                from rich.panel import Panel
                console.print(Panel("\n".join(mcp_lines), title="MCP Auto-Configured", border_style="green"))
        if mcp_result.get("sse_started"):
            console.print("[green]WorkBuddy SSE server started[/green]")
        if mcp_result.get("scheduler_registered"):
            console.print("[green]Auto-start registered for next login[/green]")
    except Exception:
        pass
    # Step 6: Show identity texts
    console.rule("[bold]请将以下配置分别发给对应的 AI 助手[/bold]")

    for i, agent_cfg in enumerate(config.agents, 1):
        text = _build_identity_text(agent_cfg, edition, len(config.agents))
        panel = Panel(
            text,
            title=f"[bold cyan]{i}. {agent_cfg['name']}[/bold cyan]",
            border_style="green",
            box=rich.box.ROUNDED,
        )
        console.print(panel)

    console.rule("[bold]✓ 配置完成[/bold]")
    console.print("[dim]助手收到后把那段文字存进长期记忆即可。[/dim]")
