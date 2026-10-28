#!/usr/bin/env python3
"""heropen 回归门禁 —— 覆盖 2026-10-28 macOS 实机反馈修复的 6 类缺陷。

每条规则都带 inject 锚点：把缺陷重新注入实现，门禁必须失败并归因到该条。
「填不出 inject 锚点 ⇒ 那个缺陷不可注入 ⇒ 它没有门禁」。

用法：
    python3 scripts/assert_regressions.py --once     # 全部检查，全过 exit 0
    python3 scripts/assert_regressions.py --bidir    # 双向验证：逐条注入→必须失败→还原→必须通过
    python3 scripts/assert_regressions.py --list     # 只列规则

前置：解释器需能 import mcp（health/search 类检查依赖）。缺失时 exit 2 并说明，
不做静默跳过——「永远绿」的测试等于没有测试。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

PREAMBLE = """
import os, sys, json, sqlite3, io, contextlib
SRC = os.environ['HEROPEN_GATE_SRC']
REPO = os.environ['HEROPEN_GATE_REPO']
sys.path.insert(0, SRC)
for _m in [k for k in list(sys.modules) if k == 'heropen' or k.startswith('heropen.')]:
    del sys.modules[_m]
import heropen.core as core
assert os.path.abspath(core.__file__).startswith(os.path.abspath(SRC)), \\
    '加载到非 src 的 heropen: ' + core.__file__
HPD = core.HERO_PEN_DIR
"""

# 建一个「有 entries 表」的 _shared.db —— 这是 ATTACH 污染的必要前提
SETUP_SHARED = """
_sp = os.path.join(HPD, '_shared.db')
_sc = sqlite3.connect(_sp)
_sc.execute('''CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_date TEXT, section TEXT, content TEXT, tags TEXT, source TEXT, agent TEXT,
  embedding BLOB, created_at TEXT)''')
_sc.execute("INSERT INTO entries (entry_date, content, agent) VALUES ('2026-10-28','SHARED旧数据','other')")
_sc.commit(); _sc.close()
SHARED_COLS = ['id','entry_date','section','content','tags','source','agent','embedding','created_at']
"""

SETUP_CONFIG = """
def _write_cfg(names):
    json.dump({'version':'1.0','edition':'basic',
               'agents':[{'name':n,'db_path':os.path.join(HPD,n+'.db'),'online':True} for n in names]},
              open(os.path.join(HPD,'agent-config.json'),'w',encoding='utf-8'))
"""

RULES: dict[str, dict] = {
    "attach-no-cross-tenant-write": {
        "desc": "ATTACH _shared 时，agent 自己的库必须建出 entries 主表，数据与 DDL 都不得落到 shared",
        "setup": SETUP_SHARED + SETUP_CONFIG + "_write_cfg(['iso'])\n",
        "check": """
os.environ.pop('HEROPEN_AGENT', None)
core.add_entry('2026-10-28', '隔离验证条目', agent='iso')
_p = os.path.join(HPD, 'iso.db')
_tabs = [r[0] for r in sqlite3.connect(_p).execute(
    "SELECT name FROM main.sqlite_master WHERE type='table'")]
assert 'entries' in _tabs, 'iso.db 缺 entries 主表: %s' % _tabs
_sc = sqlite3.connect(os.path.join(HPD, '_shared.db'))
_n = _sc.execute("SELECT COUNT(*) FROM entries WHERE agent='iso'").fetchone()[0]
assert _n == 0, 'iso 的数据错位写入 _shared.db (%d 条)' % _n
_cols = [r[1] for r in _sc.execute('PRAGMA table_info(entries)')]
assert _cols == SHARED_COLS, 'shared.entries 被跨租户 ALTER 污染: %s' % _cols
""",
        # 真实历史缺陷是「两层防御同时缺失」：init 路径 attach 了 shared，且
        # PRAGMA 未限定 main。只注入一层，另一层仍保护 → 端到端不破、门禁没牙。
        # 故这里注入两处，完整复现 2.0.7 的原始 bug。
        "inject": [
            ("src/heropen/core.py",
             'cur = c.execute("PRAGMA main.table_info(entries)")',
             'cur = c.execute("PRAGMA table_info(entries)")'),
            ("src/heropen/core.py",
             "    c = _connect(agent, attach_shared=False)",
             "    c = _connect(agent)"),
        ],
    },

    "conn-selfheal-missing-schema": {
        "desc": "非 0 字节但缺 entries 的残库，conn() 必须补建 schema（不能只看文件大小）",
        "setup": SETUP_SHARED + """
