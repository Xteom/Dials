import pytest

from dials.config import ConfigError, load, loads

GOOD = """
[defaults]
monitor       = "HDMI-0"
rect          = [0.0, 0.0, 0.5, 1.0]
on_focus_loss = "hide"
pin_geometry  = false

[dials."9"]
label       = "Spotify"
match_class = "spotify"
launch      = "/snap/bin/spotify --force-device-scale-factor=0.7"
rect        = [0.0, 0.0, 0.5, 1.0]

[dials."6"]
label         = "Firefox (dial6)"
match_class   = "Dial6"
launch        = "firefox -P dial6 --class=Dial6 --no-remote --new-instance"
rect          = [0.5, 0.0, 0.5, 1.0]
on_focus_loss = "above"
"""


def test_loads_both_dials():
    cfg = loads(GOOD)
    assert set(cfg.dials) == {"9", "6"}
    assert cfg.dial("9").label == "Spotify"
    assert cfg.dial("nope") is None


def test_unset_fields_inherit_from_defaults():
    d = loads(GOOD).dial("9")
    assert d.monitor == "HDMI-0"        # not set on the dial
    assert d.on_focus_loss == "hide"    # from defaults
    assert d.pin_geometry is False


def test_explicit_fields_override_defaults():
    assert loads(GOOD).dial("6").on_focus_loss == "above"


def test_rect_becomes_a_four_tuple_of_floats():
    assert loads(GOOD).dial("6").rect == (0.5, 0.0, 0.5, 1.0)


def test_missing_file_yields_defaults_only_and_no_dials(tmp_config_path):
    cfg = load(tmp_config_path)
    assert cfg.dials == {}
    assert cfg.defaults.monitor == "HDMI-0"


def test_rejects_the_reserved_assign_slot():
    with pytest.raises(ConfigError, match="reserved"):
        loads('[dials."."]\nlabel="x"\nmatch_class="x"\n')


def test_rejects_an_unknown_slot():
    with pytest.raises(ConfigError, match="slot"):
        loads('[dials."99"]\nlabel="x"\nmatch_class="x"\n')


def test_rejects_empty_match_class():
    # An empty class would match unpredictably, so it is never accepted.
    with pytest.raises(ConfigError, match="match_class"):
        loads('[dials."9"]\nlabel="x"\nmatch_class=""\n')


def test_rejects_missing_match_class():
    with pytest.raises(ConfigError, match="match_class"):
        loads('[dials."9"]\nlabel="x"\n')


@pytest.mark.parametrize("rect", [
    "[0.0, 0.0, 0.5]",              # too few
    "[0.0, 0.0, 0.5, 1.0, 0.2]",    # too many
    "[-0.1, 0.0, 0.5, 1.0]",        # below range
    "[0.0, 0.0, 1.5, 1.0]",         # above range
    "[0.0, 0.0, 0.0, 1.0]",         # zero width
    "[0.0, 0.0, 0.5, 0.0]",         # zero height
])
def test_rejects_bad_rects(rect):
    with pytest.raises(ConfigError, match="rect"):
        loads(f'[dials."9"]\nlabel="x"\nmatch_class="x"\nrect={rect}\n')


