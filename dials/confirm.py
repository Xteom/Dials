"""Modal confirmation for a destructive Dial overwrite.

A dialog rather than a notification because reassigning an occupied slot
destroys an existing Dial: the user must be able to SEE what is about to be lost
and must be able to refuse. A notification can do neither.

Because this is a real focused window it handles its own keys - Return, Escape
and BackSpace - with no global grabs at all. That is why this flow and the
launch confirmation deliberately use different mechanisms.

Runs as its own process (`dials-confirm`) so GTK never enters the daemon.
Exit status 0 means "replace", 1 means "cancel".
"""
from __future__ import annotations

import json
import sys


def parse_payload(text: str) -> dict:
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ValueError(f"malformed payload: {exc}") from None
    for key in ("slot", "existing", "incoming"):
        if key not in data:
            raise ValueError(f"payload missing {key!r}")
    return data


def format_side(side: dict) -> str:
    """One side of the comparison, as three aligned lines."""
    rect = side.get("rect") or [0.0, 0.0, 1.0, 1.0]
    try:
        x, y, w, h = (float(v) for v in rect)
    except Exception:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    return (
        f"{side.get('label', '?')}\n"
        f"class  {side.get('match_class', '?')}\n"
        f"{side.get('monitor', '?')}   "
        f"{x * 100:.0f},{y * 100:.0f}   {w * 100:.0f}% x {h * 100:.0f}%"
    )


def _run_dialog(data: dict) -> int:
    import gi
    gi.require_version("Gtk", "3.0")
    # Gdk's version is independent of Gtk's: pinning only "Gtk" leaves Gdk
    # unpinned, and on a system where GTK4 is also installed gi resolves the
    # unpinned Gdk to whatever is "newest" (observed: 4.0 here). Gtk 3.0 then
    # depends on Gdk 3.0, which is already unavailable, and the import raises
    # - meaning the dialog would never appear, main()'s except-Exception
    # fallback would silently refuse every overwrite, and this is exactly the
    # kind of machine (GTK3 *and* GTK4 both present) this daemon targets.
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    slot = data["slot"]
    dialog = Gtk.Dialog(title=f"Dial {slot} is already assigned")
    dialog.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
    dialog.set_keep_above(True)
    dialog.set_border_width(16)

    cancel = dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
    replace = dialog.add_button("Replace", Gtk.ResponseType.OK)
    replace.get_style_context().add_class("destructive-action")
    dialog.set_default_response(Gtk.ResponseType.OK)

    box = dialog.get_content_area()
    box.set_spacing(12)
    grid = Gtk.Grid(column_spacing=18, row_spacing=6)
    for row, (heading, side) in enumerate((
        ("Currently:", data["existing"]),
        ("Replace with:", data["incoming"]),
    )):
        label = Gtk.Label(label=heading, xalign=0.0)
        label.get_style_context().add_class("dim-label")
        body = Gtk.Label(label=format_side(side), xalign=0.0)
        body.set_selectable(False)
        grid.attach(label, 0, row, 1, 1)
        grid.attach(body, 1, row, 1, 1)
    box.add(grid)

    hint = Gtk.Label(label="Enter = replace      Esc / Backspace = cancel",
                     xalign=0.0)
    hint.get_style_context().add_class("dim-label")
    box.add(hint)

    def on_key(_widget, event):
        # Backspace is unconventional but was asked for, and inside a focused
        # dialog supporting both cancels costs nothing.
        if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_BackSpace):
            dialog.response(Gtk.ResponseType.CANCEL)
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            dialog.response(Gtk.ResponseType.OK)
            return True
        return False

    dialog.connect("key-press-event", on_key)
    dialog.show_all()
    dialog.present()
    response = dialog.run()
    dialog.destroy()
    return 0 if response == Gtk.ResponseType.OK else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: dials-confirm '<json payload>'", file=sys.stderr)
        return 2
    try:
        data = parse_payload(argv[0])
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        status = _run_dialog(data)
    except Exception as exc:
        # No GTK or no display: refuse the overwrite rather than destroying a
        # Dial silently.
        from dials.notify import notify
        notify("Dial not replaced",
               f"Slot {data['slot']} still holds "
               f"{data['existing'].get('label', '?')} ({exc})")
        return 1

    if status == 0:
        from dials.config import Dial, config_path, load
        from dials.configwrite import upsert_dial
        inc = data["incoming"]
        cfg = load()
        dial = Dial(
            slot=data["slot"],
            label=inc.get("label") or inc["match_class"],
            match_class=inc["match_class"],
            # The incoming side is always a fresh capture (see
            # AssignMode.resolve in dials/assign.py, the only producer of an
            # OverwriteRequest), which never carries a launch command of its
            # own. Inheriting the EXISTING dial's launch here would attach the
            # dial being destroyed's launch command to the new match_class -
            # e.g. replacing a Spotify dial with Slack would keep launching
            # Spotify's command for a binding that now matches Slack windows.
            launch=None,
            icon="",
            monitor=inc.get("monitor", cfg.defaults.monitor),
            rect=tuple(inc.get("rect") or cfg.defaults.rect),
            on_focus_loss=cfg.defaults.on_focus_loss,
            pin_geometry=cfg.defaults.pin_geometry,
        )
        upsert_dial(config_path(), dial)
        from dials.cli import _signal_daemon
        _signal_daemon()
        from dials.notify import notify
        notify("Dial replaced", f"{data['slot']} -> {dial.label}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