_bp = os.path.join(HPD, 'heal.db')
_bc = sqlite3.connect(_bp)
_bc.execute('PRAGMA journal_mode=WAL')
_bc.execute('CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)')
_bc.commit(); _bc.close()
assert os.path.getsize(_bp) > 0, '残库应为非 0 字节'
""",
        "check": """
_c = core.conn('heal'); _c.close()
_tabs = [r[0] for r in sqlite3.connect(os.path.join(HPD, 'heal.db')).execute(
    "SELECT name FROM main.sqlite_master WHERE type='table'")]
assert 'entries' in _tabs, '残库未自愈，仍缺 entries: %s' % _tabs
""",
        # 还原历史缺陷：只按文件大小判断「是否新库」。WAL pragma 会把空库撑到
        # 4096 字节，于是建表失败过的库永远看起来「已初始化」，再无自愈机会。
        "inject": ("src/heropen/core.py",
                   """    c = _connect(agent)
    try:
        healthy = c.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='entries'"
        ).fetchone() is not None
    except Exception:
        healthy = False
    if healthy:
        return c
    c.close()
    init_db(agent)
    return _connect(agent)""",
                   """    c = _connect(agent)
    if os.path.getsize(path) > 0:
        return c
    c.close()
    init_db(agent)
    return _connect(agent)"""),
    },

    "init-db-no-attach": {
        "desc": "init_db 期间建立的连接不得挂载 shared（否则未限定探测会解析到别人的表）",
        "setup": SETUP_SHARED + SETUP_CONFIG + "_write_cfg(['probe'])\n",
        "check": """
_seen = []
_orig = core._connect
def _spy(agent=None, *, attach_shared=True):
    _seen.append(attach_shared)
    return _orig(agent, attach_shared=attach_shared)
core._connect = _spy
try:
    core.init_db('probe')
finally:
    core._connect = _orig
assert _seen, 'init_db 未建立任何连接'
assert False in _seen, 'init_db 期间挂载了 shared（attach_shared 全为 True）: %s' % _seen
""",
        "inject": ("src/heropen/core.py",
                   "    c = _connect(agent, attach_shared=False)",
                   "    c = _connect(agent)"),
    },

    "health-unknown-not-degraded": {
        "desc": "注册状态无法判定（如缺 PyYAML）时 health 必须 ok；只有确认未注册或库断连才 degraded",
        "setup": SETUP_CONFIG + "_write_cfg(['htri'])\n",
        "needs_mcp": True,
        "check": """
import heropen.mcp_server as ms
_srv = ms.create_mcp_server()
_fn = _srv._tool_manager._tools['health'].fn
def _call():
    _r = _fn()
    return json.loads(_r) if isinstance(_r, str) else _r
_orig = ms._mcp_registered
try:
    ms._mcp_registered = lambda _d: None
    _h = _call()
    assert _h['status'] == 'ok', '无法判定被判 degraded: %s' % _h['status']
    assert _h['mcp_config_found'] is None, _h['mcp_config_found']
    ms._mcp_registered = lambda _d: True
    assert _call()['status'] == 'ok'
    ms._mcp_registered = lambda _d: False
    assert _call()['status'] == 'degraded', '确认未注册却未 degraded'
finally:
    ms._mcp_registered = _orig
""",
        "inject": ("src/heropen/mcp_server.py",
                   "        _healthy = db_connected and mcp_config_found is not False",
                   "        _healthy = db_connected and mcp_config_found"),
    },

    "doctor-config-missing-nonfatal": {
        "desc": "缺 agent-config.json 时 diagnose 只告警，必须继续跑完后续检查段",
        "setup": "",
        "check": """
