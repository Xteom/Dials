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


import logging
import time

import Xlib.protocol.event  # noqa: F401  (populates Xlib.protocol.event, see below)
from Xlib import X, Xatom, Xutil, error, protocol

from dials.geometry import Rect

# `Xlib.protocol.event` is a submodule that is not imported as a side effect of
# `from Xlib import protocol` alone (only pulled in transitively by e.g.
# `Xlib.display`). We reference it below as `protocol.event.ClientMessage`, so
# import it explicitly here to guarantee the attribute exists regardless of
# what else has been imported.

log = logging.getLogger(__name__)

#: Applied to every Dial unconditionally. SKIP_TASKBAR because mutter reads that
#: same flag to exclude a window from alt-tab (see META_WINDOW_IN_NORMAL_TAB_CHAIN
#: in mutter/src/core/window-private.h). Losing the taskbar entry is the accepted
#: cost of the alt-tab requirement.
#:
#: _NET_WM_STATE_STICKY is NOT here, and is actively removed - see `apply_hints`
#: and `place_on_current_desktop`.
PANEL_HINTS = (
    "_NET_WM_STATE_SKIP_TASKBAR",
    "_NET_WM_STATE_SKIP_PAGER",
)

_STATE_REMOVE = 0
_STATE_ADD = 1
_SOURCE_PAGER = 2

