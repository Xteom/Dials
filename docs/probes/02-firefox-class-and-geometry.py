#!/usr/bin/env python3
"""Probe 2: does `firefox --class` actually set WM_CLASS on Firefox 152, and does
mutter honour the EWMH state hints the panel design depends on?

Uses a throwaway --profile dir so the user's profiles.ini is never touched.
Cleans up the window and the profile dir at the end.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from Xlib import X, display, protocol

CLASS = "FFPanelProbe"
d = display.Display()
root = d.screen().root
profile = tempfile.mkdtemp(prefix="ffpanelprobe-")


def atom(name):
    return d.intern_atom(name)


def send_state(win_id, action, *props):
    """action: 0=remove 1=add 2=toggle"""
    atoms = [atom(p) for p in props]
    data = [action, atoms[0], atoms[1] if len(atoms) > 1 else 0, 2, 0]
    win = d.create_resource_object("window", win_id)
    ev = protocol.event.ClientMessage(
        window=win, client_type=atom("_NET_WM_STATE"), data=(32, data)
    )
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()


def wm_state(win_id):
    out = subprocess.run(["xprop", "-id", hex(win_id), "_NET_WM_STATE"],
                         capture_output=True, text=True).stdout
    return [s for s in out.split("=")[-1].replace(",", " ").split() if s.startswith("_NET")]


def geometry(win_id):
    out = subprocess.run(["xdotool", "getwindowgeometry", "--shell", str(win_id)],
                         capture_output=True, text=True).stdout
    g = dict(l.split("=") for l in out.strip().splitlines() if "=" in l)
    return int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])


print(f"launching firefox --class={CLASS} with throwaway profile\n")
proc = subprocess.Popen(
    ["firefox", "--profile", profile, "--class", CLASS, "--no-remote",
     "--new-instance", "--window-size", "900,600", "about:blank"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

# ---- Probe A: WM_CLASS -----------------------------------------------------
win_id = None
for _ in range(40):
    time.sleep(0.5)
    r = subprocess.run(["xdotool", "search", "--class", CLASS],
                       capture_output=True, text=True)
    ids = [int(x) for x in r.stdout.split()]
    if ids:
        win_id = ids[-1]
        break

print("=" * 64)
print("PROBE A  does --class set WM_CLASS?")
print("=" * 64)
if win_id is None:
    print(f"  no window matched class={CLASS}")
    print("  falling back: find ANY firefox window and show its real WM_CLASS")
    r = subprocess.run(["xdotool", "search", "--class", "irefox"],
                       capture_output=True, text=True)
    for wid in [int(x) for x in r.stdout.split()][-3:]:
        out = subprocess.run(["xprop", "-id", hex(wid), "WM_CLASS"],
                             capture_output=True, text=True).stdout.strip()
        print(f"    {hex(wid)}  {out}")
    print("  VERDICT: --class did NOT take effect -> need a different id strategy")
else:
    out = subprocess.run(["xprop", "-id", hex(win_id), "WM_CLASS"],
                         capture_output=True, text=True).stdout.strip()
    print(f"  window {hex(win_id)}")
    print(f"  {out}")
    print(f"  VERDICT: --class WORKS -> unambiguous match on '{CLASS}'")

if win_id is None:
    proc.terminate()
    shutil.rmtree(profile, ignore_errors=True)
    sys.exit(1)

time.sleep(1)

# ---- Probe B: EWMH state hints --------------------------------------------
print()
print("=" * 64)
print("PROBE B  does mutter honour the panel state hints?")
print("=" * 64)
print(f"  before: {wm_state(win_id) or '(none)'}")
for prop in ("_NET_WM_STATE_ABOVE", "_NET_WM_STATE_STICKY",
             "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_SKIP_PAGER"):
    send_state(win_id, 1, prop)
    time.sleep(0.4)
    got = wm_state(win_id)
    print(f"  add {prop:<28} -> {'ACCEPTED' if prop in got else 'REJECTED'}")
print(f"  after:  {wm_state(win_id)}")

# ---- Probe C: geometry ----------------------------------------------------
print()
print("=" * 64)
print("PROBE C  can we force panel geometry? (HDMI-0 ultrawide, 50% x 100%)")
print("=" * 64)
print(f"  before: {geometry(win_id)}")
target = (860, 0, 1720, 1400)  # x, y, w, h inside HDMI-0 (3440x1440 @ +0+0)
subprocess.run(["xdotool", "windowmove", str(win_id), str(target[0]), str(target[1])])
subprocess.run(["xdotool", "windowsize", str(win_id), str(target[2]), str(target[3])])
time.sleep(0.8)
after = geometry(win_id)
print(f"  target: {target}")
print(f"  after:  {after}")
dx, dy = abs(after[0] - target[0]), abs(after[1] - target[1])
dw, dh = abs(after[2] - target[2]), abs(after[3] - target[3])
print(f"  drift:  x{dx} y{dy} w{dw} h{dh}  (small drift = WM frame offset, fine)")
print(f"  VERDICT: {'geometry control works' if dw < 40 and dh < 60 else 'geometry fought by WM'}")

# ---- Probe D: hide / show round trip -------------------------------------
print()
print("=" * 64)
print("PROBE D  minimize + restore round trip (the show/hide mechanism)")
print("=" * 64)
subprocess.run(["xdotool", "windowminimize", str(win_id)])
time.sleep(0.8)
hidden = wm_state(win_id)
print(f"  after minimize: {'_NET_WM_STATE_HIDDEN' in hidden} (hidden={hidden})")
subprocess.run(["xdotool", "windowactivate", str(win_id)])
time.sleep(0.8)
shown = wm_state(win_id)
print(f"  after activate: hidden={'_NET_WM_STATE_HIDDEN' in shown}")
print(f"  state survived round trip: {[s for s in shown if 'ABOVE' in s or 'STICKY' in s or 'SKIP' in s]}")
print(f"  VERDICT: {'show/hide works and keeps panel hints' if '_NET_WM_STATE_HIDDEN' not in shown else 'restore failed'}")

# ---- cleanup -------------------------------------------------------------
print()
print("cleaning up probe window and throwaway profile...")
proc.terminate()
try:
    proc.wait(timeout=10)
except subprocess.TimeoutExpired:
    proc.kill()
shutil.rmtree(profile, ignore_errors=True)
print(f"profile dir removed: {not os.path.exists(profile)}")
