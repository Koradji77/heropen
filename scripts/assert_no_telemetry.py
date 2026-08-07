#!/usr/bin/env python3
"""heropen C1 — 零外联断言（静态检查）。

把「零遥测 / 不向外发请求」从口头承诺变成 CI 强制断言：任何向源码引入
遥测或未经声明的外联都会让构建失败。

用法：python3 scripts/assert_no_telemetry.py <repo-root>
"""
import ipaddress
import os
import re
import sys

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
SRC = os.path.join(ROOT, "src")

# 允许出现的硬编码主机（均为本地绑定 / 用户主动触发的可选动作 / 文档引用，非遥测）：
#   127.0.0.1 / 0.0.0.0 / localhost / ::1 —— 本地绑定，非外联
#   pypi.org      —— diagnose 的版本检查（用户主动运行 heropen diagnose）
#   hf-mirror.com —— fastembed 的 HuggingFace 镜像默认值（环境变量，可覆盖）
#   github.com / raw.githubusercontent.com / files.pythonhosted.org —— 文档/依赖引用
#   voidtools.com —— file_search skill 中 Everything 工具的官网说明（纯文档引用）
#   heropen.net   —— 本项目站点
ALLOWED_DOMAINS = {
    "pypi.org", "hf-mirror.com", "github.com",
    "raw.githubusercontent.com", "files.pythonhosted.org",
    "voidtools.com", "heropen.net",
}

TELEMETRY = re.compile(
    r"telemetry|analytics|heartbeat|beacon|tracking|mixpanel|amplitude|"
    r"posthog|sentry|statsd|datadog|phone.?home",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://([^\"' )]+)")
WHILE_SLEEP = re.compile(r"while\s+True", re.IGNORECASE)


def _is_local(host: str) -> bool:
    """loopback / 私有 / 未指定地址均为本地绑定，不算外联。"""
    if host in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_unspecified
    except ValueError:
        return False


def _py_files():
    for dp, _, files in os.walk(SRC):
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


def main() -> int:
    print("== heropen 零外联断言 ==")

    # ── 1) 遥测关键词黑名单 ──
    print("-- [1/3] 检查遥测关键词 --")
    found_tele = False
    for path in _py_files():
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                s = line.strip()
                if s.startswith("#") or s.startswith('"""'):
                    continue
                if TELEMETRY.search(line):
                    print(f"  ❌ {path}:{i}: {s}")
                    found_tele = True
    if found_tele:
        print("  ⚠️  发现疑似遥测关键词")
    else:
        print("  ✅ 未发现遥测关键词")

    # ── 2) 硬编码外部域名（白名单之外即失败）──
    print("-- [2/3] 检查硬编码外部域名 --")
    domains: set[str] = set()
    for path in _py_files():
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                for m in URL_RE.findall(line):
                    host = m.split("/")[0].split(":")[0]
                    # 占位符（如 your-server）或非域名片段跳过
                    if not host or "." not in host:
                        continue
                    if _is_local(host):
                        continue  # loopback / 私有 / 未指定地址（本地绑定，非外联）
                    domains.add(host)
    bad = sorted(
        d for d in domains
        if not any(d == a or d.endswith("." + a) for a in ALLOWED_DOMAINS)
    )
    if bad:
        print(f"  ❌ 白名单之外的硬编码外部域名: {bad}")
    else:
        print(f"  ✅ 所有硬编码域名均在白名单 ({sorted(domains)})")

    # ── 3) 后台心跳循环（仅告警）──
    print("-- [3/3] 检查后台心跳循环（仅告警）--")
    heartbeat = False
    for path in _py_files():
        with open(path, encoding="utf-8", errors="ignore") as f:
            src = f.read()
        if WHILE_SLEEP.search(src) and "sleep" in src.lower():
            print(f"  ⚠️  {os.path.basename(path)} 含 'while True' + sleep，需人工复核")
            heartbeat = True
    if not heartbeat:
        print("  ✅ 未发现后台心跳循环")

    if found_tele or bad:
        print("== 零外联断言 FAILED ==")
        return 1
    print("== 零外联断言 PASSED ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
