from dials.geometry import Monitor, Rect
from dials.monitors import (
    MonitorSource, RawOutput, dedupe_and_sort, edid_name, is_internal,
    parse_selector, pick,
)

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


def test_ordering_is_by_position_not_by_name():
    """Name order and position order deliberately disagree here.

    A regression that sorted by name alone would return the reverse, so this
    pins the sort key to position-first rather than merely 'sorted somehow'.
    """
    # "DP-9" sorts BEFORE "HDMI-0" by name, but sits to its RIGHT on screen.
    left = RawOutput("HDMI-0", crtc=63, x=0, y=0, w=1920, h=1080, primary=False)
    right = RawOutput("DP-9", crtc=64, x=1920, y=0, w=1920, h=1080, primary=False)
    assert [m.name for m in dedupe_and_sort([left, right])] == ["HDMI-0", "DP-9"]
    assert [m.name for m in dedupe_and_sort([right, left])] == ["HDMI-0", "DP-9"]


def test_a_mirrored_pair_prefers_the_primary_output_name():
    """When two outputs share a CRTC, the primary one's name should win."""
    secondary = RawOutput("AAA-0", crtc=63, x=0, y=0, w=1920, h=1080, primary=False)
    primary = RawOutput("ZZZ-9", crtc=63, x=0, y=0, w=1920, h=1080, primary=True)
    mons = dedupe_and_sort([secondary, primary])
    assert len(mons) == 1
    assert mons[0].name == "ZZZ-9"
    assert mons[0].primary is True


HDMI_RAW = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False)
EDP_RAW = RawOutput("eDP-1-1", 64, 388, 1440, 2560, 1440, True)


class CountingReader:
    """Stands in for the RandR query so cache behavior is testable without X."""

    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return list(self.outputs)


def _source(reader):
    return MonitorSource(display=None, root=None, reader=reader,
                         root_rect_reader=lambda: Rect(0, 0, 3440, 2880))


def test_monitors_are_cached_after_the_first_read():
    reader = CountingReader([HDMI_RAW, EDP_RAW])
    src = _source(reader)
    assert len(src.monitors()) == 2
    src.monitors()
    src.monitors()
    assert reader.calls == 1


def test_invalidate_forces_exactly_one_more_read():
    reader = CountingReader([HDMI_RAW, EDP_RAW])
    src = _source(reader)
    src.monitors()
    src.invalidate()
    src.monitors()
    src.monitors()
    assert reader.calls == 2


def test_invalidation_picks_up_an_unplugged_monitor():
    reader = CountingReader([HDMI_RAW, EDP_RAW])
    src = _source(reader)
    assert len(src.monitors()) == 2
    reader.outputs = [HDMI_RAW]          # eDP unplugged
    src.invalidate()
    assert [m.name for m in src.monitors()] == ["HDMI-0"]


def test_a_raising_reader_keeps_the_previous_cache():
    reader = CountingReader([HDMI_RAW, EDP_RAW])
    src = _source(reader)
    good = src.monitors()

    def boom():
        raise RuntimeError("X went away")

    src.reader = boom
    src.invalidate()
    assert src.monitors() == good


def test_a_raising_reader_with_no_cache_yields_no_monitors():
    def boom():
        raise RuntimeError("X went away")

    src = _source(boom)
    assert src.monitors() == []
    # pick() then degrades to the root box, which is its documented last resort.


# Real EDID base blocks captured from this machine on 2026-08-09.
# HDMI-0 is the Dell/Alienware ultrawide; it carries a 0xFC monitor name.
EDID_ULTRAWIDE = bytes.fromhex(
    "00ffffffffffff0010ac85d156374d310423010380502178ead5c5ac5044a225"
    "0f5054a54b00714f8140818081c081009500b300d1c0e77c70a0d0a029503020"
    "3a001d4e3100001a000000ff0031534b433434340a2020202020000000fc0041"
    "573334323544574d0a202020000000fd0830b41d1e6e000a2020202020200106"
)
# eDP-1-1 is the built-in BOE panel; it carries NO 0xFC descriptor, which is
# why `internal` exists as a separate selector.
EDID_PANEL = bytes.fromhex(
    "00ffffffffffff0009e5f90900000000041f0104a5261578030f95ae5243b026"
    "0f505400000001010101010101010101010101010101e26700b0a0a0b4503020"
    "36007dd610000018000000fd0c30f086866a010a202020202020000000fe0042"
    "4f452043510a202020202020000000fe004e4531373351484d2d4e5a310a01f6"
)


def test_edid_name_reads_the_real_ultrawide_blob():
    assert edid_name(EDID_ULTRAWIDE) == "AW3425DWM"


def test_edid_name_is_none_for_a_panel_with_no_name_descriptor():
    # Not a parse failure - laptop panels are not sold as products and simply
    # do not carry 0xFC. This is the case `internal` exists to cover.
    assert edid_name(EDID_PANEL) is None


def test_edid_name_rejects_a_blob_with_a_bad_header():
    assert edid_name(b"\x01" * 128) is None


def test_edid_name_rejects_a_truncated_blob():
    assert edid_name(EDID_ULTRAWIDE[:64]) is None


def test_edid_name_handles_none_and_empty():
    assert edid_name(None) is None
    assert edid_name(b"") is None


import pytest


def test_parse_selector_bare_string_is_a_connector():
    # Back-compat: every config written before this feature keeps its meaning.
    assert parse_selector("HDMI-0") == ("connector", "HDMI-0")


def test_parse_selector_reads_the_edid_and_connector_prefixes():
    assert parse_selector("edid:AW3425DWM") == ("edid", "AW3425DWM")
    assert parse_selector("connector:eDP-1-1") == ("connector", "eDP-1-1")


def test_parse_selector_internal_is_a_bare_keyword():
    assert parse_selector("internal") == ("internal", "")


def test_parse_selector_splits_on_the_first_colon_only():
    # An EDID name containing a colon must survive intact.
    assert parse_selector("edid:ACME:17") == ("edid", "ACME:17")


def test_parse_selector_strips_surrounding_whitespace():
    assert parse_selector("  edid: AW3425DWM  ") == ("edid", "AW3425DWM")


def test_parse_selector_rejects_an_unknown_prefix():
    with pytest.raises(ValueError, match="unknown monitor selector"):
        parse_selector("foo:bar")


def test_parse_selector_rejects_an_empty_name():
    with pytest.raises(ValueError, match="empty name"):
        parse_selector("edid:")


def test_parse_selector_rejects_an_empty_value():
    with pytest.raises(ValueError, match="must not be empty"):
        parse_selector("   ")


def test_is_internal_matches_the_panel_under_both_boot_namings():
    # The rename moves only the INDEX; the connector TYPE never changes.
    assert is_internal("eDP-1")
    assert is_internal("eDP-1-1")
    assert is_internal("LVDS-0")
    assert is_internal("DSI-1")


def test_is_internal_rejects_external_connectors():
    for name in ("HDMI-0", "HDMI-1-0", "DP-1-1", "<root>"):
        assert not is_internal(name)
