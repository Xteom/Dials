"""Command-line interface.

Every side effect is injected through `Deps` so each subcommand is testable
without touching the filesystem, the daemon, or X.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from dataclasses import dataclass, field

from dials import icons, keys
from dials.config import (
    ConfigError, config_path, load as load_config, reference_path, state_dir,
)

PAUSE_FLAG = "paused"


# ---- injectable side effects --------------------------------------------

def _pause_get() -> bool:
    return (state_dir() / PAUSE_FLAG).exists()


def _pause_set(value: bool) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    flag = state_dir() / PAUSE_FLAG
    if value:
        flag.touch()
    elif flag.exists():
        flag.unlink()


def _signal_daemon() -> bool:
    """SIGHUP the running daemon so it re-reads config without restarting."""
    import subprocess
    try:
        result = subprocess.run(["pgrep", "-x", "dialsd"],
                                capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.split()]
    except Exception:
        return False
    for pid in pids:
        try:
            os.kill(pid, signal.SIGHUP)
        except Exception:
            return False
    return bool(pids)


def _list_windows():
    from Xlib import display
    from dials.windows import WindowOps
    d = display.Display()
    return WindowOps(d, d.screen().root).list_windows()


def _active_window():
    from Xlib import display
    from dials.windows import WindowOps
    d = display.Display()
    return WindowOps(d, d.screen().root).active_window()


def _window_geometry(wid):
    from Xlib import display
    from dials.windows import WindowOps
    d = display.Display()
    return WindowOps(d, d.screen().root).geometry(wid)


def _monitor_for(rect):
    """Resolve which monitor a pixel rect sits on.

    Keeping the whole resolution behind this seam means cli.py does not import
    daemon's private _monitor_containing, and capture becomes testable with a
    plain fake.
    """
    from Xlib import display
    from dials.daemon import _monitor_containing
    from dials.monitors import MonitorSource
    d = display.Display()
    src = MonitorSource(d, d.screen().root)
    return _monitor_containing(rect, src.monitors(), src.root_rect())


def _upsert(path, dial):
    from dials.configwrite import upsert_dial
    return upsert_dial(path, dial)


def _remove(path, slot):
    from dials.configwrite import remove_dial
    return remove_dial(path, slot)


def _export(live=None, ref=None):
    from dials.configwrite import export_reference
    return export_reference(live, ref)


def _differs(live=None, ref=None):
    from dials.configwrite import reference_differs
    return reference_differs(live, ref)


@dataclass
class Deps:
    load: callable = load_config
    upsert: callable = _upsert
    remove: callable = _remove
    export: callable = _export
    differs: callable = _differs
    pause_get: callable = _pause_get
    pause_set: callable = _pause_set
    signal_daemon: callable = _signal_daemon
    list_windows: callable = _list_windows
    active_window: callable = _active_window
    window_geometry: callable = _window_geometry
    monitor_for: callable = _monitor_for
    out: object = field(default_factory=lambda: sys.stdout)


# ---- subcommands --------------------------------------------------------

def _cmd_list(args, d: Deps) -> int:
    cfg = d.load()
    print(f"{'slot':<6} {'':<2} {'label':<22} {'class':<16} "
          f"{'monitor':<10} {'rect':<26} focus-loss", file=d.out)
    for slot in keys.BINDABLE_SLOTS:
        dial = cfg.dial(slot)
        if dial is None:
            print(f"{slot:<6} {'':<2} {'(unbound)':<22}", file=d.out)
            continue
        glyph = icons.glyph_for(dial.match_class, dial.icon)
        rect = ("[" + ", ".join(f"{v:g}" for v in dial.rect) + "]")
        print(f"{slot:<6} {glyph:<2} {dial.label:<22} {dial.match_class:<16} "
              f"{dial.monitor:<10} {rect:<26} {dial.on_focus_loss}", file=d.out)
    return 0


def _validate_slot(slot: str, d: Deps) -> int | None:
    if slot == keys.ASSIGN_SLOT:
        print(f"'{slot}' is reserved for assign mode", file=d.out)
        return 2
    if not keys.is_bindable(slot):
        print(f"not a bindable slot: {slot!r}", file=d.out)
        return 2
    return None


def _cmd_unbind(args, d: Deps) -> int:
    bad = _validate_slot(args.slot, d)
    if bad:
        return bad
    d.remove(config_path(), args.slot)
    d.signal_daemon()
    print(f"unbound {args.slot}", file=d.out)
    return 0


def _cmd_capture(args, d: Deps) -> int:
    """Save the matched window's current geometry into its Dial."""
    bad = _validate_slot(args.slot, d)
    if bad:
        return bad
    cfg = d.load()
    dial = cfg.dial(args.slot)
    if dial is None:
        print(f"slot {args.slot} is unbound", file=d.out)
        return 2
    from dataclasses import replace
    from dials.assign import derive_rect
    from dials.geometry import Rect
    from dials.windows import choose

    chosen = choose(d.list_windows(), dial.match_class,
                    active_id=d.active_window())
    if chosen is None:
        print(f"no window matching class {dial.match_class!r}", file=d.out)
        return 1
    rect = d.window_geometry(chosen.wid) or Rect(0, 0, 800, 600)
    monitor = d.monitor_for(rect)
    updated = replace(dial, monitor=monitor.name,
                      rect=derive_rect(rect, monitor))
    d.upsert(config_path(), updated)
    d.signal_daemon()
    print(f"captured {args.slot}: {monitor.name} "
          f"{[round(v, 3) for v in updated.rect]}", file=d.out)
    return 0