import heropen.cli_commands as cli
from pathlib import Path
_fake = os.path.join(HPD, 'fakehome')
os.makedirs(_fake, exist_ok=True)
_orig = Path.home
Path.home = lambda: Path(_fake)
_buf = io.StringIO()
try:
    with contextlib.redirect_stdout(_buf):
        cli.cmd_diagnose([])
except SystemExit:
    pass
finally:
    Path.home = _orig
_out = _buf.getvalue()
assert '不致命' in _out, '未命中 warning 分支:\\n' + _out
assert ('工具连通性' in _out and '版本检查' in _out), '后续检查段被中止:\\n' + _out
""",
        "inject": ("src/heropen/cli_commands.py",
                   '        _warn("如需多 agent 隔离，运行 heropen install 或 heropen auto-setup 生成配置")',
                   '        _warn("如需多 agent 隔离，运行 heropen install 或 heropen auto-setup 生成配置")\n        return'),
    },

    "vector-floor-configurable": {
        "desc": "向量相关度下限可用 HEROPEN_VECTOR_MIN_SIM 覆盖，非法值回退默认、越界钳制",
        "setup": "",
        "check": """
os.environ['HEROPEN_VECTOR_MIN_SIM'] = '0.35'
assert core.get_vector_min_sim() == 0.35, core.get_vector_min_sim()
os.environ['HEROPEN_VECTOR_MIN_SIM'] = 'bogus'
assert core.get_vector_min_sim() == 0.5, '非法值未回退默认'
os.environ['HEROPEN_VECTOR_MIN_SIM'] = '9'
assert core.get_vector_min_sim() == 1.0, '越界未钳制'
os.environ.pop('HEROPEN_VECTOR_MIN_SIM')
assert core.get_vector_min_sim() == 0.5
_src = open(os.path.join(SRC, 'heropen', 'core.py'), encoding='utf-8').read()
assert 'floor = get_vector_min_sim()' in _src, 'search_vector 未消费可配置阈值'
""",
        "inject": ("src/heropen/core.py",
                   "    return min(max(val, 0.0), 1.0)",
                   "    return 0.5"),
    },

    "vector-fallback-surfaced": {
        "desc": "向量因低于阈值回退时，search_memory 必须上报被丢弃的最高分（否则看起来像向量坏了）",
        "setup": SETUP_CONFIG + "_write_cfg(['fb'])\n",
        "needs_mcp": True,
        "check": """
import heropen.mcp_server as ms
_srv = ms.create_mcp_server()
_fn = _srv._tool_manager._tools['search_memory'].fn
def _fake_sv(q, limit=5, agent=None):
    core.LAST_VECTOR_FALLBACK = {'best_sim': 0.42, 'floor': 0.5}
    return None
_orig = ms.search_vector
ms.search_vector = _fake_sv
try:
    _r = _fn(query='任意查询', agent='fb')
    _r = json.loads(_r) if isinstance(_r, str) else _r
finally:
    ms.search_vector = _orig
assert 'vector_fallback' in _r, '未上报回退诊断，keys=%s' % list(_r.keys())
assert _r['vector_fallback']['best_sim'] == 0.42, _r['vector_fallback']
assert _r['method'] in ('fts', 'like'), _r['method']
""",
        "inject": ("src/heropen/mcp_server.py",
                   '            payload["vector_fallback"] = vector_fallback',
                   '            payload["vector_fallback_hidden"] = vector_fallback'),
    },

    "agents-fresh-read-consistent": {
        "desc": "进程启动后新建的 agent-config，health 必须立刻看到（与 list/search 结论一致）",
        "setup": SETUP_CONFIG + "_write_cfg(['late_a', 'late_b', 'late_c'])\n",
        "needs_mcp": True,
        "check": """
