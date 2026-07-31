"""Nerd Font glyphs for the TUI.

Glyphs rather than real bitmap icons: Ghostty ships JetBrains Mono Nerd Font by
default so these render with no font install, whereas the Kitty graphics
protocol is fiddly inside a curses screen and degrades badly elsewhere.

Resolution order: explicit per-Dial `icon`, then the class name, then the
`Icon=` key from a matching .desktop file, then a generic default.
"""
from __future__ import annotations

import configparser
from pathlib import Path

DEFAULT_GLYPH = ""          # generic window

GLYPHS: dict[str, str] = {
    "spotify": "",
    "firefox": "",
    "chromium": "",
    "google-chrome": "",
    "code": "",
    "slack": "",
    "discord": "",
    "ghostty": "",
    "gnome-terminal": "",
    "thunderbird": "",
    "nautilus": "",
    "gimp": "",
    "libreoffice": "",
    "steam": "",
    "telegram": "",
    "obsidian": "",
    "zoom": "",
}

_DEFAULT_DIRS = (
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
)


def _normalise(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def glyph_for(match_class: str, icon_override: str = "",
              desktop_icon: str = "") -> str:
    if icon_override:
        return icon_override
    for candidate in (match_class, desktop_icon):
        if not candidate:
            continue
        key = _normalise(candidate)
        if key in GLYPHS:
            return GLYPHS[key]
        for known, glyph in GLYPHS.items():
            if key.startswith(known):
                return glyph
    return DEFAULT_GLYPH


def desktop_icon_name(match_class: str, search_dirs=None) -> str:
    """Read `Icon=` from the .desktop entry matching this window class.

    Prefers a StartupWMClass match, which is how Snap-packaged apps like
    Spotify advertise their class. Best-effort: unreadable files are skipped.
    """
    dirs = [Path(d) for d in (search_dirs or _DEFAULT_DIRS)]
    target = _normalise(match_class)
    fallback = ""
    for directory in dirs:
        try:
            entries = sorted(directory.glob("*.desktop"))
        except Exception:
            continue
        for path in entries:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                section = parser["Desktop Entry"]
            except Exception:
                continue
            icon = section.get("Icon", "").strip()
            if not icon:
                continue
            if _normalise(section.get("StartupWMClass", "")) == target:
                return icon
            if _normalise(path.stem) == target and not fallback:
                fallback = icon
    return fallback
