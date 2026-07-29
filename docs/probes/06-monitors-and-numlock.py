#!/usr/bin/env python3
"""Probe 6: monitor robustness + the cost of reading NumLock.

(a) Can python-xlib enumerate monitors (name + rect) without shelling to xrandr?
(b) Can it subscribe to RandR change events, so a monitor being unplugged
    invalidates the cache event-driven instead of by polling?
(c) How expensive is get_keyboard_control() — the tray's NumLock indicator read?
"""
import time

from Xlib import X, display
from Xlib.ext import randr

d = display.Display()
root = d.screen().root

print("=" * 70)
print("(a) enumerate monitors via python-xlib RandR")
print("=" * 70)
print(f"  randr extension present: {bool(d.query_extension('RANDR'))}")
ver = randr.query_version(d)
print(f"  RandR version: {ver.major_version}.{ver.minor_version}")

print(f"  has get_monitors (RandR 1.5): {hasattr(randr, 'get_monitors')}")

# RandR 1.2 path: screen resources -> outputs -> crtcs
res = randr.get_screen_resources(root)
primary_output = randr.get_output_primary(root).output
print(f"  outputs: {len(res.outputs)}   primary output id: {primary_output}")

monitors = []
for out_id in res.outputs:
    oi = randr.get_output_info(d, out_id, res.config_timestamp)
    if oi.crtc == 0:
        print(f"    {oi.name:<10} disconnected / no crtc")
        continue
    ci = randr.get_crtc_info(d, oi.crtc, res.config_timestamp)
    monitors.append((oi.name, ci.x, ci.y, ci.width, ci.height, out_id == primary_output))
    print(f"    {oi.name:<10} {ci.width}x{ci.height}+{ci.x}+{ci.y}"
          f"  primary={out_id == primary_output}")

print(f"  usable monitors: {len(monitors)}")
prim = [m[0] for m in monitors if m[5]]
print(f"  primary: {prim or 'NONE REPORTED'}")

print()
print("=" * 70)
print("(b) subscribe to RandR change events (no polling)")
print("=" * 70)
try:
    randr.select_input(
        root,
        randr.RRScreenChangeNotifyMask
        | randr.RRCrtcChangeNotifyMask
        | randr.RROutputChangeNotifyMask,
    )
    d.sync()
    print("  select_input for RRScreenChangeNotify: OK")
    print("  -> monitor cache can be invalidated on hotplug, event-driven")
except Exception as exc:  # noqa: BLE001
    print(f"  select_input FAILED: {exc!r}")

print()
print("=" * 70)
print("(c) cost of one NumLock read (tray indicator)")
print("=" * 70)
N = 2000
t0 = time.perf_counter()
for _ in range(N):
    d.get_keyboard_control()
elapsed = time.perf_counter() - t0
per = elapsed / N
print(f"  {N} reads in {elapsed*1000:.1f} ms -> {per*1e6:.1f} us per read")
print(f"  at a 1 Hz tray poll that is {per*100:.6f}% of one core")

print()
print("=" * 70)
print("(d) degenerate-monitor handling inputs")
print("=" * 70)
geo = root.get_geometry()
print(f"  root bounding box: {geo.width}x{geo.height}")
print("  cases geometry.py must survive:")
for case in (
    "named monitor absent (DP-1-1 today) -> fall back to primary",
    "no monitor flagged primary -> fall back to first, then root box",
    "zero monitors reported -> use root bounding box",
    "monitor rect partially offscreen / negative offset -> clamp",
    "layout changes while a Dial is shown -> re-resolve on next show",
):
    print(f"    - {case}")
