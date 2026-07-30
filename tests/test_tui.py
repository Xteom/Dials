import pytest

from dials.config import Dial
from dials.tui import GRID, cell_label, detail_lines, move


def dial(slot="9"):
    return Dial(slot=slot, label="Spotify", match_class="spotify",
                launch="spotify", icon="", monitor="HDMI-0",
                rect=(0.0, 0.0, 0.5, 1.0), on_focus_loss="hide",
                pin_geometry=False)


def test_grid_mirrors_the_physical_numpad():
    assert GRID[0] == ("7", "8", "9")
    assert GRID[1] == ("4", "5", "6")
    assert GRID[2] == ("1", "2", "3")


def test_grid_contains_every_bindable_slot_and_the_assign_key():
    flat = {s for row in GRID for s in row if s}
    for slot in keys_all():
        assert slot in flat


def keys_all():
    from dials import keys
    return list(keys.SLOT_KEYCODES)


def test_cell_label_shows_the_glyph_and_a_truncated_label():
    text = cell_label(dial(), "9", "S")
    assert "9" in text and "S" in text


def test_cell_label_for_an_unbound_slot_shows_a_placeholder():
    text = cell_label(None, "5", "")
    assert "5" in text
    assert "-" in text or "·" in text


def test_cell_label_never_exceeds_the_cell_width():
    import dataclasses
    long = dataclasses.replace(dial(), label="An Extremely Long Application Name")
    assert all(len(line) <= 9 for line in cell_label(long, "9", "S").split("\n"))


def test_move_right_within_a_row():
    assert move(GRID, "7", 1, 0) == "8"


def test_move_down_a_column():
    assert move(GRID, "7", 0, 1) == "4"


def test_move_stops_at_the_grid_edge():
    assert move(GRID, "7", -1, 0) == "7"
    assert move(GRID, "7", 0, -1) == "7"


def test_move_skips_empty_grid_cells():
    # Whatever the layout, moving must always land on a real slot.
    for row in GRID:
        for slot in row:
            if not slot:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                assert move(GRID, slot, dx, dy) in {s for r in GRID for s in r if s}


def test_move_skips_an_interior_gap():
    """The gap-skip branch is unreachable with the shipped GRID, so prove it
    works against a layout that does have an interior gap."""
    synthetic = (
        ("a", "", "b"),
        ("c", "d", "e"),
    )
    # Moving right from "a" must skip the gap and land on "b", not stay on "a".
    assert move(synthetic, "a", 1, 0) == "b"
    # And leftwards symmetrically.
    assert move(synthetic, "b", -1, 0) == "a"


def test_move_stays_when_a_gap_has_nothing_beyond_it():
    """Trailing-edge gap: the skip target is out of range, so stay put.
    This is the case every gap in the real GRID hits."""
    synthetic = (
        ("a", "b", ""),
    )
    assert move(synthetic, "b", 1, 0) == "b"


def test_detail_lines_include_every_editable_field():
    text = "\n".join(detail_lines(dial()))
    for expected in ("spotify", "HDMI-0", "hide", "50"):
        assert expected in text


def test_detail_lines_for_an_unbound_slot():
    assert any("unbound" in line.lower() for line in detail_lines(None))
