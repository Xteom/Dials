import pytest

from dials.tray import (
    DORMANT, ICON_NAMES, LIVE, PAUSED, POLL_INTERVAL_MS, TOOLTIPS, icon_state,
    numlock_on,
)


@pytest.mark.parametrize("running,paused,numlock,expected", [
    (True,  False, False, LIVE),      # NumLock OFF -> the layer can fire
    (True,  False, True,  DORMANT),   # NumLock ON  -> keypad types digits
    (True,  True,  False, PAUSED),
    (True,  True,  True,  PAUSED),
    (False, False, False, PAUSED),    # no daemon is indistinguishable from paused
    (False, False, True,  PAUSED),
])
def test_full_state_table(running, paused, numlock, expected):
    assert icon_state(running, paused, numlock) == expected


def test_paused_beats_numlock():
    assert icon_state(True, True, False) == PAUSED


def test_every_state_has_an_icon_and_a_tooltip():
    for state in (LIVE, DORMANT, PAUSED):
        assert ICON_NAMES[state]
        assert TOOLTIPS[state]


def test_tooltips_explain_why_the_layer_is_inert():
    assert "NumLock" in TOOLTIPS[DORMANT]
    assert "paused" in TOOLTIPS[PAUSED].lower()


def test_poll_interval_is_one_second():
    """Measured at 28.4us per read - 0.0028% of a core at 1Hz."""
    assert POLL_INTERVAL_MS == 1000


class FakeKeyboardControl:
    def __init__(self, led_mask):
        self.led_mask = led_mask


class FakeDisplay:
    def __init__(self, led_mask):
        self._mask = led_mask

    def get_keyboard_control(self):
        return FakeKeyboardControl(self._mask)


def test_numlock_is_read_from_bit_two_of_the_led_mask():
    assert numlock_on(FakeDisplay(0b0010)) is True
    assert numlock_on(FakeDisplay(0b0000)) is False


def test_numlock_ignores_other_led_bits():
    assert numlock_on(FakeDisplay(0b1001)) is False   # capslock only
    assert numlock_on(FakeDisplay(0b1010)) is True    # capslock + numlock


def test_numlock_degrades_to_false_when_x_fails():
    class Broken:
        def get_keyboard_control(self):
            raise RuntimeError("no display")

    assert numlock_on(Broken()) is False
