# -*- coding: utf-8 -*-
"""
heropen 驻留角标 —— 原生 Windows 通知区（系统托盘）模块。

形态：任务栏右下角托盘图标（与微信/蓝牙/输入法同栏），不遮挡任何窗口内容。
由 `heropen tray` 命令启动（需安装 heropen[tray] 扩展）。

悬停 tooltip（四段，自解释身份，避免被当垃圾误删）：
    heropen agent 记忆系统
    存储方式：自动识别
    已存入：N 条
    已使用：N 天

数据源：优先读真实库 ~/.heropen/*.db（entries 表聚合）。读不到库时显示
        「暂无数据 / 0」，绝不编造数字。传 --mock 才强制用演示大数字。

菜单（2.0.4 起双语，语言选择持久化在 state.lang）：
  heropen 状态行
  版本 vX.Y.Z           (点开发布日志)
  ├ 存储模式  ▸ 大块存储 / 一轮一存 / 自动识别   (radio, 按 agent 分别设置)
  ├ Language / 语言  ▸ 简体中文 / English        (radio，双语菜单项写死双语，任一语言下都找得到)
  ├ 打开工作台          (左键单击图标同效)
  ├ 打开主页
  ├ 检查更新          (pip install -U heropen)
  ├ 开机自启          (开关，默认开，写入 HKCU Run 键)
  ├ 重启
  └ 退出

依赖：pystray, pillow（由 heropen[tray] extra 提供）。
运行：heropen tray  (或 python -m heropen.tray [--mock])
自检：heropen tray --selftest   (只生成图标/打印 tooltip 并退出)

存储模式（大块/一轮/自动）已接入内核：决定记忆往 heropen **落档的频率与粒度**
      ——chunk 攒批落 / sentence 一轮一存 / auto 按触发词智能选，按 agent 分别设置。
      详见 docs/2.0-开场签名与存储内核.md。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import html as _html
import webbrowser
import winreg

from PIL import Image, ImageDraw, ImageFont
import pystray

# 状态/单例/PID 文件一律落在数据目录 ~/.heropen/（不污染安装目录 / 项目目录）
HEROPEN_HOME = pathlib.Path.home() / ".heropen"
HEROPEN_HOME.mkdir(parents=True, exist_ok=True)

BASE = pathlib.Path(__file__).resolve().parent
STATE_FILE = HEROPEN_HOME / "_tray_state.json"
PID_FILE = HEROPEN_HOME / "_tray.pid"
ICON_PNG = HEROPEN_HOME / "_tray_icon.png"
ICON_ICO = HEROPEN_HOME / "_tray_icon.ico"
# 工作台是「每次点击临时生成」的页，放系统 temp 目录，点击即打开、不落项目
WORKBENCH = pathlib.Path(tempfile.gettempdir()) / "heropen_workbench.html"

HOMEPAGE = "https://heropen.net"
REFRESH_SECONDS = 60
TIP_MAX = 120   # Windows szTip = 128 wchar（含终止符），留余量

MODES = [("chunk", "大块存储"), ("sentence", "一轮一存"), ("auto", "自动识别")]
LABEL = dict(MODES)

# ---------------------------------------------------------------- i18n（2.0.4）
# 双语（简体中文 / English）。语言选择持久化在 state.lang，切换即时生效
# （重建菜单 + 刷 tooltip，无需重启角标）。「Language / 语言」这一项本身
# 双语写死——保证在任一语言下用户都能找到切换入口。

LANGS = [("zh", "简体中文"), ("en", "English")]

I18N = {
    "zh": {
        "product": "heropen agent 记忆系统",
        "storage_line": "存储方式：按 agent 分别设置（{n} 个）",
        "stored_line": "已存入：{v}",
        "used_line": "已使用：{v}",
        "unit_entries": "条",
        "unit_days": "天",
        "no_data": "暂无数据",
        "plan_free": "免费版",
        "status": "运行中 · {plan} · {n} 个 agent · 记忆 {ent}",
        "version": "版本 v{v}",
        "lang_menu": "Language / 语言",
        "storage_menu": "存储模式（按 agent 分别设置）",
        "kernel_note": "已接入内核：决定落档频率与粒度",
        "mode_chunk": "大块存储",
        "mode_sentence": "一轮一存",
        "mode_auto": "自动识别",
        "open_workbench": "打开工作台",
        "open_home": "打开主页",
        "check_update": "检查更新",
        "autostart": "开机自启",
        "restart": "重启",
        "quit": "退出",
        "notify_mode_set": "已设置「{agent}」的存储模式：{mode}（已生效：决定何时、多细地落档）",
        "notify_upgrade_ok": "升级完成，请重启角标生效",
        "notify_upgrade_fail": "升级失败，请手动执行 pip install -U heropen",
        "notify_upgrade_err": "升级出错：{e}",
        "notify_wb_err": "工作台生成失败：{e}",
        "rel_today": "今天",
        "rel_yesterday": "昨天",
        "rel_days": "{n} 天前",
        # 工作台页（wb_*）
        "wb_title": "工作台",
        "wb_running": "运行中",
        "wb_overview": "概览",
        "wb_stored": "已存记忆",
        "wb_used": "已使用",
        "wb_since": "自",
        "wb_banner_nodata": "未检测到本地 heropen 记忆库（~/.heropen/*.db）。装好 agent 并开始对话后，这里会显示真实记忆量与使用天数。",
        "wb_banner_mock": "演示数据（--mock）：以下为示例数字，非真实记忆量。",
        "wb_no_agents": "未发现 agent 库",
        "wb_modes_title": "存储模式（每个 agent 独立设置）",
        "wb_kernel_bold": "已接入内核",
        "wb_kernel_rest": "：下方三种模式决定记忆往 heropen <b>落档的频率与粒度</b>——大块存储攒批落、一轮一存句句落、自动识别按「记住 / 决定 / 路径 / 偏好」等触发词智能选。模式只管落档，不管检索与显示；写的仍是本机事实库。",
        "wb_modes_hint": "三种可选项：普通聊天用大块、讨论调教用一轮一存、平时交给自动。具体设置见下方 Agent 表。",
        "wb_chunk_desc": "普通聊天用。攒批落档，对话最快。",
        "wb_sentence_desc": "讨论、调教用。一句一存，重要的全留下。",
        "wb_auto_desc": "由 agent 判断该用哪种，无需手动切。",
        "wb_agents_title": "Agent（{n} 个 · 真实库）",
        "wb_agents_hint": "模式列即每个 agent 当前的落档策略（已生效）：大块存储攒批落、一轮一存句句落、自动识别按触发词智能选。在角标右键菜单「存储模式（按 agent 分别设置）」中调整——一人对多 agent，各自存法不同。条数与最近写入来自真实库。",
        "wb_th_mode": "模式",
        "wb_th_count": "记忆条数",
        "wb_th_last": "最近写入",
        "wb_th_7d": "近 7 天",
        "wb_skill_title": "Skill 收集与共享",
        "wb_skill_desc": "把记住的东西变成能复用的 skill，跨 agent 共享。",
        "wb_skill_btn": "Plus 专属",
        "wb_health_title": "记忆健康",
        "wb_health_all": "全部 {n} 个 agent 近 7 天均有新增记忆",
        "wb_health_part": "{a}/{n} 个 agent 近 7 天有新增记忆",
        "wb_health_msg": "本地 {n} 个 agent · 最早记忆 {since} · 累计 {total} 条；本地存储无外部依赖，记忆压缩后仍可调用，不丢失之前的记忆。",
        "wb_footer_local": "本地库 · 无外部依赖",
        "wb_footer_tray": "托盘角标",
    },
    "en": {
        "product": "heropen agent memory",
        "storage_line": "Storage: per agent ({n})",
        "stored_line": "Stored: {v}",
        "used_line": "In use: {v}",
        "unit_entries": "entries",
        "unit_days": "days",
        "no_data": "No data yet",
        "plan_free": "Free",
        "status": "Running · {plan} · {n} agents · {ent}",
        "version": "Version v{v}",
        "lang_menu": "Language / 语言",
        "storage_menu": "Storage mode (per agent)",
        "kernel_note": "Wired into the core: controls write frequency & granularity",
        "mode_chunk": "Chunk (batched)",
        "mode_sentence": "Per-turn",
        "mode_auto": "Auto",
        "open_workbench": "Open workbench",
        "open_home": "Open homepage",
        "check_update": "Check for updates",
        "autostart": "Start at login",
        "restart": "Restart",
        "quit": "Quit",
        "notify_mode_set": "Storage mode for \"{agent}\" set to {mode} (effective immediately)",
        "notify_upgrade_ok": "Update installed - restart the tray icon to apply",
        "notify_upgrade_fail": "Update failed - run: pip install -U heropen",
        "notify_upgrade_err": "Update error: {e}",
        "notify_wb_err": "Failed to build workbench: {e}",
        "rel_today": "Today",
        "rel_yesterday": "Yesterday",
        "rel_days": "{n} days ago",
        # workbench page (wb_*)
        "wb_title": "Workbench",
        "wb_running": "Running",
        "wb_overview": "Overview",
        "wb_stored": "Memories stored",
        "wb_used": "In use",
        "wb_since": "Since",
        "wb_banner_nodata": "No local heropen memory library detected (~/.heropen/*.db). Once an agent is set up and conversations start, real memory counts and usage days will appear here.",
        "wb_banner_mock": "Demo data (--mock): the numbers below are samples, not real memories.",
        "wb_no_agents": "No agent libraries found",
        "wb_modes_title": "Storage mode (per agent)",
        "wb_kernel_bold": "Wired into the core",
        "wb_kernel_rest": ": the three modes below control <b>how often and how finely</b> memories are written to heropen - Chunk batches, Per-turn writes every turn, Auto picks by trigger words such as \"remember / decide / path / preference\". Modes affect writing only, not retrieval or display; everything stays in the local fact base.",
        "wb_modes_hint": "Three options: Chunk for everyday chat, Per-turn for decisions and coaching, Auto otherwise. Set them per agent in the table below.",
        "wb_chunk_desc": "For everyday chat. Batched writes, fastest conversations.",
        "wb_sentence_desc": "For discussions and tuning. Every turn saved, nothing important lost.",
        "wb_auto_desc": "The agent picks the right mode - no manual switching.",
        "wb_agents_title": "Agents ({n} · real libraries)",
        "wb_agents_hint": "The mode column is each agent's live write policy: Chunk batches, Per-turn saves every turn, Auto picks by trigger words. Adjust it in the tray menu under \"Storage mode (per agent)\" - one person, many agents, each with its own policy. Counts and last-write come from the real libraries.",
        "wb_th_mode": "Mode",
        "wb_th_count": "Entries",
        "wb_th_last": "Last write",
        "wb_th_7d": "Last 7 days",
        "wb_skill_title": "Skill collection & sharing",
        "wb_skill_desc": "Turn remembered knowledge into reusable skills, shared across agents.",
        "wb_skill_btn": "Plus only",
        "wb_health_title": "Memory health",
        "wb_health_all": "All {n} agents added memories in the last 7 days",
        "wb_health_part": "{a} of {n} agents added memories in the last 7 days",
        "wb_health_msg": "{n} local agents · earliest memory {since} · {total} entries in total; local storage with no external dependencies - memories stay usable after compression, nothing from the past is lost.",
        "wb_footer_local": "Local storage · no external dependencies",
        "wb_footer_tray": "tray icon",
    },
}

# 模式代码 -> i18n key
MODE_KEY = {"chunk": "mode_chunk", "sentence": "mode_sentence", "auto": "mode_auto"}


def tr(lang: str, key: str, **kw) -> str:
    """取翻译：lang 缺失回退中文，key 缺失回退中文同 key（永不抛 KeyError）。"""
    d = I18N.get(lang) or I18N["zh"]
    s = d.get(key) or I18N["zh"].get(key) or key
    return s.format(**kw) if kw else s


def mode_label(mode: str, lang: str = "zh") -> str:
    return tr(lang, MODE_KEY.get(mode, "mode_auto"))

# agent 库忽略名单：只排除本机开发/测试残留与明确无用库。
# 这是随包发布的名单，必须只含「绝不可能是真实用户 agent 名」的标识，
# 以免误伤真人库（DF 评审要求：别误伤真人库）。
# 真实活跃库（如 xiaokai、_shared）不在此列，会正常显示。
AGENT_IGNORE = {"install-agent2-gateway", "妮妮"}
# 演示用 mock 数据：仅在显式 --mock 时展示（给不会读库的环境看大数字）。
# 真实环境读不到库时一律显示「暂无数据 / 0」，绝不编造数字。
MOCK_DEMO = {"entries": 12846, "days": 104}
DEFAULT_STATE = {
    "modes": {},             # 按 agent 分别设置： {agent名: mode}
    "default_mode": "auto",  # 新发现 agent 的默认模式
    "plan": "free",          # free / plus（当前写死免费版；Plus 激活检测未落地）
    "autostart": True,       # 默认开机自启（用户已确认保持开启）
    "lang": "zh",            # 角标显示语言：zh / en（2.0.4 起双语）
}

# 开机自启：写入 HKCU\...\Run（无需管理员，pythonw 避免登录弹黑窗）
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VAL = "heropen"


def pythonw_exe() -> str:
    """优先 pythonw（无控制台），缺失则退回 python。"""
    p = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return p if os.path.exists(p) else sys.executable


def get_version() -> str:
    """取当前 heropen 版本：优先已装包，其次 ~/.heropen/.version，再兜底常量。"""
    try:
        import importlib.metadata as m

        v = m.version("heropen")
        if v:
            return v
    except Exception:
        pass
    try:
        v = (HEROPEN_HOME / ".version").read_text(encoding="utf-8", errors="replace").strip()
        if v:
            return v
    except Exception:
        pass
    # 兜底返回「未知」而非写死版本号：避免 heropen 卸载后误显示旧常量，
    # 宁可诚实标未知，也不要给出错的版本。
    return "未知"


def set_autostart_reg(enabled: bool) -> None:
    """把角标写进/移出开机自启注册表项（指向 `pythonw -m heropen.tray`）。"""
    # 用 -m heropen.tray 而非直接脚本路径：安装后落点在 site-packages，
    # 跨版本升级不会被旧路径绑死。pythonw 保证登录时不出控制台黑窗。
    cmd = f'"{pythonw_exe()}" -m heropen.tray'
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(k, AUTOSTART_VAL, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, AUTOSTART_VAL)
            except FileNotFoundError:
                pass
        winreg.CloseKey(k)
    except Exception:
        pass


# ---------------------------------------------------------------- 真实库统计


def read_real_stats() -> dict | None:
    """聚合 ~/.heropen/*.db 的 entries 表：总条数 + 最早记忆日 -> 已使用天数。

    返回 None 表示读不到任何有效库（调用方回退 mock）。
    """
    if not HEROPEN_HOME.is_dir():
        return None
    total = 0
    earliest: str | None = None
    for db in sorted(HEROPEN_HOME.glob("*.db")):
        name = db.stem
        if name in AGENT_IGNORE:
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = con.cursor()
            has = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'"
            ).fetchone()
            if not has:
                con.close()
                continue
            row = cur.execute("SELECT COUNT(*), MIN(entry_date) FROM entries").fetchone()
            con.close()
            n = int(row[0] or 0)
            mn = (row[1] or "")[:10]
            total += n
            if mn and (earliest is None or mn < earliest):
                earliest = mn
        except Exception:
            continue
    if earliest is None and total == 0:
        return None
    days = 0
    if earliest:
        try:
            d0 = _dt.date.fromisoformat(earliest)
            days = max((_dt.date.today() - d0).days + 1, 1)
        except Exception:
            pass
    return {"entries": total, "days": days, "since": earliest, "real": True}


def discover_agents() -> list:
    """从真实库目录发现活 agent（库名 = agent 名），过滤测试残留与空壳库。

    只列真正含 entries 记忆表的库——避免把空壳库（如小凯.db 占位壳）当成
    agent 列进菜单，造成同一人重复计数或计数失真。
    """
    if not HEROPEN_HOME.is_dir():
        return []
    out = []
    for db in sorted(HEROPEN_HOME.glob("*.db")):
        name = db.stem
        if name in AGENT_IGNORE:
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = con.cursor()
            has = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'"
            ).fetchone()
            con.close()
            if has:
                out.append(name)
        except Exception:
            continue
    return out


def _rel_time(date_str, lang: str = "zh") -> str:
    """把 'YYYY-MM-DD' 转成「今天/昨天/N 天前/具体日期」相对描述（双语）。"""
    try:
        d = _dt.date.fromisoformat(str(date_str)[:10])
        diff = (_dt.date.today() - d).days
        if diff <= 0:
            return tr(lang, "rel_today")
        if diff == 1:
            return tr(lang, "rel_yesterday")
        if diff < 7:
            return tr(lang, "rel_days", n=diff)
        return d.isoformat()
    except Exception:
        return str(date_str)[:10]


def per_agent_stats(lang: str = "zh") -> list:
    """每个活 agent 的真实统计：名称 / 总条数 / 最近写入 / 近 7 天每日分布。

    读真实库 ~/.heropen/<agent>.db 的 entries 表，与角标 tooltip 同源。
    """
    if not HEROPEN_HOME.is_dir():
        return []
    today = _dt.date.today()
    seven_ago = (today - _dt.timedelta(days=6)).isoformat()
    out = []
    for db in sorted(HEROPEN_HOME.glob("*.db")):
        name = db.stem
        if name in AGENT_IGNORE:
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = con.cursor()
            if not cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries'"
            ).fetchone():
                con.close()
                continue
            count = int(cur.execute("SELECT COUNT(*) FROM entries").fetchone()[0] or 0)
            last = cur.execute("SELECT MAX(entry_date) FROM entries").fetchone()[0]
            daily = cur.execute(
                "SELECT substr(entry_date,1,10) AS d, COUNT(*) FROM entries WHERE d >= ? GROUP BY d",
                (seven_ago,),
            ).fetchall()
            con.close()
            by_day = {str(r[0]): int(r[1]) for r in daily}
            bars = [
                by_day.get((today - _dt.timedelta(days=i)).isoformat(), 0)
                for i in range(6, -1, -1)
            ]
            out.append(
                {
                    "name": name,
                    "count": count,
                    "last": _rel_time(last, lang) if last else "—",
                    "bars": bars,
                }
            )
        except Exception:
            continue
    return out


# 工作台动态页样式（与静态 heropen_workbench.html 视觉一致）
WORKBENCH_CSS = """
  :root{
    --bg:#fafafa; --card:#ffffff; --line:#ececec; --line2:#e2e2e2;
    --ink:#111111; --ink2:#555555; --ink3:#8c8c8c;
    --live:#12b981; --warn:#e0a020; --plus:#111111;
    --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:var(--bg); color:var(--ink); font-family:var(--sans);
    font-size:14px; line-height:1.6; -webkit-font-smoothing:antialiased;
    padding:36px 40px 64px;
  }
  .wrap{max-width:880px;margin:0 auto}
  header{display:flex;align-items:baseline;gap:12px;padding-bottom:18px;border-bottom:1px solid var(--line)}
  .brand{font-size:20px;font-weight:700;letter-spacing:-.02em}
  .sub{color:var(--ink3);font-size:13px}
  .spacer{flex:1}
  .pill{font-size:12px;color:var(--ink2);border:1px solid var(--line2);border-radius:999px;padding:2px 10px;font-family:var(--mono)}
  .pill.live::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--live);margin-right:6px;vertical-align:1px;box-shadow:0 0 0 3px rgba(18,185,129,.15)}
  h2{font-size:12px;font-weight:600;letter-spacing:.08em;color:var(--ink3);text-transform:uppercase;margin:34px 0 12px}
  .hint{font-size:12.5px;color:var(--ink2);margin:-4px 0 14px;line-height:1.55}
  .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .metric{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .metric .k{font-size:12px;color:var(--ink3)}
  .metric .v{font-family:var(--mono);font-size:26px;font-weight:600;letter-spacing:-.02em;margin-top:2px}
  .metric .v small{font-size:13px;color:var(--ink3);font-weight:400;margin-left:4px}
  .modes{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
  .mode{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;cursor:default}
  .mode.on{border-color:var(--ink);box-shadow:0 0 0 1px var(--ink) inset}
  .mode .n{display:flex;align-items:center;gap:8px;font-weight:600}
  .mode .n .dot{width:7px;height:7px;border-radius:50%;background:var(--line2)}
  .mode.on .n .dot{background:var(--ink)}
  .mode .d{font-size:12.5px;color:var(--ink2);margin-top:6px}
  .mode .tag{font-family:var(--mono);font-size:11px;color:var(--ink3);margin-top:8px}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  th,td{padding:11px 16px;text-align:left;border-bottom:1px solid var(--line)}
  tr:last-child td{border-bottom:none}
  th{font-size:12px;font-weight:600;color:var(--ink3);background:#fcfcfc}
  td.mono,.mono{font-family:var(--mono);font-size:12.5px}
  .aname{font-weight:600}
  .bars{display:flex;align-items:flex-end;gap:3px;height:22px}
  .bars i{display:block;width:6px;background:#dcdcdc;border-radius:2px}
  .bars i.hi{background:var(--ink)}
  .skill{background:var(--card);border:1px dashed var(--line2);border-radius:12px;padding:16px 18px;display:flex;align-items:center;gap:14px;color:var(--ink3)}
  .skill .btn{margin-left:auto;font-size:12.5px;border:1px solid var(--line2);border-radius:8px;padding:5px 12px;color:var(--ink2);background:#fafafa;cursor:default}
  .plus-tag{font-family:var(--mono);font-size:10.5px;border:1px solid var(--line2);border-radius:4px;padding:1px 5px;color:var(--ink3)}
  .health{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
  .health .row{display:flex;align-items:center;gap:12px}
  .health .lbl{font-size:13px}
  .health .msg{color:var(--ink2);font-size:12.5px;margin-top:4px}
  .bar{height:6px;background:#efefef;border-radius:999px;overflow:hidden;margin-top:12px}
  .bar span{display:block;height:100%;width:0;background:var(--ink)}
  footer{margin-top:36px;color:var(--ink3);font-size:12px;display:flex;gap:14px;align-items:center}
  footer code{font-family:var(--mono);background:#f2f2f2;border-radius:4px;padding:1px 6px}
  .plan-note{background:#fff8ec;border:1px solid #f0d9a8;border-radius:12px;padding:14px 18px;color:#7a5b1a;font-size:12.5px;line-height:1.6}
"""


def build_workbench_html(stats: dict, agents: list, modes: dict, plan: str, version: str,
                         lang: str = "zh") -> str:
    """用真实数据生成工作台 HTML（替代写死假数的静态 demo 页）。2.0.4 起双语。"""
    plan_label = tr(lang, "plan_free") if plan == "free" else "Plus"
    no_data = bool(stats.get("no_data"))
    is_mock = bool(stats.get("mock"))
    total = 0 if no_data else stats.get("entries", 0)
    days = 0 if no_data else stats.get("days", 0)
    since = stats.get("since") or "—"
    ent_disp = tr(lang, "no_data") if no_data else f"{total:,}<small>{tr(lang, 'unit_entries')}</small>"
    day_disp = f"{days}<small>{tr(lang, 'unit_days')}</small>"
    if no_data:
        banner = ('<div class="plan-note">⚠ '
                  + (tr(lang, "wb_banner_nodata"))
                  + '</div>')
    elif is_mock:
        banner = ('<div class="plan-note">'
                  + tr(lang, "wb_banner_mock")
                  + '</div>')
    else:
        banner = ""

    # 记忆健康：真实派生指标——近 7 天有新增记忆的 agent 占比（非写死百分比）
    total_agents = len(agents)
    active_7d = sum(1 for a in agents if any(a.get("bars") or []))
    active_ratio = int(round(active_7d / total_agents * 100)) if total_agents else 0
    if total_agents == 0:
        active_label = tr(lang, "wb_no_agents")
    elif active_7d == total_agents:
        active_label = tr(lang, "wb_health_all", n=total_agents)
    else:
        active_label = tr(lang, "wb_health_part", a=active_7d, n=total_agents)

    rows = []
    for a in agents:
        mode = modes.get(a["name"], "auto")
        bars = a["bars"]
        mx = max(bars) if bars else 0
        bars_html = "".join(
            '<i class="{hi}" style="height:{h}px"></i>'.format(
                hi=("hi" if i == len(bars) - 1 else ""),
                h=(max(int(b / mx * 20), 3) if mx else 3),
            )
            for i, b in enumerate(bars)
        )
        rows.append(
            "<tr>"
            f'<td class="aname">{_html.escape(a["name"])}</td>'
            f'<td class="mono">{_html.escape(mode_label(mode, lang))}</td>'
            f'<td class="mono">{a["count"]:,}</td>'
            f'<td class="mono">{_html.escape(a["last"])}</td>'
            f'<td><div class="bars">{bars_html}</div></td>'
            "</tr>"
        )
    rows_str = "\n".join(rows) if rows else (
        f'<tr><td colspan="5" class="mono">{tr(lang, "wb_no_agents")}</td></tr>')

    html_lang = "zh-CN" if lang == "zh" else "en"
    return f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>heropen · {tr(lang, "wb_title")}</title>
<style>{WORKBENCH_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">heropen</div>
    <div class="sub">{tr(lang, "wb_title")}</div>
    <div class="spacer"></div>
    <div class="pill live">{tr(lang, "wb_running")}</div>
    <div class="pill">{plan_label}</div>
  </header>

  {banner}

  <h2>{tr(lang, "wb_overview")}</h2>
  <div class="metrics">
    <div class="metric"><div class="k">{tr(lang, "wb_stored")}</div><div class="v">{ent_disp}</div></div>
    <div class="metric"><div class="k">{tr(lang, "wb_used")}</div><div class="v">{day_disp}</div></div>
    <div class="metric"><div class="k">{tr(lang, "wb_since")}</div><div class="v" style="font-size:20px">{_html.escape(str(since))}</div></div>
  </div>

  <h2>{tr(lang, "wb_modes_title")}</h2>
  <div class="plan-note">✅ <b>{tr(lang, "wb_kernel_bold")}</b>{tr(lang, "wb_kernel_rest")}</div>
  <p class="hint">{tr(lang, "wb_modes_hint")}</p>
  <div class="modes">
    <div class="mode"><div class="n"><span class="dot"></span>{tr(lang, "mode_chunk")}</div><div class="d">{tr(lang, "wb_chunk_desc")}</div><div class="tag">chunk</div></div>
    <div class="mode"><div class="n"><span class="dot"></span>{tr(lang, "mode_sentence")}</div><div class="d">{tr(lang, "wb_sentence_desc")}</div><div class="tag">sentence</div></div>
    <div class="mode"><div class="n"><span class="dot"></span>{tr(lang, "mode_auto")}</div><div class="d">{tr(lang, "wb_auto_desc")}</div><div class="tag">auto</div></div>
  </div>

  <h2>{tr(lang, "wb_agents_title", n=len(agents))}</h2>
  <p class="hint">{tr(lang, "wb_agents_hint")}</p>
  <table>
    <thead><tr><th>Agent</th><th>{tr(lang, "wb_th_mode")}</th><th>{tr(lang, "wb_th_count")}</th><th>{tr(lang, "wb_th_last")}</th><th>{tr(lang, "wb_th_7d")}</th></tr></thead>
    <tbody>
{rows_str}
    </tbody>
  </table>

  <h2>{tr(lang, "wb_skill_title")} <span class="plus-tag">PLUS</span></h2>
  <div class="skill">
    <span>{tr(lang, "wb_skill_desc")}</span>
    <span class="btn">{tr(lang, "wb_skill_btn")}</span>
  </div>

  <h2>{tr(lang, "wb_health_title")}</h2>
  <div class="health">
    <div class="row"><span class="lbl">{active_label}</span></div>
    <div class="msg">{tr(lang, "wb_health_msg", n=len(agents), since=since, total=f"{total:,}")}</div>
    <div class="bar"><span style="width:{active_ratio}%"></span></div>
  </div>

  <footer>
    <span>{tr(lang, "wb_footer_local")}</span><span>·</span>
    <span>heropen v{_html.escape(version)}</span><span>·</span>
    <span>{tr(lang, "wb_footer_tray")} <code>heropen tray</code></span>
  </footer>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------- state


def load_state() -> dict:
    st = dict(DEFAULT_STATE)
    try:
        if STATE_FILE.exists():
            st.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return st


def save_state(st: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 单例控制（避免出现两个角标）


def _owns_process(pid: int) -> bool:
    """确认该 pid 确实是本模块托盘实例（防 PID 复用误杀）。"""
    # 经由 `pythonw -m heropen.tray` 启动，命令行含 "heropen.tray"
    ps = (
        "$c=(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CommandLine; "
        "if ($c -like '*heropen.tray*') { 'YES' } else { 'NO' }" % pid
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
        return "YES" in (r.stdout or "")
    except Exception:
        return False


def stop_previous(verbose: bool = True) -> bool:
    """停掉上一次遗留的托盘实例（读 PID 文件 + 校验命令行）。"""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    if not _owns_process(pid):
        if verbose:
            print(f"stale pid file (pid {pid} not a tray instance) - ignored")
        try:
            PID_FILE.unlink()
        except Exception:
            pass
        return False
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, timeout=20)
        if verbose:
            print(f"stopped previous tray instance: pid {pid}")
    except Exception as e:
        if verbose:
            print(f"failed to stop pid {pid}: {e}")
        return False
    try:
        PID_FILE.unlink()
    except Exception:
        pass
    return True


def write_pid() -> None:
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- icon


def _load_font(px: int):
    for name in ("segoeuib.ttf", "msyhbd.ttc", "arialbd.ttf"):
        p = pathlib.Path("C:/Windows/Fonts") / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), px)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


def make_icon_image(size: int = 64) -> Image.Image:
    """深色圆角方块 + 白色小写 h。亮/暗任务栏下均可辨识。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [1, 1, size - 2, size - 2], radius=int(size * 0.24), fill=(17, 17, 17, 255)
    )
    font = _load_font(int(size * 0.70))
    txt = "h"
    bbox = d.textbbox((0, 0), txt, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(
        ((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - size * 0.03),
        txt,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return img


def export_icon_files() -> None:
    img = make_icon_image(256)
    img.save(ICON_PNG)
    img.save(ICON_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


# ---------------------------------------------------------------- app


class TrayApp:
    def __init__(self, force_mock: bool = False) -> None:
        self.force_mock = force_mock
        self.state = load_state()
        # 显示语言（2.0.4）：只认 zh/en，非法值回退中文
        self.lang = self.state.get("lang") if self.state.get("lang") in ("zh", "en") else "zh"
        self.state["lang"] = self.lang
        # 按 agent 分设：以 discover_agents()（只含真正有 entries 表的活 agent）
        # 为准，补齐新发现 agent 的默认模式，并裁掉已消失/空壳的旧 agent，
        # 避免把空壳库（如小凯.db 占位壳）当成可设模式的 agent 列进菜单。
        self.state.setdefault("modes", {})
        live = discover_agents()
        for ag in live:
            self.state["modes"].setdefault(ag, self.state.get("default_mode", "auto"))
        for ag in list(self.state["modes"].keys()):
            if ag not in live:
                self.state["modes"].pop(ag, None)
        self.stats: dict = {}
        self.refresh_stats()
        set_autostart_reg(self.state.get("autostart", False))  # 开机自启：按状态写入注册表
        self.icon = pystray.Icon(
            "heropen",
            icon=make_icon_image(64),
            title=self._tooltip(),
            menu=self._build_menu(),
        )

    # -- helpers
    def refresh_stats(self) -> None:
        """优先真实库；--mock 才用演示大数字；读不到库显示「暂无数据 / 0」。"""
        if self.force_mock:
            self.stats = {**MOCK_DEMO, "since": None, "real": False, "mock": True}
            return
        got = read_real_stats()
        if got:
            self.stats = got
        else:
            self.stats = {"entries": 0, "days": 0, "since": None, "real": False, "no_data": True}

    def _tooltip(self) -> str:
        """四段悬停提示：身份 / 存储方式 / 已存条数 / 已使用天数（双语）。"""
        n_agents = len(self.state.get("modes", {}))
        if self.stats.get("no_data"):
            ent_disp = tr(self.lang, "no_data")
            day_disp = "0"
        else:
            ent_disp = f"{self.stats.get('entries', 0):,} {tr(self.lang, 'unit_entries')}"
            day_disp = f"{self.stats.get('days', 0)} {tr(self.lang, 'unit_days')}"
        tip = "\n".join(
            [
                tr(self.lang, "product"),
                tr(self.lang, "storage_line", n=n_agents),
                tr(self.lang, "stored_line", v=ent_disp),
                tr(self.lang, "used_line", v=day_disp),
            ]
        )
        return tip[:TIP_MAX]

    def _status_line(self, item=None) -> str:
        plan = tr(self.lang, "plan_free") if self.state["plan"] == "free" else "Plus"
        n = len(self.state.get("modes", {}))
        ent = tr(self.lang, "no_data") if self.stats.get("no_data") else f"{self.stats.get('entries', 0):,} {tr(self.lang, 'unit_entries')}"
        return tr(self.lang, "status", plan=plan, n=n, ent=ent)

    def _refresh(self) -> None:
        self.refresh_stats()
        save_state(self.state)
        self.icon.title = self._tooltip()
        self.icon.update_menu()

    # -- actions
    def set_mode_for(self, agent: str, mode: str):
        def handler(icon, item):
            self.state.setdefault("modes", {})[agent] = mode
            self._refresh()
            # 模式已实时写入 state，get_storage_mode 即时读 → 落档策略真实生效
            try:
                icon.notify(
                    tr(self.lang, "notify_mode_set", agent=agent, mode=mode_label(mode, self.lang)),
                    "heropen",
                )
            except Exception:
                pass
        return handler

    def set_lang(self, lang: str):
        """切换显示语言（radio）：写入 state、重建菜单、刷 tooltip，即时生效。"""
        def handler(icon, item):
            if lang not in ("zh", "en"):
                return
            self.lang = lang
            self.state["lang"] = lang
            self._refresh()
            # 菜单文案是静态构建的，必须整体重建才能换语言
            try:
                icon.menu = self._build_menu()
                icon.update_menu()
            except Exception:
                pass
            try:
                icon.notify(
                    ("Language switched to English" if lang == "en" else "已切换为简体中文"),
                    "heropen",
                )
            except Exception:
                pass
        return handler

    def open_workbench(self, icon=None, item=None):
        """左键单击默认动作：用真实数据动态生成工作台页并打开。

        每次点击都重新聚合真实库（与角标 tooltip 同源），写 temp 目录的
        heropen_workbench.html 再 webbrowser 打开，避免静态 demo 的假数据。
        """
        try:
            self.refresh_stats()
            agents = per_agent_stats(self.lang)
            html = build_workbench_html(
                self.stats,
                agents,
                self.state.get("modes", {}),
                self.state.get("plan", "free"),
                get_version(),
                lang=self.lang,
            )
            WORKBENCH.write_text(html, encoding="utf-8")
            webbrowser.open(WORKBENCH.as_uri())
        except Exception as e:
            try:
                (icon or self.icon).notify(tr(self.lang, "notify_wb_err", e=e), "heropen")
            except Exception:
                pass

    def open_home(self, icon=None, item=None):
        webbrowser.open(HOMEPAGE)

    def show_version(self, icon=None, item=None):
        # 版本行点击 -> 打开官方文档（含更新日志）
        webbrowser.open(HOMEPAGE + "/docs")

    def upgrade(self, icon=None, item=None):
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "heropen"],
                capture_output=True, text=True, errors="replace", timeout=300,
            )
            ok = r.returncode == 0
            (icon or self.icon).notify(
                tr(self.lang, "notify_upgrade_ok") if ok else tr(self.lang, "notify_upgrade_fail"),
                "heropen",
            )
        except Exception as e:
            (icon or self.icon).notify(tr(self.lang, "notify_upgrade_err", e=e), "heropen")

    def toggle_autostart(self, icon, item):
        self.state["autostart"] = not self.state["autostart"]
        set_autostart_reg(self.state["autostart"])
        self._refresh()

    def quit(self, icon, item):
        # 先隐藏图标，再停止消息循环
        try:
            icon.visible = False
        except Exception:
            pass
        icon.stop()
        # _setup 后台线程是 non-daemon，消息循环退出后不会自动死，
        # 必须强杀进程，否则菜单「退出」点了图标消失、python 进程却残留。
        os._exit(0)

    def restart(self, icon, item):
        """重启：拉起一个新的 `pythonw -m heropen.tray` 实例（加载最新代码），
        再强杀当前进程。一律用 pythonw（Windows 子系统，无控制台窗口），
        避免重启时弹出可被误关的黑窗。
        """
        try:
            icon.visible = False
        except Exception:
            pass
        icon.stop()
        # 先清掉自己的 PID，让新实例干净接管（避免 stop_previous 误判/竞态）
        try:
            PID_FILE.unlink()
        except Exception:
            pass
        try:
            # pythonw -m heropen.tray：无控制台窗口，跨版本升级不被旧脚本路径绑死
            subprocess.Popen(
                [pythonw_exe(), "-m", "heropen.tray"],
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 8),
                close_fds=True,
            )
        except Exception:
            # pythonw 缺失时回退（仍尝试 detached，但可能仍带控制台窗口）
            subprocess.Popen(
                [sys.executable, "-m", "heropen.tray"],
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 8),
                close_fds=True,
            )
        os._exit(0)

    # -- menu
    def _build_menu(self) -> pystray.Menu:
        L = self.lang
        return pystray.Menu(
            # 注：Win32 对 enabled=False 的菜单项强制置灰（MFS_DISABLED）。
            # 这两行是「信息行」，要黑字就只能是 enabled=True + 空动作
            # （action=None 被 pystray 包成 lambda *_ : None，点击无副作用）。
            pystray.MenuItem("heropen", None),
            pystray.MenuItem(self._status_line, None),
            pystray.MenuItem(tr(L, "version", v=get_version()), self.show_version),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                tr(L, "storage_menu"),
                pystray.Menu(
                    # 模式已接入内核：决定落档频率与粒度（实时生效）
                    pystray.MenuItem(tr(L, "kernel_note"), None, enabled=False),
                    pystray.Menu.SEPARATOR,
                    *[
                        pystray.MenuItem(
                            ag,
                            pystray.Menu(
                                *[
                                    pystray.MenuItem(
                                        mode_label(mode, L),
                                        self.set_mode_for(ag, mode),
                                        checked=lambda item, a=ag, m=mode: self.state.get("modes", {}).get(a) == m,
                                        radio=True,
                                    )
                                    for mode in ("chunk", "sentence", "auto")
                                ]
                            ),
                        )
                        for ag in sorted(self.state.get("modes", {}).keys())
                    ]
                ),
            ),
            # 双语切换（2.0.4）：菜单项标题写死双语，任一语言下都找得到
            pystray.MenuItem(
                tr(L, "lang_menu"),
                pystray.Menu(
                    *[
                        pystray.MenuItem(
                            label,
                            self.set_lang(code),
                            checked=lambda item, c=code: self.lang == c,
                            radio=True,
                        )
                        for code, label in LANGS
                    ]
                ),
            ),
            pystray.MenuItem(tr(L, "open_workbench"), self.open_workbench, default=True),
            pystray.MenuItem(tr(L, "open_home"), self.open_home),
            pystray.MenuItem(tr(L, "check_update"), self.upgrade),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(tr(L, "autostart"), self.toggle_autostart,
                             checked=lambda item: self.state["autostart"]),
            pystray.MenuItem(tr(L, "restart"), self.restart),
            pystray.MenuItem(tr(L, "quit"), self.quit),
        )

    def _setup(self, icon) -> None:
        """pystray 的 setup 回调在独立线程执行：定时把真实统计刷进 tooltip。"""
        icon.visible = True
        while True:
            time.sleep(REFRESH_SECONDS)
            try:
                self.refresh_stats()
                self.icon.title = self._tooltip()
            except Exception:
                pass

    def run(self) -> None:
        self.icon.run(setup=self._setup)


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="heropen tray")
    ap.add_argument("--selftest", action="store_true", help="只生成图标/打印 tooltip 并退出")
    ap.add_argument("--mock", action="store_true", help="强制使用内置 mock 数据（演示大数字）")
    ap.add_argument("--stop", action="store_true", help="停掉正在运行的托盘实例并退出")
    args = ap.parse_args(argv)

    if args.stop:
        return 0 if stop_previous() else 1

    if args.selftest:
        export_icon_files()
        app = TrayApp(force_mock=args.mock)
        img = make_icon_image(64)
        print("selftest ok; icon size:", img.size, "->", ICON_PNG.name, ICON_ICO.name)
        print("--- tooltip (zh default) ---")
        print(app._tooltip())
        print("--- tooltip (en, temporary switch, not saved) ---")
        app.lang = "en"
        print(app._tooltip())
        app.lang = "zh"
        print("--- source ---", "mock" if app.stats.get("real") is not True else f"real ({app.stats.get('since')} 起)")
        return 0

    export_icon_files()
    stop_previous()
    write_pid()
    TrayApp(force_mock=args.mock).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
