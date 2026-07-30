"""Config writing. Imported by the CLI only, never by the daemon.

Kept separate from `dials.config` for two reasons: the daemon's resource budget
forbids importing a TOML writer it will never use, and writes need to be atomic
in a way reads do not.

Every write re-reads the file first and merges onto current content, so a TUI
session left open in another terminal cannot silently revert a Dial written
somewhere else.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import tomli_w

from dials.config import Config, Dial, config_path, load, reference_path

REFERENCE_HEADER = """\
# REFERENCE COPY - NOT LIVE. Nothing reads this file.
# The live config is ~/.config/dials/config.toml
# Refresh this snapshot with:  dials config export
"""


def to_toml(config: Config) -> str:
    """Serialise a Config. Slot order is sorted so diffs stay stable."""
    doc: dict = {"defaults": {
        "monitor": config.defaults.monitor,
        "rect": list(config.defaults.rect),
        "on_focus_loss": config.defaults.on_focus_loss,
        "pin_geometry": config.defaults.pin_geometry,
    }}
    dials: dict = {}
    for slot in sorted(config.dials):
        d = config.dials[slot]
        body = {
            "label": d.label,
            "match_class": d.match_class,
            "icon": d.icon,
            "monitor": d.monitor,
            "rect": list(d.rect),
            "on_focus_loss": d.on_focus_loss,
            "pin_geometry": d.pin_geometry,
        }
        if d.launch:
            body["launch"] = d.launch
        dials[slot] = body
    if dials:
        doc["dials"] = dials
    return tomli_w.dumps(doc)


def write_atomic(path: Path, text: str) -> None:
    """Write via temp file in the same directory, fsync, then rename.

    Same-directory temp matters: os.replace is only atomic within a filesystem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def upsert_dial(path: Path | None, dial: Dial) -> Config:
    """Add or replace one Dial, preserving everything else on disk."""
    path = Path(path) if path is not None else config_path()
    current = load(path)
    merged = Config(
        defaults=current.defaults,
        dials={**current.dials, dial.slot: dial},
    )
    write_atomic(path, to_toml(merged))
    return merged


def remove_dial(path: Path | None, slot: str) -> Config:
    path = Path(path) if path is not None else config_path()
    current = load(path)
    remaining = {s: d for s, d in current.dials.items() if s != slot}
    merged = Config(defaults=current.defaults, dials=remaining)
    write_atomic(path, to_toml(merged))
    return merged


def export_reference(live_path: Path | None = None,
                     ref_path: Path | None = None) -> Path:
    """Overwrite the repo's reference snapshot from the live config."""
    live = Path(live_path) if live_path is not None else config_path()
    ref = Path(ref_path) if ref_path is not None else reference_path()
    write_atomic(ref, REFERENCE_HEADER + "\n" + to_toml(load(live)))
    return ref


def reference_differs(live_path: Path | None = None,
                      ref_path: Path | None = None) -> bool:
    """True if the snapshot no longer matches the live config."""
    live = Path(live_path) if live_path is not None else config_path()
    ref = Path(ref_path) if ref_path is not None else reference_path()
    if not Path(ref).exists():
        return True
    return to_toml(load(live)) != to_toml(load(ref))
