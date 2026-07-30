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
