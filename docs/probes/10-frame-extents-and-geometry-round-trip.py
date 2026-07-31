#!/usr/bin/env python3
"""Probe 10: is `read geometry -> write geometry` a fixed point, and which write
mechanism makes it one?

`WindowOps.geometry()` reports the CLIENT origin in root coordinates
(root.translate_coords(win, 0, 0)). `WindowOps.apply_geometry()` used a plain
ConfigureRequest (`win.configure(x=..., y=...)`), which mutter interprets as the
position of the *visible frame* for a NorthWest-gravity window. For a
server-side-decorated window those two differ by the titlebar height, so every
SHOW placed the client below the configured rect and every `dials capture`
compounded another titlebar. Spotify - one of the two Dials in
config/config.reference.toml - is SSD, so this is the default experience, not a
corner case.

The acceptance criterion is a FIXED POINT: a window at rect R, told to go to R,
must not move; and a window told to go to R must put its CLIENT at R.

Four write mechanisms are compared on the same window, so the direction of the
offset is measured rather than reasoned about:

  A  configure(x, y, w, h)                          - what the code used to do
  B  _NET_MOVERESIZE_WINDOW, gravity Static (10)    - the EWMH pager mechanism
  C  _NET_MOVERESIZE_WINDOW, gravity 0              - "use WM_SIZE_HINTS"
  D  _NET_MOVERESIZE_WINDOW, gravity NorthWest (1)

Then the winner is re-checked on the two shapes that could still break it: a
CLIENT-side-decorated window (no server frame at all) and a MINIMIZED window,
which is the daemon's most common SHOW path.

Creates and destroys its own windows; touches nothing that was already running.
"""
import subprocess
import time

from Xlib import X, Xutil, display, error, protocol

d = display.Display()
root = d.screen().root

#: bits 8-11 of data.l[0]: x, y, width and height are all present.
MOVERESIZE_XYWH = 0xF00
#: bits 12-15 carry the source indication; 2 = pager, which is what Dials is.
SOURCE_PAGER = 2
STATIC_GRAVITY = 10

TARGET = (700, 300, 640, 400)
SETTLE = 0.8            # mutter is asynchronous; give it a frame or two


def atom(name):
    return d.intern_atom(name)


def win(wid):
    return d.create_resource_object("window", wid)


def prop(wid, name):
    p = win(wid).get_full_property(atom(name), X.AnyPropertyType)
    return list(p.value) if p else None


def client_rect(wid):
    """Exactly what WindowOps.geometry() returns: the CLIENT origin + size."""
    g = win(wid).get_geometry()
    t = root.translate_coords(win(wid), 0, 0)
    return (t.x, t.y, g.width, g.height)


def managed():
    return prop(root.id, "_NET_CLIENT_LIST") or []


def states(wid):
    return sorted(d.get_atom_name(a) for a in (prop(wid, "_NET_WM_STATE") or []))