assert core.AGENTS == {}, 'AGENTS 快照应仍为启动时的空值，实为 %s' % dict(core.AGENTS)
assert sorted(core.current_agents()) == ['late_a', 'late_b', 'late_c'], core.current_agents()
import heropen.mcp_server as ms
_srv = ms.create_mcp_server()
_h = _srv._tool_manager._tools['health'].fn()
_h = json.loads(_h) if isinstance(_h, str) else _h
assert sorted(_h['agents'].keys()) == ['late_a', 'late_b', 'late_c'], \\
    'health 与配置不一致: %s' % sorted(_h['agents'].keys())
""",
        "inject": ("src/heropen/mcp_server.py",
                   "        agents = [bound_agent] if bound_agent is not None else list(_heropen_core.current_agents().keys())",
                   "        agents = [bound_agent] if bound_agent is not None else list(AGENTS.keys())"),
    },

    "arm64-hint-accurate": {
        "desc": "Apple Silicon 缺 onnxruntime 时不得说「无匹配 wheel」（官方有 wheel）；Linux aarch64 仍保留该提示",
        "setup": "",
        "check": """
import builtins
_ri = builtins.__import__
def _no_onnx(n, *a, **k):
    if n == 'onnxruntime':
        raise ImportError('No module named onnxruntime')
    return _ri(n, *a, **k)
_ot, _oa = core._platform_triplet, core.is_arm64
try:
    core._platform_triplet = lambda: ('Darwin', 'arm64')
    core.is_arm64 = lambda: True
    builtins.__import__ = _no_onnx
    _d = core.get_embedding_status(refresh=True)['detail']
    assert 'Apple Silicon' in _d, _d
    assert '无匹配 wheel' not in _d, 'Apple Silicon 仍被误导: ' + _d
    core._platform_triplet = lambda: ('Linux', 'aarch64')
    builtins.__import__ = _no_onnx
    _d2 = core.get_embedding_status(refresh=True)['detail']
    assert '无匹配 wheel' in _d2, 'Linux aarch64 提示丢失: ' + _d2
finally:
    builtins.__import__ = _ri
    core._platform_triplet, core.is_arm64 = _ot, _oa
    core._EMBEDDING_STATUS_CACHE = None
""",
        "inject": ("src/heropen/core.py",
                   '            if _sys == "Darwin":',
                   '            if False:'),
    },

    "pyyaml-base-dependency": {
        "desc": "PyYAML 必须在基础依赖里（否则裸装会静默跳过 YAML 客户端配置，如 Hermes）",
        "setup": "",
        "check": """
_txt = open(os.path.join(REPO, 'pyproject.toml'), encoding='utf-8').read()
_m = re.search(r'^dependencies = \\[(.*?)^\\]', _txt, re.S | re.M)
assert _m, 'pyproject 未找到 dependencies 数组'
_block = _m.group(1)
# 只认真实的依赖声明行（引号包裹的 "PyYAML..."），忽略注释里的 PyYAML 字样——
# 否则删掉声明行后，解释性注释仍会让 'PyYAML' in block 命中，门禁就没牙了。
_decls = [ln.strip() for ln in _block.splitlines()
          if ln.strip().startswith('"') or ln.strip().startswith("'")]
_yaml = [d for d in _decls if re.search(r'pyyaml', d, re.I)]
assert _yaml, '基础依赖缺 PyYAML 声明行:\\n' + _block
# 不得被平台条件限制（PyYAML 全平台都需要，用于读 Hermes 等 YAML 客户端配置）
assert not any('platform_system' in d for d in _yaml), \\
    'PyYAML 被平台条件限制，非全平台基础依赖: %s' % _yaml
""",
        "inject": ("pyproject.toml", '    "PyYAML>=6.0",\n', ''),
    },

    "yaml-skip-visible": {
        "desc": "YAML 客户端因缺依赖被跳过时必须可见，且不得误报「没找到任何配置文件」",
        "setup": "",
        "check": """
