#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heropen.panel_gen — 本地面板生成器（Plan-C / Agent 下钻）

读取 ~/.heropen/agent-config.json + 各 agent 的 .db（entries 表），
渲染成方案 C（金棕、agent 网格 → 私有记忆域下钻）的静态 HTML，
写到 ~/.heropen/panel.html，全程本地、不出本机、不登录。

免费(basic) 显示前 FREE_AGENT_LIMIT(=2) 个 agent；plus 显示前 6 个。
调用 build_and_open() 会生成文件并用默认浏览器打开（file:// 协议），
作为 ``heropen panel`` 命令的底层实现。

零第三方依赖：仅标准库 + heropen.core 的两个常量。
"""
from __future__ import annotations

import json
import os
import sqlite3
import webbrowser
from datetime import datetime

from heropen.core import HERO_PEN_DIR, FREE_AGENT_LIMIT

CONFIG = os.path.join(HERO_PEN_DIR, "agent-config.json")
OUT = os.path.join(HERO_PEN_DIR, "panel.html")
PLUS_AGENT_LIMIT = 6


def humanize(ts: str) -> str:
    """把 created_at 文本转「刚刚 / N分钟前 / N小时前 / 昨天 / N天前 / 日期」。"""
    if not ts:
        return "—"
    t = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            t = datetime.strptime(ts.strip()[:19], fmt)
            break
        except ValueError:
            continue
    if t is None:
        try:
            t = datetime.fromisoformat(ts.strip()[:19])
        except Exception:
            return ts[:10]
    delta = datetime.now() - t
    s = delta.total_seconds()
    if s < 60:
        return "刚刚"
    if s < 3600:
        return f"{int(s // 60)}分钟前"
    if s < 86400:
        return f"{int(s // 3600)}小时前"
    if s < 86400 * 2:
        return "昨天"
    if s < 86400 * 30:
        return f"{int(s // 86400)}天前"
    return t.strftime("%Y-%m-%d")


def read_agent(name: str, db_path: str, online=None) -> dict:
    """
    读单个 agent 库：记忆条数、最近活跃、最近若干条记忆。

    在线状态由 config 的 ``online`` 字段显式决定（True→online / 其他→offline），
    不按时间戳启发式推算——"在线"是产品/用户认知概念，不是可自动算的时间量。
    """
    status0 = "online" if online is True else "offline"
    info = {"id": name, "name": name, "mem": 0, "last": "—",
            "status": status0, "entries": []}
    p = db_path if os.path.isabs(db_path) else os.path.join(HERO_PEN_DIR, db_path)
    if not os.path.exists(p):
        # 退回按名字猜
        alt = os.path.join(HERO_PEN_DIR, f"{name}.db")
        if os.path.exists(alt):
            p = alt
        else:
            return info
    try:
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        cols = {r[1] for r in c.execute("PRAGMA table_info(entries)")}
        if not cols:
            c.close()
            return info
        info["mem"] = list(c.execute("SELECT COUNT(*) FROM entries"))[0][0]
        order = "created_at" if "created_at" in cols else "id"
        rows = list(c.execute(
            f"SELECT section, content, tags, {order} AS ts FROM entries "
            f"ORDER BY {order} DESC LIMIT 30"))
        if rows:
            info["last"] = humanize(rows[0]["ts"] or "")
        for r in rows:
            content = (r["content"] or "").strip()
            section = (r["section"] or "").strip()
            title = section or (content[:22] + ("…" if len(content) > 22 else ""))
            info["entries"].append({
                "title": title,
                "content": content,
                "tags": (r["tags"] or "").strip(),
                "date": humanize(r["ts"] or ""),
            })
        c.close()
    except Exception as e:
        info["error"] = str(e)
    return info


def build() -> str:
    """组装面板 HTML（含真实数据 payload）。"""
    if not os.path.exists(CONFIG):
        # 没配置文件时不崩，给个空壳提示
        payload = {"edition": "basic", "tierLabel": "免费版", "limit": FREE_AGENT_LIMIT,
                   "totalMem": 0, "agents": []}
        return TEMPLATE.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))

    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    edition = (cfg.get("edition") or "basic").lower()
    limit = PLUS_AGENT_LIMIT if edition in ("plus", "pro") else FREE_AGENT_LIMIT
    agents_cfg = [a for a in cfg.get("agents", []) if not str(a["name"]).startswith("_")]
    shown = agents_cfg[:limit]

    data = [read_agent(a["name"], a.get("db_path", ""), a.get("online")) for a in shown]
    total_mem = sum(a["mem"] for a in data)
    tier_label = "Plus" if limit == PLUS_AGENT_LIMIT else "免费版"

    payload = {
        "edition": edition,
        "tierLabel": tier_label,
        "limit": limit,
        "totalMem": total_mem,
        "agents": data,
    }
    return TEMPLATE.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))


def build_and_open() -> str:
    """
    生成 panel.html 并用默认浏览器打开（file:// 协议，数据不出本机）。
    返回生成的文件路径。无 GUI / 浏览器打开失败时打印降级提示，不抛异常。
    """
    html = build()
    os.makedirs(HERO_PEN_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"🖥️  已生成本地面板：{OUT}（{os.path.getsize(OUT)} bytes）")
    try:
        ok = webbrowser.open(f"file://{OUT}", new=2)
        if ok:
            print("✅ 已在浏览器新标签页打开面板。")
        else:
            print(f"⚠️  无法自动打开浏览器，请手动打开文件：{OUT}")
    except Exception:
        print(f"🌐  请在浏览器打开面板：{OUT}")
    return OUT


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>heropen · 本地面板</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,'Helvetica Neue','Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; background:#0F0D0B; color:#F0EBE3; min-height:100vh; -webkit-font-smoothing:antialiased; }
  .tier-tag { position:fixed; top:16px; right:16px; z-index:50; background:rgba(196,164,89,.15); border:1px solid #C4A459; color:#C4A459; font-size:12px; font-weight:700; padding:6px 12px; border-radius:20px; }
  .sidebar { position:fixed; left:0; top:0; bottom:0; width:280px; background:rgba(255,255,255,.02); border-right:1px solid #2D2823; padding:24px; overflow-y:auto; }
  .sidebar .logo { font-size:18px; font-weight:900; background:linear-gradient(135deg,#C4A459,#D4B56A); -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin-bottom:6px; }
  .sidebar .sub { font-size:11px; color:#5C544A; margin-bottom:20px; }
  .sidebar .stats { font-size:12px; color:#8A7F72; margin-bottom:20px; line-height:1.8; }
  .sidebar .stats span { color:#F5E6D3; font-weight:700; }
  .sidebar .nav-item { padding:10px 12px; border-radius:8px; font-size:13px; color:#8A7F72; margin-bottom:2px; }
  .sidebar .nav-item.active { background:rgba(255,255,255,.05); color:#F5E6D3; }
  .sidebar .hint { margin-top:24px; font-size:11px; color:#5C544A; line-height:1.7; border-top:1px solid #2D2823; padding-top:16px; }
  .main { margin-left:280px; padding:32px 40px; max-width:960px; }
  .view-title { font-size:20px; font-weight:700; margin-bottom:20px; }
  .view-title .count { font-size:14px; color:#8A7F72; font-weight:400; }
  .memories { display:flex; flex-direction:column; gap:8px; }
  .memory-card { background:rgba(255,255,255,.03); border:1px solid #2D2823; border-radius:12px; padding:16px 20px; transition:border-color .2s; }
  .memory-card:hover { border-color:#3D3528; }
  .memory-card .title { font-size:14px; font-weight:700; color:#F5E6D3; margin-bottom:4px; }
  .memory-card .content { font-size:13px; color:#8A7F72; line-height:1.6; white-space:pre-wrap; }
  .memory-card .meta { font-size:11px; margin-top:8px; display:flex; gap:12px; flex-wrap:wrap; }
  .memory-card .meta .tag { color:#C4A459; }
  .memory-card .meta .date { color:#5C544A; }
  .agent-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:10px; }
  .agent-card { background:rgba(255,255,255,.03); border:1px solid #2D2823; border-radius:12px; padding:16px 18px; cursor:pointer; transition:border-color .2s; display:flex; align-items:center; gap:12px; }
  .agent-card:hover { border-color:#C4A459; }
  .agent-avatar { width:40px; height:40px; border-radius:50%; background:linear-gradient(135deg,#D4B56A,#C4A459); color:#0F0D0B; font-weight:800; display:flex; align-items:center; justify-content:center; font-size:15px; flex-shrink:0; }
  .agent-info { flex:1; min-width:0; }
  .agent-name { font-size:14px; font-weight:700; color:#F5E6D3; display:flex; align-items:center; gap:8px; }
  .agent-role { font-size:12px; color:#8A7F72; margin-top:2px; }
  .agent-stat { font-size:12px; color:#8A7F72; text-align:right; }
  .agent-stat b { color:#F5E6D3; }
  .status-dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
  .status-dot.online { background:#7BA05B; box-shadow:0 0 6px #7BA05B; }
  .status-dot.idle { background:#ffb454; box-shadow:0 0 6px #ffb454; }
  .status-dot.offline { background:#5C544A; }
  .breadcrumb { font-size:13px; color:#8A7F72; margin-bottom:18px; }
  .breadcrumb .crumb { color:#C4A459; cursor:pointer; }
  .breadcrumb .crumb:hover { text-decoration:underline; }
  .empty { color:#5C544A; font-size:13px; padding:24px 0; }
  .locked { margin-top:14px; background:rgba(196,164,89,.06); border:1px dashed #3D3528; border-radius:12px; padding:16px 18px; font-size:12px; color:#8A7F72; }
  .locked b { color:#C4A459; }
  @media (max-width:768px){ .sidebar{display:none;} .main{margin-left:0;padding:20px;} }
</style>
</head>
<body>
<div class="tier-tag" id="tierTag"></div>
<div class="sidebar">
  <div class="logo">🖊️ heropen</div>
  <div class="sub">本地面板 · 数据不出本机</div>
  <div class="stats" id="stats"></div>
  <div class="nav-item active">🤖 Agent 状态</div>
  <div class="hint">只读视图。记忆由 agent 对话时自动写入，无需手动录入。<br>免费版显示 2 个 Agent，Plus 版显示 6 个。</div>
</div>
<div class="main">
  <div id="viewGrid">
    <div class="view-title">🤖 Agent 状态 <span class="count">点任一 Agent 看它存了什么</span></div>
    <div id="agentGrid" class="agent-grid"></div>
    <div id="locked"></div>
  </div>
  <div id="viewDrill" style="display:none">
    <div class="breadcrumb"><span class="crumb" onclick="goGrid()">🤖 Agent 状态</span> / <span id="crumbName"></span></div>
    <div id="drillHead" class="view-title"></div>
    <div id="drillMem" class="memories"></div>
  </div>
</div>
<script>
const DATA = /*__DATA__*/;
const STLABEL = {online:'在线', idle:'闲置', offline:'离线'};
function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
document.getElementById('tierTag').textContent = DATA.tierLabel + ' · ' + DATA.agents.length + ' 个 Agent';
document.getElementById('stats').innerHTML = '共 <span>'+DATA.totalMem+'</span> 条记忆<br><span>'+DATA.agents.length+'</span> 个 Agent（'+DATA.tierLabel+'）';
function renderGrid(){
  document.getElementById('agentGrid').innerHTML = DATA.agents.map((a,i)=>`
    <div class="agent-card" onclick="drill(${i})">
      <div class="agent-avatar">${esc(a.name[0]||'?')}</div>
      <div class="agent-info">
        <div class="agent-name">${esc(a.name)} <span class="status-dot ${a.status}"></span></div>
        <div class="agent-role">${STLABEL[a.status]||''} · 私有记忆域</div>
      </div>
      <div class="agent-stat"><b>${a.mem}</b> 条<br><span style="color:#5C544A;">${esc(a.last)}</span></div>
    </div>`).join('') || '<div class="empty">未发现本机 Agent。</div>';
  const lockCount = (DATA.tierLabel==='免费版') ? (6 - DATA.agents.length) : 0;
  document.getElementById('locked').innerHTML = lockCount>0
    ? `<div class="locked">升级 <b>Plus</b> 可显示最多 <b>6</b> 个 Agent，并开启共享记忆域（跨 Agent 互通）。当前免费版显示前 2 个。</div>` : '';
}
function drill(i){
  const a = DATA.agents[i];
  document.getElementById('viewGrid').style.display='none';
  document.getElementById('viewDrill').style.display='block';
  document.getElementById('crumbName').textContent = a.name;
  document.getElementById('drillHead').innerHTML = `${esc(a.name)} 的私有记忆域 <span class="count">(${a.mem} 条 · ${STLABEL[a.status]})</span>`;
  const mem = (a.entries||[]);
  document.getElementById('drillMem').innerHTML = mem.length ? mem.map(m=>`
    <div class="memory-card">
      <div class="title">${esc(m.title)}</div>
      <div class="content">${esc(m.content)}</div>
      <div class="meta">${m.tags?('<span class="tag">#'+esc(m.tags)+'</span>'):''}<span class="date">${esc(m.date)}</span></div>
    </div>`).join('') : '<div class="empty">这个 Agent 还没有记忆。</div>';
}
function goGrid(){
  document.getElementById('viewGrid').style.display='block';
  document.getElementById('viewDrill').style.display='none';
}
renderGrid();
</script>
</body>
</html>"""


if __name__ == "__main__":
    build_and_open()
