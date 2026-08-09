import pytest

from dials.geometry import Monitor, Rect
from dials.monitors import (
    MonitorSource, RawOutput, dedupe_and_sort, edid_name, is_internal,
    parse_selector, pick, selector_for,
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


def test_dedupe_populates_the_display_name_from_edid():
    raw = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False, edid=EDID_ULTRAWIDE)
    assert dedupe_and_sort([raw])[0].display_name == "AW3425DWM"


def test_dedupe_leaves_display_name_none_for_a_panel_without_one():
    raw = RawOutput("eDP-1-1", 64, 0, 0, 2560, 1440, True, edid=EDID_PANEL)
    assert dedupe_and_sort([raw])[0].display_name is None


def test_a_failed_edid_read_is_not_the_same_as_no_edid():
    # Both have display_name None; only one is an unreliable identity, and the
    # write path refuses on that one.
    absent = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False, edid=None)
    failed = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False,
                       edid=None, edid_failed=True)
    assert dedupe_and_sort([absent])[0].identity_reliable is True
    assert dedupe_and_sort([failed])[0].identity_reliable is False


def test_existing_positional_rawoutput_construction_still_works():
    # Tests across this suite build RawOutput positionally; the new fields must
    # be appended with defaults, never inserted.
    raw = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False)
    assert raw.edid is None and raw.edid_failed is False


# The two boot configurations this whole feature exists to survive. Same
# physical desk; X named the outputs differently on 2026-08-07 and 2026-08-09.
BOOT_INTEL_PRIMARY = [
    RawOutput("HDMI-1-0", 522, 0, 0, 3440, 1440, False, edid=EDID_ULTRAWIDE),
    RawOutput("eDP-1", 62, 488, 1440, 2560, 1440, True, edid=EDID_PANEL),
]
BOOT_NVIDIA_PRIMARY = [
    RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, False, edid=EDID_ULTRAWIDE),
    RawOutput("eDP-1-1", 62, 388, 1440, 2560, 1440, True, edid=EDID_PANEL),
]


def test_edid_selector_finds_the_same_display_across_both_boots():
    """THE regression test. This is the incident, encoded.

    A connector name picks the ultrawide on one boot and silently falls back to
    the laptop panel on the other. An EDID name must pick the ultrawide on both.
    """
    for raws in (BOOT_INTEL_PRIMARY, BOOT_NVIDIA_PRIMARY):
        mons = dedupe_and_sort(raws)
        chosen, reason = pick("edid:AW3425DWM", mons, ROOT)
        assert chosen.rect == Rect(0, 0, 3440, 1440)
        assert reason is None


def test_internal_selector_finds_the_panel_across_both_boots():
    for raws in (BOOT_INTEL_PRIMARY, BOOT_NVIDIA_PRIMARY):
        mons = dedupe_and_sort(raws)
        chosen, reason = pick("internal", mons, ROOT)
        assert chosen.rect.w == 2560
        assert reason is None


def test_connector_prefix_is_equivalent_to_a_bare_name():
    mons = dedupe_and_sort(BOOT_NVIDIA_PRIMARY)
    assert pick("connector:HDMI-0", mons, ROOT)[0].name == "HDMI-0"
    assert pick("HDMI-0", mons, ROOT)[0].name == "HDMI-0"


def test_absent_edid_name_falls_back_and_lists_what_is_present():
    mons = dedupe_and_sort(BOOT_NVIDIA_PRIMARY)
    chosen, reason = pick("edid:NOPE", mons, ROOT)
    assert chosen.name == "eDP-1-1"           # primary
    assert "NOPE" in reason
    assert "AW3425DWM" in reason              # what you could have typed


def test_two_displays_sharing_a_name_are_ambiguous_not_first_wins():
    """Silently taking the first match would LOOK like success.

    That is the failure class this feature exists to end, so an ambiguous
    selector must fall back and say so.
    """
    twin_a = RawOutput("DP-1", 70, 0, 0, 1920, 1080, False, edid=EDID_ULTRAWIDE)
    twin_b = RawOutput("DP-2", 71, 1920, 0, 1920, 1080, False, edid=EDID_ULTRAWIDE)
    panel = RawOutput("eDP-1", 62, 0, 1080, 2560, 1440, True, edid=EDID_PANEL)
    mons = dedupe_and_sort([twin_a, twin_b, panel])
    chosen, reason = pick("edid:AW3425DWM", mons, ROOT)
    assert chosen.name == "eDP-1"             # fell back to primary
    assert "ambiguous" in reason
    assert "DP-1" in reason and "DP-2" in reason


def test_two_internal_panels_are_ambiguous():
    a = RawOutput("eDP-1", 62, 0, 0, 2560, 1440, True)
    b = RawOutput("eDP-2", 63, 2560, 0, 2560, 1440, False)
    chosen, reason = pick("internal", dedupe_and_sort([a, b]), ROOT)
    assert chosen.name == "eDP-1"             # fell back to primary
    assert "more than one internal" in reason


def test_no_internal_panel_present_falls_back():
    mons = dedupe_and_sort([RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, True)])
    chosen, reason = pick("internal", mons, ROOT)
    assert chosen.name == "HDMI-0"
    assert "no internal panel" in reason


