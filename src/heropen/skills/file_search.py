"""
file_search.py — HeroPen File Search Skill
============================================
跨平台文件名搜索模块，不依赖任何第三方包。
三平台后端自动降级：
  Windows: Everything (es.exe) → dir /s
  macOS:   mdfind
  Linux:   mlocate (locate) → find

用法:
    from heropen_skills.file_search import search_files
    result = search_files("季度报告", path="~/Documents")
"""

import os
import re
import sys
import time
import subprocess
import platform
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── 默认排除目录 ──────────────────────────────────────────────
DEFAULT_EXCLUDE_DIRS = [
    ".git", "node_modules", "__pycache__",
    ".venv", "venv", ".cache",
    "Library", "AppData",
]

# ── 平台判断 ──────────────────────────────────────────────────
IS_WINDOWS = platform.system() == "Windows"
IS_MACOS   = platform.system() == "Darwin"
IS_LINUX   = platform.system() == "Linux"


# ── 辅助函数 ──────────────────────────────────────────────────

def _expand_path(path: str) -> str:
    """展开 ~ 和变量，返回 OS 原生格式的路径"""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def _normalize_path(path: str) -> str:
    """统一路径格式：Windows 用反斜杠，其他用正斜杠"""
    if IS_WINDOWS:
        return path.replace("/", "\\")
    return path.replace("\\", "/")


