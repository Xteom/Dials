# Monitor identity: selecting a display that survives being renamed

Design, 2026-08-09. Companion to `docs/OPEN-PROBLEMS.md` §6, which records the
incident this exists to prevent.

## The problem

A Dial names its target display with one exact connector string:

```toml
monitor = "HDMI-0"
```

On this laptop that string is not stable across reboots. Both GPUs supply
outputs; whichever one the firmware marks `boot_vga` becomes X's primary device,
and the other becomes a secondary RandR provider whose outputs get the provider
index spliced into their names:

| X primary | ultrawide | internal panel |
| --- | --- | --- |
| NVIDIA | `HDMI-0` | `eDP-1-1` |
| Intel (`modesetting`) | `HDMI-1-0` | `eDP-1` |

Same cable, same panel, same desk. Verified across two boots from the `*` that
marks the primary PCI device in `Xorg.log`, and from `boot_vga` in sysfs. The
leading hypothesis for what flips it is whether the external monitor is
connected at power-on; two samples, not proven, and **the fix must not depend on
knowing.**

When the configured name matches nothing, `pick()` falls back to primary exactly
as documented — and primary is the laptop panel. Every Dial opens on the wrong
screen.

`system76-power graphics` reports `hybrid` in both cases and is **not** the
trigger. An earlier version of this document said it was; that was wrong.

### Why a candidate list is not the answer

`monitor = ["HDMI-0", "HDMI-1-0"]` would work today and is unsound in general.
`DP-1-1` is a valid connector name under **both** boot configurations and refers
to a *different physical port* in each. Connector names are not merely unstable;
they are ambiguous across configurations. Any fix built on normalising or
enumerating them inherits that.

## Goals

Identify a display by something the display itself carries, so a Dial lands on
the intended screen regardless of how X happens to name outputs this boot. Make
every failure to do so *visible*, because the whole cost of the original
incident was that it was silent.

## Non-goals

- Making X's output naming deterministic. That is a system-level fix
  (`Option "PrimaryGPU"`), outside this project, with a real battery cost.
- Identifying two physically identical monitors apart. See *Ambiguity* — this
  design detects that case and refuses to guess, rather than solving it.
- Mirrored-output support beyond what exists today. See *Deferred*.

## Config grammar

One `monitor` key, optionally prefixed.

```toml
monitor = "edid:AW3425DWM"     # the display's own EDID name
monitor = "internal"           # the built-in panel
monitor = "connector:HDMI-0"   # explicitly a connector name
monitor = "HDMI-0"             # bare string: connector, exactly as before
```

Rules:

- Split on the **first** colon only, so an EDID name containing one survives.
- A bare string is a connector name. Every config written before this change
  keeps its current meaning, untouched.
- `internal` is a reserved word. This does technically change the meaning of a
  connector literally named `internal`; no such connector exists on any hardware
  this runs on, and the escape hatch `connector:internal` remains.
- An unrecognised prefix (`foo:bar`) is a `ConfigError` at load, not a silently
  mismatched name. Same strictness as `dials config bogus` being an error.
  This also means a connector name containing a colon must be written
  `connector:...`. None exist here.
- An empty name (`edid:`, `connector:`) is a `ConfigError`.
- EDID names are compared exactly, after stripping surrounding whitespace.
  No case folding: the value is what the display reports, and `dials status`
  prints it so it never has to be guessed.

Validation happens in `config.py`, for `[defaults]` and every per-Dial override.
The parser itself lives in `monitors.py` and raises `ValueError`; `config.py`
wraps that as `ConfigError`. `monitors.py` must not import `ConfigError` —
that would create a config↔monitors dependency where neither module currently
knows the other exists.

## Data model

`monitors.py`'s split — pure functions fully unit-tested, X I/O confined to
`MonitorSource` producing `RawOutput` records — is preserved. It is what lets
this be tested with no X server.

```python
@dataclass(frozen=True)
class RawOutput:
    ...                                  # existing fields unchanged
    edid: bytes | None = None            # raw blob, or None
    edid_failed: bool = False            # True only if the read RAISED
```

The two flags are a tri-state on purpose:

| `edid` | `edid_failed` | meaning |
| --- | --- | --- |
| bytes | False | identity known |
| None | False | display genuinely has no EDID |
| None | True | **read failed** — identity unknown, not absent |

Collapsing the last two would let a transient X failure look like a display
with no name. That is tolerable when placing a window and **not** tolerable
when writing to config. See *Write path*.