def test_an_invalid_selector_falls_back_rather_than_raising():
    # config.py rejects these at load; pick must still never raise.
    mons = dedupe_and_sort(BOOT_NVIDIA_PRIMARY)
    chosen, reason = pick("foo:bar", mons, ROOT)
    assert chosen.name == "eDP-1-1"
    assert "invalid monitor selector" in reason


class _FakeProp:
    def __init__(self, value):
        self.value = value


def test_read_edid_distinguishes_absent_from_failed(monkeypatch):
    """The tri-state, at the boundary that creates it."""
    src = MonitorSource(display=None, root=None, reader=lambda: [])

    from Xlib.ext import randr

    monkeypatch.setattr(randr, "get_output_property",
                        lambda *a, **k: _FakeProp(list(EDID_ULTRAWIDE)))
    assert src._read_edid(1, 2) == (EDID_ULTRAWIDE, False)

    monkeypatch.setattr(randr, "get_output_property",
                        lambda *a, **k: _FakeProp([]))
    assert src._read_edid(1, 2) == (None, False)      # no EDID, not a failure

    def _boom(*a, **k):
        raise OSError("X went away")

    monkeypatch.setattr(randr, "get_output_property", _boom)
    assert src._read_edid(1, 2) == (None, True)       # failure, not absence


def test_selector_for_prefers_edid_for_a_named_external_display():
    mons = dedupe_and_sort(BOOT_NVIDIA_PRIMARY)
    ultrawide = next(m for m in mons if m.name == "HDMI-0")
    assert selector_for(ultrawide, mons) == "edid:AW3425DWM"


def test_selector_for_prefers_internal_over_a_model_name():
    # "the built-in screen" is a more durable statement of intent than a
    # panel's model number, so internal outranks edid:.
    named_panel = RawOutput("eDP-1", 62, 0, 0, 2560, 1440, True,
                            edid=EDID_ULTRAWIDE)
    mons = dedupe_and_sort([named_panel])
    assert selector_for(mons[0], mons) == "internal"


def test_selector_for_will_not_write_a_name_two_displays_share():
    twin_a = RawOutput("DP-1", 70, 0, 0, 1920, 1080, True, edid=EDID_ULTRAWIDE)
    twin_b = RawOutput("DP-2", 71, 1920, 0, 1920, 1080, False, edid=EDID_ULTRAWIDE)
    mons = dedupe_and_sort([twin_a, twin_b])
    assert selector_for(mons[0], mons) == "DP-1"      # bare connector


def test_selector_for_falls_back_to_the_bare_connector_when_unnamed():
    raw = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, True)
    mons = dedupe_and_sort([raw])
    assert selector_for(mons[0], mons) == "HDMI-0"


def test_selector_for_refuses_when_the_identity_could_not_be_read():
    # None means "do not persist anything" - see the write path.
    raw = RawOutput("HDMI-0", 63, 0, 0, 3440, 1440, True, edid_failed=True)
    mons = dedupe_and_sort([raw])
    assert selector_for(mons[0], mons) is None


def test_selector_for_internal_but_not_unique_falls_through():
    # Regression: the internal check must not short-circuit when there are
    # multiple internal monitors. If the inner if became if/else, a future
    # refactor would wrongly return "internal" for the second panel.
    panel_a = RawOutput("eDP-1", 62, 0, 0, 2560, 1440, True, edid=EDID_PANEL)
    panel_b = RawOutput("eDP-2", 63, 2560, 0, 2560, 1440, False, edid=EDID_ULTRAWIDE)
    mons = dedupe_and_sort([panel_a, panel_b])
    # Neither should return "internal" because both are internal.
    assert selector_for(mons[0], mons) != "internal"
    assert selector_for(mons[1], mons) != "internal"


def test_selector_for_refuses_unreliable_identity_even_on_internal_connector():
    # Regression: identity_reliable gate must stay BEFORE the internal check.
    # If it moved below, an internal connector with edid_failed=True would
    # wrongly return "internal" instead of None.
    panel = RawOutput("eDP-1", 62, 0, 0, 2560, 1440, True, edid_failed=True)
    mons = dedupe_and_sort([panel])
    assert selector_for(mons[0], mons) is None


def test_selector_for_refuses_the_root_sentinel():
    # `<root>` is the synthetic destination pick() falls back to when there is
    # no usable monitor at all - the case the refusal gate most obviously
    # exists for. It has no connector and no EDID behind it, so persisting it
    # would produce a config value that can never match anything again and
    # would warn on every keypress.
    root = Monitor("<root>", ROOT, True, 0, identity_reliable=False)
    assert selector_for(root, [root]) is None


def test_pick_falls_back_to_a_root_sentinel_with_unreliable_identity():
    # Same fact, from the other side: pick()'s own fallback must construct the
    # sentinel with identity_reliable=False so callers that write config never
    # have to special-case the string "<root>".
    chosen, reason = pick("HDMI-0", [], ROOT)
    assert chosen.name == "<root>"
    assert chosen.identity_reliable is False
    assert selector_for(chosen, [chosen]) is None
