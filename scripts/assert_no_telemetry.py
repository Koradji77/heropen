#!/usr/bin/env python3
"""heropen C1 — 零外联断言（静态检查）。

把「零遥测 / 不向外发请求」从口头承诺变成 CI 强制断言：任何向源码引入
遥测或未经声明的外联都会让构建失败。

用法：python3 scripts/assert_no_telemetry.py <repo-root>
      python3 scripts/assert_no_telemetry.py --selftest   # 只跑守卫自检（正则检出力回归）
"""
import ipaddress
import os
import re
import sys

# Avoid UnicodeEncodeError on Windows GBK consoles during release checks.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
ROOT = _ARGS[0] if _ARGS else "."
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
    r"posthog|(?<![A-Za-z0-9])sentry(?![A-Za-z0-9])|statsd|datadog|phone.?home",
    re.IGNORECASE,
)
# ⚠️ 为什么只有 sentry 带词边界（2026-10-27 修）：
#   Windows 的 Toolhelp32 进程枚举 API 叫 PROCESSENTRY32 / PROCESSENTRY32W，
#   裸子串匹配 sentry 会命中 PRO-CESSENTRY-32，导致 connection_status.py 的
#   Windows 分支被误判为「疑似遥测」（2.0.7 回流后 CI 变红，实为误报）。
#   真实 Sentry SDK 用法（sentry_sdk / SENTRY_DSN / sentry.io / Sentry.init）
#   都有非字母数字分隔，词边界足以全部抓住；其余关键词的派生形式
#   （beaconing / trackingPixel / BACKTRACKING_SEARCH）本身即遥测语义，
#   故保持子串匹配不动 —— 检出力零损失。
#   改动本正则前必须先跑 --selftest。

# 守卫自身的回归测试：(代码样本, 是否应被判定为遥测)
# 目的：任何人收紧/放宽上面的正则时，检出力退化会立刻被发现，而不是静默变弱。
SELFTEST_CASES: list[tuple[str, bool]] = [
    # —— 真遥测：必须全部命中（检出力下限）——
    ("import sentry_sdk", True),
    ("sentry_sdk.init(dsn)", True),
    ('SENTRY_DSN = "https://x"', True),
    ("Sentry.init()", True),
    ("@sentry.traced", True),
    ('url = "https://sentry.io/2"', True),
    ("from posthog import Posthog", True),
    ('mixpanel.track("event")', True),
    ("import analytics", True),
    ('statsd_client.increment("a")', True),
    ("datadog.dogstatsd()", True),
    ('self.beacon_url = "http://x"', True),
    ("beaconing = True", True),
    ('os.environ["HEROPEN_NO_TELEMETRY"] = "1"', True),
    ("send_telemetry()", True),
    ("phone_home()", True),
    ("trackingPixel()", True),
    ("BACKTRACKING_SEARCH = 1", True),
    ("heartbeat_interval = 5", True),
    ("amplitude.logEvent()", True),
    # —— 误报样本：必须全部不命中（精度下限）——
    ("class PROCESSENTRY32(ctypes.Structure):", False),
    ("pe = PROCESSENTRY32()", False),
    ("pe.dwSize = ctypes.sizeof(PROCESSENTRY32)", False),
    ("PROCESSENTRY32W()", False),
    ("TH32CS_SNAPPROCESS = 0x00000002", False),
    ("MySentryHandler", False),
    ("ProcessEntryIterator", False),
]


def selftest() -> int:
    """校验 TELEMETRY 正则的检出力与精度，返回失败用例数。"""
    print("-- [0/3] 守卫自检（正则检出力回归）--")
    fails = 0
    for sample, expect in SELFTEST_CASES:
        got = bool(TELEMETRY.search(sample))
        if got != expect:
            kind = "漏检（检出力退化）" if expect else "误报"
            print(f"  ❌ {kind}: 期望 {expect} 实得 {got} | {sample}")
            fails += 1
    total = len(SELFTEST_CASES)
    if fails:
        print(f"  自检失败 {fails}/{total} 例 —— 正则被改坏，拒绝继续")
    else:
        print(f"  ✅ 自检通过 {total}/{total} 例")
    return fails
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

    # ── 0) 守卫自检：正则被改坏时立即失败，而非静默降低检出力 ──
    if selftest():
        print("== 零外联断言 FAILED（守卫自检未过）==")
        return 1

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
    if "--selftest" in sys.argv:
        sys.exit(1 if selftest() else 0)
    sys.exit(main())
