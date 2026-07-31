from dials.icons import DEFAULT_GLYPH, desktop_icon_name, glyph_for


def test_an_explicit_override_always_wins():
    assert glyph_for("spotify", icon_override="X") == "X"


def test_known_classes_map_to_glyphs():
    assert glyph_for("spotify") != DEFAULT_GLYPH
    assert glyph_for("firefox") != DEFAULT_GLYPH


def test_matching_is_case_insensitive():
    assert glyph_for("Spotify") == glyph_for("spotify")


def test_unknown_class_falls_back_to_the_default_glyph():
    assert glyph_for("some-unknown-app") == DEFAULT_GLYPH


def test_the_desktop_icon_name_is_consulted_before_giving_up():
    # Dial6's class is not a known app name, but its .desktop Icon= is firefox.
    assert glyph_for("Dial6", desktop_icon="firefox") == glyph_for("firefox")


def test_override_beats_the_desktop_icon():
    assert glyph_for("Dial6", icon_override="Z", desktop_icon="firefox") == "Z"


def test_every_glyph_is_a_single_display_unit():
    from dials.icons import GLYPHS
    for name, g in GLYPHS.items():
        assert len(g) <= 2, f"{name} glyph too wide: {g!r}"


def test_desktop_icon_name_reads_the_icon_key(tmp_path):
    (tmp_path / "slack.desktop").write_text(
        "[Desktop Entry]\nName=Slack\nIcon=slack-icon\nExec=slack\n"
    )
    assert desktop_icon_name("slack", search_dirs=[tmp_path]) == "slack-icon"


def test_desktop_icon_name_matches_on_startupwmclass(tmp_path):
    (tmp_path / "spotify_spotify.desktop").write_text(
        "[Desktop Entry]\nName=Spotify\nIcon=spotify-linux-128\n"
        "StartupWMClass=spotify\nExec=/snap/bin/spotify\n"
    )
    assert desktop_icon_name("spotify", search_dirs=[tmp_path]) == "spotify-linux-128"


def test_startupwmclass_beats_a_filename_match_scanned_earlier(tmp_path):
    """A filename hit must not pre-empt a StartupWMClass hit in a later file.

    Files are scanned in sorted() order, so `myapp.desktop` (filename match) is
    seen BEFORE `zz-other.desktop` (StartupWMClass match). If the filename hit
    returned early instead of only setting the fallback, this would return
    "filename-icon".
    """
    (tmp_path / "myapp.desktop").write_text(
        "[Desktop Entry]\nName=My App\nIcon=filename-icon\nExec=myapp\n"
    )
    (tmp_path / "zz-other.desktop").write_text(
        "[Desktop Entry]\nName=Other\nIcon=startupwmclass-icon\n"
        "StartupWMClass=myapp\nExec=other\n"
    )
    assert desktop_icon_name("myapp", search_dirs=[tmp_path]) == "startupwmclass-icon"


def test_a_filename_match_is_used_when_no_startupwmclass_matches(tmp_path):
    """The fallback must still win when nothing declares a matching class."""
    (tmp_path / "myapp.desktop").write_text(
        "[Desktop Entry]\nName=My App\nIcon=filename-icon\nExec=myapp\n"
    )
    (tmp_path / "zz-other.desktop").write_text(
        "[Desktop Entry]\nName=Other\nIcon=other-icon\n"
        "StartupWMClass=somethingelse\nExec=other\n"
    )
    assert desktop_icon_name("myapp", search_dirs=[tmp_path]) == "filename-icon"


def test_desktop_icon_name_returns_empty_when_nothing_matches(tmp_path):
    assert desktop_icon_name("nope", search_dirs=[tmp_path]) == ""


def test_desktop_icon_name_survives_a_missing_directory():
    assert desktop_icon_name("x", search_dirs=["/nonexistent/dir"]) == ""


def test_desktop_icon_name_survives_a_malformed_file(tmp_path):
    (tmp_path / "broken.desktop").write_bytes(b"\xff\xfe not ini at all")
    assert desktop_icon_name("broken", search_dirs=[tmp_path]) == ""
