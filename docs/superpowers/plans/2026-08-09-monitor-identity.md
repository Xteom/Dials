# Monitor Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Dial name its target display by the display's own EDID name, so it lands on the intended screen even when X renames the connector between reboots.

**Architecture:** `monitors.py` keeps its pure/IO split — new pure functions (`edid_name`, `parse_selector`, `is_internal`, `selector_for`) are unit-tested with no X server, and `MonitorSource` gains one property read per connected output. `pick()` grows selector dispatch with three outcomes (zero/one/many matches) in front of its existing, unchanged fallback chain. Both config-write paths route through one shared `selector_for`.

**Tech Stack:** Python 3.10, python-xlib (RandR 1.2), pytest, TOML via `tomli`.

Design: `docs/superpowers/specs/2026-08-09-monitor-identity-design.md`.

## Global Constraints

- **python-xlib floor is `>=0.29`.** `get_output_property` exists in 0.29 and 0.33 — verified. Do not use RandR 1.5 (`get_monitors`); it is absent in 0.29.
- **`monitors.py` must not import from `config.py`.** The selector parser raises `ValueError`; `config.py` wraps it as `ConfigError`.
- **Pure/IO split in `monitors.py` is load-bearing.** No X calls in any function outside `MonitorSource`.
- **Run pytest from the repo root** with `.venv/bin/python -m pytest`. A sibling `dials/` directory shadows the installed package.
- **Every new call site gets mutation-tested** (`README.md:184`): break the caller, watch a test fail, restore. Pure-predicate tests alone have twice let a broken caller ship here.
- **Nothing raises out of the X read path.** A failed EDID read degrades that output; it never crashes the daemon.
- Backwards compatibility: a bare `monitor = "HDMI-0"` must keep meaning exactly what it means today.

---

### Task 1: Parse the EDID monitor name

**Files:**
- Modify: `dials/monitors.py` (add `edid_name` after the imports)
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: nothing
- Produces: `edid_name(blob: bytes | None) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_monitors.py`:

```python
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
```

Update the import at the top of `tests/test_monitors.py`:

```python
from dials.monitors import (
    MonitorSource, RawOutput, dedupe_and_sort, edid_name, pick,
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitors.py -k edid_name -v`
Expected: FAIL — `ImportError: cannot import name 'edid_name'`

- [ ] **Step 3: Implement `edid_name`**

Add to `dials/monitors.py`, after the `RawOutput` dataclass:

```python
#: EDID descriptor tag for the model name. Descriptors live in the base block
#: at bytes 54, 72, 90, 108 - four 18-byte slots, each tagged by byte 3.
_EDID_NAME_TAG = 0xFC
_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def edid_name(blob: bytes | None) -> str | None:
    """The display's own model name from its EDID, or None.

    None covers three different things on purpose - a malformed blob, a blob
    that is not EDID at all, and a display that simply carries no 0xFC
    descriptor. Laptop panels are the third case: a panel is not sold as its
    own product, so it has no model name to report. Callers distinguish "no
    name" from "could not read" via RawOutput.edid_failed, not via this.

    Never raises. A display with an unparseable EDID is a display without a
    name, not a crashed daemon.
    """
    if not blob:
        return None
    b = bytes(blob)
    if len(b) < 128 or b[:8] != _EDID_HEADER:
        return None
    for i in range(54, 126, 18):
        d = b[i:i + 18]
        if d[0:3] == b"\x00\x00\x00" and d[3] == _EDID_NAME_TAG:
            text = d[5:18].split(b"\n")[0]
            return text.decode("ascii", "replace").strip() or None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_monitors.py -k edid_name -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add dials/monitors.py tests/test_monitors.py
git commit -m "feat(monitors): parse the model name out of an EDID base block"
```

---

### Task 2: Selector grammar, and validate it at config load