#: Printed whenever the flag was written but no daemon could be signalled. The
#: flag persists, so a daemon started later picks it up - but a daemon running
#: RIGHT NOW still holds all 32 grabs, and pause is the safety valve for a game
#: or a remote-desktop session. Claiming success there is the one lie this
#: command must never tell.
_UNSIGNALLED = ("could not signal a running dialsd - the flag is written (a "
                "daemon started later will honour it), but any daemon running "
                "now has NOT changed state")


def _cmd_pause(args, d: Deps) -> int:
    d.pause_set(True)
    if not d.signal_daemon():
        print(f"pause flag set, but {_UNSIGNALLED}; a running daemon still "
              f"holds every grab", file=d.out)
        return 1
    print("paused - the numpad now behaves normally in both NumLock states",
          file=d.out)
    return 0


def _cmd_resume(args, d: Deps) -> int:
    d.pause_set(False)
    if not d.signal_daemon():
        print(f"pause flag cleared, but {_UNSIGNALLED}; a running daemon is "
              f"still paused", file=d.out)
        return 1
    print("resumed", file=d.out)
    return 0


def _cmd_reload(args, d: Deps) -> int:
    ok = d.signal_daemon()
    print("reload signalled" if ok else "no running dialsd found", file=d.out)
    return 0 if ok else 1


def _cmd_status(args, d: Deps) -> int:
    try:
        cfg = d.load()
        bound = len(cfg.dials)
        problem = None
    except ConfigError as exc:
        bound, problem = 0, str(exc)
    print(f"state:     {'paused' if d.pause_get() else 'active'}", file=d.out)
    print(f"dials:     {bound} bound of {len(keys.BINDABLE_SLOTS)} slots",
          file=d.out)
    if problem:
        print(f"config:    ERROR {problem}", file=d.out)
    return 0


def _cmd_config(args, d: Deps) -> int:
    if getattr(args, "export", False):
        path = d.export()
        print(f"reference snapshot written: {path}", file=d.out)
        return 0
    print(f"live:      {config_path()}", file=d.out)
    print(f"reference: {reference_path()}", file=d.out)
    drifted = d.differs()
    print(f"snapshot:  {'differs from live' if drifted else 'up to date'}",
          file=d.out)
    return 0


# ---- entry point --------------------------------------------------------

def main(argv=None, deps: Deps | None = None, tui=None) -> int:
    d = deps or Deps()
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(prog="dials",
                                     description="Numpad window Dials")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="show every slot")
    p = sub.add_parser("unbind", help="clear a slot")
    p.add_argument("slot")
    p = sub.add_parser("capture", help="save the window's current geometry")
    p.add_argument("slot")
    sub.add_parser("pause", help="release all grabs")
    sub.add_parser("resume", help="re-install grabs")
    sub.add_parser("reload", help="SIGHUP the daemon")
    sub.add_parser("status", help="daemon and config health")
    cfg = sub.add_parser("config", help="show or export config paths")
    # choices, so `dials config bogus` is an error rather than being silently
    # treated as a bare `dials config`. Absent stays valid: argparse only checks
    # the default against choices when it is a string, and this one is None.
    cfg.add_argument("export", nargs="?", default=None, choices=["export"])

    if not argv:
        if tui is None:
            from dials.tui import run as tui
        return tui(d.load())

    args = parser.parse_args(argv)
    if args.command == "config":
        args.export = (args.export == "export")

    handlers = {
        "list": _cmd_list, "unbind": _cmd_unbind, "capture": _cmd_capture,
        "pause": _cmd_pause, "resume": _cmd_resume, "reload": _cmd_reload,
        "status": _cmd_status, "config": _cmd_config,
    }
    return handlers[args.command](args, d)


if __name__ == "__main__":
    raise SystemExit(main())
