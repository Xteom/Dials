#!/usr/bin/env python3
"""Generate the Dials tray icons: a spiral gastropod shell, three states.

Run from anywhere:  python3 icons/generate.py

WHY GENERATED. The shell is the region between two logarithmic spirals, which
gives a whorl band that tapers correctly from the apex to the aperture. Those
coordinates cannot be hand-authored or hand-tuned - a single wrong control point
turns a conch into a blob, and nobody editing this later could tell which of
sixty numbers to change. Tune the constants below and re-run instead.

A gastropod rather than a scallop, deliberately: the One Piece Dials the project
is named for are spiral shells, and it is the shape people mean by "seashell" -
a conch you can blow, or what a hermit crab lives in.

CONSTRAINT: the emitted files must keep <svg> on line 1. gdk-pixbuf identifies
an image by sniffing its leading bytes, so a comment ahead of the root element
makes the icon fail to load as "unrecognised format" - which shows up as a
broken image in the panel with nothing wrong in any log. tests/test_tray.py pins
this.
"""
from __future__ import annotations

import math
from pathlib import Path

# --- shape constants (tune these, then re-run) -------------------------------
#
# The shell is a COIL OF DECREASING THICKNESS, not a band between two spirals.
# The band version was tried first and rejected: at 16px a constant-ish band
# reads as a drawn line - a snail coil or an "@" - rather than a shell body.
# Stroking the spiral centreline with a width proportional to the radius gives
# the taper a real conch has, from a fat body whorl down to a fine apex, and
# round linecaps make the segments merge into one smooth surface.

CX, CY = 7.7, 8.0       #: spiral pole, in the 16x16 viewBox
GROWTH = 0.20           #: b in r = a*e^(b*theta); larger = looser coil
THETA_START = -6.1      #: radians; the tight end, i.e. the apex
THETA_END = 3.30        #: radians; the wide end, i.e. the aperture lip
R_MAX = 4.75            #: centreline radius at THETA_END, which sets the scale
ROTATE = -2.05          #: radians; turns the aperture to face down-left
WIDTH_RATIO = 0.66      #: stroke width as a fraction of the local radius
SEGMENTS = 72           #: taper resolution; overlapping round caps hide seams

#: A flat log spiral has a CIRCULAR silhouette, and at 22px an outline is all
#: you get - so a flat coil reads as a disc, or as an "@". A conch is read by its
#: spindle outline: bulbous body whorl tapering to a canal. Rotating and then
#: squashing one axis turns the coil oblique and gives it that spindle. Applied
#: as an SVG transform rather than baked into the maths so the two effects stay
#: separately tunable; it scales the stroke widths anisotropically too, which
#: makes the round caps slightly elliptical and reads as organic rather than
#: geometric.
OBLIQUE_ROTATE = -30    #: degrees
OBLIQUE_SQUASH = 0.74   #: y scale after that rotation

#: a is derived so the widest point lands exactly on R_MAX
A = R_MAX / math.exp(GROWTH * THETA_END)


def _radius(theta: float) -> float:
    return A * math.exp(GROWTH * theta)


def _spiral(theta: float) -> tuple[float, float]:
    r = _radius(theta)
    t = theta + ROTATE
    return CX + r * math.cos(t), CY + r * math.sin(t)


def coil(colour: str, opacity: str = "1") -> list[str]:
    """The shell body: overlapping stroked arcs, each thinner than the last."""
    out = []
    span = THETA_END - THETA_START
    for i in range(SEGMENTS):
        t0 = THETA_START + span * i / SEGMENTS
        t1 = THETA_START + span * (i + 1.35) / SEGMENTS   # overlap hides seams
        t1 = min(t1, THETA_END)
        (x0, y0), (x1, y1) = _spiral(t0), _spiral(t1)
        w = max(0.30, _radius((t0 + t1) / 2) * WIDTH_RATIO)
        out.append(f'<path d="M {x0:.2f} {y0:.2f} L {x1:.2f} {y1:.2f}" '
                   f'stroke="{colour}" stroke-width="{w:.2f}" opacity="{opacity}"/>')
    return out


def seam() -> str:
    """The suture line: where each whorl meets the one it coils over.

    Drawn just inside the coil's outer face, which is what makes the shape read
    as three dimensional rather than as a flat spiral.
    """
    pts = []
    for i in range(SEGMENTS + 1):
        t = THETA_START + (THETA_END - THETA_START) * i / SEGMENTS
        r = _radius(t) - _radius(t) * WIDTH_RATIO * 0.28
        a = t + ROTATE
        pts.append(f"{CX + r * math.cos(a):.2f},{CY + r * math.sin(a):.2f}")
    return "M " + " ".join(pts)


def aperture() -> str:
    """The opening at the wide end - the part you would blow into.

    An ellipse across the mouth of the coil, angled along the lip. Without it
    the widest whorl just stops, and the icon looks like a spiral with a blunt
    end instead of a shell you could pick up.
    """
    x, y = _spiral(THETA_END)
    r = _radius(THETA_END) * WIDTH_RATIO / 2
    ang = math.degrees(THETA_END + ROTATE) + 90
    return (f'<ellipse cx="{x:.2f}" cy="{y:.2f}" rx="{r * 0.52:.2f}" '
            f'ry="{r * 0.95:.2f}" transform="rotate({ang:.1f} {x:.2f} {y:.2f})"')


HEADER = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'

DOC = """  <!-- GENERATED by icons/generate.py - edit the constants there, not this path.
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


def render(name: str, spec: dict) -> str:
    body = "\n".join(f"      {p}" for p in coil(spec["body"]))
    slash = ('\n    <!-- slash last, so it stays legible over the whorls -->\n'
             '    <path d="M 2.6 13.4 L 13.4 2.6" stroke="#E0564A" '
             'stroke-width="1.7" stroke-linecap="round" fill="none"/>'
             ) if spec["slash"] else ""
    oblique = (f'translate({CX} {CY}) rotate({OBLIQUE_ROTATE}) '
               f'scale(1 {OBLIQUE_SQUASH}) translate({-CX} {-CY})')
    return f"""{HEADER}
{DOC.format(state=spec["note"])}
  <g fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g opacity="{spec["group_op"]}">
      <g transform="{oblique}">
        <!-- body whorls: one stroked segment per step, tapering to the apex -->
{body}
        <!-- suture line, which makes the coil look three dimensional -->
        <path d="{seam()}" stroke="{spec["seam"]}" stroke-width="0.55"
              opacity="0.65"/>
        <!-- the mouth of the shell -->
        {aperture()} fill="{spec["mouth"]}" opacity="0.9"/>
      </g>
    </g>{slash}
  </g>
</svg>
"""


def main() -> int:
    here = Path(__file__).resolve().parent
    for name, spec in STATES.items():
        path = here / f"{name}.svg"
        path.write_text(render(name, spec))
        print(f"  wrote {path.name}  ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
