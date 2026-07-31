import pytest

from dials.config import Dial, load, loads
from dials.configwrite import (
    export_reference, reference_differs, remove_dial, to_toml, upsert_dial,
    write_atomic,
)


def _dial(slot="4", cls="slack"):
    return Dial(slot=slot, label="Slack", match_class=cls, launch="slack",
                icon="", monitor="HDMI-0", rect=(0.0, 0.0, 0.5, 1.0),
                on_focus_loss="hide", pin_geometry=False)


def test_round_trip_through_toml_preserves_every_field():
    original = _dial()
    text = to_toml(loads("").__class__(defaults=loads("").defaults,
                                       dials={"4": original}))
    reparsed = loads(text).dial("4")
    assert reparsed == original


def test_write_atomic_leaves_no_temp_files_behind(tmp_path):
    target = tmp_path / "config.toml"
    write_atomic(target, "hello = 1\n")
    assert target.read_text() == "hello = 1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]


def test_write_atomic_replaces_content_wholesale(tmp_path):
    target = tmp_path / "config.toml"
    write_atomic(target, "a = 1\n")
    write_atomic(target, "b = 2\n")
    assert target.read_text() == "b = 2\n"


def test_write_atomic_creates_parent_directories(tmp_path):
    target = tmp_path / "deep" / "nested" / "config.toml"
    write_atomic(target, "x = 1\n")
    assert target.exists()


def test_write_atomic_cleans_up_its_temp_file_when_the_write_fails(tmp_path, monkeypatch):
    """A failed write must not litter, and must re-raise rather than swallow.

    Without the except-branch cleanup, the mkstemp file would survive as
    .config-XXXX.toml in the user's config directory on every failed write.
    """
    import dials.configwrite as mod

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        mod.write_atomic(tmp_path / "config.toml", "a = 1\n")

    assert not (tmp_path / "config.toml").exists()
    assert list(tmp_path.iterdir()) == [], "temp file was left behind"


def test_upsert_adds_a_dial_to_a_missing_file(tmp_config_path):
    cfg = upsert_dial(tmp_config_path, _dial())
    assert cfg.dial("4").match_class == "slack"
    assert load(tmp_config_path).dial("4").match_class == "slack"


def test_upsert_re_reads_the_file_so_a_stale_caller_cannot_revert_others(tmp_config_path):
    # Concurrency guard: writing dial 4 must not drop dial 9 written meanwhile.
    upsert_dial(tmp_config_path, _dial(slot="9", cls="spotify"))
    upsert_dial(tmp_config_path, _dial(slot="4", cls="slack"))
    on_disk = load(tmp_config_path)
    assert set(on_disk.dials) == {"9", "4"}


def test_upsert_overwrites_an_existing_slot(tmp_config_path):
    upsert_dial(tmp_config_path, _dial(slot="4", cls="old"))
    upsert_dial(tmp_config_path, _dial(slot="4", cls="new"))
    assert load(tmp_config_path).dial("4").match_class == "new"


def test_remove_dial_deletes_only_that_slot(tmp_config_path):
    upsert_dial(tmp_config_path, _dial(slot="4"))
    upsert_dial(tmp_config_path, _dial(slot="9"))
    cfg = remove_dial(tmp_config_path, "4")
    assert set(cfg.dials) == {"9"}
    assert set(load(tmp_config_path).dials) == {"9"}


def test_remove_missing_slot_is_a_no_op(tmp_config_path):
    upsert_dial(tmp_config_path, _dial(slot="9"))
    remove_dial(tmp_config_path, "4")
    assert set(load(tmp_config_path).dials) == {"9"}


def test_export_writes_the_reference_header(tmp_path):
    live = tmp_path / "config.toml"
    ref = tmp_path / "config.reference.toml"
    upsert_dial(live, _dial())
    export_reference(live, ref)
    text = ref.read_text()
    assert "NOT LIVE" in text
    assert "dials config export" in text
    assert load(ref).dial("4").match_class == "slack"


def test_reference_differs_reports_drift(tmp_path):
    live = tmp_path / "config.toml"
    ref = tmp_path / "config.reference.toml"
    upsert_dial(live, _dial(slot="4"))
    export_reference(live, ref)
    assert reference_differs(live, ref) is False
    upsert_dial(live, _dial(slot="9"))
    assert reference_differs(live, ref) is True


@pytest.mark.parametrize("slot", ["/", "*", "+", "-", "enter"])
def test_operator_slots_survive_a_toml_round_trip(tmp_config_path, slot):
    """`/`, `*` and `+` are not valid TOML bare keys and must be quoted.

    If the writer emitted `[dials./]` the file would not parse back, so a user
    binding an operator key would silently corrupt their config.
    """
    upsert_dial(tmp_config_path, _dial(slot=slot, cls="someapp"))
    reloaded = load(tmp_config_path)
    assert slot in reloaded.dials
    assert reloaded.dial(slot).match_class == "someapp"


#: Modules the always-on daemon must never pull in. The <=16 MB budget depends
#: on this graph staying clean, and every module the daemon imports is resident
#: for the whole session. `tomli_w` and `dials.configwrite` are the TOML writer
#: (the daemon imports it LAZILY inside the one function that persists a Dial);
#: `curses`, `gi` and the two UI modules belong to the CLI/TUI/tray processes.
FORBIDDEN_IN_DAEMON = ("tomli_w", "curses", "gi", "dials.tui", "dials.tray",
                       "dials.configwrite")


def test_importing_the_daemon_pulls_in_none_of_the_forbidden_modules():
    """The daemon's budget forbids the TOML writer, curses, GTK and the UI.

    Runs in a SUBPROCESS deliberately: this test suite imports several of these
    modules itself, so an in-process sys.modules check would be polluted and
    pass vacuously - and for three of the six it would pass while the daemon
    imported them eagerly.

    Checked by import graph rather than by grepping daemon.py, because the lazy
    imports the daemon does make are correct and a text search would fail on
    correct code.
    """
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import sys, json, dials.daemon; "
         f"print(json.dumps([m for m in {list(FORBIDDEN_IN_DAEMON)!r} "
         "if m in sys.modules]))"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    leaked = json.loads(result.stdout.strip())
    assert leaked == [], f"dials.daemon imported {leaked}"