import heropen.auto_mcp as am
_src = open(os.path.join(SRC, 'heropen', 'auto_mcp.py'), encoding='utf-8').read()
assert 'result["skipped"].append' in _src, 'skipped 未被记录'
_res = {'configured': [], 'already_had': [], 'errors': [],
        'skipped': ['Hermes (/home/u/.hermes/config.yaml)']}
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    am.print_setup_summary(_res)
_out = _buf.getvalue()
assert 'No supported agent config files found' not in _out, '仍误报未找到配置文件:\\n' + _out
assert 'PyYAML' in _out, '未给出可操作的修复提示:\\n' + _out
""",
        "inject": ("src/heropen/auto_mcp.py",
                   '    total = len(result["configured"]) + len(result["already_had"]) + len(result["errors"]) + len(result.get("skipped", []))',
                   '    total = len(result["configured"]) + len(result["already_had"]) + len(result["errors"])'),
    },
}

NEEDS_MCP = [k for k, v in RULES.items() if v.get("needs_mcp")]


def _run_rule(name: str, tmpdir: str) -> tuple[bool, str]:
    """在独立子进程 + 独立 HERO_PEN_DIR 里跑一条规则。返回 (通过?, 输出)。"""
    rule = RULES[name]
    hpd = os.path.join(tmpdir, name)
    os.makedirs(hpd, exist_ok=True)
    env = dict(os.environ)
    env["HERO_PEN_DIR"] = hpd
    env["HEROPEN_GATE_SRC"] = SRC
    env["HEROPEN_GATE_REPO"] = REPO
    env.pop("HEROPEN_AGENT", None)
    env["HEROPEN_NO_UPDATE_CHECK"] = "1"
    code = PREAMBLE + "import re\n" + rule.get("setup", "") + rule["check"]
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, cwd=REPO, timeout=300)
    out = (p.stdout or "") + (p.stderr or "")
    # 过滤安装钩子噪音，只留有信息量的行
    noise = re.compile(
        r"自动配置|自配置|already had|Restart your agent|SSE mode|setup\.log|"
        r"工具已注册|日志文件|配置成功|heropen 配置|No supported agent config|"
        r"Manual setup|^\s*[·•]\s|^\s*\{\"(command|type)\"|DeprecationWarning|"
        r"^\s*r = asyncio|^\s*$")
    keep = [ln for ln in out.splitlines() if not noise.search(ln)]
    return p.returncode == 0, "\n".join(keep[-14:])


def _inject_specs(rule: dict) -> list[tuple[str, str, str]]:
    """Normalize the inject spec to a list of (rel, old, new).

    A rule may inject a single point (tuple) or several (list of tuples) — the
    latter is needed when the real historical defect was the *simultaneous*
    absence of two independent guards, so injecting only one leaves the other
    protecting the path and the gate has no teeth.
    """
    spec = rule["inject"]
    if isinstance(spec, tuple):
        spec = [spec]
    out = []
    for item in spec:
        assert len(item) == 3, f"inject 项必须是 (rel, old, new) 三元组: {item!r}"
        out.append((item[0], item[1], item[2]))
    return out


def _apply_inject(rule: dict) -> dict[str, bytes]:
    """Inject the defect(s); return {path: original_bytes} snapshot for restore.

    Anchors must each match exactly once. Every touched file's original bytes
    are snapshotted BEFORE any write, so restore is a pure byte copy-back and
    can never be confused by an empty/repeated `new` string (a str.replace('',
    x) would otherwise explode the file — this happened once with pyproject).
    """
    specs = _inject_specs(rule)
    paths = list(dict.fromkeys(os.path.join(REPO, rel.replace("/", os.sep))
                               for rel, _o, _n in specs))
    snapshot = {p: open(p, "rb").read() for p in paths}

    # Validate all anchors first — do not write if any anchor is ambiguous.
    # Line endings are normalized: pyproject.toml in this repo is CRLF, so an
    # anchor written with \n would match 0 times and the rule would silently
    # lose its teeth. Try the anchor as-is, then its CRLF form (and normalize
    # the replacement text the same way so the file's endings stay consistent).
    resolved = []  # (path, old_as_in_file, new_as_in_file)
    for rel, old, new in specs:
        path = os.path.join(REPO, rel.replace("/", os.sep))
        txt = snapshot[path].decode("utf-8")
        if txt.count(old) == 1:
            resolved.append((path, old, new))
            continue
        old_crlf = old.replace("\n", "\r\n")
        if old_crlf != old and txt.count(old_crlf) == 1:
            resolved.append((path, old_crlf, new.replace("\n", "\r\n")))
            continue
        raise AssertionError(
            f"注入锚点在 {rel} 出现 {txt.count(old)} 次（要求恰好 1 次；"
            f"已尝试 LF/CRLF 两种行尾）")

    # Apply (recompute from the snapshot so multi-edits to one file stack)
    for path in paths:
        txt = snapshot[path].decode("utf-8")
        for p, old, new in resolved:
            if p == path:
                txt = txt.replace(old, new)
        open(path, "wb").write(txt.encode("utf-8"))
    return snapshot


def _restore(snapshot: dict[str, bytes]) -> None:
    """Byte-for-byte restore of every snapshotted file."""
    for path, raw in snapshot.items():
        open(path, "wb").write(raw)


def main() -> int:
    args = sys.argv[1:]
    if "--list" in args:
        for i, (k, v) in enumerate(RULES.items(), 1):
            specs = _inject_specs(v)
            print(f"{i:2d}. {k}\n    {v['desc']}")
            for _rel, _o, _n in specs:
                print(f"    inject: {_rel}")
        return 0

    # 前置：不做静默跳过——缺 mcp 就明确失败
    try:
        subprocess.run([sys.executable, "-c", "import mcp"], check=True,
                       capture_output=True, timeout=60)
    except Exception:
        print("❌ 当前解释器无法 import mcp，%d 条规则（%s）无法执行。"
              % (len(NEEDS_MCP), ", ".join(NEEDS_MCP)))
        print("   请用装了 heropen 运行依赖的解释器跑本门禁（CI 里 pip install 后即可）。")
        return 2

    tmp = tempfile.mkdtemp(prefix="heropen-gate-")
    bidir = "--bidir" in args

    if not bidir:
        print("== heropen 回归门禁（--once）==")
        fails = 0
        for k in RULES:
            ok, out = _run_rule(k, tmp)
            print(f"  {'✅' if ok else '❌'} {k}")
            if not ok:
                fails += 1
                print("     " + out.replace("\n", "\n     ")[:900])
        print(f"\n{'== 全部通过 ==' if fails == 0 else f'== {fails} 条失败 =='}")
        return 1 if fails else 0

    print("== heropen 回归门禁（--bidir 双向验证）==")
    fails = 0
    for k, rule in RULES.items():
        ok0, out0 = _run_rule(k, tmp)
        if not ok0:
            print(f"  ❌ {k}: 注入前就失败（实现或检查有问题）")
            print("     " + out0.replace("\n", "\n     ")[:700])
            fails += 1
            continue
        try:
            snapshot = _apply_inject(rule)
        except AssertionError as e:
            print(f"  ❌ {k}: {e}")
            fails += 1
            continue
        try:
            ok1, out1 = _run_rule(k, tmp + "-inj")
        finally:
            _restore(snapshot)
        ok2, out2 = _run_rule(k, tmp + "-res")
        if ok1:
            print(f"  ❌ {k}: 注入缺陷后仍然通过 → 这条门禁没有牙齿")
            fails += 1
        elif not ok2:
            print(f"  ❌ {k}: 还原后未恢复通过")
            print("     " + out2.replace("\n", "\n     ")[:500])
            fails += 1
        else:
            why = [ln for ln in out1.splitlines() if "AssertionError" in ln or "assert" in ln.lower()]
            print(f"  ✅ {k}: 注入→失败(归因: {(why[-1].strip()[:80] if why else '见输出')}) →还原→通过")
    print(f"\n{'== 双向验证全部通过 ==' if fails == 0 else f'== {fails} 条不合格 =='}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
