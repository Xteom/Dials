"""Monitor enumeration and selection.

RandR 1.2 is used because python-xlib 0.29 does not implement the RandR 1.5
`get_monitors` call (verified in docs/probes/06). 1.2 enumerates *outputs*, so
mirrored outputs share a CRTC and must be deduplicated to avoid reporting two
monitors with identical rects.

This module is split: the functions below are pure and fully unit-tested; the
X I/O lives in `MonitorSource` (added in the next task) and simply produces
`RawOutput` records for them.
"""
from __future__ import annotations

from dataclasses import dataclass

from dials.geometry import Monitor, Rect


@dataclass(frozen=True)
class RawOutput:
    """One RandR output as read from the server. crtc == 0 means disconnected."""
    name: str
    crtc: int
    x: int
    y: int
    w: int
    h: int
    primary: bool


def dedupe_and_sort(raws: list[RawOutput]) -> list[Monitor]:
    """Drop disconnected outputs, collapse mirrored ones, order deterministically.

    Sorting the candidates by name before deduplicating means the surviving
    name for a shared CRTC does not depend on the server's reply order, so
    "first usable monitor" in `pick` means the same thing on every run.
    """
    live = [r for r in raws if r.crtc != 0 and r.w > 0 and r.h > 0]
    by_crtc: dict[int, RawOutput] = {}
    for raw in sorted(live, key=lambda r: r.name):
        if raw.crtc not in by_crtc:
            by_crtc[raw.crtc] = raw
        elif raw.primary:
            # Prefer the primary output's name for a mirrored pair.
            by_crtc[raw.crtc] = raw

    monitors = [
        Monitor(
            name=r.name,
            rect=Rect(r.x, r.y, r.w, r.h),
            primary=r.primary,
            crtc=r.crtc,
        )
        for r in by_crtc.values()
    ]
    return sorted(monitors, key=lambda m: (m.rect.x, m.rect.y, m.name))


def pick(
    name: str, monitors: list[Monitor], root_rect: Rect
) -> tuple[Monitor, str | None]:
    """Resolve a Dial's monitor name through the four-step fallback chain.

    Returns (monitor, fallback_reason). `reason` is None only when the named
    monitor was found; otherwise it is a one-line explanation for `dials status`
    so a Dial landing on the wrong screen is visible rather than mysterious.
    """
    for m in monitors:
        if m.name == name:
            return m, None

    for m in monitors:
        if m.primary:
            return m, f"monitor {name!r} absent; using primary {m.name!r}"

    if monitors:
        m = monitors[0]
        return m, f"monitor {name!r} absent and no primary; using first {m.name!r}"

    return (
        Monitor(name="<root>", rect=root_rect, primary=True, crtc=0),
        f"monitor {name!r} absent and no usable monitors; using root box",
    )