```python
@dataclass(frozen=True)
class Monitor:
    ...                                  # existing fields unchanged
    display_name: str | None = None      # parsed 0xFC descriptor
    identity_reliable: bool = True       # False when the EDID read failed
```

New pure functions in `monitors.py`:

- `edid_name(blob: bytes) -> str | None` — parse the `0xFC` descriptor from the
  128-byte base block. Returns None for a short blob, a bad header, or no `0xFC`
  descriptor. Never raises.
- `parse_selector(value: str) -> tuple[str, str]` — `("edid"|"connector"|"internal", name)`.
- `is_internal(name: str) -> bool` — see below.
- `selector_for(monitor, all_monitors) -> str | None` — see *Write path*.

### Why `0xFC` only

EDID also carries a manufacturer ID, product code, a numeric serial, and a
`0xFF` serial string. Your ultrawide has all of them (`DEL`, `0xd185`,
`1SKC444`). A manufacturer+product+serial tuple is a stronger *identity* than a
model name — but `edid:1SKC444` is unmemorable, and identity only has to be
strong enough to be **unambiguous among the displays actually connected**. The
design gets that guarantee from ambiguity detection instead, which costs less
and reads better. If two identical monitors ever appear here, the honest fix is
to add a serial-qualified selector then, with the hardware in hand.

`0xFE` free-form text is not parsed. On the panel here it yields both `BOE CQ`
and `NE173QHM-NZ1`, and nothing in the spec says which is the model.

### `internal`

The built-in panel reports no `0xFC` name — panels are not sold as products —
so it needs a different handle. The rule is the connector **type**, the part
before the first `-`: `eDP`, `LVDS`, or `DSI`, matched case-insensitively.

This works precisely because the rename only ever moves the *index*:
`eDP-1` ↔ `eDP-1-1`, `HDMI-0` ↔ `HDMI-1-0`. The type never changes.

RandR exposes a `ConnectorType` property whose `Panel` value would be a stronger
signal, and it was rejected after testing it on this machine:

```
HDMI-0     [... 'EDID', 'ConnectorType', 'ConnectorNumber', ...]   has it
eDP-1-1    [... 'EDID', 'panel orientation', 'scaling mode', ...]  does NOT
```

The internal panel is the one output that lacks it. `modesetting` drives that
panel under **both** boot configurations and never sets the property; the NVIDIA
driver sets it on the external output, where it is not needed. `ConnectorType`
may be consulted as confirmation when present, and can never be required.

## Resolution

`pick()` resolves the selector, then runs its **existing** fallback chain
unchanged: primary → first → root box, each keeping its current reason string.

What changes is that resolution has three outcomes, not two:

| matches | result |
| --- | --- |
| exactly one | that monitor, `reason=None` |
| zero | fallback chain, reason names the namespace and what is present |
| **two or more** | fallback chain, reason says *ambiguous* and lists the connectors |

The many-case is the one that must not be got wrong. Silently taking the first
match would be worse than today's behaviour: it would look like success. An
ambiguous selector is a config problem the user has to see.

Reason strings name what was searched, so a typo and a rename are
distinguishable:

```
no display named 'AW3425DWMM' (displays present: AW3425DWM); using primary 'eDP-1-1'
display name 'U2718Q' is ambiguous (DP-1, DP-2); using primary 'eDP-1-1'
no internal panel found (connectors: HDMI-0); using primary 'HDMI-0'
```

## X I/O

`MonitorSource._read_from_x` gains one call per **connected** output:

```python
edid = randr.get_output_property(display, oid, EDID_atom, 0, 0, 128, False, False)
```

`get_output_property` exists in python-xlib 0.29 and 0.33. The project floor is
`>=0.29`, so unlike RandR 1.5's `get_monitors` — absent in 0.29, which is why
this module uses RandR 1.2 — this API needs no floor change.

Read eagerly for every connected output on each cache refresh. Measured on this
machine, 20 iterations of a full refresh:

```
without EDID   147.75 ms   (11 outputs, 0 edid reads)
with EDID      145.95 ms   (11 outputs, 2 edid reads)
```

The difference is below run-to-run noise. Lazy reading would save nothing
measurable while either coupling `monitors.py` to config, or pushing X plumbing
into the pure `pick()` and destroying its testability. It would also make
`dials status` unable to list display names unless something had already asked
for them, which breaks the discoverability this design depends on.

A per-output failure sets `edid_failed=True` and never propagates, matching how
`monitors()` already degrades to the last good read rather than crashing.

## Write path

