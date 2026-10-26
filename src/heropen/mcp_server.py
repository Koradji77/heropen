"""
heropen MCP Server — Expose heropen memory tools via MCP protocol.

Tools:
  search_memory, prime_conversation, add_memory, update_memory, list_memory, health

Run:
  heropen-mcp                  # stdio (for Hermes native MCP)
  heropen-mcp --http           # SSE (for HTTP MCP clients)
"""
from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path

from heropen.core import (
    AGENTS,
    HERO_PEN_DIR,
    get_bound_agent,
    resolve_agent,
    add_entry,
    build_signature,
    conn,
    flag_memory_hygiene,
    flush_pending,
    get_default_agent,
    search_vector,
    search_fts,
    store_memory,
    update_entry,
    startup_self_heal,
    resolve_conflicts,
)
from heropen.core import auto_save_turn as _core_auto_save_turn
from heropen.core import flush_turn_buffer as _core_flush_turn_buffer
from heropen import turn_storage
from heropen.core import session_checkpoint as _core_checkpoint
from heropen.core import session_recover as _core_recover
from heropen import __version__

# Record startup version for staleness detection
_STARTUP_VERSION = __version__

# Marker file path for pending setup
_MARKER_PATH = Path(HERO_PEN_DIR) / ".pending_setup"


def _install_call_observer(mcp) -> None:
    """只包装协议入口，不把启动、工具枚举或本地探测当作调用验证。

    握手单独记录为「已连接」（不声称用过工具）；tools/call 才记为最近调用。
    """
    try:
        import hashlib
        from functools import wraps
        from mcp.types import CallToolRequest, ListToolsRequest
        from heropen import connection_status

        lowlevel = mcp._mcp_server
        original = lowlevel.request_handlers[CallToolRequest]
        if getattr(original, "_heropen_call_observer", False):
            return
        digest = hashlib.sha256(
            Path(__file__).read_bytes() + Path(connection_status.__file__).read_bytes()
        ).hexdigest()[:16]
        build_marker = f"{__version__}:{connection_status.BUILD_MARKER}:{digest}"

        def _client_name():
            # request_context.session 是当前真实会话；client_params 只在
            # 握手完成后才有值，因此能证明这是一个已建立的真实连接。
            session = lowlevel.request_context.session
            info = getattr(session.client_params, "clientInfo", None)
            return getattr(info, "name", None)

        original_list = lowlevel.request_handlers.get(ListToolsRequest)
        if original_list is not None and not getattr(original_list, "_heropen_connect_observer", False):
            @wraps(original_list)
            async def observed_list(request):
                try:
                    # 客户端握手后枚举工具：记为「已连接」，但不代表用过工具。
                    connection_status.record_connect(
                        client_name=_client_name(),
                        bound_agent=get_bound_agent(),
                        data_directory=HERO_PEN_DIR,
                        build_marker=build_marker,
                    )
                except Exception:
                    pass
                return await original_list(request)

            observed_list._heropen_connect_observer = True
            lowlevel.request_handlers[ListToolsRequest] = observed_list

        @wraps(original)
        async def observed_call(request):
            try:
                connection_status.record_call(
                    client_name=_client_name(),
                    bound_agent=get_bound_agent(),
                    data_directory=HERO_PEN_DIR,
                    tool=request.params.name,
                    build_marker=build_marker,
                )
            except Exception:
                # 观测失败不能改变工具结果或吞掉工具自身的异常。
                pass
            return await original(request)

        observed_call._heropen_call_observer = True
        lowlevel.request_handlers[CallToolRequest] = observed_call
    except Exception:
        # SDK 缺少入口或元数据不可读时不阻断服务，也绝不生成假验证。
        pass


def _mcp_registered(hero_pen_dir: str) -> bool | None:
    """Whether a known client config declares this heropen server.

    Returns True when found, False when every known location was readable and
    none declared heropen, and None when the answer cannot be determined (for
    example an existing Hermes config while PyYAML is unavailable). Reporting
    None as False would produce a false "not configured" warning, so callers
    must treat None as unknown. Only the presence of a "heropen" entry is read
    here; no secret values are exposed.
    """
    home = Path.home()
    if (Path(hero_pen_dir) / "agent-config.json").exists():
        return True
    undetermined = False
    json_candidates = [
        home / ".workbuddy/mcp.json",
        home / ".zcode/cli/config.json",
        home / ".agents/mcp.json",
    ]
    for path in json_candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError:
            undetermined = True
            continue
        try:
            data = json.loads(text)
        except ValueError:
            undetermined = True
            continue
        if not isinstance(data, dict):
            undetermined = True
            continue
        for container in (data.get("mcpServers"), (data.get("mcp") or {}).get("servers")):
            if isinstance(container, dict) and "heropen" in container:
                return True
    hermes_config = home / ".hermes/config.yaml"
    if hermes_config.exists():
        try:
            import yaml
        except Exception:
            # Config exists but cannot be parsed without PyYAML: unknown.
            undetermined = True
        else:
            try:
                hermes = yaml.safe_load(hermes_config.read_text(encoding="utf-8"))
            except OSError:
                undetermined = True
            except Exception:
                undetermined = True
            else:
                if isinstance(hermes, dict) and "heropen" in (hermes.get("mcp_servers") or {}):
                    return True
    return None if undetermined else False