**Files:**
- Modify: `dials/monitors.py` (add `parse_selector`, `is_internal`)
- Modify: `dials/config.py` (add `_monitor`, use it in `_defaults` and `_dial`)
- Test: `tests/test_monitors.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `parse_selector(value: str) -> tuple[str, str]` — kind is `"edid"`, `"connector"`, or `"internal"`; raises `ValueError`
  - `is_internal(connector: str) -> bool`
  - `config._monitor(value, where: str) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_monitors.py`:

```python
import pytest


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
```

Extend the import in `tests/test_monitors.py`:

```python
from dials.monitors import (
    MonitorSource, RawOutput, dedupe_and_sort, edid_name, is_internal,
    parse_selector, pick,
)
```

Append to `tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitors.py tests/test_config.py -k "selector or is_internal or monitor_prefix or monitor_accepts" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_selector'`

- [ ] **Step 3: Implement the parser and the internal rule**

Add to `dials/monitors.py`, after `edid_name`:

```python
#: Connector types that mean "the panel built into this machine".
INTERNAL_TYPES = ("edp", "lvds", "dsi")


def parse_selector(value: str) -> tuple[str, str]:
    """Split a config `monitor` value into (kind, name).

        "edid:NAME"       -> ("edid", "NAME")
        "connector:NAME"  -> ("connector", "NAME")
        "internal"        -> ("internal", "")
        "NAME"            -> ("connector", "NAME")   - unchanged meaning

    Raises ValueError, NOT ConfigError: `config.py` wraps it. monitors.py must
    not import config.py - neither module knows the other exists today, and a
    cycle here would be gratuitous.

    Splits on the FIRST colon so an EDID name containing one survives.
    """
    text = value.strip()
    if not text:
        raise ValueError("monitor must not be empty")
    if text == "internal":
        return ("internal", "")
    head, sep, tail = text.partition(":")
    if not sep:
        return ("connector", text)
    kind, name = head.strip(), tail.strip()
    if kind not in ("edid", "connector"):
        raise ValueError(
            f"unknown monitor selector {kind!r}; use 'edid:', 'connector:', "
            "'internal', or a bare connector name"
        )
    if not name:
        raise ValueError(f"{kind}: selector has an empty name")
    return (kind, name)


def is_internal(connector: str) -> bool:
    """True if `connector` is this machine's built-in panel.

    Keys off the connector TYPE - the part before the first '-' - because the
    rename this feature exists to survive only ever moves the INDEX:
    eDP-1 <-> eDP-1-1, HDMI-0 <-> HDMI-1-0. The type never changes.

    RandR's ConnectorType property ("Panel") would be a stronger signal and
    cannot be used. Measured on this machine, the internal panel is the one
    output that does NOT expose it - modesetting drives that panel under both
    boot configurations and never sets the property - while the NVIDIA driver
    sets it on the external output, where it is useless.
    """
    return connector.split("-")[0].lower() in INTERNAL_TYPES
```

- [ ] **Step 4: Wire validation into config load**

In `dials/config.py`, add to the imports at the top:

```python
from dials.monitors import parse_selector
```

Add after `_str`:

```python
def _monitor(value, field_where: str) -> str:
    """Validate the monitor SELECTOR at load time, not at show time.

    A bad selector is a startup error with a clear message, the same treatment
    a bad rect already gets - rather than a Dial that silently lands on the
    wrong screen, which is the exact failure this whole feature exists to end.
    """
    text = _str(value, "monitor", field_where)
    try:
        parse_selector(text)
    except ValueError as exc:
        raise ConfigError(f"{field_where}: monitor={text!r}: {exc}") from None
    return text
```

In `_defaults`, replace the `monitor=` line with:

```python
        monitor=_monitor(raw.get("monitor", base.monitor), "defaults"),
```

In `_dial`, replace the `monitor=` line with:

