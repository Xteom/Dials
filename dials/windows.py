"""Window selection policy and EWMH operations.

The policy half is pure and heavily tested, because a class match can return
several windows - two Slack windows, a browser with three - and every branch of
the Dial state machine depends on picking one deterministically rather than on
whatever order _NET_CLIENT_LIST happens to report.
"""
from __future__ import annotations

from dataclasses import dataclass

EXCLUDED_TYPES = frozenset({
    "_NET_WM_WINDOW_TYPE_DIALOG",
    "_NET_WM_WINDOW_TYPE_UTILITY",
    "_NET_WM_WINDOW_TYPE_SPLASH",
    "_NET_WM_WINDOW_TYPE_MENU",
    "_NET_WM_WINDOW_TYPE_TOOLTIP",
})


@dataclass(frozen=True)
class WindowInfo:
    wid: int
    wm_class: str          # the CLASS field of WM_CLASS (the second one)
    wtype: str
    override_redirect: bool
    transient_for: int | None
    appeared: float        # monotonic time the daemon first saw this window


def choose(
    windows: list[WindowInfo],
    match_class: str,
    active_id: int | None = None,
    recent: tuple[int, ...] = (),
    since: float | None = None,
) -> WindowInfo | None:
    """Pick the one window a Dial acts on.

    Order: active window, then most-recently-active, then lowest window id.
    The last rule exists purely so the choice is stable across presses instead
    of arbitrary.

    `since` is used by the post-launch waiter: it restricts candidates to
    windows that appeared after the launch, so a pre-existing window of the
    same class is not mistaken for the newly spawned one.
    """
    candidates = [
        w for w in windows
        if w.wm_class == match_class
        and w.wtype not in EXCLUDED_TYPES
        and not w.override_redirect
        and (since is None or w.appeared > since)
    ]
    if not candidates:
        return None

    by_id = {w.wid: w for w in candidates}
    if active_id is not None and active_id in by_id:
        return by_id[active_id]
    for wid in recent:
        if wid in by_id:
            return by_id[wid]
    return min(candidates, key=lambda w: w.wid)


def group_ids(chosen: WindowInfo, windows: list[WindowInfo]) -> set[int]:
    """The chosen window plus any transients it owns.

    Focus moving to a Dial's own dialog is not focus loss, so the whole group
    counts as "the Dial is still active".
    """
    ids = {chosen.wid}
    ids.update(w.wid for w in windows if w.transient_for == chosen.wid)
    return ids


import time

import Xlib.protocol.event  # noqa: F401  (populates Xlib.protocol.event, see below)
from Xlib import X, Xatom, Xutil, error, protocol

from dials.geometry import Rect

# `Xlib.protocol.event` is a submodule that is not imported as a side effect of
# `from Xlib import protocol` alone (only pulled in transitively by e.g.
# `Xlib.display`). We reference it below as `protocol.event.ClientMessage`, so
# import it explicitly here to guarantee the attribute exists regardless of
# what else has been imported.

#: Applied to every Dial unconditionally. STICKY so a Dial is reachable from
#: any workspace; SKIP_TASKBAR because mutter reads that same flag to exclude a
#: window from alt-tab (see META_WINDOW_IN_NORMAL_TAB_CHAIN in
#: mutter/src/core/window-private.h). Losing the taskbar entry is the accepted
#: cost of the alt-tab requirement.
PANEL_HINTS = (
    "_NET_WM_STATE_STICKY",
    "_NET_WM_STATE_SKIP_TASKBAR",
    "_NET_WM_STATE_SKIP_PAGER",
)

_STATE_ADD = 1
_SOURCE_PAGER = 2


