#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heropen.update_check — 统一的新版本检查（本地缓存优先，永不阻塞主流程）

为什么需要这个模块
------------------
在 v1.9.0 之前，"有新版本"的提示只存在于两条几乎没人走的路径上：
``heropen panel --gui/--tui`` 与 ``heropen diagnose``。而绝大多数用户装完
heropen 之后，日常只通过 MCP 让 AI 助手调用 search_memory / prime_conversation，
从不敲 CLI —— 结果就是升级提示的实际触达率约等于零。

本模块把版本检查收敛成一份实现，并让高频路径（MCP 对话前上下文、本地面板）
也能读到结果。

设计原则（与 heropen「本地优先」承诺一致）
------------------------------------------
1. **读路径零联网、零延迟**：调用方（primer / 面板）只读本地缓存文件，
   永远不会在用户等待的路径上发起网络请求。
2. **写路径后台节流**：仅由 MCP server 启动时起一个 daemon 线程刷新，
   且 24 小时内最多查一次。
3. **只取版本号，不上报任何本机信息**：纯 GET 公开的 PyPI JSON 接口，
   不带查询参数、不带任何机器标识，不发送记忆内容或使用数据。
4. **失败静默**：网络不通、超时、解析失败一律降级为"不提示"，
   绝不影响 heropen 主功能。
5. **可完全关闭**：设置环境变量 ``HEROPEN_NO_UPDATE_CHECK=1`` 后，
   连后台线程都不会启动。

零第三方依赖：仅标准库 + heropen.core 的两个常量。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from heropen.core import HERO_PEN_DIR, __version__ as CURRENT_VERSION

# 缓存文件：只存"最近一次查到的最新版本号 + 查询时间"，不存任何用户数据
CACHE_PATH = os.path.join(HERO_PEN_DIR, ".update-check.json")

# 两次联网检查的最小间隔
CHECK_INTERVAL_HOURS = 24

# 公开的 PyPI 元数据接口（与 `heropen diagnose` 使用同一个，可靠且无需鉴权）
PYPI_JSON_URL = "https://pypi.org/pypi/heropen/json"

_REQUEST_TIMEOUT = 5


def is_disabled() -> bool:
    """用户是否已关闭版本检查（HEROPEN_NO_UPDATE_CHECK=1）。"""
    val = os.environ.get("HEROPEN_NO_UPDATE_CHECK", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def parse_ver(s: str) -> tuple:
    """把 '1.9.10' 解析成可比较的 (1, 9, 10)。非数字段落一律忽略。

    只用于同为正式版号之间的比较；解析失败返回空元组（后续比较即视为不更新）。
    """
    parts = []
    for chunk in str(s).strip().split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def read_cache() -> dict:
    """读本地缓存。永不联网。任何异常都返回空字典。"""
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cache(data: dict) -> None:
    try:
        os.makedirs(HERO_PEN_DIR, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _due_for_check(cache: dict) -> bool:
    """距上次检查是否已超过节流间隔。"""
    ts = cache.get("checked_at")
    if not ts:
        return True
    try:
        last = datetime.fromisoformat(ts)
    except Exception:
        return True
    return datetime.now() - last >= timedelta(hours=CHECK_INTERVAL_HOURS)


def fetch_latest_version() -> str | None:
    """联网取 PyPI 上的最新版本号。失败返回 None。

    纯 GET 公开元数据，不带任何本机信息。
    """
    try:
        import urllib.request

        req = urllib.request.Request(
            PYPI_JSON_URL,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        latest = payload.get("info", {}).get("version")
        return str(latest) if latest else None
    except Exception:
        return None


def refresh_cache(force: bool = False) -> dict:
    """刷新缓存（会联网）。受关闭开关与节流间隔约束。返回最新的缓存字典。

    这是唯一会发起网络请求的函数，只应由后台线程或显式的 CLI 命令调用。
    """
    if is_disabled():
        return {}

    cache = read_cache()
    if not force and not _due_for_check(cache):
        return cache

    latest = fetch_latest_version()
    now_iso = datetime.now().isoformat(timespec="seconds")
    if latest is None:
        # 查询失败也要记时间，避免每次启动都重试拖慢体验
        cache["checked_at"] = now_iso
        _write_cache(cache)
        return cache

    cache = {
        "latest": latest,
        "checked_at": now_iso,
        "seen_version": CURRENT_VERSION,
    }
    _write_cache(cache)
    return cache


def refresh_in_background() -> None:
    """起一个 daemon 线程刷新缓存，绝不阻塞调用方。

    供 MCP server 启动时调用：用户对话不会因为版本检查而变慢，
    检查结果留给下一次对话读取。
    """
    if is_disabled():
        return
    try:
        import threading

        t = threading.Thread(target=_safe_refresh, name="heropen-update-check", daemon=True)
        t.start()
    except Exception:
        pass


def _safe_refresh() -> None:
    try:
        refresh_cache()
    except Exception:
        pass


def has_update() -> bool:
    """本地缓存中是否存在比当前安装版更新的版本。永不联网。"""
    if is_disabled():
        return False
    latest = read_cache().get("latest")
    if not latest:
        return False
    cur_t, new_t = parse_ver(CURRENT_VERSION), parse_ver(latest)
    if not cur_t or not new_t:
        return False
    return new_t > cur_t


def latest_version() -> str | None:
    """缓存中记录的最新版本号（可能等于当前版本）。永不联网。"""
    return read_cache().get("latest")


def get_update_notice() -> str | None:
    """给 AI 助手看的一行升级提示；无新版或已关闭时返回 None。永不联网。"""
    if not has_update():
        return None
    return (
        f"- heropen 有新版本可用：{CURRENT_VERSION} → {latest_version()}"
        f"（升级命令：pip install --upgrade heropen；升级后请重启 AI 助手。"
        f"如需关闭此提示，设置环境变量 HEROPEN_NO_UPDATE_CHECK=1）"
    )