```python
        monitor=_monitor(raw.get("monitor", defaults.monitor), where),
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (previously 405, now 405 + 14 new)

- [ ] **Step 6: Mutation-test the config call sites**

Temporarily change `_dial`'s `monitor=_monitor(...)` back to `monitor=_str(...)`.
Run: `.venv/bin/python -m pytest tests/test_config.py -k monitor_prefix -v`
Expected: `test_unknown_monitor_prefix_is_a_config_error_on_a_dial` FAILS.
Restore, re-run, confirm PASS. Repeat for `_defaults`.

- [ ] **Step 7: Commit**

```bash
git add dials/monitors.py dials/config.py tests/test_monitors.py tests/test_config.py
git commit -m "feat(config): a monitor selector can name a display, not just a connector"
```

---

### Task 3: Carry display identity through the data model

**Files:**
- Modify: `dials/geometry.py` (`Monitor` gains two fields)
- Modify: `dials/monitors.py` (`RawOutput` gains two fields; `dedupe_and_sort` populates)
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: `edid_name` (Task 1)
- Produces: `Monitor.display_name: str | None`, `Monitor.identity_reliable: bool`, `RawOutput.edid: bytes | None`, `RawOutput.edid_failed: bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_monitors.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitors.py -k "display_name or failed_edid or positional" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'edid'`

- [ ] **Step 3: Add the fields**

In `dials/geometry.py`, replace the `Monitor` dataclass:

```python
@dataclass(frozen=True)
class Monitor:
    name: str
    rect: Rect
    primary: bool
    crtc: int
    #: The display's own EDID model name, when it reports one. None for a
    #: panel that carries no 0xFC descriptor AND for one whose EDID could not
    #: be read - `identity_reliable` is what separates those.
    display_name: str | None = None
    #: False only when the EDID read RAISED. Degrading is fine when placing a
    #: window and wrong when writing config, so the write path checks this.
    identity_reliable: bool = True
```

In `dials/monitors.py`, replace the `RawOutput` dataclass:

```python
@dataclass(frozen=True)
class RawOutput:
    """One RandR output as read from the server. crtc == 0 means disconnected."""
    name: str
    crtc: int
    x: int
    y: int
    w: int
    h: int
    primary: bool
    #: Raw EDID base block. New fields are APPENDED with defaults because this
    #: is constructed positionally throughout the test suite.
    edid: bytes | None = None
    #: True only if the property read raised - never for a display that simply
    #: has no EDID.
    edid_failed: bool = False
```

In `dedupe_and_sort`, replace the `Monitor(...)` construction:

```python
        Monitor(
            name=r.name,
            rect=Rect(r.x, r.y, r.w, r.h),
            primary=r.primary,
            crtc=r.crtc,
            display_name=edid_name(r.edid),
            identity_reliable=not r.edid_failed,
        )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add dials/geometry.py dials/monitors.py tests/test_monitors.py
git commit -m "feat(monitors): carry display identity, and whether it was readable"
```

---

### Task 4: Selector dispatch in `pick`, with zero/one/many

**Files:**
- Modify: `dials/monitors.py` (`pick`, plus a `_fallback` helper)
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: `parse_selector`, `is_internal` (Task 2); `Monitor.display_name` (Task 3)
- Produces: `pick(selector: str, monitors: list[Monitor], root_rect: Rect) -> tuple[Monitor, str | None]` — same signature, selector-aware

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_monitors.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitors.py -k "across_both_boots or ambiguous or internal_panel or invalid_selector" -v`
Expected: FAIL — `pick("edid:AW3425DWM", ...)` falls back, because `pick` still compares the whole string to `Monitor.name`.

- [ ] **Step 3: Rewrite `pick` with selector dispatch**

In `dials/monitors.py`, replace the whole `pick` function:

