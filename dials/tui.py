"""curses numpad grid.

stdlib curses rather than a TUI framework: it adds no dependency, matches the
sibling clip project's lean policy, and carries no risk with the pinned
Python 3.10 / system-site-packages venv.

Layout and navigation are pure functions so they can be tested without a
terminal; only `run` touches curses.
"""
from __future__ import annotations

from dials import icons, keys
from dials.config import Config, Dial

#: Mirrors the physical keypad. Empty strings are gaps in the grid.
GRID: tuple[tuple[str, ...], ...] = (
    ("7", "8", "9"),
    ("4", "5", "6"),
    ("1", "2", "3"),
    ("0", ".", "enter"),
    ("/", "*", "-"),
    ("+", "", ""),
)

CELL_W = 9


def cell_label(dial: Dial | None, slot: str, glyph: str) -> str:
    """Two lines for one grid cell: the key, then the Dial (or a placeholder)."""
    head = slot if slot != "enter" else "ent"
    if dial is None:
        body = "·" if slot != keys.ASSIGN_SLOT else "assign"
    else:
        body = f"{glyph}{dial.label}" if glyph else dial.label
    return f"{head[:CELL_W]}\n{body[:CELL_W]}"


def move(grid, slot: str, dx: int, dy: int) -> str:
    """Arrow-key navigation that always lands on a real slot."""
    position = None
    for r, row in enumerate(grid):
        for c, s in enumerate(row):
            if s == slot:
                position = (r, c)
    if position is None:
        return slot
    r, c = position
    nr, nc = r + dy, c + dx
    if not (0 <= nr < len(grid)) or not (0 <= nc < len(grid[nr])):
        return slot
    if not grid[nr][nc]:
        # Gap: continue one more step in the same direction. NOTE: for the
        # GRID shipped today this never succeeds, because every gap sits at
        # the trailing edge and the second step is out of range, so control
        # falls through to the stay-put return below. It is kept as a guard
        # for future layouts with an interior gap, and is covered by
        # test_move_skips_an_interior_gap using a synthetic grid.
        nr2, nc2 = nr + dy, nc + dx
        if 0 <= nr2 < len(grid) and 0 <= nc2 < len(grid[nr2]) and grid[nr2][nc2]:
            return grid[nr2][nc2]
        return slot
    return grid[nr][nc]


def detail_lines(dial: Dial | None) -> list[str]:
    if dial is None:
        return ["(unbound)", "", "press b to bind a window to this slot"]
    x, y, w, h = dial.rect
    return [
        f"label          {dial.label}",
        f"match_class    {dial.match_class}",
        f"launch         {dial.launch if dial.launch is not None else '(none)'}",
        f"monitor        {dial.monitor}",
        f"rect           {x * 100:.0f},{y * 100:.0f}  "
        f"{w * 100:.0f}% x {h * 100:.0f}%",
        f"on_focus_loss  {dial.on_focus_loss}",
        f"pin_geometry   {'yes' if dial.pin_geometry else 'no'}",
    ]


# ---- curses front end ---------------------------------------------------

