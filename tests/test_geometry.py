import pytest

from dials.geometry import Monitor, Rect, resolve

HDMI = Rect(0, 0, 3440, 1440)          # ultrawide, as measured by probe 06
EDP = Rect(388, 1440, 2560, 1440)      # laptop, primary
ROOT = Rect(0, 0, 3440, 2880)


def test_left_half_of_ultrawide():
    assert resolve((0.0, 0.0, 0.5, 1.0), HDMI, ROOT) == Rect(0, 0, 1720, 1440)


def test_right_half_of_ultrawide():
    assert resolve((0.5, 0.0, 0.5, 1.0), HDMI, ROOT) == Rect(1720, 0, 1720, 1440)


def test_fractions_are_relative_to_the_monitor_not_the_root():
    # eDP is offset +388+1440; a full-monitor rect must land on it, not at 0,0
    assert resolve((0.0, 0.0, 1.0, 1.0), EDP, ROOT) == Rect(388, 1440, 2560, 1440)


def test_rounding_is_stable_and_never_exceeds_the_monitor():
    r = resolve((0.0, 0.0, 1 / 3, 1.0), HDMI, ROOT)
    assert r.w == 1146  # int(3440/3)
    assert r.x + r.w <= HDMI.x + HDMI.w


def test_result_is_clamped_to_the_root_box():
    # A monitor rect claiming to extend past the root cannot yield an
    # unreachable window.
    rogue = Rect(3000, 0, 2000, 1440)
    r = resolve((0.0, 0.0, 1.0, 1.0), rogue, ROOT)
    assert r.x + r.w <= ROOT.w
    assert r.x >= 0


def test_negative_monitor_offset_is_clamped():
    rogue = Rect(-500, -200, 1000, 800)
    r = resolve((0.0, 0.0, 1.0, 1.0), rogue, ROOT)
    assert r.x >= 0 and r.y >= 0


def test_zero_sized_result_is_forced_to_one_pixel():
    # Config validation rejects w/h of 0, but a tiny fraction on a small
    # monitor must still not produce a 0x0 window.
    tiny = Rect(0, 0, 100, 100)
    r = resolve((0.0, 0.0, 0.001, 0.001), tiny, ROOT)
    assert r.w >= 1 and r.h >= 1


def test_clamped_to_is_a_pure_intersection_style_fit():
    assert Rect(10, 10, 50, 50).clamped_to(Rect(0, 0, 100, 100)) == Rect(10, 10, 50, 50)
    assert Rect(90, 90, 50, 50).clamped_to(Rect(0, 0, 100, 100)) == Rect(90, 90, 10, 10)


def test_monitor_dataclass_carries_identity():
    m = Monitor(name="HDMI-0", rect=HDMI, primary=False, crtc=63)
    assert m.name == "HDMI-0" and m.crtc == 63 and m.primary is False


def test_rects_are_frozen():
    # Assert the specific error, not bare Exception: a typo in the attribute
    # name would also raise, and the test would pass for the wrong reason.
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        Rect(0, 0, 1, 1).x = 5


def test_monitor_field_order_is_positional_stable():
    """Later tasks construct Monitor positionally; a field reorder must fail here."""
    m = Monitor("HDMI-0", HDMI, False, 63)
    assert m.name == "HDMI-0"
    assert m.rect == HDMI
    assert m.primary is False
    assert m.crtc == 63


def test_clamped_to_pins_the_far_edge_boundary():
    """x at the far edge must stay addressable inside bounds, never at w itself."""
    assert Rect(100, 0, 1, 1).clamped_to(Rect(0, 0, 100, 100)) == Rect(99, 0, 1, 1)
    assert Rect(0, 100, 1, 1).clamped_to(Rect(0, 0, 100, 100)) == Rect(0, 99, 1, 1)
    assert Rect(500, 500, 10, 10).clamped_to(Rect(0, 0, 100, 100)) == Rect(99, 99, 1, 1)