```python
def _fallback(
    monitors: list[Monitor], root_rect: Rect, problem: str
) -> tuple[Monitor, str | None]:
    """The unchanged destination chain: primary -> first -> root box.

    Split out of `pick` so every selector kind shares one set of destinations
    and only the `problem` half of the reason differs.
    """
    for m in monitors:
        if m.primary:
            return m, f"{problem}; using primary {m.name!r}"
    if monitors:
        m = monitors[0]
        return m, f"{problem} and no primary; using first {m.name!r}"
    return (
        Monitor(name="<root>", rect=root_rect, primary=True, crtc=0),
        f"{problem} and no usable monitors; using root box",
    )


def pick(
    selector: str, monitors: list[Monitor], root_rect: Rect
) -> tuple[Monitor, str | None]:
    """Resolve a Dial's monitor selector to a monitor.

    Returns (monitor, fallback_reason). `reason` is None only on an exact,
    UNAMBIGUOUS match; otherwise it is a one-line explanation for
    `dials status`, so a Dial landing on the wrong screen is visible rather
    than mysterious.

    Three outcomes, not two. Two displays matching one selector must NOT
    resolve to the first of them: that would look like success, and looking
    like success while placing windows on the wrong screen is the failure this
    exists to end.
    """
    try:
        kind, name = parse_selector(selector)
    except ValueError as exc:
        return _fallback(monitors, root_rect, f"invalid monitor selector: {exc}")

    if kind == "edid":
        matches = [m for m in monitors if m.display_name == name]
        present = ", ".join(
            m.display_name for m in monitors if m.display_name
        ) or "none"
        absent = f"no display named {name!r} (displays present: {present})"
        ambiguous = f"display name {name!r} is ambiguous"
    elif kind == "internal":
        matches = [m for m in monitors if is_internal(m.name)]
        connectors = ", ".join(m.name for m in monitors) or "none"
        absent = f"no internal panel found (connectors: {connectors})"
        ambiguous = "more than one internal panel"
    else:
        matches = [m for m in monitors if m.name == name]
        absent = f"monitor {name!r} absent"
        ambiguous = f"connector {name!r} is ambiguous"

    if len(matches) == 1:
        return matches[0], None
    if matches:
        found = ", ".join(m.name for m in matches)
        return _fallback(monitors, root_rect, f"{ambiguous} ({found})")
    return _fallback(monitors, root_rect, absent)
```

Note: the connector-kind `absent` string is byte-identical to the old one, so the three existing fallback tests keep passing unchanged.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, including the pre-existing `test_pick_falls_back_to_*` tests

- [ ] **Step 5: Commit**

```bash
git add dials/monitors.py tests/test_monitors.py
git commit -m "feat(monitors): resolve a selector to zero, one, or ambiguously many"
```

---

### Task 5: Read EDID from X

**Files:**
- Modify: `dials/monitors.py` (`MonitorSource._read_from_x`, new `_read_edid`)
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: `RawOutput.edid`, `RawOutput.edid_failed` (Task 3)
- Produces: `MonitorSource._read_edid(oid, atom) -> tuple[bytes | None, bool]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_monitors.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_monitors.py -k read_edid -v`
Expected: FAIL — `AttributeError: 'MonitorSource' object has no attribute '_read_edid'`

- [ ] **Step 3: Implement the read**

In `dials/monitors.py`, replace `MonitorSource._read_from_x` and add `_read_edid`:

```python
    def _read_from_x(self) -> list[RawOutput]:
        from Xlib.ext import randr
        res = randr.get_screen_resources(self.root)
        primary = randr.get_output_primary(self.root).output
        edid_atom = self.display.get_atom("EDID")
        out: list[RawOutput] = []
        for oid in res.outputs:
            info = randr.get_output_info(self.display, oid, res.config_timestamp)
            if info.crtc == 0:
                out.append(RawOutput(info.name, 0, 0, 0, 0, 0, False))
                continue
            crtc = randr.get_crtc_info(self.display, info.crtc, res.config_timestamp)
            # Read EDID eagerly, for connected outputs only. Measured on this
            # machine: 147.75 ms per refresh without, 145.95 ms with - the
            # reads are below run-to-run noise, and a refresh happens only at
            # startup and on RandR events, never at idle or per keypress.
            edid, failed = self._read_edid(oid, edid_atom)
            out.append(RawOutput(
                name=info.name, crtc=info.crtc,
                x=crtc.x, y=crtc.y, w=crtc.width, h=crtc.height,
                primary=(oid == primary),
                edid=edid, edid_failed=failed,
            ))
        return out

    def _read_edid(self, oid, atom) -> tuple[bytes | None, bool]:
        """(blob, failed). An empty property is NOT a failure.

        A display with no EDID and a display whose EDID could not be read are
        different facts, and only the second one must stop `dials capture`
        from writing. Collapsing them would let a transient X hiccup silently
        downgrade a rename-proof selector to a fragile connector name.
        """
        from Xlib.ext import randr
        try:
            prop = randr.get_output_property(
                self.display, oid, atom, 0, 0, 128, False, False)
        except Exception:
            return None, True
        value = getattr(prop, "value", None)
        if not value:
            return None, False
        return bytes(value), False
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Verify against the real X server**

Run:

```bash
.venv/bin/python -c "
from Xlib import display as xd
from dials.monitors import MonitorSource
d = xd.Display(); src = MonitorSource(d, d.screen().root)
for m in src.monitors():
    print(m.name, m.display_name, m.identity_reliable)
