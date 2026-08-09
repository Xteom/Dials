"""Pure geometry: monitor rects, fraction resolution, clamping.

Dial geometry is stored as a monitor name plus fractions rather than absolute
pixels because the layout on this machine changes: DP-1-1 was connected during
one probe and gone by the next, and eDP-1-1 shifted by 15px in between.

Deliberately does NOT consult _NET_WORKAREA. That property is a single
desktop-wide rectangle and cannot express per-monitor reserved regions, so
intersecting every monitor against it would wrongly shrink monitors an
unrelated panel never touched. If reserved space ever appears here, the correct
fix is per-monitor _NET_WM_STRUT_PARTIAL aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    def clamped_to(self, bounds: "Rect") -> "Rect":
        """Shift and shrink so this rect fits inside `bounds`."""
        x = max(bounds.x, min(self.x, bounds.x + bounds.w - 1))
        y = max(bounds.y, min(self.y, bounds.y + bounds.h - 1))
        w = max(1, min(self.w, bounds.x + bounds.w - x))
        h = max(1, min(self.h, bounds.y + bounds.h - y))
        return Rect(x, y, w, h)


@dataclass(frozen=True)
class Monitor:
    name: str
    rect: Rect
    primary: bool
    crtc: int
    #: The display's own EDID model name, when it reports one. None for a
    #: panel that carries no 0xFC descriptor AND for one whose EDID could not
    #: be read - `identity_reliable` is what separates those.
    display_name: str | None = None
    #: False only when the EDID read RAISED. Degrading is fine when placing a
    #: window and wrong when writing config, so the write path checks this.
    identity_reliable: bool = True


def resolve(
    fractions: tuple[float, float, float, float],
    monitor_rect: Rect,
    root_rect: Rect,
) -> Rect:
    """Turn (x, y, w, h) fractions of `monitor_rect` into a pixel Rect.

    The result is clamped to `root_rect`, so a monitor that reports a rect
    extending past the screen cannot place a window somewhere unreachable.
    """
    fx, fy, fw, fh = fractions
    x = monitor_rect.x + int(monitor_rect.w * fx)
    y = monitor_rect.y + int(monitor_rect.h * fy)
    w = max(1, int(monitor_rect.w * fw))
    h = max(1, int(monitor_rect.h * fh))
    return Rect(x, y, w, h).clamped_to(root_rect)
