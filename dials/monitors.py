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
    #: Raw EDID base block. New fields are APPENDED with defaults because this
    #: is constructed positionally throughout the test suite.
    edid: bytes | None = None
    #: True only if the property read raised - never for a display that simply
    #: has no EDID.
    edid_failed: bool = False


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


#: Connector types that mean "the panel built into this machine".
INTERNAL_TYPES = ("edp", "lvds", "dsi")


def parse_selector(value: str) -> tuple[str, str]:
    """Split a config `monitor` value into (kind, name).

        "edid:NAME"       -> ("edid", "NAME")
        "connector:NAME"  -> ("connector", "NAME")
        "internal"        -> ("internal", "")
        "NAME"            -> ("connector", "NAME")   - unchanged meaning

    Raises ValueError, NOT ConfigError: `config.py` wraps it. monitors.py must
    not import config.py - neither module knows the other exists today, and a
    cycle here would be gratuitous.

    Splits on the FIRST colon so an EDID name containing one survives.
    """
    text = value.strip()
    if not text:
        raise ValueError("monitor must not be empty")
    if text == "internal":
        return ("internal", "")
    head, sep, tail = text.partition(":")
    if not sep:
        return ("connector", text)
    kind, name = head.strip(), tail.strip()
    if kind not in ("edid", "connector"):
        raise ValueError(
            f"unknown monitor selector {kind!r}; use 'edid:', 'connector:', "
            "'internal', or a bare connector name"
        )
    if not name:
        raise ValueError(f"{kind}: selector has an empty name")
    return (kind, name)


def is_internal(connector: str) -> bool:
    """True if `connector` is this machine's built-in panel.

    Keys off the connector TYPE - the part before the first '-' - because the
    rename this feature exists to survive only ever moves the INDEX:
    eDP-1 <-> eDP-1-1, HDMI-0 <-> HDMI-1-0. The type never changes.

    RandR's ConnectorType property ("Panel") would be a stronger signal and
    cannot be used. Measured on this machine, the internal panel is the one
    output that does NOT expose it - modesetting drives that panel under both
    boot configurations and never sets the property - while the NVIDIA driver
    sets it on the external output, where it is useless.
    """
    return connector.split("-")[0].lower() in INTERNAL_TYPES


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
            display_name=edid_name(r.edid),
            identity_reliable=not r.edid_failed,
        )
        for r in by_crtc.values()
    ]
    return sorted(monitors, key=lambda m: (m.rect.x, m.rect.y, m.name))


def _fallback(
    monitors: list[Monitor], root_rect: Rect, problem: str
) -> tuple[Monitor, str | None]:
    """The unchanged destination chain: primary -> first -> root box.

    Split out of `pick` so every selector kind shares one set of destinations
    and only the `problem` half of the reason differs.
    """
    for m in monitors:
        if m.primary:
            return m, f"{problem}; using primary {m.name!r}"
    if monitors:
        m = monitors[0]
        return m, f"{problem} and no primary; using first {m.name!r}"
    return (
        Monitor(name="<root>", rect=root_rect, primary=True, crtc=0),
        f"{problem} and no usable monitors; using root box",
    )


def pick(
    selector: str, monitors: list[Monitor], root_rect: Rect
) -> tuple[Monitor, str | None]:
    """Resolve a Dial's monitor selector to a monitor.

    Returns (monitor, fallback_reason). `reason` is None only on an exact,
    UNAMBIGUOUS match; otherwise it is a one-line explanation for
    `dials status`, so a Dial landing on the wrong screen is visible rather
    than mysterious.

    Three outcomes, not two. Two displays matching one selector must NOT
    resolve to the first of them: that would look like success, and looking
    like success while placing windows on the wrong screen is the failure this
    exists to end.
    """
    try:
        kind, name = parse_selector(selector)
    except ValueError as exc:
        return _fallback(monitors, root_rect, f"invalid monitor selector: {exc}")

    if kind == "edid":
        matches = [m for m in monitors if m.display_name == name]
        present = ", ".join(
            m.display_name for m in monitors if m.display_name
        ) or "none"
        absent = f"no display named {name!r} (displays present: {present})"
        ambiguous = f"display name {name!r} is ambiguous"
    elif kind == "internal":
        matches = [m for m in monitors if is_internal(m.name)]
        connectors = ", ".join(m.name for m in monitors) or "none"
        absent = f"no internal panel found (connectors: {connectors})"
        ambiguous = "more than one internal panel"
    else:
        matches = [m for m in monitors if m.name == name]
        absent = f"monitor {name!r} absent"
        ambiguous = f"connector {name!r} is ambiguous"

    if len(matches) == 1:
        return matches[0], None
    if matches:
        found = ", ".join(m.name for m in matches)
        return _fallback(monitors, root_rect, f"{ambiguous} ({found})")
    return _fallback(monitors, root_rect, absent)


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
