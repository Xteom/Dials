#!/usr/bin/env python3
"""Probe 5: what actually reserves screen space, and can we observe NumLock?

(a) Which windows set _NET_WM_STRUT / _NET_WM_STRUT_PARTIAL, and how does
    _NET_WORKAREA compare to the raw RandR monitor rects? Decides whether
    cosmic-dock affects Dial geometry.
(b) Can NumLock state be read, and can changes be observed event-driven, so the
    tray icon can light up when the Dial layer is live?
"""
import subprocess

from Xlib import X, display

d = display.Display()
root = d.screen().root


def prop(win, name):
    a = d.intern_atom(name)
    p = win.get_full_property(a, X.AnyPropertyType)
    return list(p.value) if p else None


print("=" * 70)
print("(a1) windows that reserve space via struts")
print("=" * 70)
out = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"],
                     capture_output=True, text=True).stdout
ids = [int(t.strip(), 16) for t in out.split("#", 1)[-1].split(",") if t.strip()]
print(f"  managed windows: {len(ids)}")
found_struts = False
for wid in ids:
    w = d.create_resource_object("window", wid)
    sp = prop(w, "_NET_WM_STRUT_PARTIAL")
    s = prop(w, "_NET_WM_STRUT")
    if sp or s:
        found_struts = True
        cls = prop(w, "WM_CLASS")
        name = bytes(cls).decode("latin-1").replace("\x00", " ").strip() if cls else "?"
        print(f"  {hex(wid)} {name!r}")
        print(f"      STRUT_PARTIAL (l,r,t,b,...): {sp}")
        print(f"      STRUT         (l,r,t,b):     {s}")
if not found_struts:
    print("  NONE of the managed windows reserve space")

# The shell's top bar / dock are not in _NET_CLIENT_LIST (they are shell chrome,
# not managed windows), so compare workarea vs the full screen instead.
print()
print("=" * 70)
print("(a2) _NET_WORKAREA vs monitor rects")
print("=" * 70)
wa = prop(root, "_NET_WORKAREA")
geo = root.get_geometry()
print(f"  root geometry:  {geo.width}x{geo.height}")
print(f"  _NET_WORKAREA:  {wa[:4] if wa else None}   (x, y, w, h of desktop 0)")

mon = subprocess.run(["xrandr", "--listmonitors"], capture_output=True, text=True).stdout
print("  xrandr --listmonitors:")
for line in mon.strip().splitlines()[1:]:
    print(f"      {line.strip()}")

if wa:
    x, y, w, h = wa[0], wa[1], wa[2], wa[3]
    print()
    print(f"  workarea inset from root: left={x} top={y} "
          f"right={geo.width - (x + w)} bottom={geo.height - (y + h)}")
    print("  -> a non-zero top inset is the GNOME top bar; a non-zero bottom inset")
    print("     would mean the dock reserves space (it should not, dock-fixed=false)")

print()
print("=" * 70)
print("(b) observing NumLock for the tray indicator")
print("=" * 70)
kc = d.get_keyboard_control()
print(f"  get_keyboard_control().led_mask = {kc.led_mask:#010b}  "
      f"-> NumLock {'ON' if kc.led_mask & 0x2 else 'OFF'}")
print(f"  xset LED mask agrees: "
      f"{subprocess.run(['xset', 'q'], capture_output=True, text=True).stdout.split('LED mask:')[1].split()[0]}")

# Is XKB reachable from python-xlib (needed for event-driven indicator updates)?
print()
print(f"  server has XKEYBOARD extension: {bool(d.query_extension('XKEYBOARD'))}")
try:
    import Xlib.ext.xkb  # noqa: F401
    print("  python-xlib exposes Xlib.ext.xkb: YES -> event-driven possible")
except ImportError:
    print("  python-xlib exposes Xlib.ext.xkb: NO")
import Xlib.ext
print(f"  python-xlib extensions available: {Xlib.ext.__all__}")
print()
print("  -> if no xkb: tray reads led_mask on a low-rate timer, or the daemon")
print("     grabs Num_Lock in sync mode and replays it")