def _format_timestamp(ts: float) -> str:
    """时间戳 → '2026-03-15 14:30:00'"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _make_result(filepath: str, size: int = 0, modified: str = "") -> dict:
    """构建统一的结果条目"""
    return {
        "path": _normalize_path(filepath),
        "name": os.path.basename(filepath),
        "size": size,
        "modified": modified,
        "action_hint": "open",
    }


def _should_exclude(path: str, exclude_dirs: list[str]) -> bool:
    """检查路径是否包含排除目录"""
    path_lower = path.lower()
    for ex in exclude_dirs:
        # 匹配路径中的任意段
        if os.sep + ex.lower() + os.sep in path_lower:
            return True
        # 也匹配开头
        if path_lower.startswith(ex.lower() + os.sep):
            return True
        # 匹配结尾 (可能不带最后的 separator)
        if path_lower.endswith(os.sep + ex.lower()):
            return True
    return False


# ── Windows 检测 Everything ──────────────────────────────────

def _has_everything() -> tuple[bool, str]:
    """检测 Windows 上是否安装了 Everything + 返回 es.exe 路径"""
    es_paths = [
        r"C:\Program Files\Everything\es.exe",
        r"C:\Program Files (x86)\Everything\es.exe",
    ]

    # 方法1: 检查注册表
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\voidtools\Everything"
        ) as key:
            install_path, _ = winreg.QueryValueEx(key, "InstallPath")
            if install_path:
                es = os.path.join(install_path, "es.exe")
                if os.path.isfile(es):
                    return True, es
    except (OSError, ImportError):
        pass

    # 方法2: 检查常见安装路径
    for es_path in es_paths:
        if os.path.isfile(es_path):
            return True, es_path

    # 方法3: 检查进程列表
    try:
        output = subprocess.check_output(
            "tasklist /FI \"IMAGENAME eq Everything.exe\"",
            shell=True, timeout=5, stderr=subprocess.DEVNULL,
            text=True
        )
        if "Everything.exe" in output:
            # 进程在跑，尝试在 PATH 里找 es.exe
            for es_path in es_paths:
                if os.path.isfile(es_path):
                    return True, es_path
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass

    return False, ""


# ── Windows: Everything 搜索 ─────────────────────────────────

def _search_everything(
    query: str, path: str, limit: int, exclude_dirs: list[str], es_path: str
) -> list[dict]:
    """用 Everything SDK (es.exe) 搜索"""
    results = []

    # es.exe 参数: -n <count> <query> <path>
    cmd = [es_path, "-n", str(limit), query, path]

    try:
        output = subprocess.check_output(
            cmd, timeout=15, text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return results

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # 检查是否被排除
        if _should_exclude(line, exclude_dirs):
            continue
        try:
            st = os.stat(line)
            results.append(_make_result(
                line, size=st.st_size,
                modified=_format_timestamp(st.st_mtime)
            ))
        except OSError:
            # 权限不够等，跳过
            continue

    return results


# ── Windows: dir /s 降级搜索 ─────────────────────────────────

def _parse_dir_datetime(date_str: str, time_str: str, ampm: str = "") -> str:
    """
    解析 Windows dir 输出的日期时间。
    Windows 中文/英文格式不同，做兼容。
    输出: '2026-03-15 14:30:00'
    """
    # 去掉可能的 AM/PM 后缀
    time_str = time_str.replace(ampm, "").strip()
    if ampm:
        time_str = time_str.strip()

    # 尝试常见格式
    # 英文: 03/15/2026  02:30 PM
    # 中文: 2026/03/15  14:30
    try:
        # 尝试 "YYYY/MM/DD HH:MM"
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y/%m/%d %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    try:
        # 尝试 "MM/DD/YYYY HH:MM" (24h)
        dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    try:
        # 尝试 "MM/DD/YYYY HH:MM AM/PM"
        dt = datetime.strptime(f"{date_str} {time_str} {ampm}", "%m/%d/%Y %I:%M %p")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    # 兜底：存原始文本
    return f"{date_str} {time_str}"


def _parse_dir_size(size_str: str) -> int:
    """解析 dir 输出的文件大小，去掉逗号"""
    try:
        return int(size_str.replace(",", ""))
    except ValueError:
        return 0


def _search_dir_s(
    query: str, root_path: str, limit: int, exclude_dirs: list[str],
    timeout: int = 10
) -> list[dict]:
    """Windows dir /s 降级搜索，10秒超时截断"""
    results = []
    query_lower = query.lower()
    root_path = _expand_path(root_path)
    start_time = time.time()

    try:
        proc = subprocess.Popen(
            ["cmd", "/c", "dir", root_path, "/s", "/b", "/a:-d"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return results

    # 先收集所有文件路径（带超时）
    all_files = []
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        all_files = stdout.splitlines()
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        if stdout:
            all_files = stdout.splitlines()
        # 标记已截断（通过返回的 truncated 状态，不是在这里做）

    elapsed = time.time() - start_time
    truncated = (elapsed >= timeout)

    # 用 stat 获取文件信息 + 名字过滤
    for filepath in all_files:
        filepath = filepath.strip()
        if not filepath:
            continue

        # 子串匹配（大小写不敏感）
        basename = os.path.basename(filepath)
        if query_lower not in basename.lower():
            continue

        # 排除目录检查
        if _should_exclude(filepath, exclude_dirs):
            continue

        try:
            st = os.stat(filepath)
            results.append(_make_result(
                filepath,
                size=st.st_size,
                modified=_format_timestamp(st.st_mtime)
            ))
        except OSError:
            continue

        if len(results) >= limit:
            break

    return results, truncated


# ── macOS: mdfind ────────────────────────────────────────────

def _search_mdfind(
    query: str, path: str, limit: int, exclude_dirs: list[str]
) -> list[dict]:
    """用 macOS Spotlight (mdfind) 搜索文件名"""
    results = []
    root = _expand_path(path)

    # mdfind -onlyin <dir> -name <query>
    cmd = [
        "mdfind", "-onlyin", root, "-name", query
    ]

    try:
        output = subprocess.check_output(
            cmd, timeout=30, text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return results

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if _should_exclude(line, exclude_dirs):
            continue

        try:
            st = os.stat(line)
            results.append(_make_result(
                line, size=st.st_size,
                modified=_format_timestamp(st.st_mtime)
            ))
        except OSError:
            continue

        if len(results) >= limit:
            break

    return results


# ── Linux: locate / find ─────────────────────────────────────

def _search_locate(
    query: str, path: str, limit: int, exclude_dirs: list[str]
) -> list[dict]:
    """用 mlocate/locate 搜索（Linux 优先后端）"""
    results = []
    root = _expand_path(path)

    # locate -b -i <query>  # -b: basename匹配, -i: 忽略大小写
    cmd = ["locate", "-b", "-i", query]

    try:
        output = subprocess.check_output(
            cmd, timeout=30, text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return results

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # 只保留指定路径下的结果
        if not line.startswith(root):
            continue
        if _should_exclude(line, exclude_dirs):
            continue

        try:
            st = os.stat(line)
            results.append(_make_result(
                line, size=st.st_size,
                modified=_format_timestamp(st.st_mtime)
            ))
        except OSError:
            continue

        if len(results) >= limit:
            break

    return results


def _search_find(
    query: str, path: str, limit: int, exclude_dirs: list[str]
) -> list[dict]:
    """Linux find 降级搜索"""
    results = []
    root = _expand_path(path)

    # find <path> -iname "*<query>*" -type f
    cmd = [
        "find", root, "-iname", f"*{query}*", "-type", "f",
    ]
    # 加排除目录（-path 模式）
    for ex in exclude_dirs:
        cmd.extend(["-not", "-path", f"*/{ex}/*"])
        cmd.extend(["-not", "-path", f"{root}/{ex}/*"])

    try:
        output = subprocess.check_output(
            cmd, timeout=60, text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        return results

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            st = os.stat(line)
            results.append(_make_result(
                line, size=st.st_size,
                modified=_format_timestamp(st.st_mtime)
            ))
        except OSError:
            continue

        if len(results) >= limit:
            break

    return results


# ── 主入口 ────────────────────────────────────────────────────

def search_files(
    query: str,
    path: str = "~",
    limit: int = 20,
    exclude_dirs: Optional[list[str]] = None,
) -> dict:
    """
    跨平台文件名搜索主函数。

    参数:
        query:       搜索关键字（子串匹配，大小写不敏感）
        path:        搜索根路径（默认用户主目录）
        limit:       最大返回条数
        exclude_dirs: 排除的目录名列表（覆盖默认值）

    返回:
        {
            "ok": True/False,
            "error": "错误信息" (if not ok),
            "results": [...],
            "total_found": N,
            "truncated": False,
            "suggestion": None/"安装 Everything 可加速搜索"
        }
    """
    # ── 参数校验 ──
    if not query or not query.strip():
        return {
            "ok": False,
            "error": "搜索关键字不能为空",
            "results": [],
            "total_found": 0,
            "truncated": False,
            "suggestion": None,
        }

    query = query.strip()
    root_path = _expand_path(path)

    if not os.path.isdir(root_path):
        return {
            "ok": False,
            "error": f"路径不存在或无法访问: {root_path}",
            "results": [],
            "total_found": 0,
            "truncated": False,
            "suggestion": None,
        }

    exclude = list(exclude_dirs) if exclude_dirs is not None else list(DEFAULT_EXCLUDE_DIRS)

    # ── 执行搜索 ──
    results = []
    truncated = False
    suggestion = None

    try:
        if IS_WINDOWS:
            has_es, es_path = _has_everything()
            if has_es:
                results = _search_everything(query, root_path, limit, exclude, es_path)
            else:
                results, truncated = _search_dir_s(query, root_path, limit, exclude)
                if truncated:
                    suggestion = "安装 Everything 可加速搜索 (https://www.voidtools.com)"

        elif IS_MACOS:
            results = _search_mdfind(query, root_path, limit, exclude)

        elif IS_LINUX:
            # 检测 locate 是否可用
            try:
                subprocess.run(
                    ["which", "locate"],
                    check=True, capture_output=True, text=True, timeout=5
                )
                results = _search_locate(query, root_path, limit, exclude)
            except (subprocess.CalledProcessError, FileNotFoundError):
                results = _search_find(query, root_path, limit, exclude)

        else:
            return {
                "ok": False,
                "error": f"不支持的平台: {platform.system()}",
                "results": [],
                "total_found": 0,
                "truncated": False,
                "suggestion": None,
            }

    except Exception as e:
        return {
            "ok": False,
            "error": f"搜索执行异常: {str(e)}",
            "results": [],
            "total_found": 0,
            "truncated": False,
            "suggestion": None,
        }

    # ── 限制结果数 ──
    if len(results) > limit:
        results = results[:limit]
        truncated = True

    return {
        "ok": True,
        "error": None,
        "results": results,
        "total_found": len(results),
        "truncated": truncated,
        "suggestion": suggestion,
    }


# ── CLI 自测 ──────────────────────────────────────────────────

def _cli_demo():
    """CLI 测试入口：python file_search.py <query> [path]"""
    q = sys.argv[1] if len(sys.argv) > 1 else "report"
    p = sys.argv[2] if len(sys.argv) > 2 else "~"
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    print(f"🔍 搜索: \"{q}\" 在 {p} (limit={lim})")
    print(f"  平台: {platform.system()}")

    result = search_files(q, path=p, limit=lim)

    if not result["ok"]:
        print(f"❌ 错误: {result['error']}")
        sys.exit(1)

    print(f"\n📁 找到 {result['total_found']} 个结果" +
          (" (已截断)" if result["truncated"] else ""))
    if result["suggestion"]:
        print(f"💡 提示: {result['suggestion']}")

    for r in result["results"]:
        print(f"  {r['name']:<40s} {r['size']:>8d}  {r['modified']}")
        print(f"    └─ {r['path']}")


if __name__ == "__main__":
    _cli_demo()
