#!/usr/bin/env python3
"""Probe 8: settle the codex review's disputed and unverified claims.

Uses windows this client creates itself, so nothing depends on xterm mapping or on
a third-party app's quirks.

A. THE CORE PREMISE, FOR ALL 16 KEYS, BOTH DIRECTIONS.
   Earlier probes only checked keycode 91, and only checked "not delivered to us"
   rather than "actually reaches the application". With the corrected grab set
   {0, LockMask}:
     NumLock off -> event must arrive at the GRAB (root)
     NumLock on  -> event must arrive at OUR WINDOW, carrying Mod2, as the digit
   owner_events=False so grabbed keys always land on root and the two cases are
   unambiguous.

B. IS _NET_WM_STATE_FOCUSED EXCLUSIVE?
   Codex claims EWMH permits the WM to set FOCUSED on more than one window (e.g. a
   modal dialog and its parent), which would make it the wrong predicate for the
   three-state toggle. Tested with a real parent + WM_TRANSIENT_FOR child.
"""
import subprocess
import time

from Xlib import X, Xatom, display, error
from Xlib.ext import xtest

KEYS = {
    "7": 79, "8": 80, "9": 81, "4": 83, "5": 84, "6": 85,
    "1": 87, "2": 88, "3": 89, "0": 90, ".": 91,
    "/": 106, "*": 63, "-": 82, "+": 86, "enter": 104,
}
LOCK = X.LockMask

d = display.Display()
root = d.screen().root
screen = d.screen()


