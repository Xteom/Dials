import pytest

from dials import keys


def test_all_sixteen_keycodes_present():
    assert len(keys.SLOT_KEYCODES) == 16


def test_fifteen_bindable_slots_excluding_assign():
    assert len(keys.BINDABLE_SLOTS) == 15
    assert keys.ASSIGN_SLOT == "."
    assert keys.ASSIGN_SLOT not in keys.BINDABLE_SLOTS


@pytest.mark.parametrize("slot,keycode", [
    ("7", 79), ("8", 80), ("9", 81),
    ("4", 83), ("5", 84), ("6", 85),
    ("1", 87), ("2", 88), ("3", 89),
    ("0", 90), (".", 91),
    ("/", 106), ("*", 63), ("-", 82), ("+", 86), ("enter", 104),
])
def test_round_trip(slot, keycode):
    assert keys.keycode_for(slot) == keycode
    assert keys.slot_for(keycode) == slot


def test_slot_for_unknown_keycode_returns_none():
    assert keys.slot_for(9999) is None


def test_keycode_for_unknown_slot_raises():
    with pytest.raises(keys.UnknownSlot):
        keys.keycode_for("99")


def test_assign_slot_is_not_bindable():
    assert keys.is_bindable("9") is True
    assert keys.is_bindable(".") is False
    assert keys.is_bindable("nope") is False


def test_confirm_keycodes():
    assert keys.RETURN_KEYCODE == 36
    assert keys.KP_ENTER_KEYCODE == 104