"
```

Expected on this machine (NVIDIA-primary boot): `HDMI-0 AW3425DWM True` and `eDP-1-1 None True`.

- [ ] **Step 6: Commit**

```bash
git add dials/monitors.py tests/test_monitors.py
git commit -m "feat(monitors): read EDID per connected output on each cache refresh"
```

---

### Task 6: `selector_for` — the durable value to persist

**Files:**
- Modify: `dials/monitors.py`
- Test: `tests/test_monitors.py`

**Interfaces:**
- Consumes: `is_internal` (Task 2), `Monitor.display_name` / `identity_reliable` (Task 3)
- Produces: `selector_for(monitor: Monitor, all_monitors: list[Monitor]) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_monitors.py`:

```python
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
```

Extend the import in `tests/test_monitors.py` to include `selector_for`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_monitors.py -k selector_for -v`
Expected: FAIL — `ImportError: cannot import name 'selector_for'`

- [ ] **Step 3: Implement**

Add to `dials/monitors.py`, after `is_internal`:

```python
def selector_for(monitor: Monitor, all_monitors: list[Monitor]) -> str | None:
    """The most durable config value naming `monitor`, or None to refuse.

    Needs the FULL monitor list, not just one monitor: whether a display name
    identifies anything is a question about the whole set. A name two displays
    share is not an identity.

    Returns None when the identity could not be read. Callers that PERSIST the
    result must write nothing in that case. Degrading to a connector name is
    fine for placing a window and wrong for config: it would swap a
    rename-proof selector for a fragile one at exactly the moment X is
    misbehaving, and the damage outlives the moment.
    """
    if not monitor.identity_reliable:
        return None
    if is_internal(monitor.name):
        if sum(1 for m in all_monitors if is_internal(m.name)) == 1:
            return "internal"
    name = monitor.display_name
    if name and sum(1 for m in all_monitors if m.display_name == name) == 1:
        return f"edid:{name}"
    return monitor.name
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add dials/monitors.py tests/test_monitors.py
git commit -m "feat(monitors): one helper for the selector both write paths persist"
```

---

### Task 7: Both write paths persist a selector

**Files:**
- Modify: `dials/cli.py` (`Deps` already gained `monitors` in commit `2096071`; `_cmd_capture`)
- Modify: `dials/daemon.py:306` (assign mode)
- Test: `tests/test_cli.py`, `tests/test_daemon.py`

**Interfaces:**
- Consumes: `selector_for` (Task 6), `Deps.monitors()` (already present)
- Produces: nothing new

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_capture_writes_an_edid_selector_not_a_connector():
    """Capture is how this bug walks back in. It writes to config."""
    named = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=False, crtc=63,
                    display_name="AW3425DWM")
    d, state, _ = deps({"9": dial()}, monitor=named)
    d.monitors = lambda: ([named], Rect(0, 0, 3440, 1440))
    assert main(["capture", "9"], deps=d) == 0
    assert state["config"].dials["9"].monitor == "edid:AW3425DWM"


def test_capture_refuses_when_the_identity_could_not_be_read():
    # cli.py already refuses to guess when the window list is unreadable,
    # because capture writes to config. Same rule, same reason.
    unreadable = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=False,
                         crtc=63, identity_reliable=False)
    d, state, out = deps({"9": dial()}, monitor=unreadable)
    d.monitors = lambda: ([unreadable], Rect(0, 0, 3440, 1440))
    assert main(["capture", "9"], deps=d) == 1
    assert state["upserts"] == 0
    assert "identity" in out.getvalue().lower()