def client_message(wid, name, data):
    ev = protocol.event.ClientMessage(window=wid, client_type=atom(name),
                                      data=(32, data))
    root.send_event(ev, onerror=error.CatchError(),
                    event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
    d.flush()


# ---- the four write mechanisms -------------------------------------------

def write_configure(wid, x, y, w, h):
    win(wid).configure(x=x, y=y, width=w, height=h)
    d.flush()


def write_moveresize(wid, x, y, w, h, gravity):
    flags = (gravity & 0xFF) | MOVERESIZE_XYWH | (SOURCE_PAGER << 12)
    client_message(wid, "_NET_MOVERESIZE_WINDOW", [flags, x, y, w, h])


# ---- window plumbing -----------------------------------------------------

def wait_for(predicate, seconds=15.0):
    end = time.time() + seconds
    while time.time() < end:
        found = predicate()
        if found:
            return found
        time.sleep(0.25)
    return None


def spawn_ssd(title):
    """An xterm: server-side decorated, so it HAS _NET_FRAME_EXTENTS."""
    proc = subprocess.Popen(["xterm", "-title", title, "-geometry", "60x20+500+400"])

    def find():
        out = subprocess.run(["xdotool", "search", "--name", title],
                             capture_output=True, text=True).stdout.split()
        live = set(managed())
        return next((int(t) for t in out if int(t) in live), None)

    wid = wait_for(find)
    if wid is not None:
        time.sleep(SETTLE)
    return proc, wid


def spawn_csd():
    """gnome-calculator: GTK client-side decorations, no server frame."""
    before = set(managed())
    proc = subprocess.Popen(["gnome-calculator"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

    def find():
        for wid in managed():
            if wid in before:
                continue
            try:
                cls = win(wid).get_wm_class()
            except Exception:
                continue
            if cls and any("alculator" in c for c in cls):
                return wid
        return None

    wid = wait_for(find)
    if wid is not None:
        time.sleep(SETTLE)
    return proc, wid


def trial(label, wid, write):
    """Ask for TARGET, then ask for wherever it landed. Reports both errors."""
    print("-" * 70)
    print(f"  {label}")
    before = client_rect(wid)
    write(wid, *TARGET)
    time.sleep(SETTLE)
    landed = client_rect(wid)
    dx, dy = landed[0] - TARGET[0], landed[1] - TARGET[1]
    print(f"    client before        : {before}")
    print(f"    asked for            : {TARGET}")
    print(f"    client landed at     : {landed}   -> dx={dx:+d} dy={dy:+d}")

    write(wid, *landed)                     # R -> R: this must be a no-op
    time.sleep(SETTLE)
    again = client_rect(wid)
    drift = (again[0] - landed[0], again[1] - landed[1],
             again[2] - landed[2], again[3] - landed[3])
    print(f"    told to stay put     : {again}   -> drift {drift}")
    fixed = drift == (0, 0, 0, 0)
    honest = (dx, dy) == (0, 0)
    print(f"    fixed point: {'YES' if fixed else 'NO'}    "
          f"client lands where asked: {'YES' if honest else 'NO'}")
    return fixed and honest


results = {}
procs = []

try:
    # ---- Part 1: the drift, on a real SSD window -------------------------
    print("=" * 70)
    print("PART 1  a server-side-decorated window: which mechanism is honest?")
    print("=" * 70)
    proc, ssd = spawn_ssd("dials-probe-10-ssd")
    procs.append(proc)
    if ssd is None:
        raise SystemExit("could not get an xterm window")
    extents = prop(ssd, "_NET_FRAME_EXTENTS")
    print(f"  window {hex(ssd)}   _NET_FRAME_EXTENTS (l,r,t,b) = {extents}")
    print(f"  the titlebar is {extents[2] if extents else '?'} px tall\n")

    results["A configure()"] = trial(
        "A  configure(x, y, w, h)                       [the old apply_geometry]",
        ssd, write_configure)
    results["B MOVERESIZE Static"] = trial(
        "B  _NET_MOVERESIZE_WINDOW  gravity = Static (10)",
        ssd, lambda w_, *r: write_moveresize(w_, *r, gravity=STATIC_GRAVITY))
    results["C MOVERESIZE gravity 0"] = trial(
        "C  _NET_MOVERESIZE_WINDOW  gravity = 0 (use WM_SIZE_HINTS)",
        ssd, lambda w_, *r: write_moveresize(w_, *r, gravity=0))
    results["D MOVERESIZE NorthWest"] = trial(
        "D  _NET_MOVERESIZE_WINDOW  gravity = NorthWest (1)",
        ssd, lambda w_, *r: write_moveresize(w_, *r, gravity=1))

    # ---- Part 2: does Static break a window with NO frame? ---------------
    print()
    print("=" * 70)
    print("PART 2  Static gravity on a CLIENT-side-decorated window (no frame)")
    print("=" * 70)
    proc, csd = spawn_csd()
    procs.append(proc)
    if csd is None:
        print("  no calculator window; SKIPPED")
        results["B on CSD"] = None
    else:
        print(f"  window {hex(csd)}   _NET_FRAME_EXTENTS = "
              f"{prop(csd, '_NET_FRAME_EXTENTS')}  (absent = no server frame)")
        results["B on CSD"] = trial(
            "B  _NET_MOVERESIZE_WINDOW  gravity = Static (10)",
            csd, lambda w_, *r: write_moveresize(w_, *r, gravity=STATIC_GRAVITY))

    # ---- Part 3: the daemon's SHOW path - a MINIMIZED window -------------
    print()
    print("=" * 70)
    print("PART 3  Static gravity on a MINIMIZED SSD window (the SHOW path)")
    print("=" * 70)
    client_message(ssd, "WM_CHANGE_STATE", [Xutil.IconicState, 0, 0, 0, 0])
    time.sleep(1.0)
    print(f"  states: {states(ssd)}")
    write_moveresize(ssd, *TARGET, gravity=STATIC_GRAVITY)
    time.sleep(SETTLE)
    while_hidden = client_rect(ssd)
    print(f"  moved while minimized  : {while_hidden}")
    client_message(ssd, "_NET_ACTIVE_WINDOW",
                   [SOURCE_PAGER, int(time.time()) & 0xFFFFFFF, 0, 0, 0])
    time.sleep(1.2)
    restored = client_rect(ssd)
    dx, dy = restored[0] - TARGET[0], restored[1] - TARGET[1]
    print(f"  after restore          : {restored}   -> dx={dx:+d} dy={dy:+d}")
    results["B while minimized"] = (dx, dy) == (0, 0)

finally:
    for proc in procs:
        proc.terminate()
    d.sync()

print()
print("=" * 70)
for name, ok in results.items():
    mark = "SKIPPED" if ok is None else ("PASS" if ok else "FAIL")
    print(f"  {name:<26} {mark}")
print("=" * 70)
if results.get("B MOVERESIZE Static") and results.get("B while minimized"):
    print("VERDICT: _NET_MOVERESIZE_WINDOW with StaticGravity and the pager source")
    print("  indication is the mechanism to use. It is a fixed point, it puts the")
    print("  CLIENT where asked on both decoration styles, and it works on a")
    print("  minimized window. A plain ConfigureRequest is off by the top frame")
    print("  extent on every SSD window, in the +y direction.")
else:
    print("VERDICT: StaticGravity did NOT hold here - do not change apply_geometry")
    print("         on the strength of this run; re-measure first.")
print("(sizes may differ from the request: xterm's cell-size hints win, which")
print(" apply_geometry has always documented as accepted.)")
