import io

import pytest

from dials.cli import Deps, main
from dials.config import Config, Defaults, Dial


def dial(slot="9", cls="spotify"):
    return Dial(slot=slot, label="Spotify", match_class=cls, launch="spotify",
                icon="", monitor="HDMI-0", rect=(0.0, 0.0, 0.5, 1.0),
                on_focus_loss="hide", pin_geometry=False)


def deps(dials=None, paused=False, windows=()):
    state = {
        "config": Config(defaults=Defaults(), dials=dials or {}),
        "paused": paused,
        "signals": [],
        "exports": 0,
    }
    out = io.StringIO()

    def _load(path=None):
        return state["config"]

    def _upsert(path, d):
        state["config"] = Config(state["config"].defaults,
                                 {**state["config"].dials, d.slot: d})
        return state["config"]

    def _remove(path, slot):
        state["config"] = Config(
            state["config"].defaults,
            {s: v for s, v in state["config"].dials.items() if s != slot},
        )
        return state["config"]

    def _export(live=None, ref=None):
        state["exports"] += 1
        return "/repo/config/config.reference.toml"

    d = Deps(
        load=_load, upsert=_upsert, remove=_remove,
        export=_export, differs=lambda live=None, ref=None: True,
        pause_get=lambda: state["paused"],
        pause_set=lambda v: state.__setitem__("paused", v),
        signal_daemon=lambda: state["signals"].append("HUP") or True,
        list_windows=lambda: list(windows),
        out=out,
    )
    return d, state, out


def test_list_shows_every_slot_including_empty_ones():
    d, _, out = deps({"9": dial()})
    assert main(["list"], deps=d) == 0
    text = out.getvalue()
    assert "Spotify" in text
    assert "9" in text
    assert text.count("\n") >= 15      # all 15 bindable slots listed


def test_list_marks_unbound_slots():
    d, _, out = deps({})
    main(["list"], deps=d)
    assert "unbound" in out.getvalue().lower() or "-" in out.getvalue()


def test_unbind_removes_the_slot_and_signals_the_daemon():
    d, state, _ = deps({"9": dial()})
    assert main(["unbind", "9"], deps=d) == 0
    assert "9" not in state["config"].dials
    assert state["signals"] == ["HUP"]


def test_unbind_rejects_the_reserved_slot():
    d, _, out = deps({})
    assert main(["unbind", "."], deps=d) != 0
    assert "reserved" in out.getvalue().lower()


def test_unbind_rejects_an_unknown_slot():
    d, _, _ = deps({})
    assert main(["unbind", "99"], deps=d) != 0


def test_pause_sets_the_flag_and_signals():
    d, state, _ = deps({})
    assert main(["pause"], deps=d) == 0
    assert state["paused"] is True
    assert state["signals"] == ["HUP"]


def test_resume_clears_the_flag_and_signals():
    d, state, _ = deps({}, paused=True)
    assert main(["resume"], deps=d) == 0
    assert state["paused"] is False
    assert state["signals"] == ["HUP"]


def test_status_reports_paused_state():
    d, _, out = deps({"9": dial()}, paused=True)
    assert main(["status"], deps=d) == 0
    assert "paused" in out.getvalue().lower()


def test_status_counts_bound_dials():
    d, _, out = deps({"9": dial(), "6": dial("6", "Dial6")})
    main(["status"], deps=d)
    assert "2" in out.getvalue()


def test_config_prints_both_paths_and_flags_drift():
    d, _, out = deps({})
    assert main(["config"], deps=d) == 0
    text = out.getvalue()
    assert "config.toml" in text
    assert "reference" in text.lower()
    assert "differ" in text.lower()


def test_config_export_refreshes_the_snapshot():
    d, state, out = deps({"9": dial()})
    assert main(["config", "export"], deps=d) == 0
    assert state["exports"] == 1


def test_reload_signals_the_daemon():
    d, state, _ = deps({})
    assert main(["reload"], deps=d) == 0
    assert state["signals"] == ["HUP"]


def test_no_arguments_requests_the_tui():
    d, _, _ = deps({})
    called = []
    assert main([], deps=d, tui=lambda cfg: called.append(True) or 0) == 0
    assert called == [True]


def test_unknown_command_exits_nonzero():
    d, _, _ = deps({})
    with pytest.raises(SystemExit):
        main(["frobnicate"], deps=d)