def leds():
    out = subprocess.run(["xset", "q"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "LED mask" in line:
            return int(line.split("LED mask:")[1].strip(), 16)
    return 0


def num_on():
    return bool(leds() & 0x2)


def set_num(want):
    if num_on() != want:
        subprocess.run(["xdotool", "key", "Num_Lock"], check=False)
        time.sleep(0.4)


def make_window(title, w=420, h=140, x=120, y=120, transient_for=None):
    win = root.create_window(
        x, y, w, h, 1, screen.root_depth,
        background_pixel=screen.white_pixel,
        event_mask=X.KeyPressMask | X.StructureNotifyMask | X.PropertyChangeMask,
    )
    win.set_wm_name(title)
    win.set_wm_class("dialsprobe", "DialsProbe")
    win.change_property(d.intern_atom("WM_PROTOCOLS"), Xatom.ATOM, 32,
                        [d.intern_atom("WM_DELETE_WINDOW")])
    if transient_for is not None:
        win.change_property(Xatom.WM_TRANSIENT_FOR, Xatom.WINDOW, 32, [transient_for.id])
    win.map()
    d.sync()
    time.sleep(1.2)
    return win


def activate(win):
    ev = __import__("Xlib.protocol.event", fromlist=["ClientMessage"]).ClientMessage(
        window=win, client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
        data=(32, [2, X.CurrentTime, 0, 0, 0]))
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()
    time.sleep(0.9)


def wm_state(win):
    out = subprocess.run(["xprop", "-id", hex(win.id), "_NET_WM_STATE"],
                         capture_output=True, text=True).stdout.strip()
    if "not found" in out or "=" not in out:
        return set()
    return {t.strip() for t in out.split("=", 1)[1].split(",") if t.strip()}


def collect(seconds=0.7):
    """Return (to_root, to_ours) key press events."""
    to_root, to_ours = [], []
    end = time.time() + seconds
    while time.time() < end:
        while d.pending_events():
            e = d.next_event()
            if e.type == X.KeyPress:
                (to_root if e.window.id == root.id else to_ours).append((e.detail, e.state))
        time.sleep(0.02)
    return to_root, to_ours


def press(kc):
    xtest.fake_input(d, X.KeyPress, kc)
    xtest.fake_input(d, X.KeyRelease, kc)
    d.sync()


orig_num = num_on()
target = make_window("dials probe - keyboard target")
activate(target)

# corrected grab set: {0, LockMask}, never Mod2Mask -- owner_events False
installed = []
for name, kc in KEYS.items():
    for mask in (0, LOCK):
        catch = error.CatchError(error.BadAccess)
        root.grab_key(kc, mask, False, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
        d.sync()
        if catch.get_error():
            print(f"  !! grab failed: {name} mask=0x{mask:02x}")
        else:
            installed.append((kc, mask))
print(f"grabs installed: {len(installed)} (expected {len(KEYS)*2})\n")

print("=" * 74)
print("A1  NumLock OFF -> every key must reach the GRAB, not the application")
print("=" * 74)
set_num(False)
activate(target)
collect(0.3)
a1_fail = []
for name, kc in KEYS.items():
    press(kc)
    r, o = collect(0.5)
    ok = bool(r) and not o
    if not ok:
        a1_fail.append(name)
    print(f"  {name:<6} kc={kc:<4} grab={len(r)} app={len(o)}  {'ok' if ok else 'FAIL'}")
print(f"  -> {'all keys captured by grab' if not a1_fail else 'FAILURES: ' + ', '.join(a1_fail)}")

print()
print("=" * 74)
print("A2  NumLock ON -> every key must reach the APPLICATION as its digit")
print("=" * 74)
set_num(True)
activate(target)
collect(0.3)
a2_fail = []
for name, kc in KEYS.items():
    press(kc)
    r, o = collect(0.5)
    sym = ""
    if o:
        ks = d.keycode_to_keysym(kc, 1)
        sym = __import__("Xlib.XK", fromlist=["keysym_to_string"]).keysym_to_string(ks) or hex(ks)
    ok = bool(o) and not r
    if not ok:
        a2_fail.append(name)
    mod2 = all(st & X.Mod2Mask for _, st in o) if o else False
    print(f"  {name:<6} kc={kc:<4} grab={len(r)} app={len(o)} mod2={mod2} sym={sym!r}"
          f"  {'ok' if ok else 'FAIL'}")
print(f"  -> {'all keys passed through to the app' if not a2_fail else 'FAILURES: ' + ', '.join(a2_fail)}")

for kc, mask in installed:
    root.ungrab_key(kc, mask)
d.sync()

print()
print("=" * 74)
print("B  is _NET_WM_STATE_FOCUSED exclusive? (parent + WM_TRANSIENT_FOR child)")
print("=" * 74)
parent = make_window("dials probe - PARENT", w=520, h=200, x=300, y=300)
activate(parent)
print(f"  parent focused alone: {sorted(wm_state(parent))}")
child = make_window("dials probe - MODAL CHILD", w=300, h=120, x=380, y=380,
                    transient_for=parent)
activate(child)
ps, cs = wm_state(parent), wm_state(child)
p_foc = "_NET_WM_STATE_FOCUSED" in ps
c_foc = "_NET_WM_STATE_FOCUSED" in cs
active = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                        capture_output=True, text=True).stdout.strip()
print(f"  with child active:")
print(f"    parent: FOCUSED={p_foc}  {sorted(ps)}")
print(f"    child : FOCUSED={c_foc}  {sorted(cs)}")
print(f"  _NET_ACTIVE_WINDOW = {active.split('#')[-1].strip()}")
print(f"    parent id={hex(parent.id)}  child id={hex(child.id)}")
print()
if p_foc and c_foc:
    print("  CONFIRMED: FOCUSED is NOT exclusive -> wrong predicate for the toggle.")
    print("  Pressing the Dial key while its own dialog is up would hide the parent.")
elif c_foc and not p_foc:
    print("  FOCUSED was exclusive in this case; _NET_ACTIVE_WINDOW is still the")
    print("  stricter predicate, and EWMH permits non-exclusivity regardless.")
else:
    print("  unexpected combination - inspect above")

for w in (child, parent, target):
    try:
        w.destroy()
    except Exception:  # noqa: BLE001
        pass
d.sync()
set_num(orig_num)
print(f"\ncleaned up. NumLock restored to {'ON' if num_on() else 'OFF'}")