def test_capture_reports_the_selector_it_wrote():
    named = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=False, crtc=63,
                    display_name="AW3425DWM")
    d, _, out = deps({"9": dial()}, monitor=named)
    d.monitors = lambda: ([named], Rect(0, 0, 3440, 1440))
    main(["capture", "9"], deps=d)
    assert "edid:AW3425DWM" in out.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "capture_writes or capture_refuses or capture_reports" -v`
Expected: FAIL — the config records `"HDMI-0"`, not `"edid:AW3425DWM"`

- [ ] **Step 3: Update `_cmd_capture`**

In `dials/cli.py`, replace lines 205–213 of `_cmd_capture`:

```python
    rect = d.window_geometry(chosen.wid) or Rect(0, 0, 800, 600)
    monitor = d.monitor_for(rect)
    try:
        mons, _root = d.monitors()
    except Exception as exc:
        print(f"could not read the monitor list from X: {exc}", file=d.out)
        return 1
    selector = monitors.selector_for(monitor, mons)
    if selector is None:
        # Same rule as the unreadable window list above: capture writes to the
        # config, so guessing has a persistent cost. Writing a bare connector
        # name here would silently swap a rename-proof selector for the exact
        # fragile one this feature exists to retire.
        print(f"could not read {monitor.name}'s identity from X; refusing to "
              f"write a connector name that may not survive a reboot",
              file=d.out)
        return 1
    updated = replace(dial, monitor=selector, rect=derive_rect(rect, monitor))
    d.upsert(config_path(), updated)
    d.signal_daemon()
    print(f"captured {args.slot}: {selector} "
          f"{[round(v, 3) for v in updated.rect]}", file=d.out)
    return 0
```

- [ ] **Step 4: Update assign mode in the daemon**

In `dials/daemon.py`, replace line 306 and the `Capture(` construction that follows it:

```python
            monitor = _monitor_containing(win_rect, mons, root)
            selector = monitors_mod.selector_for(monitor, mons)
            if selector is None:
                self._notify("Assign mode",
                             f"could not read {monitor.name}'s identity; "
                             "not binding")
                return
            self.assign.arm(Capture(
                wid=active,
                wm_class=info.wm_class,
                label=self.ops.window_name(active),
                monitor=selector,
```

Leave the remaining `Capture(...)` arguments as they are.

- [ ] **Step 5: Add the daemon-side tests**

Append to `tests/test_daemon.py`, beside the existing
`test_arm_assign_with_an_unreadable_window_list_says_so_and_does_not_arm`.
`resolve()` is used rather than reaching into `assign._capture`, so the test
goes through the public path a real binding takes:

```python
def test_arm_assign_captures_an_edid_selector_not_a_connector():
    """Assign mode is the OTHER path that writes a monitor into the config.

    It persisted `monitor.name`, so a window bound during one boot wrote a
    connector name that the next boot could rename out from under it.
    """
    named = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=True, crtc=63,
                    display_name="AW3425DWM")

    class NamedMonitors(FakeMonitors):
        def monitors(self):
            return [named]

    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops)
    d.monitors = NamedMonitors()

    d.arm_assign()
    assert d.assign.armed is True

    bound = d.assign.resolve("9", existing=None)
    assert bound.monitor == "edid:AW3425DWM"


def test_arm_assign_refuses_when_the_identity_could_not_be_read():
    """Same rule as the unreadable window list above: binding writes to the
    config, so a guess outlives the moment X misbehaved."""
    unreadable = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=True,
                         crtc=63, identity_reliable=False)

    class BrokenMonitors(FakeMonitors):
        def monitors(self):
            return [unreadable]

    notes = Notes()
    ops = FakeOps(windows=[win(5)], active=5)
    d = daemon(ops, notifier=notes)
    d.monitors = BrokenMonitors()

    d.arm_assign()

    assert d.assign.armed is False
    assert notes.mentions("identity")
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Mutation-test both write sites**

