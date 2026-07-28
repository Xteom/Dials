#!/usr/bin/env python3
"""Feasibility probe: can a client grab numpad keys with an EMPTY modifier mask,
so that NumLock-on presses are never intercepted and still type digits?

Proves/disproves the core assumption of the numpad-panels design.
Uses XTEST (same path as real hardware) so passive grabs are exercised for real.
"""
import subprocess
import sys
import time

from Xlib import X, display, error
from Xlib.ext import xtest

# NumLock-off keysym -> keycode, from xmodmap -pke on this machine
KEYS = {
    "KP_Home/7": 79, "KP_Up/8": 80, "KP_Prior/9": 81,
    "KP_Left/4": 83, "KP_Begin/5": 84, "KP_Right/6": 85,
    "KP_End/1": 87, "KP_Down/2": 88, "KP_Next/3": 89,
    "KP_Insert/0": 90, "KP_Delete/.": 91,
    "KP_Divide": 106, "KP_Multiply": 63, "KP_Subtract": 82,
    "KP_Add": 86, "KP_Enter": 104,
}
PROBE_KC = 91  # numpad '.' — assign-mode key in the design

d = display.Display()
root = d.screen().root

if not d.has_extension("XTEST"):
    print("XTEST missing; cannot synthesize hardware-equivalent presses")
    sys.exit(1)


def numlock_on():
    out = subprocess.run(["xset", "q"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "LED mask" in line:
            return bool(int(line.split("LED mask:")[1].strip(), 16) & 0x2)
    return None


def set_numlock(want):
    if numlock_on() != want:
        subprocess.run(["xdotool", "key", "Num_Lock"], check=False)
        time.sleep(0.4)


def drain(seconds):
    """Collect grab-delivered key events for `seconds`."""
    got = []
    end = time.time() + seconds
    while time.time() < end:
        while d.pending_events():
            e = d.next_event()
            if e.type == X.KeyPress:
                got.append((e.detail, e.state))
        time.sleep(0.02)
    return got


original_numlock = numlock_on()
print(f"NumLock at start: {'ON' if original_numlock else 'OFF'}\n")

# ---- Part 1: can we grab every numpad key with mask 0? --------------------
print("=" * 62)
print("PART 1  grab each numpad keycode with modifier mask 0")
print("=" * 62)
conflicts = []
for name, kc in KEYS.items():
    catch = error.CatchError(error.BadAccess)
    root.grab_key(kc, 0, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
    d.sync()
    if catch.get_error():
        conflicts.append(name)
        print(f"  {name:<14} kc={kc:<4} BadAccess - already grabbed by another client")
    else:
        print(f"  {name:<14} kc={kc:<4} ok")
print()
if conflicts:
    print(f"  !! {len(conflicts)} conflict(s): {', '.join(conflicts)}")
else:
    print("  all 16 keys grabbable with an empty modifier mask")

# ---- Part 2: does the grab fire with NumLock OFF? -------------------------
victim = subprocess.Popen(["xterm", "-title", "grabtest-victim", "-geometry", "20x3+50+50"])
time.sleep(1.5)
subprocess.run(["xdotool", "search", "--name", "grabtest-victim", "windowactivate"],
               check=False, capture_output=True)
time.sleep(0.5)

print()
print("=" * 62)
print("PART 2  NumLock OFF -> press numpad '.' (keycode 91)")
print("=" * 62)
set_numlock(False)
drain(0.3)
xtest.fake_input(d, X.KeyPress, PROBE_KC)
xtest.fake_input(d, X.KeyRelease, PROBE_KC)
d.sync()
off_events = drain(1.0)
print(f"  delivered to us: {off_events}")
print(f"  VERDICT: {'CAUGHT -> hotkey fires (correct)' if off_events else 'NOT caught -> design broken'}")

# ---- Part 3: does the grab stay out of the way with NumLock ON? -----------
print()
print("=" * 62)
print("PART 3  NumLock ON -> press numpad '.' (same keycode 91)")
print("=" * 62)
set_numlock(True)
drain(0.3)
xtest.fake_input(d, X.KeyPress, PROBE_KC)
xtest.fake_input(d, X.KeyRelease, PROBE_KC)
d.sync()
on_events = drain(1.0)
print(f"  delivered to us: {on_events}")
print(f"  VERDICT: {'NOT caught -> types normally (correct)' if not on_events else 'CAUGHT -> would break number typing'}")

# ---- cleanup -------------------------------------------------------------
for kc in KEYS.values():
    root.ungrab_key(kc, 0)
d.sync()
victim.terminate()
set_numlock(original_numlock)

print()
print("=" * 62)
ok = bool(off_events) and not on_events and not conflicts
print(f"OVERALL: {'FEASIBLE - mask-0 grabs distinguish NumLock state' if ok else 'PROBLEM - see above'}")
print(f"NumLock restored to: {'ON' if numlock_on() else 'OFF'}")
