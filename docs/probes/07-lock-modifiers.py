#!/usr/bin/env python3
"""Probe 7: does CapsLock break a mask-0 grab?

The spec grabs numpad keycodes with modifier mask 0 ONLY. X11 requires the
modifier state to match the grab mask exactly, and CapsLock sets LockMask (0x02).
If that is true, every Dial silently stops working while CapsLock is on.

Tests the bug and the proposed fix (also grab mask=LockMask) in one run.
Restores CapsLock and NumLock to their original states.
"""
import subprocess
import sys
import time

from Xlib import X, display, error
from Xlib.ext import xtest

KC = 91  # numpad '.'
LOCK = X.LockMask     # CapsLock  -> 0x02
NUM = X.Mod2Mask      # NumLock   -> 0x10

d = display.Display()
root = d.screen().root
print(f"LockMask=0x{LOCK:02x}  Mod2Mask=0x{NUM:02x}\n")


def leds():
    out = subprocess.run(["xset", "q"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "LED mask" in line:
            return int(line.split("LED mask:")[1].strip(), 16)
    return 0


def caps_on():
    return bool(leds() & 0x1)


def num_on():
    return bool(leds() & 0x2)


def set_lock(key, want, getter):
    if getter() != want:
        subprocess.run(["xdotool", "key", key], check=False)
        time.sleep(0.4)


def drain(seconds=0.8):
    got = []
    end = time.time() + seconds
    while time.time() < end:
        while d.pending_events():
            e = d.next_event()
            if e.type == X.KeyPress:
                got.append((e.detail, e.state))
        time.sleep(0.02)
    return got


def press():
    xtest.fake_input(d, X.KeyPress, KC)
    xtest.fake_input(d, X.KeyRelease, KC)
    d.sync()


def grab(mask):
    catch = error.CatchError(error.BadAccess)
    root.grab_key(KC, mask, True, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
    d.sync()
    return catch.get_error() is None


orig_caps, orig_num = caps_on(), num_on()
print(f"original: CapsLock={'ON' if orig_caps else 'OFF'}  NumLock={'ON' if orig_num else 'OFF'}")

# Absorb any keystroke that is NOT caught by the grab, so stray '.' characters
# do not land in the user's terminal.
victim = subprocess.Popen(["gnome-calculator"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(4)
subprocess.run(["xdotool", "search", "--class", "gnome-calculator", "windowactivate"],
               check=False, capture_output=True)
time.sleep(1)

set_lock("Num_Lock", False, num_on)
set_lock("Caps_Lock", False, caps_on)

if not grab(0):
    print("could not grab with mask 0")
    victim.terminate()
    sys.exit(1)

results = {}

print()
print("=" * 66)
print("A  baseline: CapsLock OFF, NumLock OFF, grab mask 0")
print("=" * 66)
drain(0.3)
press()
ev = drain()
results["caps_off"] = bool(ev)
print(f"  events: {ev}")
print(f"  {'CAUGHT (expected)' if ev else 'NOT caught (unexpected!)'}")

print()
print("=" * 66)
print("B  the suspicion: CapsLock ON, still only mask 0 grabbed")
print("=" * 66)
set_lock("Caps_Lock", True, caps_on)
drain(0.3)
press()
ev = drain()
results["caps_on_mask0"] = bool(ev)
print(f"  events: {ev}")
if ev:
    print("  CAUGHT -> CapsLock does NOT break the grab; spec is fine as written")
else:
    print("  NOT CAUGHT -> CONFIRMED BUG: all Dials die while CapsLock is on")

print()
print("=" * 66)
print("C  the proposed fix: also grab mask = LockMask, CapsLock still ON")
print("=" * 66)
ok = grab(LOCK)
print(f"  grab(mask=LockMask) installed: {ok}")
drain(0.3)
press()
ev = drain()
results["caps_on_masklock"] = bool(ev)
print(f"  events: {ev}   (state should show 0x{LOCK:02x})")
print(f"  {'CAUGHT -> fix works' if ev else 'NOT caught -> fix insufficient'}")

print()
print("=" * 66)
print("D  regression check: NumLock ON + CapsLock ON must still type a digit")
print("=" * 66)
set_lock("Num_Lock", True, num_on)
drain(0.3)
press()
ev = drain()
results["num_on_caps_on"] = bool(ev)
print(f"  events: {ev}")
if ev:
    print("  CAUGHT -> BAD: would break digit typing while CapsLock is on")
else:
    print("  not caught -> correct, types normally")

# ---- cleanup ----
for m in (0, LOCK):
    root.ungrab_key(KC, m)
d.sync()
set_lock("Caps_Lock", orig_caps, caps_on)
set_lock("Num_Lock", orig_num, num_on)
victim.terminate()

print()
print("=" * 66)
print("SUMMARY")
print("=" * 66)
bug = results["caps_off"] and not results["caps_on_mask0"]
fixed = results["caps_on_masklock"] and not results["num_on_caps_on"]
print(f"  CapsLock breaks a mask-0-only grab : {bug}")
print(f"  grabbing {{0, LockMask}} fixes it   : {fixed}")
print(f"  restored: CapsLock={'ON' if caps_on() else 'OFF'} NumLock={'ON' if num_on() else 'OFF'}")
if bug:
    print("\n  => spec must grab the cross product of ignorable lock modifiers,")
    print("     i.e. {0, LockMask}, while never including Mod2Mask (NumLock).")