#: Bits 8-11 of _NET_MOVERESIZE_WINDOW's data.l[0]: x, y, width and height are
#: all supplied. Bits 12-15 carry the source indication, hence the << 12 below.
_MOVERESIZE_XYWH = 0xF00
#: WM_SIZE_HINTS.win_gravity value StaticGravity: the x, y in the message is the
#: CLIENT window's position, not the frame's. See `apply_geometry`.
_STATIC_GRAVITY = 10


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
            # Routine: a window can vanish between being listed and being
            # read. Not worth surfacing above debug.
            log.debug("failed to read %s for window %#x", name, wid, exc_info=True)
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
            # A write that is supposed to succeed silently failing means
            # Dials does nothing and nobody knows why - that deserves to be
            # visible by default.
            log.warning(
                "client message %s failed for window %#x", type_name, wid,
                exc_info=True,
            )

    # ---- reads -----------------------------------------------------------

    def list_windows(self) -> list["WindowInfo"] | None:
        """Every WM-managed window, or **None** when the list is UNREADABLE.

        `None` is not an empty list and no caller may conflate the two. An empty
        list makes every Dial look unlaunched, so a keypress takes the LAUNCH
        branch for an app that is in fact running and confirming it starts a
        SECOND instance. Returning `[]` here is what made that reachable through
        nothing worse than a transient X error, and _NET_CLIENT_LIST is
        genuinely unset for a moment across a `gnome-shell --replace`.

        Callers treat `None` as "do nothing this event"; the pruning below is
        skipped too, so a window's `appeared` time - which choose(since=) relies
        on as identity, not liveness - survives the gap.
        """
        raw = self._prop(self.root.id, "_NET_CLIENT_LIST")
        if raw is None:
            log.warning("could not read _NET_CLIENT_LIST; reporting unknown")
            return None
        ids = raw
        now = time.monotonic()
        out: list[WindowInfo] = []
        for wid in ids:
            # Register first-seen BEFORE any property read: a transient read
            # failure must never reset a window's identity age, or
            # choose(since=) could adopt a pre-existing window as a freshly
            # launched one.
            self._first_seen.setdefault(wid, now)
            try:
                w = self._win(wid)
                cls = w.get_wm_class()
                attrs = w.get_attributes()
                transient = w.get_full_property(Xatom.WM_TRANSIENT_FOR, X.AnyPropertyType)
                types = self._prop(wid, "_NET_WM_WINDOW_TYPE") or []
                wtype = self.d.get_atom_name(types[0]) if types else \
                    "_NET_WM_WINDOW_TYPE_NORMAL"
            except Exception:
                # Routine: the window can have disappeared mid-enumeration.
                log.debug("could not read properties of window %#x", wid,
                          exc_info=True)
                continue
            out.append(WindowInfo(
                wid=wid,
                wm_class=(cls[1] if cls and len(cls) > 1 else ""),
                wtype=wtype,
                override_redirect=bool(getattr(attrs, "override_redirect", False)),
                transient_for=(transient.value[0] if transient else None),
                appeared=self._first_seen[wid],
            ))
        # Prune against the WM's own client list, not against windows we
        # could successfully read this scan - otherwise a transient read
        # failure would delete the entry outright (see above), which is
        # exactly as bad as resetting it.
        live = set(ids)
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
            # Routine: the window can have disappeared since it was listed.
            log.debug("failed to read geometry for window %#x", wid, exc_info=True)
            return None

    def window_name(self, wid: int) -> str:
        raw = self._prop(wid, "_NET_WM_NAME")
        if raw:
            try:
                return bytes(raw).decode("utf-8", "replace")
            except Exception:
                # Routine: fall through to the WM_NAME fallback below.
                log.debug("failed to decode _NET_WM_NAME for window %#x", wid,
                          exc_info=True)
        try:
            return self._win(wid).get_wm_name() or ""
        except Exception:
            # Routine: the window can have disappeared since it was listed.
            log.debug("failed to read WM_NAME for window %#x", wid, exc_info=True)
            return ""

    # ---- writes ----------------------------------------------------------

    def apply_geometry(self, wid: int, rect: Rect) -> None:
        """Ask for a rect. The app's size hints may win; that is accepted.

        Sent as _NET_MOVERESIZE_WINDOW with **StaticGravity** rather than as a
        plain ConfigureRequest, so that this WRITE and `geometry()`'s READ mean
        the same rectangle. `geometry()` reports the CLIENT origin (via
        translate_coords), while mutter reads a NorthWest-gravity
        ConfigureRequest's x, y as the position of the visible FRAME. On a
        server-side-decorated window those differ by the titlebar height, so a
        read -> write round trip walked the window DOWN the screen by 37 px every
        time - and `dials capture` compounded another 37 px each run. Spotify, one
        of the two Dials in config/config.reference.toml, is SSD, so this was the
        default experience rather than a corner case.

        All four candidates were measured on one window in
        docs/probes/10-frame-extents-and-geometry-round-trip.py; the direction of
        the offset was measured rather than derived, because a previous
        coordinate-maths "fix" in this project was exactly backwards:

            configure(x, y, w, h)                 client lands +37 in y, drifts
            _NET_MOVERESIZE_WINDOW gravity 0      client lands +37 in y, drifts
            _NET_MOVERESIZE_WINDOW NorthWest (1)  client lands +37 in y, drifts
            _NET_MOVERESIZE_WINDOW Static  (10)   client lands EXACTLY, no drift

        StaticGravity is a fixed point on an SSD window, on a CSD window with no
        server frame at all, and on a MINIMIZED window - the last being the
        daemon's most common SHOW path, so it had to be checked separately.

        _SOURCE_PAGER is the same source indication the other writes use; EWMH
        names pagers as the intended senders of this message, which is why this
        was preferred over reading _NET_FRAME_EXTENTS and compensating: no extra
        round trip, and nothing to keep in sync with the frame changing.

        ONE target cannot be honoured, and it is a mutter constraint rather than
        a bug here: mutter will not put a server-side titlebar off the top of the
        screen, so an SSD client is clamped to y >= the top frame extent. Measured
        on the reference config's own rect (`rect = [0.0, 0.0, 0.5, 1.0]`, i.e.
        y = 0): asked 0/10/20/36 -> landed 37; asked 37/38/60/200 -> landed
        exactly. Every one of those landings is still a FIXED POINT, so the drift
        no longer compounds - which is the part that mattered. Do not "fix" this
        by subtracting the extent: that would push the client further down, and
        the WM would clamp it right back.
        """
        self._client_message(
            wid, "_NET_MOVERESIZE_WINDOW",
            [_STATIC_GRAVITY | _MOVERESIZE_XYWH | (_SOURCE_PAGER << 12),
             rect.x, rect.y, rect.w, rect.h],
        )

    def apply_hints(self, wid: int, above: bool) -> None:
        """Make `wid` behave as a panel, and set or clear its always-on-top state.

        ABOVE is explicitly REMOVED when `above` is false, rather than merely not
        added. _NET_WM_STATE messages are add/remove, not a full assignment, so
        an add-only version could never undo itself: changing a Dial from
        on_focus_loss="above" to "normal" left the window permanently on top
        until the app was restarted, and the config quietly disagreed with the
        screen. The other hints are unconditional, so they never need the remove
        branch.

        STICKY is removed for the same add/remove reason, and additionally because
        a window that was made sticky by an earlier version of this code stays
        sticky forever otherwise - it is not enough to stop adding it. See
        `place_on_current_desktop` for what replaces it.
        """
        for name in PANEL_HINTS:
            self._client_message(
                wid, "_NET_WM_STATE",
                [_STATE_ADD, self._atom(name), 0, _SOURCE_PAGER, 0],
            )
        for name, on in (("_NET_WM_STATE_ABOVE", above),
                         ("_NET_WM_STATE_STICKY", False)):
            self._client_message(
                wid, "_NET_WM_STATE",
                [_STATE_ADD if on else _STATE_REMOVE,
                 self._atom(name), 0, _SOURCE_PAGER, 0],
            )

    def current_desktop(self) -> int | None:
        """The workspace index the user is looking at, or None if unreadable."""
        v = self._prop(self.root.id, "_NET_CURRENT_DESKTOP")
        return v[0] if v else None

    def place_on_current_desktop(self, wid: int) -> None:
        """Move `wid` to the workspace the user is on right now.

        This replaces _NET_WM_STATE_STICKY, and the difference is the whole point.
        Sticky satisfies "reachable from any workspace" by making the window
        *present on every* workspace - so switching workspace (a four-finger swipe
        here) carries every Dial along with you, which is not what a dropdown
        panel should do.

        Setting _NET_WM_DESKTOP to the current index gives the reachable half
        without the present-everywhere half: pressing a Dial's key brings its
        window to wherever you are, and it exists on no other workspace.

        Unreadable _NET_CURRENT_DESKTOP is a no-op rather than a guess: leaving a
        window where it is beats moving it to a workspace picked at random.
        """
        idx = self.current_desktop()
        if idx is None:
            log.debug("no _NET_CURRENT_DESKTOP; leaving window %#x where it is", wid)
            return
        self._client_message(
            wid, "_NET_WM_DESKTOP", [idx, _SOURCE_PAGER, 0, 0, 0],
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
