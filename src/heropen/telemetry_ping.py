"""heropen.telemetry_ping — Lightweight anonymous telemetry.

Fires a one-shot HTTPS POST to ksmn.cc to count active users.
Non-blocking (subprocess, no wait). Safe to call on every CLI invocation.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

_TELEMETRY_URL = "https://ksmn.cc/api/level/ping"
_HPD = os.environ.get("HERO_PEN_DIR", "")
HERO_PEN_DIR = _HPD if _HPD else os.path.expanduser("~/.heropen")
_ANON_ID_FILE = os.path.join(HERO_PEN_DIR, ".anon_id")
_VERSION_FILE = os.path.join(HERO_PEN_DIR, ".version")


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
    """Get the installed version, cached."""
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


def fire_ping() -> None:
    """Fire a single anonymous ping. Non-blocking subprocess, never raises."""
    pid = _ensure_anon_id()
    ver = _get_version()
    if not pid:
        return

    # Use \n for proper multiline Python — single-line try: only accepts
    # one statement, but we need multiple (Request, urlopen).
    code = (
        "import urllib.request, json\n"
        "try:\n"
        "  req = urllib.request.Request(\n"
        f"    '{_TELEMETRY_URL}',\n"
        "    data=json.dumps({\n"
        f"      'user_id': '{pid}', 'version': '{ver}'\n"
        "    }).encode(),\n"
        "    headers={'Content-Type': 'application/json'},\n"
        "  )\n"
        "  urllib.request.urlopen(req, timeout=3)\n"
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