def _check_pending_setup(clear: bool = False) -> dict:
    """Report first-run setup state, based on evidence that setup actually ran.

    The .pending_setup marker alone is not proof of a fresh install: it is
    written once at install time and can outlive a completed setup on an
    in-place upgrade. A missing marker alongside a completed setup sentinel
    (.auto_setup_done) or existing memory databases means this is NOT a first
    run, so we must not tell an active user "you just installed heropen".
    """
    if clear and _MARKER_PATH.exists():
        _MARKER_PATH.unlink()
        return {"pending_setup": False}

    if _MARKER_PATH.exists():
        try:
            data = json.loads(_MARKER_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        # Evidence that the system has already been used on this machine.
        setup_done = (Path(HERO_PEN_DIR) / ".auto_setup_done").exists()
        used_before = any(Path(HERO_PEN_DIR).glob("*.db"))
        if setup_done or used_before:
            return {"pending_setup": False, "installed_at": data.get("installed_at", ""),
                    "version": data.get("version", ""), "setup_completed": True}
        return {
            "pending_setup": True,
            "installed_at": data.get("installed_at", ""),
            "version": data.get("version", ""),
            "agent_completed": data.get("agent_completed", False),
            "welcome_message": "I noticed you just installed heropen memory system! "
                               "Would you like me to set it up for you? It takes about 30 seconds.",
        }

    return {"pending_setup": False}


def _compute_time_gap(results: list) -> float | None:
    """Compute hours since the most recent entry's created_at. Returns None if unavailable."""
    if not results:
        return None
    try:
        from datetime import datetime
        last = max(r.get("created_at", "") or "" for r in results if r.get("created_at"))
        if not last:
            return None
        dt = datetime.fromisoformat(last)
        now = datetime.now()
        return round((now - dt).total_seconds() / 3600, 1)
    except Exception:
        return None


# ── Pre-conversation Priming（对话前准备阶段：时间感知）────────────
def _period_label(hour: int) -> str:
    """把 24h 映射为 8 段中文时段词，供 LLM 寒暄/行为有据。"""
    if 0 <= hour < 5:
        return "凌晨"
    if 5 <= hour < 8:
        return "早上"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    return "深夜"


def _gap_words(hours: float) -> str:
    """把间隔小时数转自然语言（贴合中文口语）。"""
    if hours < 0.05:
        return "刚刚"
    if hours < 1:
        return f"约 {round(hours * 60)} 分钟前"
    if hours < 24:
        return f"约 {round(hours)} 小时前"
    days = hours / 24
    if days < 2:
        return "昨天"
    if days < 7:
        return f"约 {round(days)} 天前"
    if days < 30:
        return f"约 {round(days / 7)} 周前"
    return f"约 {round(days / 30)} 个月前"


def build_conversation_primer(agent: str | None = None) -> str:
    """对话前准备阶段：构造自然语言时间上下文，注入 agent 对话。

    解决两类问题：
    1) 时间裸奔 → LLM 寒暄混乱（如晚上说"早上好"）。
    2) 相对时间词锚点错位 → 用户凌晨说"明天弄面板"指"醒来后的今天"，
       不能只按"距上次 N 小时"字面推算（需区分短休息连续 vs 跨睡眠新段）。

    返回纯文本区块，供 host agent 在 system/context 直接读入。
    零新增 schema（复用 entries.created_at），纯本地计算、不加依赖、零成本。
    """
    from datetime import datetime, date

    now = datetime.now()  # 本机本地时区，禁止 server UTC 裸跑
    agent = resolve_agent(agent) or get_default_agent()

    last_at = ""
    today_count = 0
    try:
        c = conn(agent)
        row = c.execute(
            "SELECT created_at FROM entries ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_at = row[0] if row else ""
        today = date.today().isoformat()
        today_count = c.execute(
            "SELECT COUNT(*) FROM entries WHERE entry_date = ?", (today,)
        ).fetchone()[0]
        c.close()
    except Exception:
        pass

    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[now.weekday()]
    period = _period_label(now.hour)

    lines = [
        "## 对话前时间上下文（heropen 自动生成，供你感知当前状态）",
        f"- 现在：{now.year}年{now.month}月{now.day}日 {weekday} {now.hour:02d}:{now.minute:02d}（用户本地时区）",
        f"- 当前时段：{period}",
    ]

    crossed_sleep = False
    different_day = False
    gap_h = None
    if last_at:
        try:
            last_dt = datetime.fromisoformat(last_at)
            delta = now - last_dt
            gap_h = delta.total_seconds() / 3600
            # 跨睡眠判定（精确）：日历日不同，或上次/本次分别落在本地睡眠窗(23:00-07:00)两侧。
            # 不靠"间隔>10h"裸判——同一天内上午9点→晚上10点的长间隔不应误判为跨睡眠。
            different_day = last_dt.date() != now.date()
            last_in_sleep = last_dt.hour >= 23 or last_dt.hour < 7
            now_in_sleep = now.hour >= 23 or now.hour < 7
            if different_day or (last_in_sleep != now_in_sleep):
                crossed_sleep = True
        except Exception:
            pass

    if last_at and gap_h is not None:
        last_str = last_at[:16].replace("T", " ")
        lines.append(f"- 距上次对话：{_gap_words(gap_h)}（上次：{last_str}）")
        if crossed_sleep and different_day:
            lines.append("- 会话连续性：你已休息过（跨睡眠周期），这是新的一天的工作段")
        elif crossed_sleep and not different_day:
            lines.append("- 会话连续性：你在睡眠后继续工作（同一日历日，但已跨过睡眠周期）")
        else:
            lines.append("- 会话连续性：与上次在同一清醒工作段内，连续未断")
    else:
        lines.append("- 距上次对话：首次对话（无历史记录）")

    lines.append(f"- 今日对话轮次：第 {today_count + 1} 次（按本机本地日期计）")
    lines.append(
        "- 时间锚点提示：用户口中的「明天/下周」等相对词，请以「今天」为基准日历日推算；"
        "若你刚跨过睡眠，用户说的「明天」可能指「今天醒来后的工作段」，勿机械按 N 小时后解读。"
    )

    # 新版本提示：只读本地缓存，零联网、零延迟。
    # 绝大多数用户从不敲 CLI，只经由 MCP 使用 heropen —— 这里是唯一能真正
    # 触达用户的路径，否则升级提示等于不存在。
    try:
        from heropen.update_check import get_update_notice

        notice = get_update_notice()
        if notice:
            lines.append(notice)
            lines.append("- 请在本次回复末尾用一句话把上面的升级提示转告用户，然后正常继续对话。")
    except Exception:
        pass

    return "\n".join(lines)


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit(
            "heropen requires mcp 1.x (FastMCP). "
            "mcp 2.x renamed FastMCP → MCPServer and is not yet supported.\n"
            "Fix: pip install 'mcp>=1.6,<2'\n"
            f"Import error: {exc}"
        ) from exc
    _PRIMING_INSTRUCTIONS = (
        "⏱ 时间感知强制约定（heropen）：\n"
        "在【每一次】对话/会话开场时，你必须先调用 prime_conversation 工具获取「对话前时间上下文」，"
        "把它作为首批上下文读入，然后再开始回应或与用户寒暄。\n"
        "原因：heropen 不会自动把时间注入你的 prompt；若不先调 prime_conversation，你将失去时间感"
        "（典型故障：深夜误说「早上好」、把用户凌晨说的「明天」机械当成字面次日）。\n"
        "替代路径：若你在开场调用了 search_memory（其返回已内置 time_context），同样满足本约定。\n"
        "\n"
        "💾 每轮存档：宿主应在每次回复后调用 auto_save_turn 一次，并提供本轮回复 content 及用户输入 user_content。"
        "自动模式按重要内容或累计信息量触发写入，最迟累计十轮写入；十轮一存累计十轮；步步为营每轮写入。"
        "每轮先在数据库中事务式接收，待存轮跨进程重启保留；不读取、迁移或删除旧 JSON 缓冲。"
        "conversation_id 是批次隔离，不是读权限隔离；同一轮重试必须保持 conversation_id 和 turn_id 稳定。"
        "省略 turn_id 会生成 ID，重试须复用返回的 turn_id；若宿主已自动调用，请勿重复调用。"
        "可用 turn_storage_status 只读查看状态，结束时用 flush_turns 显式保存指定会话尾批；空会话 ID 只刷新默认会话。"
        "显式 add_memory 立即保存，不受频率设置影响。"
    )
    mcp = FastMCP("heropen", instructions=_PRIMING_INSTRUCTIONS)

    # Advertise *our* version in the MCP `initialize` handshake. FastMCP exposes no
    # `version` parameter, so the low-level Server falls back to the `mcp` SDK's own
    # version (`pkg_version("mcp")`) and clients/directories would show e.g. 1.30.0.
    try:
        mcp._mcp_server.version = __version__
    except Exception:  # cosmetic only — never block startup over it
        pass

    # 绑定时只检查自身，不扫描或修复其他 Agent。
    _bound_agent = get_bound_agent()
    _all_agents = [_bound_agent] if _bound_agent is not None else list(AGENTS.keys())
    _recovered = 0
    for _agent in _all_agents:
        _r = startup_self_heal(_agent)
        if _r["status"] == "recovered":
            _recovered += 1
    if _recovered > 0:
        print(f"🔄 heropen 启动自愈: 恢复 {_recovered} 个 agent 的记忆库", file=sys.stderr, flush=True)

    @mcp.tool()
    def search_memory(
        query: str,
        limit: int = 5,
        agent: str | None = None,
        date_from: str = "",
        date_to: str = "",
    ) -> str:
        """搜索用户的长期记忆。在以下情况必须调用：
- 对话开始时，了解用户背景和历史
- 用户提到人名、项目名、偏好等关键词时
- 用户询问"还记得吗"或类似问题时
不要等用户要求才搜索。主动搜索能让你更懂用户。三层回退：向量 → 全文 → 模糊匹配。

        Args:
            query: Keywords to search for. Use the user's own words or inferred topics.
            limit: Max results (1-20, default 5).
            agent: Which agent's memory to search (default: auto).
            date_from: Only return entries on or after this date (YYYY-MM-DD).
            date_to: Only return entries on or before this date (YYYY-MM-DD).
        """
        agent = resolve_agent(agent)
        limit = min(limit, 20)
        results = search_vector(query, limit, agent)
        method = "vector"
        if not results:
            keywords = [k.strip() for k in query.split() if k.strip()]
            results = search_fts(keywords or [query], limit, agent)
            method = "fts"
        if not results:
            import sqlite3
            c = conn(agent)
            keywords = [k.strip() for k in query.split() if k.strip()]
            if not keywords:
                keywords = [query]
            conditions = []
            params = []
            for kw in keywords:
                conditions.append("(section LIKE ? OR content LIKE ? OR tags LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
            where = " OR ".join(conditions)
            sql = f"SELECT id, entry_date, section, content, tags, source, agent, created_at FROM entries WHERE {where} ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = [dict(r) for r in c.execute(sql, params).fetchall()]
            c.close()
            results = rows
            method = "like"

        if results is None:
            results = []

        if date_from or date_to:
            filtered = []
            for r in results:
                ed = r.get("entry_date", "")
                if date_from and ed < date_from:
                    continue
                if date_to and ed > date_to:
                    continue
                filtered.append(r)
            results = filtered

        for r in results:
            r.pop("embedding", None)
            if "similarity" in r and r["similarity"] is not None:
                r["score"] = round(r["similarity"], 4)
                r["score_label"] = (
                    "高相关" if r["score"] >= 0.7
                    else ("中相关" if r["score"] >= 0.4 else "低相关")
                )
            else:
                r["score"] = None
                r["score_label"] = "关键词匹配"

        # Append entries from _shared.db for cross-agent knowledge
        try:
            from heropen.core import search_shared
            shared_results = (
                search_shared(agent or get_default_agent(), limit)
                if get_bound_agent() is None else []
            )
            existing_contents = {r.get("content", "")[:80] for r in results if r.get("content")}
            for sr in shared_results:
                if sr.get("content", "")[:80] not in existing_contents:
                    results.append(sr)
        except Exception:
            pass

        # C4 记忆卫生（只标不删）：给结果附加 hygiene_flags 提示，绝不删除原数据
        try:
            flag_memory_hygiene(results)
        except Exception:
            pass

        # C3 确定性矛盾消解：标注被压制的冲突条目，附裁决理由（只标不删）
        conflict_resolution = []
        try:
            _cr = resolve_conflicts(results, agent)
            for r in results:
                rid = r.get("id")
                if rid in _cr["suppressed_map"]:
                    r["conflict_suppressed"] = True
                    r["conflict_reason"] = _cr["suppressed_map"][rid]
            conflict_resolution = _cr["clusters"]
        except Exception:
            pass

        return json.dumps(
            {"method": method, "count": len(results), "results": results[:limit], "time_gap_hours": _compute_time_gap(results), "time_context": build_conversation_primer(agent), "conflict_resolution": conflict_resolution},
            ensure_ascii=False,
        )

    @mcp.tool()
    def prime_conversation(agent: str | None = None) -> str:
        """对话开始前必须调用：获取当前时间上下文（heropen 自动生成）。

        返回自然语言时间区块，包含：当前本地时间、时段词、距上次对话间隔、
        是否跨睡眠周期（区分"工作-短休息-工作"与"工作-长休息-继续工作"）、
        今日对话轮次、以及相对时间词锚点提示。

        把这段文本作为上下文读入，你的寒暄与对"明天/下周"等相对时间的理解才有据可依，
        不会出现"晚上说早上好"或把用户凌晨说的"明天"误判为字面次日。

        返回内容开头会带一行「开场签名」（品牌 + 状态），形如：
        heropen 已就绪 · 本地记忆 187 条 · 距上次对话约 3 小时 · agent=xiaokai

        Args:
            agent: Which agent's context to prime (default: auto).
        """
        agent = resolve_agent(agent) or get_default_agent()
        # 仅处理旧版本 chunk 遗留缓冲；新引擎待存轮跨开场保留，
        # 由存储策略或显式 flush_turns 写入，开场不重置计数。
        try:
            flush_pending(agent)
        except Exception:
            pass
        # 签名贴在时间上下文开头：开场自动亮相的一行状态
        return build_signature(agent) + "\n\n" + build_conversation_primer(agent)

    @mcp.tool()
    def add_memory(
        section: str,
        content: str,
        tags: str = "",
        agent: str | None = None,
        entry_date: str = "",
    ) -> str:
        """保存信息到用户的长期记忆。在以下情况必须调用：
- 用户分享了新的偏好、习惯、约定、项目信息
- 对话中产生了需要后续跟进的事项
- 对话即将结束时，保存本次产生的关键信息
优先主动保存，不要等用户说"记住这个"。content 上限 5000 字。

        Args:
            section: Category/topic for this memory (e.g. '用户偏好', '项目状态', '对话记录').
            content: The information to remember (max 5000 chars). Write it clearly so future you can understand it.
            tags: Comma-separated keywords for better search.
            agent: Which agent's memory to write to (default: auto).
            entry_date: Date for this entry (YYYY-MM-DD, default: today).
        """
        from datetime import date
        ed = entry_date if entry_date else date.today().isoformat()
        res = store_memory(
            agent=agent,
            entry_date=ed,
            content=content,
            section=section,
            tags=tags,
            source="mcp",
        )
        if res.get("written"):
            if res.get("id"):
                return json.dumps({"ok": True, "id": res["id"], "section": section,
                                   "mode": res.get("mode"), "reason": res.get("reason")},
                                  ensure_ascii=False)
            return json.dumps({"ok": False, "error": "Write failed"}, ensure_ascii=False)
        # chunk：已攒进缓冲区（攒够阈值会自动落库，开场也会补落）
        return json.dumps({"ok": True, "buffered": res.get("buffered"),
                           "flushed": res.get("flushed"), "section": section,
                           "mode": "chunk", "reason": res.get("reason")}, ensure_ascii=False)

    @mcp.tool()
    def auto_save_turn(
        content: str,
        agent: str | None = None,
        user_content: str = "",
        section: str = "",
        tags: str = "",
        conversation_id: str = "",
        turn_id: str = "",
    ) -> str:
        """宿主在每轮 assistant 回复完成后调用一次；事务式接收完整话轮。
        自动模式按重要内容或累计信息量触发写入，最迟十轮；十轮模式累计十轮；每轮模式立即保存。
        只有实际调用才会计数，不会自行读取对话。待存轮持久化于数据库，不触碰旧 JSON。
        source 固定 auto_turn；不自动追加标签。请勿用本工具替代断点（session_checkpoint）。

        Args:
            content: 本轮 assistant 回复原文；与用户输入合计超过 1,000,000 字符会拒绝，不截断。
            agent: 哪个 agent 的记忆（默认自动）。
            user_content: 可选，本轮用户输入原文。
            section: 原始分类（默认空字符串）。
            tags: 原始标签（默认空字符串，原样保存）。
            conversation_id: 批次隔离标识，不是读权限隔离；空字符串为独立默认会话。
            turn_id: 当前会话内稳定的话轮标识，重试必须复用且保持 conversation_id 不变。
                省略时生成 ID，须复用返回的 turn_id 重试；无 ID 重试无法幂等。
                同 ID 同请求返回 duplicate，同 ID 不同正文或元数据会拒绝。

        Returns (JSON):
            saved / parts (int): 本次新建 entries 数；parts 不是列表。
            flushed (int): 本次关联到 entries 的轮数，不是条目数。
            received_turns (int): 当前会话累计接收轮数，重复请求不增加。
            pending_turns (int): 当前会话待存轮数。
            buffered (bool): 当前轮是否仍待存，不是待存轮数。
            duplicate (bool): 是否为重复话轮；entry_id 为当前轮关联条目或 null。
            entry_ids (list[int]): 本次新建条目 ID。
            conversation_id / turn_id (str): 会话与话轮标识。
            generated_id (bool) / idempotency_warning (str): ID 生成情况及重试提示。
            configured_mode / mode / reason (str): 配置模式、本轮判定模式及原因。
            last_received_at (str | null): 当前会话最近接收时间。
            source 固定 auto_turn，embedding / graph 为 deferred。
            助手回复为空时 skipped=true，不计为完整轮。
        """
        res = _core_auto_save_turn(
            agent=agent,
            content=content,
            user_content=user_content,
            section=section,
            tags=tags,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        return json.dumps(res, ensure_ascii=False)

    @mcp.tool()
    def ingest_local_turns(source: str = "all", apply: bool = False,
                           include_subagents: bool = False) -> str:
        """主动读取本机客户端会话记录，把真实完整话轮补交给轮次存储。

        适用场景：宿主没有在每轮回复后调用 auto_save_turn，导致自动累计空转
        （received_turns 恒为 0）。本工具不需要宿主配合，直接读取客户端已写在
        磁盘上的会话记录；不修改客户端配置、不安装钩子、不改动客户端数据。

        只使用记录里原生存在的会话与轮次标识作为幂等键，因此可安全重复调用，
        不会重复计数；用户输入与助手回复均取自记录原文，不会生成或补造内容。
        注入块（system-reminder、task-notification、浏览器上下文等）不是用户
        发言，已剔除；只有除注入块外没有真实用户输入、或没有助手回复的轮次会
        被跳过，绝不伪造。

        Args:
            source: "all"（默认，读取所有已知客户端）、"zcode" 或 "workbuddy"。
            apply: 默认 False 只统计不写入（可先看将要提交多少轮）；True 才真正提交。
            include_subagents: 是否一并读取子代理会话（默认否，子代理对话通常非人类主会话）。

        Returns (JSON):
            considered / submitted / skipped_incomplete (int): 候选 / 提交 / 跳过轮数。
            duplicate / saved / buffered (int): 重复未计数 / 本次新建条目 / 仍待存轮数。
            per_source (dict): 按客户端分别返回上述计数，便于定位哪一端读到了多少。
            conversations 在单选来源时返回按会话的计数，用于核对第 1—9 轮递增、
                第 10 轮存档。
            source / apply (str | bool): 数据来源与实际是否写入。
        """
        from heropen import turn_ingest

        if source not in ("all", "zcode", "workbuddy"):
            return json.dumps({"error": f"暂不支持的数据来源：{source}"}, ensure_ascii=False)
        if source == "all":
            readers = (("zcode", turn_ingest.ingest_zcode),
                       ("workbuddy", turn_ingest.ingest_workbuddy))
        elif source == "zcode":
            readers = (("zcode", turn_ingest.ingest_zcode),)
        else:
            readers = (("workbuddy", turn_ingest.ingest_workbuddy),)
        totals = {"considered": 0, "submitted": 0, "skipped_incomplete": 0,
                  "skipped_invalid": 0, "already_stored": 0, "deferred": 0,
                  "duplicate": 0, "saved": 0, "buffered": 0, "failed": 0,
                  "conflicts": 0}
        per_source = {}
        errors = []
        for name, reader in readers:
            try:
                summary = reader(apply=bool(apply),
                                 include_subagents=bool(include_subagents))
            except Exception as exc:  # noqa: BLE001 - one source must not block others
                per_source[name] = {"error": f"{type(exc).__name__}: {exc}"}
                errors.append({"source": name, "kind": "source",
                               "reason": f"{type(exc).__name__}: {exc}"})
                continue
            for item in summary.get("errors") or []:
                errors.append({"source": name, **item})
            per_source[name] = {key: summary.get(key) for key in
                                ("considered", "submitted", "skipped_incomplete",
                                 "skipped_invalid", "already_stored", "deferred",
                                 "duplicate", "saved", "buffered", "failed",
                                 "conflicts", "source")}
            for key in totals:
                totals[key] += int(summary.get(key) or 0)
        # A source can return normally yet fail every submission or reject
        # turns; that must not be reported as success.
        failed_sources = [n for n, v in per_source.items() if v.get("error")]
        problem_sources = [
            n for n, v in per_source.items()
            if not v.get("error") and (
                int(v.get("failed") or 0) > 0 or int(v.get("skipped_invalid") or 0) > 0)
        ]
        if failed_sources and len(failed_sources) == len(readers):
            status = "failed"
        elif failed_sources or problem_sources:
            status = ("failed" if sum(int(v.get("submitted") or 0)
                                      for v in per_source.values()) == 0
                      and problem_sources else "partial")
        else:
            status = "ok"
        return json.dumps({"ok": status == "ok", "status": status,
                           "apply": bool(apply), "source": source,
                           "failed_sources": failed_sources,
                           "problem_sources": problem_sources,
                           "errors": errors,
                           "per_source": per_source, **totals},
                          ensure_ascii=False)

    @mcp.tool()
    def turn_storage_status(agent: str | None = None) -> str:
        """只读查询 agent 的事务式话轮存储状态；不创建数据库、不初始化 schema、不刷新待存轮。

        返回 JSON：database_exists (bool)、received_turns / pending_turns / saved_turns (int)
        为该 agent 全部会话的累计接收 / 待存 / 已存轮数，buffered (bool) 表示是否有待存轮。
        conversations 按 conversation_id 返回各会话上述计数和 last_received_at。
        conversation_id 仅用于批次隔离，不是读权限隔离；此工具可查看该 agent 的全部会话计数。
        不读取、迁移或删除旧 JSON 缓冲。
        """
        agent = resolve_agent(agent)
        return json.dumps(turn_storage.status(agent=agent), ensure_ascii=False)

    @mcp.tool()
    def flush_turns(agent: str | None = None, conversation_id: str = "") -> str:
        """显式保存指定会话的全部待存尾批，即使不足十轮；不修改其他会话或旧 JSON。

        Args:
            agent: 哪个 agent 的记忆（默认自动）。
            conversation_id: 批次隔离标识，不是读权限隔离；空字符串仅刷新默认会话，非全部会话。

        Returns (JSON):
            saved / parts (int): 本次新建 entries 数；flushed (int): 本次写入关联的轮数。
            received_turns / pending_turns (int): 当前会话累计接收 / 剩余待存轮数。
            buffered (bool): 当前会话是否仍有待存轮；entry_ids (list[int]): 本次新建条目 ID。
            conversation_id (str): 本次刷新会话；其余状态字段由事务引擎原样返回。
        """
        return json.dumps(
            _core_flush_turn_buffer(agent=agent, conversation_id=conversation_id),
            ensure_ascii=False,
        )

    @mcp.tool()
    def update_memory(
        entry_id: int,
        agent: str | None = None,
        section: str | None = None,
        content: str | None = None,
        tags: str | None = None,
        entry_date: str | None = None,
    ) -> str:
        """Update an existing memory entry. Only provided fields are changed."""
        fields = {}
        if section is not None:
            fields["section"] = section
        if content is not None:
            fields["content"] = content[:5000]
        if tags is not None:
            fields["tags"] = tags
        if entry_date is not None:
            fields["entry_date"] = entry_date

        result = update_entry(entry_id, agent, **fields)
        if result:
            result.pop("embedding", None)
            return json.dumps({"ok": True, "entry": result}, ensure_ascii=False)
        return json.dumps(
            {"ok": False, "error": f"Entry {entry_id} not found or no fields to update"},
            ensure_ascii=False,
        )

    @mcp.tool()
    def list_memory(limit: int = 10, agent: str | None = None) -> str:
        """列出用户的记忆列表。用于概览已有记忆、检查冗余、了解记忆总量。

        Args:
            limit: How many recent entries to show (max 50, default 10).
            agent: Which agent's memories to list.
        """
        c = conn(agent)
        total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT id, entry_date, section, content, tags, source, created_at FROM entries ORDER BY id DESC LIMIT ?",
                (min(limit, 50),),
            ).fetchall()
        ]
        c.close()
        for r in rows:
            r.pop("embedding", None)
        return json.dumps({"total_count": total, "count": len(rows), "results": rows}, ensure_ascii=False)

    @mcp.tool()
    def health(clear_pending: bool = False) -> str:
        """Check heropen system health and connection status. Call this when you start up to verify everything is working. If pending_setup is true, it means heropen was just installed — show the welcome_message to the user and ask if they'd like to complete the setup.
        
        Args:
            clear_pending: Set to true after completing initial setup to clear the pending marker.
        """
        bound_agent = get_bound_agent()
        # Check pending setup marker
        pending = _check_pending_setup(clear=clear_pending)

        stats = {}
        db_connected = True
        mcp_config_found = True
        issues = []
        total_memory = 0
        last_memory_at = ""

        agents = [bound_agent] if bound_agent is not None else list(AGENTS.keys())
        for agent in agents:
            try:
                c = conn(agent)
                total = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
                last = c.execute(
                    "SELECT created_at FROM entries ORDER BY id DESC LIMIT 1"
                ).fetchone()
                recent = c.execute(
                    "SELECT section FROM entries WHERE section != '' ORDER BY id DESC LIMIT 3"
                ).fetchall()
                c.close()
                last_memory = last[0] if last else ""
                if last_memory and last_memory > last_memory_at:
                    last_memory_at = last_memory
                total_memory += total
                recent_topics = [r["section"] for r in recent if r["section"]]
                stats[agent] = {"entries": total, "recent_topics": recent_topics}
            except Exception:
                stats[agent] = {"entries": 0, "recent_topics": []}
                db_connected = False
                issues.append(f"database connection failed for {agent}")

        # Check MCP config. A client may register this server through its own
        # config file rather than the legacy agent-config.json in HERO_PEN_DIR.
        # Only claim "not found" when we could actually read every known
        # location; an unreadable or unparseable config is reported as unknown.
        registered = _mcp_registered(HERO_PEN_DIR)
        if registered is None:
            mcp_config_found = False
            issues.append("client configuration could not be fully read to confirm registration")
        elif not registered:
            mcp_config_found = False
            issues.append("no client configuration registers heropen")

        version_stale = None
        import importlib.metadata as _ilm
        try:
            _current = _ilm.version("heropen")
            if _current != _STARTUP_VERSION:
                version_stale = {"startup": _STARTUP_VERSION, "current": _current}
        except Exception:
            pass

        result = {
            "status": "ok" if (db_connected and mcp_config_found) else "degraded",
            "server": "heropen-mcp",
            "version": __version__,
            "version_stale": version_stale,
            "agents": stats,
            "bound_agent": bound_agent,
            "data_directory": os.path.abspath(HERO_PEN_DIR),
            "db_connected": db_connected,
            "mcp_config_found": mcp_config_found,
            "memory_count": total_memory,
            "last_memory_at": last_memory_at,
        }
        try:
            from heropen.core import get_embedding_status
            result["embedding"] = get_embedding_status()
        except Exception:
            pass

        # Merge in pending setup fields
        result.update(pending)
        if issues:
            result["issues"] = issues
            if pending.get("pending_setup") and not result.get("welcome_message"):
                result["welcome_message"] = (
                    "heropen is installed but has some configuration issues. "
                    "Would you like me to help diagnose them?"
                )

        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def session_checkpoint(agent: str | None = None, context_summary: str = "",
                           active_task: str = "", key_decisions: str = "",
                           tags: str = "") -> str:
        """Save a session checkpoint. Call periodically during long conversations
        so the agent can recover context after compression or restart.
        
        Args:
            agent: Which agent's memory
            context_summary: Summary of current conversation context
            active_task: What task is currently being worked on
            key_decisions: Comma-separated key decisions made so far
            tags: Extra comma-separated tags
        """
        decisions_list = [d.strip() for d in key_decisions.split(",") if d.strip()] if key_decisions else []
        entry_id = _core_checkpoint(
            agent=agent,
            context_summary=context_summary,
            active_task=active_task,
            key_decisions=decisions_list,
            tags=tags
        )
        if entry_id:
            return json.dumps({"ok": True, "id": entry_id}, ensure_ascii=False)
        return json.dumps({"ok": False, "error": "Checkpoint save failed"}, ensure_ascii=False)

    @mcp.tool()
    def session_recover(agent: str | None = None, limit: int = 1) -> str:
        """Recover the most recent session checkpoint(s). Use after context
        compression or agent restart to pick up where you left off.
        
        Args:
            agent: Which agent's memory
            limit: Number of recent checkpoints (default 1, max 5)
        """
        limit = min(limit, 5)
        results = _core_recover(agent=agent, limit=limit)
        for r in results:
            r.pop("content", None)
        return json.dumps({
            "count": len(results),
            "checkpoints": results
        }, ensure_ascii=False)

    # HTTP settings (only used with --http)
    # Default bind is loopback; use --host 0.0.0.0 only when intentionally exposing.
    mcp.settings.host = os.environ.get("HEROPEN_MCP_HOST", "127.0.0.1")
    mcp.settings.port = int(os.environ.get("HEROPEN_MCP_PORT", "8090"))
    # DNS rebinding protection is safe to leave on for non-loopback binds.
    # Keep it off only for local stdio/SSE-on-localhost setups that some clients need.
    host = mcp.settings.host
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    mcp.settings.transport_security.enable_dns_rebinding_protection = not is_loopback

    _install_call_observer(mcp)
    return mcp


def _hide_console_window() -> None:
    """Hide this process's console window on Windows (best effort).

    ``heropen-mcp --http`` is a long-running background service (often started
    at logon via a scheduled task). It must never leave a visible console
    window. Any failure is ignored — this is cosmetic only.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def main(argv: list[str] | None = None):
    """CLI entry for heropen-mcp and `heropen mcp`.

    When invoked as ``heropen mcp ...``, the literal ``mcp`` remains in
    ``sys.argv``; strip it so argparse only sees real flags.
    """
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "mcp":
        argv = argv[1:]

    parser = argparse.ArgumentParser(description="heropen MCP Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run as HTTP/SSE server (default host 127.0.0.1:8090)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP bind host (default: 127.0.0.1; set 0.0.0.0 to expose LAN)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP bind port (default: 8090)",
    )
    args = parser.parse_args(argv)

    if args.host:
        os.environ["HEROPEN_MCP_HOST"] = args.host
    if args.port:
        os.environ["HEROPEN_MCP_PORT"] = str(args.port)

    mcp_server = create_mcp_server()

    # 后台刷新新版本缓存（daemon 线程、24h 节流、失败静默、可用
    # HEROPEN_NO_UPDATE_CHECK=1 关闭）。不阻塞 server 启动，也不拖慢任何一次对话：
    # 检查结果写入本地缓存，供下一次 prime_conversation / 面板读取。
    try:
        from heropen.update_check import refresh_in_background

        refresh_in_background()
    except Exception:
        pass

    if args.http:
        # 常驻后台服务：不允许出现可见控制台窗口（兜底，防旧任务/手动启动露窗）
        _hide_console_window()
        host = os.environ.get("HEROPEN_MCP_HOST", "127.0.0.1")
        port = os.environ.get("HEROPEN_MCP_PORT", "8090")
        print(
            f"heropen MCP Server (SSE) listening on http://{host}:{port}",
            file=sys.stderr,
            flush=True,
        )
        mcp_server.run(transport="sse")
    else:
        mcp_server.run(transport="stdio")


if __name__ == "__main__":
    main()
