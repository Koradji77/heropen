"""heropen.telemetry_ping — Lightweight anonymous telemetry.

Fires HTTPS POST to ksmn.cc to count active users.
Non-blocking (subprocess, no wait). Safe to call on every CLI invocation.

Actions:
  - install  : first-ever ping from a new anon_id
  - ping     : routine heartbeat (every 60s while MCP server runs)
  - upgrade  : version changed since last ping
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid

_TELEMETRY_URL = "https://ksmn.cc/api/level/ping"
_HPD = os.environ.get("HERO_PEN_DIR", "")
HERO_PEN_DIR = _HPD if _HPD else os.path.expanduser("~/.heropen")
_ANON_ID_FILE = os.path.join(HERO_PEN_DIR, ".anon_id")
_VERSION_FILE = os.path.join(HERO_PEN_DIR, ".version")
_HEARTBEAT_INTERVAL = 60  # seconds


# ─── Utils ──────────────────────────────────────────────────────


def _ensure_anon_id() -> str:
    """Get or generate a persistent anonymous user ID."""
    try:
        if os.path.exists(_ANON_ID_FILE):
            with open(_ANON_ID_FILE) as f:
                aid = f.read().strip()
                if len(aid) >= 8:
                    return aid
        os.makedirs(HERO_PEN_DIR, exist_ok=True)
        aid = uuid.uuid4().hex[:16]
        with open(_ANON_ID_FILE, "w") as f:
            f.write(aid)
        return aid
    except Exception:
        return ""


def _get_version() -> str:
    """Get the installed version, caching it to .version."""
    try:
        if os.path.exists(_VERSION_FILE):
            with open(_VERSION_FILE) as f:
                return f.read().strip()
        from heropen import __version__
        try:
            os.makedirs(HERO_PEN_DIR, exist_ok=True)
            with open(_VERSION_FILE, "w") as f:
                f.write(__version__)
        except Exception:
            pass
        return __version__
    except Exception:
        return "unknown"


def _detect_action(prev_ver: str, current_ver: str) -> str:
    """Determine action based on version change.

    - If .version file did not exist before → 'install'
    - If version changed (e.g. 1.7.28 → 1.7.29) → 'upgrade'
    - Otherwise → 'ping'
    """
    if not prev_ver:
        return "install"
    if prev_ver != current_ver:
        return "upgrade"
    return "ping"


# ─── Send ───────────────────────────────────────────────────────


def _build_payload(user_id: str, version: str, action: str = "ping", new_version: str = "") -> dict:
    """Build the JSON payload to POST.

    For upgrade action, includes new_version field so the server
    knows both old and new versions.
    """
    payload: dict = {
        "user_id": user_id,
        "version": version,
        "action": action,
    }
    if action == "upgrade" and new_version:
        payload["new_version"] = new_version
    return payload


def _fire_once(user_id: str, version: str, action: str = "ping", new_version: str = "") -> None:
    """Fire a single anonymous ping. Subprocess, never raises."""
    if not user_id:
        return

    payload = _build_payload(user_id, version, action, new_version)

    code = (
        "import urllib.request, json\n"
        "try:\n"
        f"  import urllib.request as _ur\n"
        f"  req = _ur.Request(\n"
        f"    '{_TELEMETRY_URL}',\n"
        f"    data=json.dumps({json.dumps(payload)}).encode(),\n"
        f"    headers={{'Content-Type': 'application/json'}},\n"
        f"  )\n"
        f"  _ur.urlopen(req, timeout=3)\n"
        "except:\n"
        "  pass\n"
    )
    try:
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ─── Public API ─────────────────────────────────────────────────


def fire_ping() -> None:
    """Fire a single anonymous ping. Detects action automatically."""
    pid = _ensure_anon_id()
    current_ver = _get_version()
    if not pid:
        return

    # Read previous version from cached file to detect upgrade
    prev_ver = ""
    if os.path.exists(_VERSION_FILE):
        try:
            with open(_VERSION_FILE) as f:
                prev_ver = f.read().strip()
        except Exception:
            pass

    action = _detect_action(prev_ver, current_ver)

    # If this is an upgrade, the payload carries old version in 'version'
    # and new_version in 'new_version'. For install/ping, version is current.
    ver_for_payload = current_ver if action != "upgrade" else prev_ver
    new_ver_for_payload = current_ver if action == "upgrade" else ""
    _fire_once(pid, ver_for_payload, action, new_ver_for_payload)

    # Update cached version file after upgrade
    if action == "upgrade":
        try:
            os.makedirs(HERO_PEN_DIR, exist_ok=True)
            with open(_VERSION_FILE, "w") as f:
                f.write(current_ver)
        except Exception:
            pass


def start_heartbeat() -> None:
    """Start a daemon thread that fires a ping every 60 seconds.

    This gives the server a real-time "online now" signal.
    The thread is daemon=True so it dies when the main process exits.
    Network errors are swallowed — they never affect the main process.
    """
    pid = _ensure_anon_id()
    ver = _get_version()
    if not pid:
        return

    def _loop():
        # Wait a moment for the main server to fully start
        time.sleep(5)
        while True:
            try:
                _fire_once(pid, ver, action="ping")
            except Exception:
                pass  # network failure ≠ MCP server failure
            for _ in range(_HEARTBEAT_INTERVAL):
                time.sleep(1)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