def run(config: Config) -> int:
    import curses

    from dials.cli import _signal_daemon
    from dials.config import config_path
    from dials.configwrite import remove_dial, upsert_dial

    # Every other writer signals the daemon after a write (cli unbind/capture,
    # the confirm dialog, and the daemon's own in-process _persist). Without it
    # the TUI - the editing surface the README points at - would be the one path
    # that leaves the running daemon on a stale config: bind a window here,
    # press its key, nothing happens. _signal_daemon swallows every failure and
    # returns a bool, so calling it unconditionally is safe.

    def draw(stdscr, cfg, cursor, message):
        stdscr.erase()
        stdscr.addstr(0, 0, "Dials", curses.A_BOLD)
        stdscr.addstr(0, 8, "arrows move   b bind   d delete   e edit   q quit")

        top = 2
        for r, row in enumerate(GRID):
            for c, slot in enumerate(row):
                if not slot:
                    continue
                dial = cfg.dial(slot) if keys.is_bindable(slot) else None
                glyph = icons.glyph_for(dial.match_class, dial.icon) if dial else ""
                head, body = cell_label(dial, slot, glyph).split("\n")
                attr = curses.A_REVERSE if slot == cursor else curses.A_NORMAL
                y, x = top + r * 3, 2 + c * (CELL_W + 2)
                stdscr.addstr(y, x, head.ljust(CELL_W), attr | curses.A_BOLD)
                stdscr.addstr(y + 1, x, body.ljust(CELL_W), attr)

        detail_top = top + len(GRID) * 3 + 1
        stdscr.addstr(detail_top - 1, 2, f"Dial {cursor}", curses.A_BOLD)
        for i, line in enumerate(detail_lines(
            cfg.dial(cursor) if keys.is_bindable(cursor) else None
        )):
            stdscr.addstr(detail_top + i, 2, line[:78])
        if message:
            stdscr.addstr(detail_top + 9, 2, message[:78], curses.A_DIM)
        stdscr.refresh()

    def pick_window(stdscr):
        """List every managed window so one can be bound without knowing its class."""
        from Xlib import display
        from dials.windows import WindowOps
        d = display.Display()
        ops = WindowOps(d, d.screen().root)
        listed = ops.list_windows()
        if listed is None:
            return None          # unreadable client list: cancel, do not bind
        windows = [w for w in listed if w.wm_class]
        if not windows:
            return None
        index = 0
        while True:
            stdscr.erase()
            stdscr.addstr(0, 0, "Pick a window", curses.A_BOLD)
            stdscr.addstr(1, 0, "arrows move   enter bind   esc cancel")
            for i, w in enumerate(windows[:20]):
                attr = curses.A_REVERSE if i == index else curses.A_NORMAL
                name = ops.window_name(w.wid)[:40]
                stdscr.addstr(3 + i, 2,
                              f"{w.wm_class:<18} {name}".ljust(70), attr)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (27, ord("q")):
                return None
            if key in (curses.KEY_UP, ord("k")):
                index = max(0, index - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = min(len(windows[:20]) - 1, index + 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                return windows[index], ops

    def cycle_focus_loss(dial):
        from dataclasses import replace
        order = ("normal", "above", "hide")
        nxt = order[(order.index(dial.on_focus_loss) + 1) % len(order)]
        return replace(dial, on_focus_loss=nxt)

    def loop(stdscr):
        curses.curs_set(0)
        cfg = config
        cursor = "9"
        message = ""
        while True:
            draw(stdscr, cfg, cursor, message)
            message = ""
            key = stdscr.getch()

            if key in (ord("q"), 27):
                return 0
            if key in (curses.KEY_LEFT, ord("h")):
                cursor = move(GRID, cursor, -1, 0)
            elif key in (curses.KEY_RIGHT, ord("l")):
                cursor = move(GRID, cursor, 1, 0)
            elif key in (curses.KEY_UP, ord("k")):
                cursor = move(GRID, cursor, 0, -1)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = move(GRID, cursor, 0, 1)
            elif key == ord("b") and keys.is_bindable(cursor):
                picked = pick_window(stdscr)
                if picked:
                    info, ops = picked
                    from dials.assign import derive_rect
                    from dials.daemon import _monitor_containing
                    from dials.geometry import Rect
                    from dials.monitors import MonitorSource
                    from Xlib import display as _display
                    dsp = _display.Display()
                    mons = MonitorSource(dsp, dsp.screen().root)
                    rect = ops.geometry(info.wid) or Rect(0, 0, 800, 600)
                    monitor = _monitor_containing(rect, mons.monitors(),
                                                  mons.root_rect())
                    dial = Dial(
                        slot=cursor,
                        label=ops.window_name(info.wid) or info.wm_class,
                        match_class=info.wm_class, launch=None, icon="",
                        monitor=monitor.name, rect=derive_rect(rect, monitor),
                        on_focus_loss=cfg.defaults.on_focus_loss,
                        pin_geometry=cfg.defaults.pin_geometry,
                    )
                    cfg = upsert_dial(config_path(), dial)
                    _signal_daemon()
                    message = f"bound {cursor} -> {dial.label}"
            elif key == ord("d") and cfg.dial(cursor):
                cfg = remove_dial(config_path(), cursor)
                _signal_daemon()
                message = f"cleared {cursor}"
            elif key == ord("e") and cfg.dial(cursor):
                cfg = upsert_dial(config_path(), cycle_focus_loss(cfg.dial(cursor)))
                _signal_daemon()
                message = f"on_focus_loss -> {cfg.dial(cursor).on_focus_loss}"

    import curses as _curses
    return _curses.wrapper(loop)
