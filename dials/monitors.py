"""Monitor enumeration and selection.

RandR **1.2** is used deliberately, not for lack of an alternative: the 1.5
`get_monitors` call is absent from python-xlib 0.29 (probe 06 ran against the
system 0.29) and this project's floor is `python-xlib>=0.29`, so 1.2 is the
portable choice. The venv installs 0.33, which DOES have `get_monitors`, so
re-running probe 06 there reports the opposite - that is a version difference,
not a contradiction. 1.2 enumerates *outputs* rather than logical monitors,
which is exactly why the CRTC dedup below is required: mirrored outputs share
one CRTC and would otherwise appear as two monitors with identical rects.

This module is split: the functions below are pure and fully unit-tested; the
X I/O lives in `MonitorSource` and simply produces `RawOutput` records for
them.
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


#: EDID descriptor tag for the model name. Descriptors live in the base block
#: at bytes 54, 72, 90, 108 - four 18-byte slots, each tagged by byte 3.
_EDID_NAME_TAG = 0xFC
_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def edid_name(blob: bytes | None) -> str | None:
    """The display's own model name from its EDID, or None.

    None covers three different things on purpose - a malformed blob, a blob
    that is not EDID at all, and a display that simply carries no 0xFC
    descriptor. Laptop panels are the third case: a panel is not sold as its
    own product, so it has no model name to report. Callers distinguish "no
    name" from "could not read" via RawOutput.edid_failed, not via this.

    Never raises. A display with an unparseable EDID is a display without a
    name, not a crashed daemon.
    """
    if not blob:
        return None
    b = bytes(blob)
    if len(b) < 128 or b[:8] != _EDID_HEADER:
        return None
    for i in range(54, 126, 18):
        d = b[i:i + 18]
        if d[0:3] == b"\x00\x00\x00" and d[3] == _EDID_NAME_TAG:
            text = d[5:18].split(b"\n")[0]
            return text.decode("ascii", "replace").strip() or None
    return None


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


class MonitorSource:
    """Cached monitor list, invalidated by RandR events rather than polling.

    Probe 06 confirmed `randr.select_input` is accepted for
    RRScreenChangeNotify / RRCrtcChangeNotify / RROutputChangeNotify. That
    proves the mask registers; that events actually arrive and invalidate this
    cache is a live-hotplug smoke test, not a unit test.

    `reader` and `root_rect_reader` are injected so the cache logic is testable
    with no X server.
    """

    def __init__(self, display, root, reader=None, root_rect_reader=None):
        self.display = display
        self.root = root
        self.reader = reader or self._read_from_x
        self.root_rect_reader = root_rect_reader or self._read_root_rect_from_x
        self._cache: list[Monitor] | None = None
        # Survives invalidate() on purpose: it is the last SUCCESSFUL read, so a
        # failed refresh can fall back to it. Without a second slot this is
        # impossible - invalidate() has already cleared _cache by the time the
        # reader raises, so there would be nothing left to keep.
        self._last_good: list[Monitor] | None = None
        self._root_rect: Rect | None = None

    # ---- public API ------------------------------------------------------

    def monitors(self) -> list[Monitor]:
        if self._cache is None:
            try:
                self._cache = dedupe_and_sort(self.reader())
                self._last_good = self._cache
            except Exception:
                # Degrade, never crash: reuse the last successful read. On a
                # cold cache this yields [], and pick() falls back to the root
                # box.
                self._cache = (
                    self._last_good if self._last_good is not None else []
                )
        return self._cache

    def root_rect(self) -> Rect:
        if self._root_rect is None:
            try:
                self._root_rect = self.root_rect_reader()
            except Exception:
                self._root_rect = Rect(0, 0, 1920, 1080)
        return self._root_rect

    def invalidate(self) -> None:
        # _last_good is deliberately NOT cleared - see __init__.
        self._cache = None
        self._root_rect = None

    def select_events(self) -> None:
        """Subscribe to RandR change notifications. Best-effort."""
        from Xlib.ext import randr
        try:
            randr.select_input(
                self.root,
                randr.RRScreenChangeNotifyMask
                | randr.RRCrtcChangeNotifyMask
                | randr.RROutputChangeNotifyMask,
            )
            self.display.sync()
        except Exception:
            pass

    def handles(self, event) -> bool:
        """True if `event` is a RandR change that should drop the cache."""
        from Xlib.ext import randr
        base = getattr(self, "_randr_base", None)
        if base is None:
            info = self.display.query_extension("RANDR")
            base = self._randr_base = info.first_event if info else -1
        return base >= 0 and event.type in (
            base + randr.RRScreenChangeNotify,
            base + randr.RRNotify,
        )

    # ---- X I/O -----------------------------------------------------------

    def _read_from_x(self) -> list[RawOutput]:
        from Xlib.ext import randr
        res = randr.get_screen_resources(self.root)
        primary = randr.get_output_primary(self.root).output
        out: list[RawOutput] = []
        for oid in res.outputs:
            info = randr.get_output_info(self.display, oid, res.config_timestamp)
            if info.crtc == 0:
                out.append(RawOutput(info.name, 0, 0, 0, 0, 0, False))
                continue
            crtc = randr.get_crtc_info(self.display, info.crtc, res.config_timestamp)
            out.append(RawOutput(
                name=info.name, crtc=info.crtc,
                x=crtc.x, y=crtc.y, w=crtc.width, h=crtc.height,
                primary=(oid == primary),
            ))
        return out

    def _read_root_rect_from_x(self) -> Rect:
        g = self.root.get_geometry()
        return Rect(0, 0, g.width, g.height)