In `cli.py`, temporarily change `monitor=selector` back to `monitor=monitor.name`.
Run: `.venv/bin/python -m pytest tests/test_cli.py -k capture_writes -v` → expect FAIL. Restore, confirm PASS.

Then delete the `if selector is None:` guard in `cli.py`.
Run: `.venv/bin/python -m pytest tests/test_cli.py -k capture_refuses -v` → expect FAIL. Restore, confirm PASS.

- [ ] **Step 8: Commit**

```bash
git add dials/cli.py dials/daemon.py tests/test_cli.py tests/test_daemon.py
git commit -m "fix(capture): persist a selector that survives a rename, or refuse"
```

---

### Task 8: `dials status` names the displays

**Files:**
- Modify: `dials/cli.py` (`_cmd_status`, new `_monitor_label`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `is_internal` (Task 2), `Monitor.display_name` / `identity_reliable` (Task 3)
- Produces: nothing new

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_status_lists_display_names_so_they_need_not_be_guessed():
    ultrawide = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=False,
                        crtc=63, display_name="AW3425DWM")
    panel = Monitor("eDP-1-1", Rect(388, 1440, 2560, 1440), primary=True,
                    crtc=62)
    d, _, out = deps({"9": dial()})
    d.monitors = lambda: ([ultrawide, panel], Rect(0, 0, 3440, 2880))
    main(["status"], deps=d)
    line = next(ln for ln in out.getvalue().splitlines()
                if ln.startswith("monitors:"))
    assert "HDMI-0 (AW3425DWM)" in line
    assert "eDP-1-1 (internal)" in line


def test_status_marks_a_display_whose_identity_is_unreadable():
    broken = Monitor("HDMI-0", Rect(0, 0, 3440, 1440), primary=True, crtc=63,
                     identity_reliable=False)
    d, _, out = deps({"9": dial()})
    d.monitors = lambda: ([broken], Rect(0, 0, 3440, 1440))
    main(["status"], deps=d)
    assert "identity unreadable" in out.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k "lists_display_names or identity_is_unreadable" -v`
Expected: FAIL — the line reads `monitors:  HDMI-0, eDP-1-1`

- [ ] **Step 3: Implement the label**

In `dials/cli.py`, add above `_cmd_status`:

```python
def _monitor_label(m) -> str:
    """`HDMI-0 (AW3425DWM)` - the connector, plus what to actually type.

    Discoverability is the point: an `edid:` selector is useless if the name it
    needs can only be found with xrandr and a hex dump.
    """
    tags = []
    if m.display_name:
        tags.append(m.display_name)
    if monitors.is_internal(m.name):
        tags.append("internal")
    if not m.identity_reliable:
        tags.append("identity unreadable")
    return f"{m.name} ({', '.join(tags)})" if tags else m.name
```

In `_cmd_status`, replace the `names = ...` line:

```python
    names = ", ".join(_monitor_label(m) for m in mons) or "none"
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Verify against the real X server**

Run: `.venv/bin/dials status`
Expected: `monitors:  HDMI-0 (AW3425DWM), eDP-1-1 (internal)`

- [ ] **Step 6: Commit**

```bash
git add dials/cli.py tests/test_cli.py
git commit -m "feat(cli): status names each display, so edid: values are discoverable"
```

---

### Task 9: Migrate the config, correct the docs, log what was deferred

**Files:**
- Modify: `~/.config/dials/config.toml` (live config — **not** in the repo)
- Modify: `config/config.reference.toml`
- Modify: `README.md`
- Modify: `docs/OPEN-PROBLEMS.md`

**Interfaces:**
- Consumes: everything above
- Produces: nothing

- [ ] **Step 1: Migrate the live config**

Replace all four `monitor = "HDMI-1-0"` values in `~/.config/dials/config.toml` with `monitor = "edid:AW3425DWM"`, and replace the boot-mode comment block above `[defaults].monitor` with a note that the value now names the display rather than the socket.

Back it up first: `cp ~/.config/dials/config.toml ~/.config/dials/config.toml.bak-connector`

- [ ] **Step 2: Verify the live config resolves with no fallback**

Run:

