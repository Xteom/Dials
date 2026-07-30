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
        # 1s, not 5s: this call is SYNCHRONOUS and the daemon is single
        # threaded, so the whole event loop stalls here for a gdbus round trip -
        # or for the full timeout if the notification daemon is wedged. During
        # that window no Dial fires and no deadline expires, so the timeout is
        # the worst-case freeze a cosmetic notification can impose.
        result = runner(cmd, capture_output=True, text=True, timeout=1.0)
    except Exception:
        return False
    return getattr(result, "returncode", 1) == 0
