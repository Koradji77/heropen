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

数据源：优先读真实库 ~/.heropen/*.db（entries 表聚合），读不到才回退内置 mock。
        传 --mock 可强制用 mock 数据（演示大数字用）。

菜单：
  heropen 状态行
  版本 vX.Y.Z           (点开发布日志)
  ├ 存储模式  ▸ 大块存储 / 一轮一存 / 自动识别   (radio, 按 agent 分别设置)
  ├ 打开工作台          (左键单击图标同效)
  ├ 打开主页
  ├ 检查更新          (pip install -U heropen)
  ├ 开机自启          (开关，默认开，写入 HKCU Run 键)
  ├ 重启
  └ 退出

依赖：pystray, pillow（由 heropen[tray] extra 提供）。
运行：heropen tray  (或 python -m heropen.tray [--mock])
自检：heropen tray --selftest   (只生成图标/打印 tooltip 并退出)

注意：存储模式（大块/一轮/自动）在 v2.0 中**仅为偏好记录**，实际存储策略
      尚未接入 heropen 内核（诗诗的 archive_state），设置后不影响真实落档行为。
      这是诚实的预览态：用户可设，但会被告知「暂未生效」。
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

# agent 模式黑名单：开发/测试残留库 + 旧机器残留（妮妮等），不列入角标管理。
# 四号机当前真实活跃库 = xiaokai(小凯) + _shared；其余空壳/残留均排除。
# 注意：本机只反映本地 ~/.heropen 的真实库；其他机器（如三号机的诗诗）的记忆
#       不在此体现，属正常范围（托盘角标按机器各自独立）。
AGENT_IGNORE = {"agent", "agent2", "agent3", "agent5", "auto",
                "install-agent2-gateway", "妮妮"}
DEFAULT_STATE = {
    "modes": {},             # 按 agent 分别设置： {agent名: mode}
    "default_mode": "auto",  # 新发现 agent 的默认模式
    "plan": "free",          # free / plus（当前写死免费版；Plus 激活检测未落地）
    "entries": 12846,        # mock 兜底值（真实库读不到时用）
    "days": 104,             # mock 兜底值
    "autostart": True,       # 默认开机自启
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


def _rel_time(date_str) -> str:
    """把 'YYYY-MM-DD' 转成「今天/昨天/N 天前/具体日期」相对描述。"""
    try:
        d = _dt.date.fromisoformat(str(date_str)[:10])
        diff = (_dt.date.today() - d).days
        if diff <= 0:
            return "今天"
        if diff == 1:
            return "昨天"
        if diff < 7:
            return f"{diff} 天前"
        return d.isoformat()
    except Exception:
        return str(date_str)[:10]


def per_agent_stats() -> list:
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
                    "last": _rel_time(last) if last else "—",
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


def build_workbench_html(stats: dict, agents: list, modes: dict, plan: str, version: str) -> str:
    """用真实数据生成工作台 HTML（替代写死假数的静态 demo 页）。"""
    plan_label = "免费版" if plan == "free" else "Plus"
    total = stats.get("entries", 0)
    days = stats.get("days", 0)
    since = stats.get("since") or "—"

    # 记忆健康：真实派生指标——近 7 天有新增记忆的 agent 占比（非写死百分比）
    total_agents = len(agents)
    active_7d = sum(1 for a in agents if any(a.get("bars") or []))
    active_ratio = int(round(active_7d / total_agents * 100)) if total_agents else 0
    if total_agents == 0:
        active_label = "未发现 agent 库"
    elif active_7d == total_agents:
        active_label = f"全部 {total_agents} 个 agent 近 7 天均有新增记忆"
    else:
        active_label = f"{active_7d}/{total_agents} 个 agent 近 7 天有新增记忆"

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
            f'<td class="mono">{_html.escape(LABEL.get(mode, mode))}</td>'
            f'<td class="mono">{a["count"]:,}</td>'
            f'<td class="mono">{_html.escape(a["last"])}</td>'
            f'<td><div class="bars">{bars_html}</div></td>'
            "</tr>"
        )
    rows_str = "\n".join(rows) if rows else '<tr><td colspan="5" class="mono">未发现 agent 库</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>heropen · 工作台</title>
<style>{WORKBENCH_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">heropen</div>
    <div class="sub">工作台</div>
    <div class="spacer"></div>
    <div class="pill live">运行中</div>
    <div class="pill">{plan_label}</div>
  </header>

  <h2>概览</h2>
  <div class="metrics">
    <div class="metric"><div class="k">已存记忆</div><div class="v">{total:,}<small>条</small></div></div>
    <div class="metric"><div class="k">已使用</div><div class="v">{days}<small>天</small></div></div>
    <div class="metric"><div class="k">自</div><div class="v" style="font-size:20px">{_html.escape(str(since))}</div></div>
  </div>

  <h2>存储模式（每个 agent 独立设置）</h2>
  <div class="plan-note">⚠ <b>规划项（v2.0 预览态）</b>：下方三种模式用于记录你的存储偏好，<b>当前仅保存设置、尚未接入 heropen 内核</b>，不会实际改变落档行为。v2.x 接入存储内核后将按设置生效。请勿依赖其当前行为。</div>
  <p class="hint">三种可选项：普通聊天用大块、讨论调教用一轮一存、平时交给自动。具体设置见下方 Agent 表。</p>
  <div class="modes">
    <div class="mode"><div class="n"><span class="dot"></span>大块存储</div><div class="d">普通聊天用。攒批落档，对话最快。</div><div class="tag">chunk</div></div>
    <div class="mode"><div class="n"><span class="dot"></span>一轮一存</div><div class="d">讨论、调教用。一句一存，重要的全留下。</div><div class="tag">sentence</div></div>
    <div class="mode"><div class="n"><span class="dot"></span>自动识别</div><div class="d">由 agent 判断该用哪种，无需手动切。</div><div class="tag">auto</div></div>
  </div>

  <h2>Agent（{len(agents)} 个 · 真实库）</h2>
  <p class="hint">模式列即每个 agent 当前的存储设置（规划项，暂未生效），在角标右键菜单「存储模式（按 agent 分别设置）」中调整——一人对多 agent，各自存法不同。条数与最近写入来自真实库。</p>
  <table>
    <thead><tr><th>Agent</th><th>模式</th><th>记忆条数</th><th>最近写入</th><th>近 7 天</th></tr></thead>
    <tbody>
{rows_str}
    </tbody>
  </table>

  <h2>Skill 收集与共享 <span class="plus-tag">PLUS</span></h2>
  <div class="skill">
    <span>把记住的东西变成能复用的 skill，跨 agent 共享。</span>
    <span class="btn">Plus 专属</span>
  </div>

  <h2>记忆健康</h2>
  <div class="health">
    <div class="row"><span class="lbl">{active_label}</span></div>
    <div class="msg">本地 {len(agents)} 个 agent · 最早记忆 {since} · 累计 {total:,} 条；本地存储无外部依赖，记忆压缩后仍可调用，不丢失之前的记忆。</div>
    <div class="bar"><span style="width:{active_ratio}%"></span></div>
  </div>

  <footer>
    <span>本地库 · 无外部依赖</span><span>·</span>
    <span>heropen v{_html.escape(version)}</span><span>·</span>
    <span>托盘角标 <code>heropen tray</code></span>
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
        """优先真实库，失败/强制则回退 mock。"""
        got = None if self.force_mock else read_real_stats()
        if got:
            self.stats = got
        else:
            self.stats = {
                "entries": int(self.state.get("entries", 0)),
                "days": int(self.state.get("days", 0)),
                "since": None,
                "real": False,
            }

    def _tooltip(self) -> str:
        """四段悬停提示：身份 / 存储方式 / 已存条数 / 已使用天数。"""
        n_agents = len(self.state.get("modes", {}))
        tip = "\n".join(
            [
                "heropen agent 记忆系统",
                f"存储方式：按 agent 分别设置（{n_agents} 个）",
                f"已存入：{self.stats.get('entries', 0):,} 条",
                f"已使用：{self.stats.get('days', 0)} 天",
            ]
        )
        return tip[:TIP_MAX]

    def _status_line(self, item=None) -> str:
        plan = "免费版" if self.state["plan"] == "free" else "Plus"
        n = len(self.state.get("modes", {}))
        return f"运行中 · {plan} · {n} 个 agent · 记忆 {self.stats.get('entries', 0):,} 条"

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
            # 诚实提示：仅保存偏好，未接入内核、暂未生效
            try:
                icon.notify(
                    f"已保存「{agent}」的存储偏好：{LABEL[mode]}"
                    f"（规划项，暂未生效，v2.x 接入内核后生效）",
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
            agents = per_agent_stats()
            html = build_workbench_html(
                self.stats,
                agents,
                self.state.get("modes", {}),
                self.state.get("plan", "free"),
                get_version(),
            )
            WORKBENCH.write_text(html, encoding="utf-8")
            webbrowser.open(WORKBENCH.as_uri())
        except Exception as e:
            try:
                (icon or self.icon).notify(f"工作台生成失败：{e}", "heropen")
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
                "升级完成，请重启角标生效" if ok else "升级失败，请手动执行 pip install -U heropen",
                "heropen",
            )
        except Exception as e:
            (icon or self.icon).notify(f"升级出错：{e}", "heropen")

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
        return pystray.Menu(
            # 注：Win32 对 enabled=False 的菜单项强制置灰（MFS_DISABLED）。
            # 这两行是「信息行」，要黑字就只能是 enabled=True + 空动作
            # （action=None 被 pystray 包成 lambda *_ : None，点击无副作用）。
            pystray.MenuItem("heropen", None),
            pystray.MenuItem(self._status_line, None),
            pystray.MenuItem(f"版本 v{get_version()}", self.show_version),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "存储模式（按 agent 分别设置）",
                pystray.Menu(
                    # 诚实声明：规划项，暂仅记录偏好
                    pystray.MenuItem("⚠ 规划项：暂仅记录偏好，未生效", None, enabled=False),
                    pystray.Menu.SEPARATOR,
                    *[
                        pystray.MenuItem(
                            ag,
                            pystray.Menu(
                                *[
                                    pystray.MenuItem(
                                        label,
                                        self.set_mode_for(ag, mode),
                                        checked=lambda item, a=ag, m=mode: self.state.get("modes", {}).get(a) == m,
                                        radio=True,
                                    )
                                    for mode, label in MODES
                                ]
                            ),
                        )
                        for ag in sorted(self.state.get("modes", {}).keys())
                    ]
                ),
            ),
            pystray.MenuItem("打开工作台", self.open_workbench, default=True),
            pystray.MenuItem("打开主页", self.open_home),
            pystray.MenuItem("检查更新", self.upgrade),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("开机自启", self.toggle_autostart,
                             checked=lambda item: self.state["autostart"]),
            pystray.MenuItem("重启", self.restart),
            pystray.MenuItem("退出", self.quit),
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


def main() -> int:
    ap = argparse.ArgumentParser(prog="heropen tray")
    ap.add_argument("--selftest", action="store_true", help="只生成图标/打印 tooltip 并退出")
    ap.add_argument("--mock", action="store_true", help="强制使用内置 mock 数据（演示大数字）")
    ap.add_argument("--stop", action="store_true", help="停掉正在运行的托盘实例并退出")
    args = ap.parse_args()

    if args.stop:
        return 0 if stop_previous() else 1

    if args.selftest:
        export_icon_files()
        app = TrayApp(force_mock=args.mock)
        img = make_icon_image(64)
        print("selftest ok; icon size:", img.size, "->", ICON_PNG.name, ICON_ICO.name)
        print("--- tooltip ---")
        print(app._tooltip())
        print("--- source ---", "mock" if app.stats.get("real") is not True else f"real ({app.stats.get('since')} 起)")
        return 0

    export_icon_files()
    stop_previous()
    write_pid()
    TrayApp(force_mock=args.mock).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