def test_rejects_unknown_on_focus_loss():
    with pytest.raises(ConfigError, match="on_focus_loss"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\non_focus_loss="vanish"\n')


def test_rejects_malformed_toml():
    with pytest.raises(ConfigError):
        loads("[dials.\n")


def test_launch_is_optional_and_defaults_to_none():
    d = loads('[dials."9"]\nlabel="x"\nmatch_class="x"\n').dial("9")
    assert d.launch is None


def test_label_falls_back_to_the_match_class():
    d = loads('[dials."9"]\nmatch_class="slack"\n').dial("9")
    assert d.label == "slack"


def test_xdg_paths_respect_environment(monkeypatch, tmp_path):
    from dials import config
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert config.config_path() == tmp_path / "cfg" / "dials" / "config.toml"
    assert config.state_dir() == tmp_path / "state" / "dials"


def test_reference_slack_dial_is_buriable():
    from dials import config

    slack = load(config.reference_path()).dial("5")
    assert slack is not None
    assert slack.on_focus_loss == "normal"


# ---- strict type validation (Item 1, task 11b) ----------------------------


def test_rejects_pin_geometry_as_a_quoted_string_in_a_dial():
    # bool("false") is True; a quoted boolean must never be silently coerced.
    with pytest.raises(ConfigError, match="pin_geometry"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\npin_geometry="false"\n')


def test_rejects_pin_geometry_as_an_int_in_a_dial():
    # isinstance(True, int) is True in Python, so this must be checked
    # explicitly rather than falling out of a numeric check.
    with pytest.raises(ConfigError, match="pin_geometry"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\npin_geometry=1\n')


def test_accepts_pin_geometry_as_a_real_bool_in_a_dial():
    d = loads('[dials."9"]\nlabel="x"\nmatch_class="x"\npin_geometry=true\n').dial("9")
    assert d.pin_geometry is True


def test_rejects_pin_geometry_as_a_quoted_string_in_defaults():
    with pytest.raises(ConfigError, match="pin_geometry"):
        loads('[defaults]\npin_geometry="false"\n')


def test_rejects_pin_geometry_as_an_int_in_defaults():
    with pytest.raises(ConfigError, match="pin_geometry"):
        loads('[defaults]\npin_geometry=1\n')


def test_accepts_pin_geometry_as_a_real_bool_in_defaults():
    cfg = loads('[defaults]\npin_geometry=true\n')
    assert cfg.defaults.pin_geometry is True


def test_rejects_a_non_string_match_class():
    with pytest.raises(ConfigError, match="match_class"):
        loads('[dials."9"]\nlabel="x"\nmatch_class=42\n')


def test_rejects_a_non_string_monitor_in_a_dial():
    with pytest.raises(ConfigError, match="monitor"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\nmonitor=42\n')


def test_rejects_a_non_string_monitor_in_defaults():
    with pytest.raises(ConfigError, match="monitor"):
        loads('[defaults]\nmonitor=42\n')


def test_rejects_a_non_string_label():
    with pytest.raises(ConfigError, match="label"):
        loads('[dials."9"]\nlabel=42\nmatch_class="x"\n')


def test_rejects_a_non_string_icon():
    with pytest.raises(ConfigError, match="icon"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\nicon=42\n')


def test_rejects_a_non_string_launch():
    with pytest.raises(ConfigError, match="launch"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\nlaunch=42\n')


def test_launch_absent_is_still_fine():
    # Absent is not the same as wrong-typed: this must keep working.
    d = loads('[dials."9"]\nlabel="x"\nmatch_class="x"\n').dial("9")
    assert d.launch is None


def test_rejects_a_boolean_match_class():
    with pytest.raises(ConfigError, match="match_class"):
        loads('[dials."9"]\nlabel="x"\nmatch_class=true\n')


def test_rejects_a_list_monitor():
    with pytest.raises(ConfigError, match="monitor"):
        loads('[dials."9"]\nlabel="x"\nmatch_class="x"\nmonitor=["a"]\n')


# ---- malformed SHAPES, not just malformed values -------------------------
#
# All three of these used to escape as AttributeError from a `.get()` on a
# non-dict. `dials status` catches only ConfigError, so the command whose whole
# job is reporting config health tracebacked on exactly the input it exists to
# diagnose - and the daemon's SIGHUP reload path relies on the same exception
# type to keep the last-good config.

def test_rejects_dials_that_is_not_a_table():
    with pytest.raises(ConfigError, match="dials must be a table") as exc:
        loads("dials = 5\n")
    assert "int" in str(exc.value)
    assert '[dials."9"]' in str(exc.value)


def test_rejects_a_dial_body_that_is_not_a_table():
    with pytest.raises(ConfigError, match="must be a table") as exc:
        loads('[dials]\n"9" = 5\n')
    assert "dials.'9'" in str(exc.value)
    assert '[dials."9"]' in str(exc.value)


def test_rejects_defaults_that_is_not_a_table():
    with pytest.raises(ConfigError, match="defaults must be a table") as exc:
        loads('defaults = "x"\n')
    assert "str" in str(exc.value)
    assert "[defaults]" in str(exc.value)


def test_a_present_but_falsy_table_is_malformed_not_absent():
    """`dials = 0` is a typo, not an empty table; only an ABSENT key is empty."""
    with pytest.raises(ConfigError, match="dials must be a table"):
        loads("dials = 0\n")


def test_an_absent_or_empty_table_is_still_fine():
    assert loads("").dials == {}
    assert loads("[dials]\n").dials == {}
    assert loads("[defaults]\n").defaults.monitor == "HDMI-0"


def test_monitor_accepts_every_selector_form():
    for value in ("HDMI-0", "edid:AW3425DWM", "connector:eDP-1-1", "internal"):
        cfg = loads(f'[defaults]\nmonitor = "{value}"\n')
        assert cfg.defaults.monitor == value


def test_unknown_monitor_prefix_is_a_config_error_in_defaults():
    with pytest.raises(ConfigError, match="unknown monitor selector"):
        loads('[defaults]\nmonitor = "foo:bar"\n')


def test_unknown_monitor_prefix_is_a_config_error_on_a_dial():
    # Per-Dial overrides must be validated too, not just defaults.
    with pytest.raises(ConfigError, match="unknown monitor selector"):
        loads('[dials."9"]\nmatch_class = "X"\nmonitor = "foo:bar"\n')


def test_empty_edid_selector_is_a_config_error():
    with pytest.raises(ConfigError, match="empty name"):
        loads('[defaults]\nmonitor = "edid:"\n')
