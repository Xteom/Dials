#!/usr/bin/env python3
"""Probe 3 (redo of Probe B): does mutter honour _NET_WM_STATE client messages?

Probe 2's parser mistook xprop's "not found." for a state list, so its verdict was
worthless. This version prints raw xprop output, catches X errors, and tries both
source-indication values.
"""
import subprocess
import time

from Xlib import X, display, error, protocol

d = display.Display()
root = d.screen().root

PROPS = ("_NET_WM_STATE_ABOVE", "_NET_WM_STATE_STICKY",
         "_NET_WM_STATE_SKIP_TASKBAR", "_NET_WM_STATE_SKIP_PAGER")


def raw_state(win_id):
    r = subprocess.run(["xprop", "-id", hex(win_id), "_NET_WM_STATE"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def parse_state(win_id):
    """Correct parser: 'not found' -> empty; else atoms after the '=' ."""
    out = raw_state(win_id)
    if "not found" in out or "=" not in out:
        return set()
    return {t.strip() for t in out.split("=", 1)[1].split(",") if t.strip()}


def send_state(win_id, action, prop, source):
    a = d.intern_atom(prop)
    ev = protocol.event.ClientMessage(
        window=d.create_resource_object("window", win_id),
        client_type=d.intern_atom("_NET_WM_STATE"),
        data=(32, [action, a, 0, source, 0]),
    )
    catch = error.CatchError()
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
                    onerror=catch)
    d.sync()
    return catch.get_error()


def managed_windows():
    """Window ids the WM actually manages (_NET_CLIENT_LIST)."""
    out = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"],
                         capture_output=True, text=True).stdout
    return [int(t.strip(), 16) for t in out.split("#", 1)[-1].split(",") if t.strip()]


def find_victim():
    for wid in managed_windows():
        cls = subprocess.run(["xprop", "-id", hex(wid), "WM_CLASS"],
                             capture_output=True, text=True).stdout
        if "alculator" in cls:
            return wid
    return None


victim = None
win_id = find_victim()
if win_id is None:
    victim = subprocess.Popen(["gnome-calculator"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        win_id = find_victim()
        if win_id:
            break
if win_id is None:
    raise SystemExit("could not get a managed test window")
print(f"test window: {hex(win_id)} (gnome-calculator, WM-managed)\n")
print(f"raw xprop initially: {raw_state(win_id)!r}")
print(f"parsed initially:    {parse_state(win_id) or '(empty)'}\n")

for source in (1, 2):
    print("=" * 66)
    print(f"source indication = {source}  ({'normal app' if source == 1 else 'pager'})")
    print("=" * 66)
    for prop in PROPS:
        before = parse_state(win_id)
        err = send_state(win_id, 1, prop, source)
        time.sleep(0.5)
        after = parse_state(win_id)
        ok = prop in after
        note = f"  X error: {err}" if err else ""
        print(f"  add {prop:<28} {'ACCEPTED' if ok else 'rejected'}{note}")
    print(f"  raw now: {raw_state(win_id)!r}")
    # clear for the next round
    for prop in PROPS:
        send_state(win_id, 0, prop, source)
    time.sleep(0.5)
    print(f"  after removing all: {parse_state(win_id) or '(empty)'}\n")

# ---- does hidden-from-alt-tab actually follow skip_taskbar? ---------------
print("=" * 66)
print("combined: set all four, then minimize/restore round trip")
print("=" * 66)
for prop in PROPS:
    send_state(win_id, 1, prop, 1)
time.sleep(0.6)
print(f"  states set: {sorted(parse_state(win_id))}")
subprocess.run(["xdotool", "windowminimize", str(win_id)])
time.sleep(0.8)
print(f"  minimized -> {sorted(parse_state(win_id))}")
subprocess.run(["xdotool", "windowactivate", str(win_id)])
time.sleep(0.8)
final = parse_state(win_id)
print(f"  restored  -> {sorted(final)}")
kept = {p for p in PROPS if p in final}
print(f"  panel hints survived hide/show: {len(kept)}/4  {sorted(kept)}")

for prop in PROPS:
    send_state(win_id, 0, prop, 1)
if victim is not None:
    victim.terminate()
print("\ncleaned up (states cleared).")
