from pathlib import Path

import pytest

from dials.tray import (
    DORMANT, ICON_NAMES, LIVE, PAUSED, POLL_INTERVAL_MS, TOOLTIPS,
    _daemon_running, _verify_icons, icon_state, numlock_on,
)

ICON_SOURCE_DIR = Path(__file__).resolve().parent.parent / "icons"


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


def test_every_state_uses_a_project_icon_not_a_theme_icon():
    """The icons ship with Dials, so every name must be one of ours.

    Guards against drifting back to a stock name like `input-keyboard`: those
    resolve on this machine but are a keyboard, not the shell the project is
    named for, and they vary by icon theme.
    """
    for state in (LIVE, DORMANT, PAUSED):
        assert ICON_NAMES[state].startswith("dials-shell")


def test_every_icon_name_has_a_file_to_install():
    """A name with no file behind it installs cleanly and renders as broken."""
    for state in (LIVE, DORMANT, PAUSED):
        assert (ICON_SOURCE_DIR / f"{ICON_NAMES[state]}.svg").is_file()


def test_icon_files_open_with_the_svg_element_on_the_first_line():
    """gdk-pixbuf recognises a file by sniffing its leading bytes.

    A comment long enough to push `<svg` past that window makes the icon fail to
    load with "couldn't recognize the image file format" - which presents as a
    broken icon in the panel with nothing wrong in any log. Measured: an 11-byte
    leading comment loads, the ~470-byte documentation block these files used to
    carry does not. So the documentation lives inside the root element and this
    test pins the constraint.
    """
    for path in sorted(ICON_SOURCE_DIR.glob("dials-shell*.svg")):
        head = path.read_bytes()[:5]
        assert head == b"<svg ", f"{path.name} starts with {head!r}, not '<svg '"


def test_verify_icons_reports_missing_ones_rather_than_failing_silently():
    class FakeTheme:
        def __init__(self, present): self.present = present
        def has_icon(self, name): return name in self.present

    class FakeGtk:
        def __init__(self, present): self._t = FakeTheme(present)
        class IconTheme:  # replaced per-instance below
            pass

    gtk = FakeGtk(set())
    gtk.IconTheme = type("IT", (), {"get_default": staticmethod(lambda: gtk._t)})
    assert sorted(_verify_icons(gtk)) == sorted(ICON_NAMES.values())

    gtk2 = FakeGtk(set(ICON_NAMES.values()))
    gtk2.IconTheme = type("IT", (), {"get_default": staticmethod(lambda: gtk2._t)})
    assert _verify_icons(gtk2) == []


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


# ---- _daemon_running: cached-PID liveness check (Round 1 fix) ------------
#
# A `pgrep -x dialsd` fork per tick was measured at 17570.7us against a
# 23.2us led_mask read - 756x the cost the polling budget was justified by.
# These tests assert the cache actually avoids that cost in steady state,
# not just that the end result is correct.

def test_daemon_running_uses_the_cached_pid_without_rescanning():
    cache = {"pid": 123}
    scan_calls = []

    def list_pids():
        scan_calls.append(1)
        return []

    def read_comm(pid):
        assert pid == 123
        return "dialsd"

    assert _daemon_running(cache, list_pids=list_pids, read_comm=read_comm) is True
    assert scan_calls == []          # the whole point: no rescan in steady state
    assert cache["pid"] == 123


def test_daemon_running_rescans_when_the_cached_pid_comm_changes():
    cache = {"pid": 123}
    comms = {123: "bash", 456: "dialsd"}

    def list_pids():
        return [123, 456]

    def read_comm(pid):
        return comms[pid]

    assert _daemon_running(cache, list_pids=list_pids, read_comm=read_comm) is True
    assert cache["pid"] == 456


def test_daemon_running_rescans_when_the_cached_pid_has_vanished():
    cache = {"pid": 123}

    def list_pids():
        return [789]

    def read_comm(pid):
        if pid == 123:
            raise OSError("no such process")
        return "dialsd" if pid == 789 else "other"

    assert _daemon_running(cache, list_pids=list_pids, read_comm=read_comm) is True
    assert cache["pid"] == 789


def test_daemon_running_is_false_when_no_process_matches():
    cache = {"pid": None}

    def list_pids():
        return [1, 2, 3]

    def read_comm(pid):
        return "other"

    assert _daemon_running(cache, list_pids=list_pids, read_comm=read_comm) is False
    assert cache["pid"] is None


def test_daemon_running_is_false_when_the_reader_raises():
    cache = {"pid": None}

    def list_pids():
        return [1, 2, 3]

    def read_comm(pid):
        raise PermissionError("no access")

    assert _daemon_running(cache, list_pids=list_pids, read_comm=read_comm) is False
    assert cache["pid"] is None
