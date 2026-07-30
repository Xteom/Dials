"""Passive key grabs, the tolerated-lock-mask set, and autorepeat debouncing.

X11 passive grabs match the modifier state EXACTLY: the specified modifiers
must be down and no others. A mask-0-only grab therefore stops matching the
moment any other lock modifier is active - probe 07 confirmed that CapsLock
silently kills every Dial. The fix is to grab the cross product of the
*tolerated* lock modifiers, which must never include whichever modifier holds
Num_Lock, because that distinction is the entire premise of the project.
"""
from __future__ import annotations

import itertools
import time as _time

from Xlib import X, error

_MOD_MASKS = (
    X.ShiftMask, X.LockMask, X.ControlMask,
    X.Mod1Mask, X.Mod2Mask, X.Mod3Mask, X.Mod4Mask, X.Mod5Mask,
)

#: Lock-style keysyms we are willing to tolerate being active.
_TOLERATED_KEYSYMS = ("Caps_Lock", "Shift_Lock", "Scroll_Lock")
_FORBIDDEN_KEYSYMS = ("Num_Lock",)


def tolerated_masks(modifier_mapping, keysym_lookup) -> list[int]:
    """All modifier masks a Dial grab should be installed for.

    `modifier_mapping` is the 8-row table from `Display.get_modifier_mapping()`;
    `keysym_lookup(keycode) -> str` names the keysym on a keycode. Derived at
    runtime rather than hardcoded because which modifier bit Scroll_Lock
    occupies is not fixed - on this machine mod3 is unmapped, so the set is
    just {0, LockMask}.
    """
    tolerated: list[int] = []
    for index, keycodes in enumerate(modifier_mapping):
        if index >= len(_MOD_MASKS):
            break
        names = {keysym_lookup(kc) for kc in keycodes if kc}
        if names & set(_FORBIDDEN_KEYSYMS):
            continue                      # never tolerate NumLock's modifier
        if names & set(_TOLERATED_KEYSYMS):
            tolerated.append(_MOD_MASKS[index])

    masks = set()
    for r in range(len(tolerated) + 1):
        for combo in itertools.combinations(tolerated, r):
            mask = 0
            for bit in combo:
                mask |= bit
            masks.add(mask)
    return sorted(masks)


class Debouncer:
    """Swallows key autorepeat.

    Holding a Dial key down produces repeated KeyPress events, which would
    toggle a Dial show/hide for as long as the key was held. XTEST cannot
    reproduce server autorepeat, so this is covered by unit tests plus a
    physically-held-key smoke test.
    """

    def __init__(self, window_s: float = 0.18, clock=_time.monotonic):
        self.window_s = window_s
        self._clock = clock
        self._last: dict[int, float] = {}

    def allow(self, keycode: int) -> bool:
        now = self._clock()
        previous = self._last.get(keycode)
        if previous is not None and now - previous < self.window_s:
            return False
        self._last[keycode] = now
        return True


class GrabManager:
    """Installs and releases the Dial grabs; supports pause/resume."""

    def __init__(self, display, root, masks: list[int] | None = None):
        self.d = display
        self.root = root
        self.masks = masks if masks is not None else self._discover_masks()
        self.failures: dict[int, list[int]] = {}
        self._installed: list[tuple[int, int]] = []

    def _discover_masks(self) -> list[int]:
        return tolerated_masks(
            self.d.get_modifier_mapping(),
            lambda kc: _keysym_name(self.d, kc),
        )

    @property
    def active(self) -> bool:
        return bool(self._installed)

    def install(self, keycodes) -> dict[int, list[int]]:
        """Grab each keycode over each tolerated mask.

        A BadAccess on one (keycode, mask) is recorded and the rest continue -
        losing one Dial is much better than losing them all.
        """
        self.failures = {}
        for kc in keycodes:
            for mask in self.masks:
                catch = error.CatchError(error.BadAccess)
                self.root.grab_key(kc, mask, True,
                                   X.GrabModeAsync, X.GrabModeAsync,
                                   onerror=catch)
                self.d.sync()
                if catch.get_error():
                    self.failures.setdefault(kc, []).append(mask)
                else:
                    self._installed.append((kc, mask))
        return self.failures

    def remove_all(self) -> None:
        for kc, mask in self._installed:
            try:
                self.root.ungrab_key(kc, mask)
            except Exception:
                pass
        self._installed.clear()
        try:
            self.d.sync()
        except Exception:
            pass


def _keysym_name(display, keycode: int) -> str:
    from Xlib import XK
    try:
        return XK.keysym_to_string(display.keycode_to_keysym(keycode, 0)) or \
            _XK_NAME.get(display.keycode_to_keysym(keycode, 0), "")
    except Exception:
        return ""


#: keysym_to_string only handles Latin-1, so lock keys need an explicit table.
_XK_NAME = {
    0xFF7F: "Num_Lock",
    0xFFE5: "Caps_Lock",
    0xFFE6: "Shift_Lock",
    0xFF14: "Scroll_Lock",
}