```bash
.venv/bin/python -c "
from Xlib import display as xd
from dials.config import load
from dials.monitors import MonitorSource, pick
d = xd.Display(); src = MonitorSource(d, d.screen().root)
mons, root = src.monitors(), src.root_rect()
for slot, dl in sorted(load().dials.items()):
    m, why = pick(dl.monitor, mons, root)
    print(slot, dl.monitor, '->', m.name, '| fallback:', why)
"
```

Expected: every line resolves to `HDMI-0` with `fallback: None`.

- [ ] **Step 3: Update the reference snapshot by hand**

Apply the same four value changes to `config/config.reference.toml`. **Do not run `dials config export`** — it regenerates the file and strips every comment, as its own header warns.

- [ ] **Step 4: Correct the docs**

`README.md` — in *If every Dial suddenly opens on the wrong screen*, replace the `system76-power graphics nvidia`/`hybrid` explanation. The mode is `hybrid` in both cases and is not the trigger; what flips is which GPU the firmware marks `boot_vga`, and therefore which one X makes primary. Add that `monitor` now accepts `edid:`, `internal`, and `connector:`, and that `dials status` lists the names.

`docs/OPEN-PROBLEMS.md` §6 — same correction: strike the graphics-mode attribution, state the `boot_vga` mechanism, and record that the fix is EDID-based selection with a pointer to the spec.

- [ ] **Step 5: Log the four deferred problems**

Append to `docs/OPEN-PROBLEMS.md` as §7–§10, each with what is established and what the next step is:

1. `monitor_warnings` is never cleared (`daemon.py:144`), so a repeated warning is suppressed after an intervening success.
2. `select_events` omits `RROutputPropertyNotifyMask`; EDID is an output property, and a no-polling design should not rely on hotplug "normally" also emitting an output change.
3. `dedupe_and_sort` keeps one `RawOutput` per CRTC, so a mirrored pair loses one connector's identity — if the dropped one carried the matching EDID, selection falsely fails.
4. The first Dial keypress after startup pays ~146 ms building the monitor cache; prewarming it after `select_events()` would move that off the keypress without adding polling.

- [ ] **Step 6: Stage carefully and commit**

`docs/OPEN-PROBLEMS.md` may still hold unrelated uncommitted work. Check `git diff` before staging and commit only the hunks belonging to this change.

```bash
git add README.md config/config.reference.toml docs/OPEN-PROBLEMS.md
git commit -m "docs: name displays, not sockets; correct the boot_vga mechanism"
```

- [ ] **Step 7: Restart the daemon and confirm end to end**

```bash
systemctl --user restart dialsd
~/.local/share/dials/venv/bin/dials status
```

Expected: `monitors:  HDMI-0 (AW3425DWM), eDP-1-1 (internal)` and **no** `monitor: WARNING` lines. Then press a Dial key and confirm the window lands on the ultrawide.

---

## Self-Review

**Spec coverage.** Config grammar → Task 2. Data model → Task 3. `0xFC`-only parsing → Task 1. `internal` rule → Task 2. Resolution zero/one/many → Task 4. X I/O and eager reads → Task 5. Write path and refusal → Tasks 6–7. Discoverability → Task 8. Testing → distributed, with the two-boot regression in Task 4. Deferred items → Task 9 Step 5. No spec section is unimplemented.

**Placeholder scan.** No TBD/TODO. Every code step carries the actual code. Task 9 Step 4 describes prose edits rather than quoting them, which is correct for documentation whose surrounding text will have moved.

**Type consistency.** `edid_name(bytes | None) -> str | None`, `parse_selector(str) -> tuple[str, str]`, `is_internal(str) -> bool`, `selector_for(Monitor, list[Monitor]) -> str | None`, `pick(str, list[Monitor], Rect) -> tuple[Monitor, str | None]` — used identically in every task that consumes them. `Monitor.display_name` and `Monitor.identity_reliable` are named the same in Tasks 3, 4, 6, 7, and 8. `RawOutput`'s new fields are appended with defaults, which Task 3 Step 1 tests explicitly because the suite builds `RawOutput` positionally.
