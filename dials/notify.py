"""Desktop notifications via gdbus.

gdbus rather than notify-send because libnotify-bin is not installed and the
project adds no apt packages; gdbus ships with glib and probe 04 confirmed the
call works. Best-effort by design: a failed notification must never break a
keypress path.
"""
from __future__ import annotations

import subprocess

APP_NAME = "dials"
DEFAULT_ICON = "input-keyboard"

_DEST = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"
_METHOD = "org.freedesktop.Notifications.Notify"


def notify(summary: str, body: str = "", timeout_ms: int = 2500,
           icon: str = DEFAULT_ICON, runner=subprocess.run) -> bool:
    """Show a transient notification. Returns True on success, never raises."""
    cmd = [
        "gdbus", "call", "--session",
        "--dest", _DEST,
        "--object-path", _PATH,
        "--method", _METHOD,
        APP_NAME, "0", icon,
        summary, body,
        "[]", "{}", str(timeout_ms),
    ]
    try:
        result = runner(cmd, capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    return getattr(result, "returncode", 1) == 0
