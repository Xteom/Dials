import pytest

from dials.assign import (
    ASSIGN_TIMEOUT, AssignError, AssignMode, Capture, OverwriteRequest,
    derive_rect,
)
from dials.config import Dial
from dials.geometry import Monitor, Rect
from tests.conftest import FakeClock

HDMI = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=False, crtc=63)


def capture(wid=0x600001, cls="slack", label="Slack"):
    return Capture(wid=wid, wm_class=cls, label=label, monitor="HDMI-0",
                   rect=(0.0, 0.0, 0.5, 1.0), at=0.0)


def existing_dial(slot="4", cls="spotify"):
    return Dial(slot=slot, label="Spotify", match_class=cls, launch=None,
                icon="", monitor="HDMI-0", rect=(0.0, 0.0, 0.5, 1.0),
                on_focus_loss="hide", pin_geometry=False)


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def __call__(self, summary, body="", **kw):
        self.messages.append((summary, body))
        return True


def mode(clock=None, notifier=None):
    return AssignMode(clock=clock or FakeClock(),
                      notifier=notifier or FakeNotifier())


# ---- derive_rect ---------------------------------------------------------

def test_derive_rect_turns_pixels_into_monitor_fractions():
    assert derive_rect(Rect(1720, 0, 1720, 1440), HDMI) == (0.5, 0.0, 0.5, 1.0)


def test_derive_rect_handles_a_monitor_offset():
    edp = Monitor("eDP-1-1", Rect(388, 1440, 2560, 1440), True, 64)
    assert derive_rect(Rect(388, 1440, 1280, 1440), edp) == (0.0, 0.0, 0.5, 1.0)


def test_derive_rect_clamps_a_window_hanging_off_the_monitor():
    fr = derive_rect(Rect(3000, 0, 2000, 1440), HDMI)
    assert all(0.0 <= v <= 1.0 for v in fr)
    assert fr[2] > 0.0 and fr[3] > 0.0


def test_derive_rect_never_returns_zero_size():
    fr = derive_rect(Rect(0, 0, 1, 1), HDMI)
    assert fr[2] > 0.0 and fr[3] > 0.0


# ---- arming -------------------------------------------------------------

def test_not_armed_initially():
    m = mode()
    assert m.armed is False
    assert m.deadline() is None


def test_arm_notifies_and_sets_a_deadline():
    clk, notifier = FakeClock(), FakeNotifier()
    m = mode(clk, notifier)
    m.arm(capture())
    assert m.armed is True
    assert m.deadline() == clk.t + ASSIGN_TIMEOUT
    assert "Assign" in notifier.messages[0][0]


def test_capture_is_snapshotted_so_later_focus_changes_are_irrelevant():
    """The dialog itself takes focus; that must not change what gets bound."""
    m = mode()
    snap = capture(wid=0x600001, cls="slack")
    m.arm(snap)
    result = m.resolve("4", existing=None)
    assert result.match_class == "slack"


def test_resolve_on_an_empty_slot_returns_a_dial():
    m = mode()
    m.arm(capture())
    dial = m.resolve("4", existing=None)
    assert isinstance(dial, Dial)
    assert dial.slot == "4" and dial.match_class == "slack"
    assert m.armed is False        # consumed


def test_resolve_on_an_occupied_slot_returns_an_overwrite_request():
    m = mode()
    m.arm(capture())
    result = m.resolve("4", existing=existing_dial())
    assert isinstance(result, OverwriteRequest)
    assert result.existing.match_class == "spotify"
    assert result.incoming.match_class == "slack"


def test_an_overwrite_request_writes_nothing_yet():
    m = mode()
    m.arm(capture())
    result = m.resolve("4", existing=existing_dial())
    # The caller must confirm before persisting; resolve only describes.
    assert isinstance(result, OverwriteRequest)


def test_resolve_when_not_armed_raises():
    with pytest.raises(AssignError):
        mode().resolve("4", existing=None)


def test_resolve_rejects_the_reserved_slot():
    m = mode()
    m.arm(capture())
    with pytest.raises(AssignError, match="reserved"):
        m.resolve(".", existing=None)


def test_resolve_rejects_an_empty_captured_class():
    m = mode()
    m.arm(capture(cls=""))
    with pytest.raises(AssignError, match="class"):
        m.resolve("4", existing=None)


def test_label_falls_back_to_the_class_when_the_name_is_missing():
    m = mode()
    m.arm(capture(cls="slack", label=""))
    assert m.resolve("4", existing=None).label == "slack"


def test_timeout_disarms():
    clk = FakeClock()
    m = mode(clk)
    m.arm(capture())
    assert m.check_timeout() is False
    clk.advance(ASSIGN_TIMEOUT + 0.1)
    assert m.check_timeout() is True
    assert m.armed is False


def test_cancel_disarms():
    m = mode()
    m.arm(capture())
    m.cancel()
    assert m.armed is False
    assert m.deadline() is None


def test_arming_twice_replaces_the_capture():
    m = mode()
    m.arm(capture(cls="first"))
    m.arm(capture(cls="second"))
    assert m.resolve("4", existing=None).match_class == "second"
