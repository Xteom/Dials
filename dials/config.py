"""Config loading and validation.

Read-only on purpose: this module is imported by the daemon, so it must not
pull in a TOML *writer*. Writing lives in `dials.configwrite`, which only the
CLI imports.

Validation is strict and happens at load time rather than at show time, so a
bad rect or an empty match_class is a startup error with a clear message
instead of a Dial that misbehaves in a way nobody can explain.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

try:                       # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10, our pinned interpreter
    import tomli as _toml

from dials import keys

VALID_FOCUS_LOSS = ("normal", "above", "hide")


class ConfigError(ValueError):
    """Raised for any malformed or invalid configuration."""


@dataclass(frozen=True)
class Defaults:
    monitor: str = "HDMI-0"
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.5, 1.0)
    on_focus_loss: str = "hide"
    pin_geometry: bool = False


@dataclass(frozen=True)
class Dial:
    slot: str
    label: str
    match_class: str
    launch: str | None
    icon: str
    monitor: str
    rect: tuple[float, float, float, float]
    on_focus_loss: str
    pin_geometry: bool


@dataclass(frozen=True)
class Config:
    defaults: Defaults
    dials: dict[str, Dial]

    def dial(self, slot: str) -> Dial | None:
        return self.dials.get(slot)


# ---- XDG paths -----------------------------------------------------------

def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


def config_path() -> Path:
    """The live config. The ONLY file anything reads or writes."""
    return _xdg("XDG_CONFIG_HOME", ".config") / "dials" / "config.toml"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "dials"


def reference_path() -> Path:
    """The repo's reference snapshot. Read by nothing; refreshed on demand."""
    return Path(__file__).resolve().parent.parent / "config" / "config.reference.toml"


# ---- validation ----------------------------------------------------------

def _rect(value, where: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ConfigError(f"{where}: rect must be 4 numbers [x, y, w, h]")
    try:
        fx, fy, fw, fh = (float(v) for v in value)
    except (TypeError, ValueError):
        raise ConfigError(f"{where}: rect values must be numbers") from None
    for label, v in (("x", fx), ("y", fy), ("w", fw), ("h", fh)):
        if not 0.0 <= v <= 1.0:
            raise ConfigError(f"{where}: rect {label}={v} outside 0.0..1.0")
    if fw == 0.0 or fh == 0.0:
        raise ConfigError(f"{where}: rect w and h must be greater than 0")
    return (fx, fy, fw, fh)


def _focus_loss(value, where: str) -> str:
    if value not in VALID_FOCUS_LOSS:
        raise ConfigError(
            f"{where}: on_focus_loss={value!r} must be one of "
            f"{', '.join(VALID_FOCUS_LOSS)}"
        )
    return value


def _defaults(raw: dict) -> Defaults:
    base = Defaults()
    return replace(
        base,
        monitor=str(raw.get("monitor", base.monitor)),
        rect=_rect(raw.get("rect", list(base.rect)), "defaults"),
        on_focus_loss=_focus_loss(
            raw.get("on_focus_loss", base.on_focus_loss), "defaults"
        ),
        pin_geometry=bool(raw.get("pin_geometry", base.pin_geometry)),
    )


def _dial(slot: str, raw: dict, defaults: Defaults) -> Dial:
    where = f"dials.{slot!r}"
    if slot == keys.ASSIGN_SLOT:
        raise ConfigError(
            f"{where}: '{keys.ASSIGN_SLOT}' is reserved for assign mode"
        )
    if not keys.is_bindable(slot):
        raise ConfigError(f"{where}: not a bindable slot")

    match_class = str(raw.get("match_class", "")).strip()
    if not match_class:
        raise ConfigError(f"{where}: match_class is required and cannot be empty")

    return Dial(
        slot=slot,
        label=str(raw.get("label") or match_class),
        match_class=match_class,
        launch=(str(raw["launch"]) if raw.get("launch") else None),
        icon=str(raw.get("icon", "")),
        monitor=str(raw.get("monitor", defaults.monitor)),
        rect=_rect(raw.get("rect", list(defaults.rect)), where),
        on_focus_loss=_focus_loss(
            raw.get("on_focus_loss", defaults.on_focus_loss), where
        ),
        pin_geometry=bool(raw.get("pin_geometry", defaults.pin_geometry)),
    )


# ---- entry points --------------------------------------------------------

def loads(text: str) -> Config:
    try:
        raw = _toml.loads(text)
    except Exception as exc:
        raise ConfigError(f"malformed TOML: {exc}") from None
    defaults = _defaults(raw.get("defaults", {}))
    dials = {
        slot: _dial(slot, body, defaults)
        for slot, body in (raw.get("dials", {}) or {}).items()
    }
    return Config(defaults=defaults, dials=dials)


def load(path: Path | None = None) -> Config:
    """Load the live config. A missing file is not an error - it means no Dials."""
    path = Path(path) if path is not None else config_path()
    if not path.exists():
        return Config(defaults=Defaults(), dials={})
    return loads(path.read_text())
