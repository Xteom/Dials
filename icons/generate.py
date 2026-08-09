#!/usr/bin/env python3
"""Generate the Dials tray icons: a spiral gastropod shell, three states.

Run from anywhere:  python3 icons/generate.py

WHY GENERATED. The shell is a logarithmic spiral stroked with a width that grows
with the radius. Those coordinates cannot be hand-authored or hand-tuned - a
single wrong number turns a conch into a blob, and nobody editing this later
could tell which of several hundred to change. Tune the constants below and
re-run instead.

A gastropod rather than a scallop, deliberately: the One Piece Dials the project
is named for are spiral shells, and it is what people mean by "seashell" - a
conch you can blow, or what a hermit crab lives in.

CONSTRAINT: the emitted files must keep <svg> on line 1. gdk-pixbuf identifies an
image by sniffing its leading bytes, so a comment ahead of the root element makes
the icon fail to load as "unrecognised format" - which shows up as a broken image
in the panel with nothing wrong in any log. tests/test_tray.py pins this.
"""
from __future__ import annotations

import math
from pathlib import Path

BOX = 16.0              #: the viewBox is BOX x BOX

# --- shape constants (tune these, then re-run) -------------------------------
#
# The shell is a COIL OF DECREASING THICKNESS, not a band between two spirals.
# The band version was tried first and rejected: at 16px a near-constant band
# reads as a drawn line - a snail coil, or an "@" - rather than as a shell body.
# Stroking the spiral centreline with a width proportional to the radius gives
# the taper a real conch has, from a fat body whorl down to a fine apex, and
# round linecaps make the segments merge into one smooth surface.

GROWTH = 0.20           #: b in r = a*e^(b*theta); larger = looser coil
THETA_START = -6.1      #: radians; the tight end, i.e. the apex
THETA_END = 3.30        #: radians; the wide end, i.e. the aperture lip
R_MAX = 4.75            #: centreline radius at THETA_END; sets the design scale
ROTATE = -2.05          #: radians; turns the aperture to face down-left
WIDTH_RATIO = 0.66      #: stroke width as a fraction of the local radius
SEGMENTS = 72           #: taper resolution; overlapping round caps hide seams

#: A flat log spiral has a CIRCULAR silhouette, and at 22px an outline is all you
#: get - so a flat coil reads as a disc, or as an "@". A conch is recognised by
#: its spindle profile: a bulbous body whorl tapering to a canal. Rotating the
#: coil and then squashing one axis produces that.
OBLIQUE_ROTATE = -30    #: degrees
OBLIQUE_SQUASH = 0.74   #: y scale applied after that rotation

#: Fraction of the viewBox the finished shell fills, on its longer axis.
#:
#: This exists because the first version filled only ~62% of the box and looked
#: visibly smaller than every neighbouring icon in the GNOME top bar. The shell
#: is drawn in whatever coordinates the spiral maths happens to produce, so it is
#: measured and fitted rather than positioned by hand: `_fit` computes the real
#: bounding box - including half of each stroke width, which a naive path bbox
#: misses - and emits the transform that scales it to this fraction and centres
#: it. Change this one number to resize the icon; nothing else needs touching.
FIT = 0.94

#: a is derived so the widest centreline radius lands exactly on R_MAX
A = R_MAX / math.exp(GROWTH * THETA_END)

_OBQ = math.radians(OBLIQUE_ROTATE)


def _radius(theta: float) -> float:
    return A * math.exp(GROWTH * theta)


def _point(theta: float) -> tuple[float, float]:
    """A point on the coil centreline, in design space.

    The oblique rotate-then-squash is applied HERE rather than as an SVG
    transform on the group, so that `_fit` can measure the shape it will actually
    emit. With the transform living in the SVG, the bounding box would have to be
    computed through it - two places to keep in agreement, and the fit would be
    silently wrong the first time they diverged.
    """
    r, t = _radius(theta), theta + ROTATE
    x, y = r * math.cos(t), r * math.sin(t)
    xr = x * math.cos(_OBQ) - y * math.sin(_OBQ)
    yr = x * math.sin(_OBQ) + y * math.cos(_OBQ)
    return xr, yr * OBLIQUE_SQUASH


def _width(theta: float) -> float:
    return max(0.30, _radius(theta) * WIDTH_RATIO)


def _thetas(n: int = SEGMENTS):
    span = THETA_END - THETA_START
    return [THETA_START + span * i / n for i in range(n + 1)]


def _fit() -> tuple[float, float, float]:
    """Return (scale, tx, ty) placing the shell at FIT of the box, centred.

    The bounding box has to include half of each stroke width: the coil is a
    stroked centreline, so its ink extends WIDTH/2 beyond every point, and
    fitting the centreline alone would push the fat body whorl off the canvas.
    """
    xs, ys = [], []
    for t in _thetas(SEGMENTS * 4):          # oversample; cheap and exact enough
        (x, y), h = _point(t), _width(t) / 2
        xs += [x - h, x + h]
        ys += [y - h, y + h]
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    scale = BOX * FIT / max(hi_x - lo_x, hi_y - lo_y)
    tx = (BOX - (hi_x - lo_x) * scale) / 2 - lo_x * scale
    ty = (BOX - (hi_y - lo_y) * scale) / 2 - lo_y * scale
    return scale, tx, ty


