from dials.geometry import Monitor, Rect
from dials.monitors import RawOutput, dedupe_and_sort, pick

ROOT = Rect(0, 0, 3440, 2880)

HDMI = RawOutput("HDMI-0", crtc=63, x=0, y=0, w=3440, h=1440, primary=False)
EDP = RawOutput("eDP-1-1", crtc=64, x=388, y=1440, w=2560, h=1440, primary=True)


def test_disconnected_outputs_are_dropped():
    dead = RawOutput("DP-1-1", crtc=0, x=0, y=0, w=0, h=0, primary=False)
    assert [m.name for m in dedupe_and_sort([HDMI, dead, EDP])] == ["HDMI-0", "eDP-1-1"]


def test_mirrored_outputs_sharing_a_crtc_collapse_to_one_monitor():
    # RandR 1.2 enumerates OUTPUTS, not logical monitors. Two outputs cloned
    # onto one CRTC must not appear as two monitors with identical rects.
    clone = RawOutput("HDMI-1-1", crtc=63, x=0, y=0, w=3440, h=1440, primary=False)
    mons = dedupe_and_sort([HDMI, clone])
    assert len(mons) == 1
    assert mons[0].crtc == 63


def test_dedup_keeps_a_deterministic_name_regardless_of_input_order():
    clone = RawOutput("HDMI-1-1", crtc=63, x=0, y=0, w=3440, h=1440, primary=False)
    a = dedupe_and_sort([HDMI, clone])[0].name
    b = dedupe_and_sort([clone, HDMI])[0].name
    assert a == b


def test_ordering_is_deterministic_by_position_then_name():
    mons = dedupe_and_sort([EDP, HDMI])
    assert [m.name for m in mons] == ["HDMI-0", "eDP-1-1"]  # (0,0) before (388,1440)


def test_pick_finds_the_named_monitor_with_no_fallback():
    mons = dedupe_and_sort([HDMI, EDP])
    chosen, reason = pick("HDMI-0", mons, ROOT)
    assert chosen.name == "HDMI-0"
    assert reason is None


def test_pick_falls_back_to_primary_when_named_monitor_is_absent():
    # DP-1-1 is disconnected today; a Dial naming it must land on primary.
    mons = dedupe_and_sort([HDMI, EDP])
    chosen, reason = pick("DP-1-1", mons, ROOT)
    assert chosen.name == "eDP-1-1"
    assert "DP-1-1" in reason and "primary" in reason


def test_pick_falls_back_to_first_when_nothing_is_primary():
    no_primary = [
        RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False),
        RawOutput("eDP-1-1", 64, 388, 1440, 2560, 1440, False),
    ]
    mons = dedupe_and_sort(no_primary)
    chosen, reason = pick("DP-1-1", mons, ROOT)
    assert chosen.name == "HDMI-0"
    assert "first" in reason


def test_pick_falls_back_to_root_box_when_there_are_no_monitors():
    chosen, reason = pick("HDMI-0", [], ROOT)
    assert chosen.rect == ROOT
    assert "root" in reason


def test_pick_returns_a_monitor_type_in_every_branch():
    for mons in ([], dedupe_and_sort([HDMI])):
        chosen, _ = pick("nope", mons, ROOT)
        assert isinstance(chosen, Monitor)
