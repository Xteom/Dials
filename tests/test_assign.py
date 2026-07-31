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


@pytest.mark.parametrize("win_rect", [
    Rect(3440, 0, 800, 600),      # x exactly at the monitor's right edge
    Rect(4000, 200, 800, 600),    # x well past it
    Rect(0, 1440, 800, 600),      # y past the bottom edge
])
def test_derive_rect_never_starts_past_the_monitor_edge(win_rect):
    """A capture with no overlap must still yield a rect that fits."""
    fx, fy, fw, fh = derive_rect(win_rect, HDMI)
    assert all(0.0 <= v <= 1.0 for v in (fx, fy, fw, fh))
    assert fw > 0.0 and fh > 0.0
    assert fx + fw <= 1.0, f"rect starts past the right edge: {fx} + {fw}"
    assert fy + fh <= 1.0, f"rect starts past the bottom edge: {fy} + {fh}"


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


def test_resolve_uses_every_field_from_the_snapshot():
    """The snapshot is authoritative: monitor and rect come from it, not defaults."""
    m = mode()
    m.arm(Capture(wid=0x600001, wm_class="slack", label="Slack",
                  monitor="DP-9", rect=(0.25, 0.5, 0.25, 0.5), at=0.0))
    dial = m.resolve("4", existing=None)
    assert dial.match_class == "slack"
    assert dial.label == "Slack"
    assert dial.monitor == "DP-9"          # NOT the Defaults monitor
    assert dial.rect == (0.25, 0.5, 0.25, 0.5)


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


def test_an_overwrite_request_carries_both_sides_and_disarms():
    """Nothing is persisted; the caller gets both sides and the capture is spent."""
    m = mode()
    m.arm(capture(cls="slack", label="Slack"))
    req = m.resolve("4", existing=existing_dial(slot="4", cls="spotify"))
    assert req.existing.match_class == "spotify"
    assert req.existing.slot == "4"
    assert req.incoming.match_class == "slack"
    assert req.incoming.slot == "4"
    assert m.armed is False              # capture consumed on this path too
    with pytest.raises(AssignError):     # cannot be re-resolved
        m.resolve("4", existing=None)


def test_resolve_when_not_armed_raises():
    with pytest.raises(AssignError):
        mode().resolve("4", existing=None)


def test_resolve_rejects_the_reserved_slot():
    m = mode()
    m.arm(capture())
    with pytest.raises(AssignError, match="reserved"):
        m.resolve(".", existing=None)


@pytest.mark.parametrize("slot", ["99", "z", "", "F1"])
def test_resolve_rejects_a_non_bindable_slot(slot):
    m = mode()
    m.arm(capture())
    with pytest.raises(AssignError, match="bindable"):
        m.resolve(slot, existing=None)


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
