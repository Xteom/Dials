#!/usr/bin/env python3
"""Probe 4: close the last two unknowns.

1. Notifications with NO new apt package (gdbus -> org.freedesktop.Notifications),
   since notify-send/libnotify-bin is not installed.
2. Pure python-xlib show/hide + activate, so the daemon needs no xdotool at runtime:
     hide     = WM_CHANGE_STATE ClientMessage -> IconicState
     show     = _NET_ACTIVE_WINDOW ClientMessage
     geometry = configure() on the window
"""
import subprocess
import time

from Xlib import X, Xutil, display, protocol

d = display.Display()
root = d.screen().root


def managed():
    out = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"],
                         capture_output=True, text=True).stdout
    return [int(t.strip(), 16) for t in out.split("#", 1)[-1].split(",") if t.strip()]


def state(win_id):
    out = subprocess.run(["xprop", "-id", hex(win_id), "_NET_WM_STATE"],
                         capture_output=True, text=True).stdout.strip()
    if "not found" in out or "=" not in out:
        return set()
    return {t.strip() for t in out.split("=", 1)[1].split(",") if t.strip()}


def geom(win_id):
    g = d.create_resource_object("window", win_id).get_geometry()
    t = root.translate_coords(d.create_resource_object("window", win_id), 0, 0)
    return (t.x, t.y, g.width, g.height)


def client_msg(win_id, type_name, data):
    win = d.create_resource_object("window", win_id)
    ev = protocol.event.ClientMessage(
        window=win, client_type=d.intern_atom(type_name), data=(32, data)
    )
    root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()


# ---- 1. notification via gdbus, no new package ---------------------------
print("=" * 64)
print("1  desktop notification with no new apt package")
print("=" * 64)
print(f"  gdbus present: {subprocess.run(['which', 'gdbus'], capture_output=True, text=True).stdout.strip() or 'MISSING'}")
r = subprocess.run([
    "gdbus", "call", "--session",
    "--dest", "org.freedesktop.Notifications",
    "--object-path", "/org/freedesktop/Notifications",
    "--method", "org.freedesktop.Notifications.Notify",
    "numpad-panels", "0", "input-keyboard",
    "Assign mode", "Press a numpad key to bind the focused window",
    "[]", "{}", "2500",
], capture_output=True, text=True)
print(f"  rc={r.returncode}  stdout={r.stdout.strip()!r}  stderr={r.stderr.strip()[:80]!r}")
print(f"  VERDICT: {'notifications work via gdbus, zero new packages' if r.returncode == 0 else 'need libnotify-bin'}")

# ---- 2. pure-xlib window control ----------------------------------------
win_id = next((w for w in managed()
               if "alculator" in subprocess.run(["xprop", "-id", hex(w), "WM_CLASS"],
                                                capture_output=True, text=True).stdout), None)
victim = None
if win_id is None:
    victim = subprocess.Popen(["gnome-calculator"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        win_id = next((w for w in managed()
                       if "alculator" in subprocess.run(["xprop", "-id", hex(w), "WM_CLASS"],
                                                        capture_output=True, text=True).stdout), None)
        if win_id:
            break

print()
print("=" * 64)
print("2  pure python-xlib show / hide / geometry (no xdotool)")
print("=" * 64)
print(f"  window {hex(win_id)}")

# geometry via configure
target = (500, 200, 1100, 700)
w = d.create_resource_object("window", win_id)
w.configure(x=target[0], y=target[1], width=target[2], height=target[3])
d.sync()
time.sleep(0.8)
print(f"  configure() target={target}")
print(f"  configure() actual={geom(win_id)}")

# hide via WM_CHANGE_STATE -> IconicState
client_msg(win_id, "WM_CHANGE_STATE", [Xutil.IconicState, 0, 0, 0, 0])
time.sleep(0.9)
hidden = "_NET_WM_STATE_HIDDEN" in state(win_id)
print(f"  WM_CHANGE_STATE(Iconic) -> hidden={hidden}  {'ok' if hidden else 'FAILED'}")

# show via _NET_ACTIVE_WINDOW
client_msg(win_id, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0, 0, 0])
time.sleep(0.9)
st = state(win_id)
shown = "_NET_WM_STATE_HIDDEN" not in st
focused = "_NET_WM_STATE_FOCUSED" in st
print(f"  _NET_ACTIVE_WINDOW      -> shown={shown} focused={focused}  {'ok' if shown else 'FAILED'}")
print()
print(f"  VERDICT: {'daemon needs ONLY python-xlib' if hidden and shown else 'xdotool fallback needed'}")

if victim is not None:
    victim.terminate()
print("\ncleaned up.")