class WindowOps:
    """EWMH reads and writes. Every method degrades rather than raising.

    A window can be destroyed between being listed and being acted on, so all
    X access is wrapped: a dead window is a no-op, never a crash.
    """

    def __init__(self, display, root):
        self.d = display
        self.root = root
        self._first_seen: dict[int, float] = {}

    # ---- helpers ---------------------------------------------------------

    def _atom(self, name: str) -> int:
        return self.d.intern_atom(name)

    def _win(self, wid: int):
        return self.d.create_resource_object("window", wid)

    def _prop(self, wid: int, name: str):
        try:
            p = self._win(wid).get_full_property(self._atom(name), X.AnyPropertyType)
        except Exception:
            return None
        return list(p.value) if p else None

    def _client_message(self, wid: int, type_name: str, data: list[int]) -> None:
        try:
            ev = protocol.event.ClientMessage(
                window=wid,
                client_type=self._atom(type_name),
                data=(32, data),
            )
            self.root.send_event(
                ev,
                event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
                onerror=error.CatchError(),
            )
            self.d.flush()
        except Exception:
            pass

    # ---- reads -----------------------------------------------------------

    def list_windows(self) -> list["WindowInfo"]:
        """Every WM-managed window, with the fields the policy needs."""
        ids = self._prop(self.root.id, "_NET_CLIENT_LIST") or []
        now = time.monotonic()
        out: list[WindowInfo] = []
        for wid in ids:
            try:
                w = self._win(wid)
                cls = w.get_wm_class()
                attrs = w.get_attributes()
                transient = w.get_full_property(Xatom.WM_TRANSIENT_FOR, X.AnyPropertyType)
                types = self._prop(wid, "_NET_WM_WINDOW_TYPE") or []
                wtype = self.d.get_atom_name(types[0]) if types else \
                    "_NET_WM_WINDOW_TYPE_NORMAL"
            except Exception:
                continue
            self._first_seen.setdefault(wid, now)
            out.append(WindowInfo(
                wid=wid,
                wm_class=(cls[1] if cls and len(cls) > 1 else ""),
                wtype=wtype,
                override_redirect=bool(getattr(attrs, "override_redirect", False)),
                transient_for=(transient.value[0] if transient else None),
                appeared=self._first_seen[wid],
            ))
        live = {w.wid for w in out}
        for gone in [k for k in self._first_seen if k not in live]:
            del self._first_seen[gone]
        return out

    def active_window(self) -> int | None:
        vals = self._prop(self.root.id, "_NET_ACTIVE_WINDOW")
        if not vals or not vals[0]:
            return None
        return vals[0]

    def is_hidden(self, wid: int) -> bool:
        """True when minimized. _NET_WM_STATE_HIDDEN is the correct test."""
        states = self._prop(wid, "_NET_WM_STATE") or []
        target = self._atom("_NET_WM_STATE_HIDDEN")
        return target in states

    def geometry(self, wid: int) -> Rect | None:
        try:
            g = self._win(wid).get_geometry()
            t = self.root.translate_coords(self._win(wid), 0, 0)
            return Rect(t.x, t.y, g.width, g.height)
        except Exception:
            return None

    def window_name(self, wid: int) -> str:
        raw = self._prop(wid, "_NET_WM_NAME")
        if raw:
            try:
                return bytes(raw).decode("utf-8", "replace")
            except Exception:
                pass
        try:
            return self._win(wid).get_wm_name() or ""
        except Exception:
            return ""

    # ---- writes ----------------------------------------------------------

    def apply_geometry(self, wid: int, rect: Rect) -> None:
        """Ask for a rect. The app's size hints may win; that is accepted."""
        try:
            self._win(wid).configure(x=rect.x, y=rect.y,
                                     width=rect.w, height=rect.h)
            self.d.flush()
        except Exception:
            pass

    def apply_hints(self, wid: int, above: bool) -> None:
        hints = list(PANEL_HINTS)
        if above:
            hints.append("_NET_WM_STATE_ABOVE")
        for name in hints:
            self._client_message(
                wid, "_NET_WM_STATE",
                [_STATE_ADD, self._atom(name), 0, _SOURCE_PAGER, 0],
            )

    def activate(self, wid: int, timestamp: int) -> None:
        """Request activation.

        `timestamp` must be the triggering KeyPress.time, never CurrentTime:
        the daemon has a real user-activity timestamp and EWMH asks for it,
        which matters under focus-stealing prevention.
        """
        self._client_message(
            wid, "_NET_ACTIVE_WINDOW", [_SOURCE_PAGER, timestamp, 0, 0, 0]
        )

    def iconify(self, wid: int) -> None:
        self._client_message(wid, "WM_CHANGE_STATE", [Xutil.IconicState, 0, 0, 0, 0])
