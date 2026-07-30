"""Optional top-bar indicator.

Answers one question at a glance: are my Dials live right now? With NumLock ON
the keypad types digits and no Dial can fire, so the layer is genuinely inert
and the icon says so.

Gtk.StatusIcon rather than AppIndicator because Guake uses exactly that here
and its icon does appear, so no gir1.2-appindicator3 package is needed.

This is the ONLY process in the project that polls. NumLock cannot be watched
event-driven: the server has XKEYBOARD but python-xlib 0.29 ships no XKB
binding, so XkbSelectEvents is out of reach without ctypes. One led_mask read
was measured at 28.4us, i.e. 0.0028% of one core at 1Hz. Grabbing Num_Lock and
replaying it was rejected because its failure mode is NumLock breaking.
"""
from __future__ import annotations

import sys

LIVE = "live"
DORMANT = "dormant"
PAUSED = "paused"

POLL_INTERVAL_MS = 1000

_NUMLOCK_LED_BIT = 0x2

ICON_NAMES = {
    LIVE: "input-keyboard",
    DORMANT: "input-keyboard-symbolic",
    PAUSED: "action-unavailable-symbolic",
}

TOOLTIPS = {
    LIVE: "Dials live - NumLock is off, the numpad drives your windows",
    DORMANT: "Dials dormant - NumLock is on, the numpad types digits",
    PAUSED: "Dials paused - run `dials resume` to re-enable",
}


def icon_state(running: bool, paused: bool, numlock_is_on: bool) -> str:
    """Pure state selection, so the whole indicator is table-testable."""
    if not running or paused:
        return PAUSED
    return DORMANT if numlock_is_on else LIVE


def numlock_on(display) -> bool:
    """Read NumLock from the keyboard LED mask. Never raises."""
    try:
        return bool(display.get_keyboard_control().led_mask & _NUMLOCK_LED_BIT)
    except Exception:
        return False


def _daemon_running() -> bool:
    import subprocess
    try:
        return subprocess.run(["pgrep", "-x", "dialsd"],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def _is_paused() -> bool:
    from dials.config import state_dir
    return (state_dir() / "paused").exists()


def main() -> int:
    import gi
    # Every gi.repository namespace is pinned explicitly. This machine has
    # both Gtk-3.0/Gdk-3.0 and Gtk-4.0/Gdk-4.0 typelibs installed, and
    # dials/confirm.py already demonstrated the failure mode once: an
    # unpinned import silently resolves to whichever version gi considers
    # "latest", the Gtk 3.0 load then fails to satisfy its Gdk 3.0
    # dependency, and the surrounding except-Exception fallback swallows the
    # ImportError so nothing visibly breaks. Only one GLib typelib (2.0) is
    # installed system-wide, so GLib does not currently have a competing
    # version to be resolved to - but pinning it costs nothing and keeps this
    # module honest with the same rule applied to every import.
    gi.require_version("GLib", "2.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from Xlib import display as xdisplay

    from dials.config import state_dir

    d = xdisplay.Display()
    icon = Gtk.StatusIcon()
    icon.set_visible(True)
    state = {"current": None}

    pidfile = state_dir()
    pidfile.mkdir(parents=True, exist_ok=True)
    (pidfile / "tray.pid").write_text(str(__import__("os").getpid()))

    def refresh():
        new = icon_state(_daemon_running(), _is_paused(), numlock_on(d))
        if new != state["current"]:
            state["current"] = new
            icon.set_from_icon_name(ICON_NAMES[new])
            icon.set_tooltip_text(TOOLTIPS[new])
        return True

    def on_activate(_icon):
        import subprocess
        target = "resume" if _is_paused() else "pause"
        subprocess.run([sys.executable, "-m", "dials.cli", target],
                       capture_output=True)
        refresh()

    icon.connect("activate", on_activate)
    refresh()
    GLib.timeout_add(POLL_INTERVAL_MS, refresh)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
