"""Dial decision logic. Pure: no X, no config, no I/O.

Two pieces live here. `decide` is the three-state action table driven by a
keypress. `FocusTracker` models the asynchronous activation lifecycle,
because activation is a request the WM may refuse.

"is_active" always means "this window is _NET_ACTIVE_WINDOW", never
_NET_WM_STATE_FOCUSED. EWMH defines FOCUSED as whether decorations are drawn
active and explicitly permits several windows to carry it at once, so it is a
decoration hint rather than an exclusive focus flag.
"""
from __future__ import annotations

SHOW = "show"
RAISE = "raise"
HIDE = "hide"
LAUNCH = "launch"


def decide(found: bool, hidden: bool, is_active: bool) -> str:
    """Pick the action for one Dial keypress.

    Three states rather than a strict toggle: a Dial with
    on_focus_loss="normal" can be buried, and pressing its key while buried
    must surface it, not hide it.
    """
    if not found:
        return LAUNCH
    if hidden:
        return SHOW
    if is_active:
        return HIDE
    return RAISE


def reapply_geometry(action: str, pin_geometry: bool) -> bool:
    """Whether this action should re-assert the Dial's configured geometry."""
    if action == SHOW:
        return True
    if action == RAISE:
        return pin_geometry
    return False


def reapply_hints(action: str) -> bool:
    """Whether this action should re-assert the Dial's window hints.

    SHOW *and* RAISE - and deliberately NOT gated on `pin_geometry`, which is
    about position, not about window properties.

    The hints are what keep a Dial out of the taskbar and the switcher. This same
    condition also gates moving the window to the current workspace, which is what
    replaced `_NET_WM_STATE_STICKY`.

    A window that was already visible the first time the daemon saw it never goes
    through SHOW: every press on it is a RAISE, or a HIDE. Gating on SHOW therefore
    meant such a window never received the hints at all, so it stayed in the
    switcher forever - exactly what was reported for the Firefox Dial - and would
    now also stay stranded on whichever workspace it started on. They are
    idempotent client messages, a handful per press, and never run at idle.
    """
    return action in (SHOW, RAISE)


import time as _time

INACTIVE = "inactive"
ACTIVATING = "activating"
ACTIVE = "active"

#: How long to wait for the WM to honour an activation request before giving up.
ACTIVATION_TIMEOUT = 1.5


class FocusTracker:
    """Per-Dial activation lifecycle.

        INACTIVE --key--> ACTIVATING --observed active--> ACTIVE
            ^                  |                            |
            |                  +-- refused / timed out -----+
            +------------------ lost active ----------------+

    The hide guard arms ONLY on the ACTIVATING -> ACTIVE edge, i.e. only once
    _NET_ACTIVE_WINDOW has actually been observed to equal the Dial's window.
    Arming merely because an activation message was sent would let a Dial hide
    itself the instant it appeared.

    Callers pass the whole "group" of window ids belonging to the Dial - the
    window plus any transients owned by it - so a Dial's own dialog taking
    focus is not mistaken for focus loss.
    """

    def __init__(self, on_focus_loss: str, clock=_time.monotonic):
        self.on_focus_loss = on_focus_loss
        self._clock = clock
        self.state = INACTIVE
        self.window: int | None = None
        self._started: float | None = None

    def activating(self, win_id: int, now: float | None = None) -> None:
        """Record that an activation request was sent for `win_id`."""
        self.window = win_id
        self.state = ACTIVATING
        self._started = self._clock() if now is None else now

    def observe_active(self, active_win: int | None, group_ids: set[int]) -> str | None:
        """Reconcile against the CURRENT active window.

        Deliberately takes the current value rather than an event payload, so a
        stale or coalesced PropertyNotify cannot drive a wrong transition.
        Returns HIDE when this Dial should be hidden, else None.
        """
        in_group = active_win is not None and active_win in group_ids

        if self.state == ACTIVATING:
            if in_group:
                self.state = ACTIVE
                self._started = None
            return None

        if self.state == ACTIVE and not in_group:
            self.state = INACTIVE
            self._started = None
            if self.on_focus_loss == "hide":
                return HIDE
        return None

    def check_timeout(self, now: float | None = None) -> bool:
        """True if an activation request went unanswered; resets to INACTIVE."""
        if self.state != ACTIVATING or self._started is None:
            return False
        current = self._clock() if now is None else now
        if current - self._started >= ACTIVATION_TIMEOUT:
            self.state = INACTIVE
            self._started = None
            return True
        return False

    def deadline(self) -> float | None:
        """Absolute time the daemon must wake by, or None when nothing is armed.

        None at idle is what keeps the daemon's select() timeout infinite and
        its wakeup count at zero.
        """
        if self.state == ACTIVATING and self._started is not None:
            return self._started + ACTIVATION_TIMEOUT
        return None

    def forget(self) -> None:
        """Abandon any in-flight transition (config reload, Dial removed)."""
        self.state = INACTIVE
        self.window = None
        self._started = None
