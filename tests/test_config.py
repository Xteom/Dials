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