`dials capture`, assign mode, and the curses TUI's `b` (bind) key each persist
a monitor today, independently: `cli.py` via `Capture.monitor` (landing at
`assign.py:141`), `daemon.py:306` via `_monitor_containing`, and
`dials/tui.py`'s bind handler via its own call into `_monitor_containing`. All
three write `monitor.name` — a bare connector — so leaving any one of them
unconverted would reintroduce this bug. They move to one shared pure helper:

```python
selector_for(monitor, all_monitors) -> str | None
```

Precedence:

1. `internal` — if the monitor is internal and is the only internal one.
2. `edid:NAME` — if it has a display name **and no other connected monitor
   shares it**.
3. the bare connector name — last resort, today's behaviour.

`internal` outranks `edid:` deliberately: for a panel that does report a name,
"the built-in screen" is the more durable statement of intent than its model
number.

**Returns `None` when `identity_reliable` is False**, and the caller then
refuses to write. `cli.py:196` already declines to guess when the window list is
unreadable, on the grounds that capture writes to config and guessing has a
persistent cost. A failed EDID read is the same situation: it would silently
downgrade a robust selector to a fragile one at exactly the moment X is
misbehaving. `dials capture` prints the reason and exits non-zero.

`capture` reports the selector it actually wrote, not the connector.

## Discoverability

`dials status` gains the display name, so the value to type is never guessed:

```
state:     active
dials:     3 bound of 15 slots
monitors:  HDMI-0 (AW3425DWM), eDP-1-1 (internal)
monitor:   WARNING slot 9: no display named 'AW3425DWM' (displays present: none); using primary 'eDP-1-1'
```

An output whose EDID read failed is marked, rather than shown as nameless.

## Testing

The load-bearing test — the one that encodes this incident:

> Config says `edid:AW3425DWM`. Feed `pick()` the 2026-08-07 monitor list
> (`HDMI-1-0`, `eDP-1`) and the 2026-08-09 list (`HDMI-0`, `eDP-1-1`). Assert
> the **same physical monitor** is chosen both times, with `reason=None`.

Also:

- `edid_name` against the two **real blobs captured from this hardware**
  (ultrawide with `0xFC`, panel without), plus truncated, bad-header, and
  no-descriptor input.
- `parse_selector` for all four forms, unknown prefix, empty name, embedded colon.
- `pick` for each selector kind × {zero, one, many} matches.
- `is_internal` for `eDP-1`, `eDP-1-1`, `LVDS-0`, `DSI-1`, and negatives.
- `selector_for` precedence, the non-unique-name case, and the
  `identity_reliable=False` refusal.
- Caller-level: `dials status` output, `dials capture` writing a selector and
  refusing on unreliable identity, assign mode writing the same selector.

Caller-level coverage is not optional here. `README.md:184` records that this
project has twice shipped a fix covered only by a test on the pure predicate,
where reverting the caller left the suite green. Every new call site gets
mutation-tested: break it, watch a test fail, restore.

## Deferred

Real, found while designing this, out of scope, to be logged in
`docs/OPEN-PROBLEMS.md`:

1. **`monitor_warnings` is never cleared.** `daemon.py:144` writes it and
   nothing deletes it, so unplug → warn → replug → unplug suppresses the second,
   identical warning. Pre-existing, independent of this change.
2. **`select_events` omits `RROutputPropertyNotifyMask`.** EDID is an output
   property; a hotplug normally also emits an output change, but a design with
   no polling should not rely on "normally".
3. **`dedupe_and_sort` discards identities.** It keeps one `RawOutput` per CRTC,
   so a mirrored pair loses one connector's identity — if the dropped one
   carried the matching EDID, selection falsely fails. A logical `Monitor`
   should retain aliases from every output sharing its CRTC.
4. **The first Dial keypress after startup costs ~146 ms**, because the monitor
   cache is built lazily and enumerating 11 outputs is slow. Prewarming it at
   daemon start, straight after `select_events()`, would move that off the
   keypress without adding polling.

## Evidence

Everything load-bearing here was measured on this machine on 2026-08-09:

- EDID reads through `randr.get_output_property`: `HDMI-0` → 384 B →
  `AW3425DWM`; `eDP-1-1` → 256 B → no `0xFC`.
- `get_output_property` present in python-xlib 0.29 and 0.33;
  `RROutputPropertyNotifyMask` present.
- `ConnectorType` present on `HDMI-0`, absent on `eDP-1-1`.
- Refresh timings and boot-to-boot naming, as tabled above.
