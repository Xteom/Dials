#!/usr/bin/env python3
"""Probe 9: is a DUPLICATE passive key grab an error, and does one ungrab
undo it?

The daemon holds a permanent grab on KP_Enter (keycode 104, Dial slot "enter")
and used to also grab KP_Enter in the *temporary* GrabManager it installs while
a launch confirmation is pending. The code and its tests asserted that the
duplicate "is EXPECTED to fail on every mask ... X returns BadAccess for a
duplicate grab", and concluded the temporary manager could therefore never
release the permanent one.

If that belief is wrong, `release_confirm_grabs()` destroys the permanent
KP_Enter grab on every confirmation - confirmed, timed out or cancelled - and
the `enter` Dial goes dead until the next pause/resume or restart, silently.

Four questions, in order:

  1. does a client's FIRST grab of (kc, mask) succeed?                 (control)
  2. does the SAME client grabbing (kc, mask) AGAIN fail?
  3. does a DIFFERENT client grabbing it while the first holds it fail?
  4. after ONE ungrab from the holder, can another client grab it?

(2) is the falsifiable claim. (4) decides whether a single ungrab_key from the
temporary manager is enough to delete the permanent grab.

Grabs an UNMAPPED keycode (nothing on this keyboard produces it), so no real key
is taken away from the live desktop and NumLock is never touched.
"""
import sys

from Xlib import X, display, error

# X11 keycodes run 8..255. We want one with NO keysym at all: grabbing it cannot
# steal a key from anything, and no other client has a reason to hold it. Which
# keycodes are free is layout-specific - 254 is XF86 media junk on this
# keyboard - so the free one is found at runtime rather than hardcoded.
KC_SEARCH_RANGE = range(200, 256)


def grab(dpy, kc, mask):
    """Try one passive grab. Returns None on success, the X error otherwise."""
    catch = error.CatchError(error.BadAccess)
    dpy.screen().root.grab_key(kc, mask, True, X.GrabModeAsync, X.GrabModeAsync,
                               onerror=catch)
    dpy.sync()
    return catch.get_error()


def ungrab(dpy, kc, mask):
    dpy.screen().root.ungrab_key(kc, mask)
    dpy.sync()


def verdict(err):
    return "BadAccess" if err else "OK"


# Two SEPARATE X client connections. The daemon and its temporary GrabManager
# share ONE Display, so `a` models both of them and `b` models a foreign app.
a = display.Display()
b = display.Display()

PROBE_KC = next((kc for kc in KC_SEARCH_RANGE
                 if a.keycode_to_keysym(kc, 0) == 0), None)
if PROBE_KC is None:
    print(f"no unmapped keycode in {KC_SEARCH_RANGE}; refusing to grab a real key")
    sys.exit(1)
print(f"probe keycode {PROBE_KC} is unmapped (no keysym) - safe to grab\n")

# ---- Part 1-4: the four questions ----------------------------------------
print("=" * 66)
print("PART 1  who may grab keycode %d, mask 0" % PROBE_KC)
print("=" * 66)
first = grab(a, PROBE_KC, 0)
print(f"  client A, first grab                       {verdict(first)}")
second = grab(a, PROBE_KC, 0)
print(f"  client A, SECOND grab (same kc+mask)       {verdict(second)}"
      f"   <- {'silent REPLACE, not BadAccess' if not second else 'duplicate rejected'}")
cross = grab(b, PROBE_KC, 0)
print(f"  client B grabbing while A holds it         {verdict(cross)}"
      f"   <- {'cross-client only' if cross else 'no exclusion at all?!'}")

ungrab(a, PROBE_KC, 0)          # ONE ungrab, after TWO successful grabs
after_one_ungrab = grab(b, PROBE_KC, 0)
note = "A still holds it" if after_one_ungrab else "A's grab fully removed"
print(f"  client B after ONE ungrab from A           {verdict(after_one_ungrab)}"
      f"   <- {note}")
if not after_one_ungrab:
    ungrab(b, PROBE_KC, 0)
print()
print("  Grabs are NOT reference counted, and BadAccess is a CROSS-CLIENT")
print("  condition. A duplicate grab from the same client is accepted.")

# ---- Part 5: the daemon's actual shape -----------------------------------
print()
print("=" * 66)
print("PART 5  the daemon shape: permanent grab + temporary manager, ONE display")
print("=" * 66)
MASKS = (0, X.LockMask)         # what tolerated_masks() yields on this machine

permanent = []                  # stands in for the daemon's GrabManager
for m in MASKS:
    err = grab(a, PROBE_KC, m)
    if not err:
        permanent.append((PROBE_KC, m))
print(f"  permanent grab installed for masks {[hex(m) for m in MASKS]}: "
      f"{len(permanent)}/{len(MASKS)} succeeded")

temporary = []                  # stands in for the confirm-grab GrabManager
failures = {}
for m in MASKS:
    err = grab(a, PROBE_KC, m)
    if err:
        failures.setdefault(PROBE_KC, []).append(m)
    else:
        temporary.append((PROBE_KC, m))
print(f"  temporary manager on the SAME display: {len(temporary)} installed, "
      f"failures={failures or '{}'}")
print("  -> the diagnostic log keyed on `failures` can never fire; the keycode")
print("     lands in the temporary manager's _installed list instead.")

for kc, m in temporary:         # release_confirm_grabs() -> remove_all()
    ungrab(a, kc, m)
print(f"  temporary manager remove_all() ungrabbed {len(temporary)} pair(s)")

stolen = grab(b, PROBE_KC, 0)
print(f"  foreign client B can now grab (kc, 0):     {verdict(stolen)}")
if not stolen:
    ungrab(b, PROBE_KC, 0)

# ---- cleanup -------------------------------------------------------------
for m in MASKS:
    try:
        ungrab(a, PROBE_KC, m)
    except Exception:
        pass

print()
print("=" * 66)
broken = (not second) and bool(cross) and (not after_one_ungrab) and (not stolen)
if broken:
    print("VERDICT: the old comment was FALSE.")
    print("  - a same-client duplicate grab SUCCEEDS (silent replace)")
    print("  - BadAccess is raised only for a DIFFERENT client")
    print("  - one ungrab_key removes the grab outright, refcount-free")
    print("  => grabbing KP_Enter in the temporary manager makes every")
    print("     release_confirm_grabs() destroy the daemon's permanent")
    print("     KP_Enter grab. The temporary manager must grab Return ONLY.")
else:
    print("VERDICT: duplicate grabs behave as the old comment claimed;")
    print("         re-read the parts above before changing the daemon.")
