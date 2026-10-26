"""后台定时摄取本机会话记录，让「主动读取」不必手动调用。

设计取舍：
- 只做一件事：按固定间隔调用 turn_ingest，不做打断、不做并发写同一个库。
- 默认关闭自动写入，先以「只读试算」运行——自动路径一旦写错就是脏记忆，
  所以第一次上线要先让人能看见它打算做什么。
- 任何异常都不得影响面板进程：定时线程内的错误只记录，不抛出、不退出。
- 结果写入状态文件（原子替换），工作台只读该文件即可展示，不重复计算。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from heropen import turn_ingest
from heropen.core import HERO_PEN_DIR

DATA_DIR = Path(HERO_PEN_DIR).expanduser().absolute()
STATE_FILE = DATA_DIR / "_tray_state.json"
RESULT_FILE = DATA_DIR / "_turn_ingest.json"

DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 30
DEFAULT_SOURCE = "all"
# Every source whose transcripts we know how to read. "all" runs each in turn.
SOURCES = ("zcode", "workbuddy")
RESULT_KEEP = 20


def _readers() -> dict:
    """Source name -> (callable, default directory), resolved lazily."""
    return {
        "zcode": (turn_ingest.ingest_zcode, turn_ingest.default_zcode_rollout()),
        "workbuddy": (turn_ingest.ingest_workbuddy,
                      turn_ingest.default_workbuddy_projects()),
    }


def _read_state() -> dict:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def settings() -> dict:
    """Current auto-ingest settings, resolved to safe defaults.

    Auto-apply defaults to False on purpose: an unattended background writer
    can create memory the user never reviewed, so opt-in is required.
    """
    state = _read_state()
    section = state.get("turn_ingest")
    section = section if isinstance(section, dict) else {}
    enabled = section.get("enabled")
    apply = section.get("apply")
    interval = section.get("interval_seconds")
    if not isinstance(interval, int) or interval < MIN_INTERVAL_SECONDS:
        interval = DEFAULT_INTERVAL_SECONDS
    source = section.get("source")
    if not isinstance(source, str) or not source:
        source = DEFAULT_SOURCE
    elif source != "all" and source not in SOURCES:
        source = DEFAULT_SOURCE
    return {
        "enabled": enabled is True,
        "apply": apply is True,
        "interval_seconds": interval,
        "source": source,
        "configured": "turn_ingest" in state,
    }


def set_settings(*, enabled: bool | None = None, apply: bool | None = None,
                 interval_seconds: int | None = None, source: str | None = None) -> dict:
    """Persist auto-ingest settings; only provided fields change."""
    current = settings()
    state = _read_state()
    section = state.get("turn_ingest")
    section = dict(section) if isinstance(section, dict) else {}
    if enabled is not None:
        section["enabled"] = bool(enabled)
    if apply is not None:
        section["apply"] = bool(apply)
    if interval_seconds is not None:
        if not isinstance(interval_seconds, int) or interval_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"间隔不得小于 {MIN_INTERVAL_SECONDS} 秒")
        section["interval_seconds"] = interval_seconds
    if source is not None:
        if not isinstance(source, str) or not source:
            raise ValueError("数据来源不得为空")
        if source != "all" and source not in SOURCES:
            raise ValueError(f"暂不支持的数据来源：{source}")
        section["source"] = source
    state["turn_ingest"] = section
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, STATE_FILE)
    del current
    return settings()


def read_result() -> dict:
    """Last ingest run summary, for the workbench to display."""
    try:
        value = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_result(payload: dict) -> None:
    history = read_result().get("history")
    history = history if isinstance(history, list) else []
    history = (history + [payload])[-RESULT_KEEP:]
    record = {**payload, "history": history}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, RESULT_FILE)


def run_once(*, apply: bool | None = None, source: str | None = None,
             rollout_dir: Path | None = None) -> dict:
    """Run one ingest pass and record the outcome. Never raises on ingest error.

    source may name one client or be "all" to read every known client; results
    are merged so the workbench shows one row. rollout_dir overrides where
    transcripts are read from (single-source only); production leaves it unset.
    """
    current = settings()
    use_apply = current["apply"] if apply is None else bool(apply)
    use_source = source or current["source"]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        readers = _readers()
        if use_source == "all":
            names = list(SOURCES)
        elif use_source in readers:
            names = [use_source]
        else:
            raise ValueError(f"暂不支持的数据来源：{use_source}")
        totals = {"considered": 0, "submitted": 0, "skipped_incomplete": 0,
                  "skipped_invalid": 0, "already_stored": 0, "deferred": 0,
                  "duplicate": 0, "saved": 0, "buffered": 0, "failed": 0,
                  "conflicts": 0}
        per_source: dict = {}
        errors: list = []
        for name in names:
            callable_, directory = readers[name]
            if rollout_dir is not None and len(names) == 1:
                directory = rollout_dir
            try:
                summary = callable_(apply=use_apply, **({"rollout_dir": directory}
                                    if name == "zcode" else {"projects_dir": directory}))
            except Exception as exc:  # noqa: BLE001 - one bad source must not stop others
                per_source[name] = {"error": f"{type(exc).__name__}: {exc}"}
                errors.append({"source": name, "reason": f"{type(exc).__name__}: {exc}",
                               "kind": "source"})
                continue
            for key in totals:
                totals[key] += int(summary.get(key) or 0)
            for item in summary.get("errors") or []:
                errors.append({"source": name, **item})
            per_source[name] = {key: summary.get(key) for key in
                                ("considered", "submitted", "skipped_incomplete",
                                 "skipped_invalid", "already_stored", "deferred",
                                 "duplicate", "saved", "buffered", "failed",
                                 "conflicts")}
        # A source can come back without raising yet still have failed every
        # submission or rejected turns; those must not read as success. Count
        # per-turn failures and refusals, not just transport-level exceptions.
        failed_sources = [n for n, v in per_source.items() if v.get("error")]
        problem_sources = [
            n for n, v in per_source.items()
            if not v.get("error") and (
                int(v.get("failed") or 0) > 0 or int(v.get("skipped_invalid") or 0) > 0)
        ]
        if failed_sources and len(failed_sources) == len(names):
            status = "failed"
        elif failed_sources:
            status = "partial"
        elif problem_sources and sum(
                int(v.get("submitted") or 0) for v in per_source.values()) == 0:
            # Nothing succeeded and problems were reported: not a success.
            status = "failed"
        elif problem_sources:
            status = "partial"
        else:
            status = "ok"
        payload = {
            "ok": status == "ok", "status": status, "started_at": started,
            "source": use_source, "apply": use_apply, "per_source": per_source,
            "errors": errors, "failed_sources": failed_sources,
            "problem_sources": problem_sources,
            **totals,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": ("；".join(e["reason"] for e in errors)
                      if status != "ok" else ""),
        }
    except Exception as exc:  # noqa: BLE001 - a bad run must not kill the panel
        payload = {
            "ok": False, "status": "failed", "started_at": started,
            "source": use_source, "apply": use_apply, "considered": 0,
            "submitted": 0, "skipped_incomplete": 0, "skipped_invalid": 0,
            "already_stored": 0, "deferred": 0, "duplicate": 0, "saved": 0,
            "buffered": 0, "failed": 0, "conflicts": 0, "per_source": {}, "errors": [],
            "failed_sources": [], "problem_sources": [],
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": f"{type(exc).__name__}: {exc}",
        }
    # Remember the last successful run separately so the panel can show it even
    # after later failures.
    previous = read_result()
    if payload.get("ok"):
        payload["last_success_at"] = payload["finished_at"]
    elif previous.get("last_success_at"):
        payload["last_success_at"] = previous["last_success_at"]
    _write_result(payload)
    return payload


class AutoIngest:
    """A single background timer thread; safe to start once per process."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        """Start the timer if enabled and not already running."""
        if self._thread is not None and self._thread.is_alive():
            return False
        if not settings()["enabled"]:
            return False
        self._stop.clear()
        thread = threading.Thread(target=self._loop, name="heropen-turn-ingest",
                                  daemon=True)
        self._thread = thread
        thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        # First pass shortly after startup so a fresh session is picked up
        # without waiting a full interval.
        self._stop.wait(10)
        while not self._stop.is_set():
            try:
                run_once()
            except Exception:  # noqa: BLE001 - run_once already guards; belt and braces
                pass
            self._stop.wait(settings()["interval_seconds"])


_INSTANCE = AutoIngest()


def start_auto() -> bool:
    return _INSTANCE.start()


def stop_auto() -> None:
    _INSTANCE.stop()


def running() -> bool:
    thread = _INSTANCE._thread
    return thread is not None and thread.is_alive()