def coil(colour: str) -> list[str]:
    """The shell body: overlapping stroked arcs, each thinner than the last."""
    out = []
    span = THETA_END - THETA_START
    for i in range(SEGMENTS):
        t0 = THETA_START + span * i / SEGMENTS
        t1 = min(THETA_START + span * (i + 1.35) / SEGMENTS, THETA_END)
        (x0, y0), (x1, y1) = _point(t0), _point(t1)
        out.append(f'<path d="M {x0:.2f} {y0:.2f} L {x1:.2f} {y1:.2f}" '
                   f'stroke="{colour}" stroke-width="{_width((t0 + t1) / 2):.2f}"/>')
    return out


def seam() -> str:
    """The suture line: where each whorl meets the one it coils over.

    Offset inward from the centreline so it sits just inside the coil's outer
    face. This is what makes the shape read as three dimensional rather than as
    a flat spiral.
    """
    pts = []
    for t in _thetas():
        x, y = _point(t)
        nx, ny = _point(t + 0.14)                       # local tangent direction
        dx, dy = nx - x, ny - y
        length = math.hypot(dx, dy) or 1.0
        off = _width(t) * 0.26
        pts.append(f"{x + dy / length * off:.2f},{y - dx / length * off:.2f}")
    return "M " + " ".join(pts)


def aperture(fill: str) -> str:
    """The opening at the wide end - the part you would blow into.

    Without it the widest whorl simply stops, and the icon looks like a spiral
    with a blunt end rather than a shell you could pick up.
    """
    x, y = _point(THETA_END)
    r = _width(THETA_END) / 2
    ang = math.degrees(math.atan2(*reversed(
        [b - a for a, b in zip(_point(THETA_END - 0.25), _point(THETA_END))])))
    return (f'<ellipse cx="{x:.2f}" cy="{y:.2f}" rx="{r * 0.50:.2f}" '
            f'ry="{r * 0.92:.2f}" fill="{fill}" stroke="none" opacity="0.9" '
            f'transform="rotate({ang:.1f} {x:.2f} {y:.2f})"/>')


HEADER = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
          'viewBox="0 0 16 16">')

DOC = """  <!-- GENERATED by icons/generate.py - edit the constants there, not these paths.
       A spiral gastropod shell (conch), the shape the One Piece Dials take.
       {state}
       The <svg> element must stay on line 1: gdk-pixbuf sniffs leading bytes to
       recognise the format, so a comment above it breaks icon loading. -->"""

STATES = {
    "dials-shell": dict(
        note="LIVE: NumLock is off and the numpad drives your windows. Amber =\n"
             '       "charged", readable on both light and dark panels.',
        body="#F2A93B", seam="#8A5410", mouth="#7A4A12",
        group_op="1", slash=False,
    ),
    "dials-shell-dormant": dict(
        note="DORMANT: NumLock is on, the keypad types digits, no Dial can fire.\n"
             "       Same silhouette in grey - the discharged state.",
        body="#9AA0A6", seam="#6E747A", mouth="#5C6167",
        group_op="1", slash=False,
    ),
    "dials-shell-paused": dict(
        note="PAUSED: `dials pause` stood the daemon down, so nothing is grabbed\n"
             "       at all whatever NumLock says. Slashed, to differ from DORMANT.",
        body="#9AA0A6", seam="#6E747A", mouth="#5C6167",
        # 0.78, not 0.5: at half opacity the slash swamped the shell and the icon
        # read as "a red line" rather than as a disabled Dials.
        group_op="0.78", slash=True,
    ),
}


def render(spec: dict) -> str:
    scale, tx, ty = _fit()
    body = "\n".join(f"        {p}" for p in coil(spec["body"]))
    # The slash is in viewBox coordinates, not design space, so it spans the
    # canvas independently of how the shell is fitted inside it.
    slash = ('\n    <!-- slash last, so it stays legible over the whorls -->\n'
             '    <path d="M 1.9 14.1 L 14.1 1.9" stroke="#E0564A" '
             'stroke-width="1.8" stroke-linecap="round" fill="none"/>'
             ) if spec["slash"] else ""
    return f"""{HEADER}
{DOC.format(state=spec["note"])}
  <g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g opacity="{spec["group_op"]}">
      <g transform="translate({tx:.3f} {ty:.3f}) scale({scale:.4f})">
        <!-- body whorls: one stroked segment per step, tapering to the apex -->
{body}
        <!-- suture line, which makes the coil look three dimensional -->
        <path d="{seam()}" stroke="{spec["seam"]}" stroke-width="0.55"
              opacity="0.65"/>
        <!-- the mouth of the shell -->
        {aperture(spec["mouth"])}
      </g>
    </g>{slash}
  </g>
</svg>
"""


def main() -> int:
    here = Path(__file__).resolve().parent
    scale, tx, ty = _fit()
    print(f"  fit: scale={scale:.3f} translate=({tx:.2f}, {ty:.2f}) "
          f"-> shell fills {FIT:.0%} of the {BOX:.0f}px box")
    for name, spec in STATES.items():
        path = here / f"{name}.svg"
        path.write_text(render(spec))
        print(f"  wrote {path.name}  ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
