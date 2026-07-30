"""Dial decision logic. Pure: no X, no config, no I/O.

Two pieces live here. `decide` is the three-state action table driven by a
keypress. `FocusTracker` (added in the next task) models the asynchronous
activation lifecycle, because activation is a request the WM may refuse.

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
