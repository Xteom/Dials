"""Numpad slot <-> X11 keycode table.

Keycodes are from `xmodmap -pke` on this machine and are hardware/layout
specific. They are a table rather than a keysym lookup on purpose: the daemon
grabs on keycode plus an explicit modifier mask, because the NumLock-on and
NumLock-off keysyms live on the same keycode and only the mask distinguishes
them.
"""
from __future__ import annotations


class UnknownSlot(ValueError):
    """Raised when a slot name is not one of the 16 numpad keys."""


SLOT_KEYCODES: dict[str, int] = {
    "7": 79, "8": 80, "9": 81,
    "4": 83, "5": 84, "6": 85,
    "1": 87, "2": 88, "3": 89,
    "0": 90,
    ".": 91,
    "/": 106, "*": 63, "-": 82, "+": 86, "enter": 104,
}

#: '.' drives assign mode and can never hold a Dial.
ASSIGN_SLOT = "."

#: Main Return, grabbed only while a launch confirmation is armed.
RETURN_KEYCODE = 36
KP_ENTER_KEYCODE = SLOT_KEYCODES["enter"]

BINDABLE_SLOTS: tuple[str, ...] = tuple(
    s for s in SLOT_KEYCODES if s != ASSIGN_SLOT
)

_KEYCODE_SLOTS = {kc: slot for slot, kc in SLOT_KEYCODES.items()}


def keycode_for(slot: str) -> int:
    try:
        return SLOT_KEYCODES[slot]
    except KeyError:
        raise UnknownSlot(f"not a numpad slot: {slot!r}") from None


def slot_for(keycode: int) -> str | None:
    return _KEYCODE_SLOTS.get(keycode)


def is_bindable(slot: str) -> bool:
    return slot in BINDABLE_SLOTS
