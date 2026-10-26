"""实际 MCP 客户端的连接与调用观测；不是在线心跳，也不是客户端密码学认证。

仅保存握手名称和调用元数据，不接收工具参数或结果。读取不依赖 core，
不会创建目录、修改权限或刷新时间；clientInfo 是客户端自报身份。

连接是否成立由「该客户端会话进程是否还活着」判断，而不是由最近活动时间
判断：客户端空闲时连接依然存在，用时间窗口表示连接会导致“放一会儿就显示
断开”的错误。时间窗口只用于标记“最近调用过”。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import secrets
import stat
import subprocess

FRESH_SECONDS = 15 * 60
SERVER = "heropen-mcp"
BUILD_MARKER = "mcp-call-observation-v1"
TOOLS = frozenset({
    "health", "search_memory", "prime_conversation", "list_memory",
    "auto_save_turn", "add_memory", "update_memory", "turn_storage_status",
    "flush_turns", "session_checkpoint", "session_recover",
})
_CLIENT_AGENTS = {
    "workbuddy": "workbuddy", "codebuddy": "workbuddy",
    "zcode": "zcode", "hermes": "hermes",
}
_FIELDS = (
    "schema_version", "timestamp", "client_name", "bound_agent", "server",
    "pid", "build_marker", "tool", "status",
)
# Handshake observation is stored separately so the proven tool-call record
# schema stays byte-compatible with existing readers and tests.
_CONNECT_FIELDS = (
    "schema_version", "timestamp", "client_name", "bound_agent", "server",
    "pid", "build_marker", "kind", "status", "session_pids",
)
CONNECT_FIELDS_V1 = tuple(f for f in _CONNECT_FIELDS if f != "session_pids")
CONNECT_SCHEMA_VERSION = 2
# Clients such as ZCode pool connections: short-lived sessions handshake and
# exit while the client keeps working on others. Keep the observed session pids
# so liveness is "any session still alive", not "the last handshake is alive".
MAX_SESSION_PIDS = 8
CONNECT_SUFFIX = ".connect.json"
# Stronger than a self-reported name: walk the session process's ancestry and
# see which application actually launched it. A client cannot forge this by
# declaring a different clientInfo name.
_ANCESTRY_MARKERS = (
    ("zcode", ("/zcode.app/", "/zcode")),
    ("workbuddy", ("/workbuddy.app/", "/workbuddy")),
    ("hermes", (".hermes/", "/hermes-agent", "hermes-agent")),
)
MAX_ANCESTRY_DEPTH = 10


def _merge_session_pids(existing, current_pid) -> list[int]:
    """Accumulate observed session pids, newest first, bounded.

    ZCode pools MCP connections, so several sessions may handshake over time
    while only some remain alive. Keeping the recent pids lets liveness be
    "any observed session still runs" instead of "the last one still runs".
    """
    pids = []
    if isinstance(existing, list):
        pids = [p for p in existing if isinstance(p, int) and p > 1]
    if isinstance(current_pid, int) and current_pid > 1:
        pids = [current_pid] + [p for p in pids if p != current_pid]
    return pids[:MAX_SESSION_PIDS]


def _safe_agent(agent: str) -> bool:
    return (isinstance(agent, str) and len(agent) <= 128
            and re.fullmatch(r"[\w.-]+", agent) is not None
            and agent not in (".", ".."))


def identify_client(client_name: str | None) -> str | None:
    """仅接受明确支持的名称，大小写不敏感；不做子串或模糊匹配。"""
    if not isinstance(client_name, str):
        return None
    return _CLIENT_AGENTS.get(client_name.casefold())


def _libc():
    """Minimal libSystem binding for macOS process introspection."""
    global _LIBC
    if _LIBC is None:
        import ctypes
        import ctypes.util
        try:
            _LIBC = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        except OSError:
            _LIBC = False
    return _LIBC or None


_LIBC = None


# ---------------------------------------------------------------------------
# Windows 分支（2026-10-26 回流时新增）：proc_pidinfo / proc_pidpath 是 macOS
# 专属 libc 调用，Windows 上改用 kernel32 的 Toolhelp 快照 / 进程镜像名查询，
# 不引入新依赖（纯 ctypes）。语义与 mac 版对齐：失败返回 None。
# ---------------------------------------------------------------------------

def _win_parent_pid(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE:
        return None
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not k32.Process32First(snap, ctypes.byref(pe)):
            return None
        while True:
            if pe.th32ProcessID == pid:
                ppid = int(pe.th32ParentProcessID)
                return ppid if ppid > 0 else None
            if not k32.Process32Next(snap, ctypes.byref(pe)):
                return None
    except Exception:
        return None
    finally:
        k32.CloseHandle(snap)


def _win_executable_path(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(4096)
        size = wintypes.DWORD(4096)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


def _parent_pid(pid: int) -> int | None:
    import sys
    if sys.platform == "win32":
        return _win_parent_pid(pid)
    libc = _libc()
    if libc is None:
        return None
    import ctypes
    buf = ctypes.create_string_buffer(512)
    try:
        size = libc.proc_pidinfo(pid, 3, ctypes.c_uint64(0), buf, 512)
    except Exception:
        return None
    if size <= 0:
        return None
    ppid = int.from_bytes(buf.raw[16:20], "little")
    return ppid if ppid > 1 else None


def _executable_path(pid: int) -> str | None:
    import sys
    if sys.platform == "win32":
        return _win_executable_path(pid)
    libc = _libc()
    if libc is None:
        return None
    import ctypes
    buf = ctypes.create_string_buffer(4096)
    try:
        size = libc.proc_pidpath(pid, buf, 4096)
    except Exception:
        return None
    if size <= 0:
        return None
    return buf.value.decode("utf-8", "replace")


def identify_client_by_process(pid: int) -> str | None:
    """Which client launched this session process, from its ancestry.

    This is the stronger identity check: it does not trust the self-reported
    clientInfo name, only which application tree actually spawned the session.
    Returns None when the answer cannot be established (non-macOS, missing
    permissions, or an unrecognized launcher).
    """
    current = pid
    for _ in range(MAX_ANCESTRY_DEPTH):
        parent = _parent_pid(current)
        if parent is None:
            return None
        path = _executable_path(parent)
        current = parent
        if not path:
            continue
        lowered = path.casefold()
        for agent, markers in _ANCESTRY_MARKERS:
            if any(marker in lowered for marker in markers):
                return agent
        # Stop climbing once we leave the user's application layer.
        if "/usr/libexec/" in lowered or lowered.endswith("/launchd"):
            return None
    return None


def _identity_status(client_name: str, bound_agent: str | None) -> str:
    identified = identify_client(client_name)
    if identified is None:
        return "observed_unidentified"
    if bound_agent is None:
        return "observed_unbound"
    if identified != bound_agent:
        return "observed_mismatch"
    return "recent_verified"


def _open_directory(data_directory, *, create: bool) -> int:
    # 用目录描述符锚定路径；拒绝 .connections 符号链接，避免替换竞态逃逸。
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open(os.fspath(data_directory), flags)
    try:
        if create:
            try:
                os.mkdir(".connections", mode=0o700, dir_fd=root)
            except FileExistsError:
                pass
        directory = os.open(".connections", flags, dir_fd=root)
    finally:
        os.close(root)
    try:
        if create:
            os.fchmod(directory, 0o700)
        return directory
    except Exception:
        os.close(directory)
        raise


def _write_record(data_directory, filename: str, record: dict,
                  prefix: str = ".call-") -> bool:
    directory = None
    temporary = None
    try:
        directory = _open_directory(data_directory, create=True)
        temporary = f"{prefix}{secrets.token_hex(16)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        return True
    except Exception:
        return False
    finally:
        if directory is not None:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except OSError:
                    pass
            try:
                os.close(directory)
            except OSError:
                pass


def _base_record(client_name, bound_agent, build_marker) -> dict | None:
    if bound_agent is not None and not _safe_agent(bound_agent):
        return None
    if not isinstance(build_marker, str) or not 0 < len(build_marker) <= 256:
        return None
    if (not isinstance(client_name, str) or not client_name
            or len(client_name) > 128 or not client_name.isprintable()):
        client_name = "unknown"
    return {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "client_name": client_name,
        "bound_agent": bound_agent,
        "server": SERVER,
        "pid": os.getpid(),
        "build_marker": build_marker,
    }


def record_call(*, client_name: str | None, bound_agent: str | None,
                data_directory, tool: str, build_marker: str) -> bool:
    """由真实 tools/call 入口调用；失败静默，不影响原工具执行。

    未绑定时按已识别客户端（或 unknown）落盘，但永远不算验证。
    有绑定时总是写绑定名称，绝不根据工具参数里的 agent 改写归属。
    """
    try:
        if tool not in TOOLS:
            return False
        record = _base_record(client_name, bound_agent, build_marker)
        if record is None:
            return False
        record["tool"] = tool
        record["status"] = _identity_status(record["client_name"], bound_agent)
        agent = bound_agent or identify_client(record["client_name"]) or "unknown"
        return _write_record(data_directory, f"{agent}.json", record)
    except Exception:
        return False


def record_connect(*, client_name: str | None, bound_agent: str | None,
                   data_directory, build_marker: str) -> bool:
    """Record a real MCP session handshake.

    A handshake proves the client connected and negotiated this server; it does
    not prove any tool was used. Stored apart from tool calls so readers can
    distinguish "connected" from "recently active". Observed session pids are
    accumulated so a pooled client stays connected while any session lives.
    """
    try:
        record = _base_record(client_name, bound_agent, build_marker)
        if record is None:
            return False
        record["kind"] = "handshake"
        record["status"] = _identity_status(record["client_name"], bound_agent)
        record["schema_version"] = CONNECT_SCHEMA_VERSION
        agent = bound_agent or identify_client(record["client_name"]) or "unknown"
        existing = _read_connect(agent, data_directory) or {}
        record["session_pids"] = _merge_session_pids(
            existing.get("session_pids"), record["pid"])
        return _write_record(data_directory, f"{agent}{CONNECT_SUFFIX}", record,
                             prefix=".connect-")
    except Exception:
        return False


def extract_executable_paths(lines) -> list[str]:
    """Mapped executable paths from lsof -Fn output (helper, no side effects)."""
    return [line[1:] for line in lines
            if isinstance(line, str) and line.startswith("n")]


def _looks_like_heropen_session(path: str) -> bool:
    """Whether an executable path could be a live heropen MCP session.

    Accepts the packaged binary (…/heropen-bin/heropen), a console script named
    heropen, and a Python interpreter, because heropen also runs as `python -m
    heropen…` in non-packaged installs. This only exists to reduce PID-reuse
    false positives; existence is still the primary signal.
    """
    if not isinstance(path, str) or not path:
        return False
    if "heropen" in path:
        return True
    return os.path.basename(path).startswith("python")


def _process_alive(pid: int) -> bool:
    """Whether a client session process still runs, guarding against PID reuse.

    os.kill(pid, 0) alone can be fooled by PID reuse, so when the mapped
    executable is readable we also require it to look like a heropen session or
    a Python interpreter. If the path cannot be read (permissions, non-macOS)
    the existence answer stands.
    """
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"],
            capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return True  # cannot verify identity; existence already established
    paths = extract_executable_paths(result.stdout.splitlines())
    if not paths:
        return True  # lsof produced no path; keep the existence answer
    return any(_looks_like_heropen_session(p) for p in paths)


def _live_from_calls(agent: str, data_directory, launch_agent=None) -> dict:
    """Fallback liveness from the agent's tool-call record.

    A pooled client may handshake on a session that exits while it keeps
    working on another session that only shows up in tool-call records. This
    returns an empty dict when there is nothing usable, so callers can merge it
    without clobbering the handshake result.
    """
    result: dict = {}
    try:
        directory = _open_directory(data_directory, create=False)
    except OSError:
        return result
    try:
        fd = os.open(f"{agent}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                    or info.st_size > 8192):
                return result
            record = json.loads(stream.read(8193))
    except Exception:
        return result
    finally:
        try:
            os.close(directory)
        except OSError:
            pass
    pid = record.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return result
    if not _process_alive(pid):
        return result
    identified = identify_client_by_process(pid)
    if identified is not None and identified != agent:
        # A live session belonging to a different client is a conflict.
        return {"connected": False, "connect_process_alive": True,
                "connect_identity_verified": False,
                "connect_status": "observed_mismatch",
                "connect_session_pids": [pid], "connect_launch_agent": identified}
    verified = identified == agent if identified is not None else (
        record.get("status") == "recent_verified")
    return {"connected": True,
            "connect_process_alive": True,
            "connect_identity_verified": verified,
            "connect_status": "recent_verified" if verified else "connected_unidentified",
            "connect_session_pids": [pid],
            "connect_launch_agent": identified}


def _read_connect(agent: str, data_directory) -> dict | None:
    """Read one handshake record; returns None when absent or untrustworthy."""
    directory = None
    try:
        directory = _open_directory(data_directory, create=False)
        fd = os.open(f"{agent}{CONNECT_SUFFIX}",
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                    or info.st_size > 8192):
                return None
            record = json.loads(stream.read(8193))
        if not isinstance(record, dict):
            return None
        # Accept v1 (single pid) records written before pooling support.
        schema = record.get("schema_version")
        expected = _CONNECT_FIELDS if schema == CONNECT_SCHEMA_VERSION else CONNECT_FIELDS_V1
        if set(record) != set(expected):
            return None
        if (type(schema) is not int or schema not in (1, CONNECT_SCHEMA_VERSION)
                or record["server"] != SERVER or record["kind"] != "handshake"
                or type(record["pid"]) is not int or record["pid"] <= 0):
            return None
        session_pids = record.get("session_pids")
        if session_pids is not None:
            if (not isinstance(session_pids, list) or not session_pids
                    or len(session_pids) > MAX_SESSION_PIDS
                    or any(type(p) is not int or p <= 1 for p in session_pids)):
                return None
        name, bound = record["client_name"], record["bound_agent"]
        if not isinstance(name, str) or not 0 < len(name) <= 128 or not name.isprintable():
            return None
        if bound is not None and not _safe_agent(bound):
            return None
        if (bound or identify_client(name) or "unknown") != agent:
            return None
        identity_status = _identity_status(name, bound)
        if record["status"] != identity_status:
            return None
        timestamp = datetime.fromisoformat(record["timestamp"])
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            return None
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if age < 0:
            return None
        pids = session_pids if session_pids else [record["pid"]]
        alive = any(_process_alive(p) for p in pids)
        # Prefer process ancestry over the self-reported name: a live session
        # whose launcher is a different client is a real conflict.
        launch_agent = None
        for pid in pids:
            identified = identify_client_by_process(pid)
            if identified is not None:
                launch_agent = identified
                break
        return {**record, "fresh": age <= FRESH_SECONDS, "age_seconds": int(age),
                "session_pids": list(pids), "process_alive": alive,
                "launch_agent": launch_agent}
    except Exception:
        return None
    finally:
        if directory is not None:
            try:
                os.close(directory)
            except OSError:
                pass


def read_status(agent: str, data_directory) -> dict:
    """只读、容错地返回最近调用状态；十五分钟内才是 recent_verified。

    每次重新校验文件归属、握手名称和绑定身份，不信任落盘的 status。
    未来时间、无时区时间和损坏记录均不得成为最近验证。
    """
    result = {"agent": agent, "status": "not_observed", "fresh": False,
              "recent_verified": False, "fresh_seconds": FRESH_SECONDS,
              "connected": False, "connect_status": "not_connected",
              "connect_identity_verified": False, "connect_age_seconds": None}
    if not _safe_agent(agent):
        return {**result, "status": "invalid_agent"}
    connect = _read_connect(agent, data_directory)
    if connect is not None:
        identity = connect["status"]
        alive = connect["process_alive"]
        launch_agent = connect.get("launch_agent")
        if identity in ("recent_verified", "observed_unidentified"):
            # A handshake on this agent's own bound server is a real connection.
            # Connection persists while the session process lives; it is NOT a
            # 15-minute window, otherwise an idle client looks disconnected.
            result["connected"] = alive
            # Identity is verified when the launcher is this agent, or — when
            # ancestry is unavailable — when the self-reported name matches.
            # A launcher that is a different client is a hard conflict.
            if launch_agent is not None and launch_agent != agent:
                result["connect_status"] = "observed_mismatch"
                result["connect_identity_verified"] = False
                result["connect_launch_agent"] = launch_agent
                result["connect_age_seconds"] = connect["age_seconds"]
                result["connect_process_alive"] = alive
                result["connect_session_pids"] = connect["session_pids"]
                return result
            verified = (launch_agent == agent) if launch_agent is not None else (
                identity == "recent_verified")
            result["connect_identity_verified"] = verified
            if alive:
                result["connect_status"] = (
                    "recent_verified" if verified else "connected_unidentified")
            else:
                result["connect_status"] = (
                    "ended_verified" if verified else "ended_unidentified")
        else:
            # A recognized but different name is a genuine mismatch.
            result["connect_status"] = identity
        result["connect_age_seconds"] = connect["age_seconds"]
        result["connect_process_alive"] = alive
        result["connect_session_pids"] = connect["session_pids"]
        result["connect_launch_agent"] = launch_agent
        if not alive and not result["connected"]:
            # The latest handshake's session exited, but a pooled client may
            # still be running on another session that only appears in its tool
            # call records. Treat that as connected rather than "ended".
            result.update(_live_from_calls(agent, data_directory, launch_agent))
    directory = None
    try:
        directory = _open_directory(data_directory, create=False)
        fd = os.open(f"{agent}.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            # 并发 replace 后已打开的旧文件可为 0 链接；只拒绝额外硬链接。
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                    or info.st_size > 8192):
                raise ValueError("不是有效的独立调用记录文件")
            record = json.loads(stream.read(8193))
        if not isinstance(record, dict) or set(record) != set(_FIELDS):
            raise ValueError("调用记录字段无效")
        if (type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["server"] != SERVER
                or type(record["pid"]) is not int or record["pid"] <= 0
                or not isinstance(record["build_marker"], str)
                or not 0 < len(record["build_marker"]) <= 256
                or record["tool"] not in TOOLS):
            raise ValueError("调用记录元数据无效")
        name, bound = record["client_name"], record["bound_agent"]
        if not isinstance(name, str) or not 0 < len(name) <= 128 or not name.isprintable():
            raise ValueError("客户端名称无效")
        if bound is not None and not _safe_agent(bound):
            raise ValueError("绑定名称无效")
        if (bound or identify_client(name) or "unknown") != agent:
            raise ValueError("文件归属与身份不符")
        identity_status = _identity_status(name, bound)
        if record["status"] != identity_status:
            raise ValueError("身份与记录状态不符")
        timestamp = datetime.fromisoformat(record["timestamp"])
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError("时间必须为 UTC")
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if age < 0:
            raise ValueError("记录时间在未来")
        fresh = age <= FRESH_SECONDS
        status = identity_status
        if status == "recent_verified" and not fresh:
            status = "stale_verified"
        return {**result, **record, "status": status, "fresh": fresh,
                "recent_verified": status == "recent_verified"}
    except FileNotFoundError:
        return result
    except Exception:
        return {**result, "status": "invalid_record"}
    finally:
        if directory is not None:
            try:
                os.close(directory)
            except OSError:
                pass
