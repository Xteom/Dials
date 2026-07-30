# Dials — design

Date: 2026-07-28 (revised 2026-07-29)
System: Pop!_OS 22.04 LTS, GNOME 42 (mutter), X11.

## Name

A **Dial** is one numpad key holding one window. The name is a One Piece reference: Dials are
shells that store energy and matter and release it when pressed — which is exactly what a key here
does with a window. The project icon is a shell.

Terminology used throughout: a *Dial* is a binding, a *slot* is the physical key it lives on, and
the *Dial layer* is the whole set, live only while NumLock is off.

## Problem

The numeric keypad is only ever used with NumLock **on**, to type digits. With NumLock **off** its
eleven navigation keys (`KP_Home`, `KP_Up`, `KP_Prior`, …) are dead weight — duplicate
Home/PageUp/arrow keys that never get pressed.

That makes NumLock-off a completely free 15-key layer. This project turns it into a set of
quake-style dropdown panels: NumLock-off `9` shows Spotify overlaid on the current work and hides it
again; NumLock-off `6` does the same for a dedicated Firefox window; other keys hold whatever else
is wanted. NumLock-on typing is unaffected.

## Why this is possible at all

X11 assigns numpad keys **two keysyms on one keycode**, selected by NumLock, and NumLock is exposed
as modifier `mod2`:

```
keycode  81 = KP_Prior KP_9      # NumLock off -> KP_Prior, on -> KP_9
mod2        Num_Lock (0x4d)
```

X11 passive grabs match the modifier state **exactly**: the specified modifiers must be down and no
others may be. So a grab on a keycode with an empty mask fires only while no modifier at all is
active — including no `mod2`. NumLock-on presses never match the grab and are delivered to the
focused application as normal digits.

Exact matching cuts both ways, and this is where the naive version of the design is **wrong**: any
*other* lock modifier also breaks the match. CapsLock sets `LockMask` (`0x02`), so with CapsLock on
a mask-0-only grab stops matching and every Dial silently dies — no error, no log, just a dead
keypad until you notice CapsLock is on. Probe `07` confirms exactly that.

The grab set is therefore the **cross product of the tolerated lock modifiers**, which must never
include `Mod2Mask`:

```
for each keycode:  grab(mask = 0)          # nothing held
                   grab(mask = LockMask)    # CapsLock on, still a Dial
                   #  ... never Mod2Mask -- that is the whole premise
```

The tolerated set is derived at runtime from `get_modifier_mapping()` rather than hardcoded, because
which bit ScrollLock occupies is not fixed. On this machine `mod3` is unmapped, so ScrollLock
contributes no modifier and the set is just `{0, LockMask}` — 2 masks × 16 keycodes = **32 grabs**.
If ScrollLock were mapped the cross product would grow to 4 masks per key.

This is the load-bearing assumption of the whole design, so it was verified empirically rather than
assumed — including the CapsLock case, which an earlier draft got wrong because the first probe
happened to run with CapsLock off.

## Verified findings

Every claim below was measured on this machine. The probe scripts live in `docs/probes/` so they can
be re-run if GNOME, Firefox, or the monitor layout changes.

| Claim | Result | Probe |
| --- | --- | --- |
| All 16 numpad keycodes grabbable with modifier mask 0 | Yes — no `BadAccess`, so mutter, pop-shell and Guake contend for none of them | `01` |
| All 32 grabs of the corrected set `{0, LockMask}` install cleanly | Yes, 32/32 | `08` |
| **CapsLock breaks a mask-0-only grab** | **Yes — every Dial dies while CapsLock is on** | `07` |
| Adding a `LockMask` grab fixes it without breaking digit typing | Yes — caught as `(91, 2)` with CapsLock on; still passes through when NumLock is also on | `07` |
| NumLock **off** → grab fires, for **all 16 keys** | Yes, all 16 reach the grab and none reach the focused app | `08` |
| NumLock **on** → **all 16 keys reach the application** as their digit | Yes — every key delivered to a real focused window with `Mod2` set and the correct `KP_*` keysym (`0xffb9` = `KP_9`, …) | `08` |
| `firefox --class=NAME` sets WM_CLASS on Firefox 152 | Yes → `WM_CLASS = "firefox", "FFPanelProbe"`; the **class** (second) field carries the custom value | `02` |
| mutter honours `_NET_WM_STATE_ABOVE`, `STICKY`, `SKIP_TASKBAR`, `SKIP_PAGER` | Yes, all four, with source indication 1 or 2 — the property is set; the resulting *shell behaviors* (alt-tab exclusion, stacking) are inferred from the mutter source cited below, not observed | `03` |
| Dial hints survive a minimize/restore round trip | Yes, 4/4 | `03` |
| `_NET_WM_STATE` reports `HIDDEN` when minimized | Yes | `03` |
| `_NET_WM_STATE_FOCUSED` is exclusive on mutter (parent + transient) | Yes in this test — the parent *lost* `FOCUSED` when its transient became active. **Not relied upon**: EWMH permits the WM to set it on several windows, so `_NET_ACTIVE_WINDOW` is used instead | `08` |
| Geometry accepted on a foreign window | Exact for the window types tested (GTK app, Firefox). **Not** a general guarantee — see *Geometry limits* | `02`, `04` |
| Hide/show without `xdotool` | Yes — `WM_CHANGE_STATE`→`IconicState` and `_NET_ACTIVE_WINDOW` | `04` |
| Desktop notifications without a new apt package | Yes — `gdbus` → `org.freedesktop.Notifications` | `04` |
| Anything on this system reserves screen space | No managed window sets `_NET_WM_STRUT`/`_STRUT_PARTIAL`, and `_NET_WORKAREA` equals the full root box with zero insets. Shell chrome (top bar) is not a managed window and is not covered by this | `05` |
| NumLock state readable | Yes — `get_keyboard_control().led_mask & 0x2`, agrees with `xset` | `05` |
| python-xlib exposes XKB (for event-driven NumLock) | **No** — server has `XKEYBOARD`, the binding does not | `05` |
| Monitors enumerable in pure python-xlib | Yes, via the RandR **1.2** path; `get_monitors` (1.5) is absent from the binding | `06` |
| Monitor hotplug **subscription** accepted | Yes — `randr.select_input(RRScreenChangeNotifyMask …)` succeeds. This proves the mask registers, **not** that events are received and decoded; a live hotplug test is an implementation task | `06` |
| Cost of one NumLock read | 28.4 µs; a 1 Hz poll is 0.0028 % of one core | `06` |

Two facts established from source rather than by probe:

- GNOME 42 excludes `skip_taskbar` windows from alt-tab. `mutter/src/core/window-private.h`:
  `#define META_WINDOW_IN_NORMAL_TAB_CHAIN(w) (meta_window_is_focusable (w) && META_WINDOW_IN_NORMAL_TAB_CHAIN_TYPE (w) && (!(w)->skip_taskbar))`
- Guake's top-bar icon comes from `Gtk.StatusIcon` (grep of `/usr/lib/python3/dist-packages/guake/`),
  and it does appear, so the legacy status-icon path works here with the already-enabled
  `ubuntu-appindicators@ubuntu.com` extension. No `gir1.2-appindicator3` package is needed.

**Consequence: the runtime needs no new system package.** `python-xlib` 0.29 and `gdbus` are already
present; `wmctrl` and `notify-send` are absent and deliberately not adopted.

## Key inventory

16 keycodes are grabbable. `.` is reserved for assign mode, leaving **15** Dials.

| Slot | Keycode | NumLock-off keysym | NumLock-on keysym |
| --- | --- | --- | --- |
| `7` `8` `9` | 79 80 81 | `KP_Home` `KP_Up` `KP_Prior` | `KP_7` `KP_8` `KP_9` |
| `4` `5` `6` | 83 84 85 | `KP_Left` `KP_Begin` `KP_Right` | `KP_4` `KP_5` `KP_6` |
| `1` `2` `3` | 87 88 89 | `KP_End` `KP_Down` `KP_Next` | `KP_1` `KP_2` `KP_3` |
| `0` | 90 | `KP_Insert` | `KP_0` |
| `.` — **reserved, assign mode** | 91 | `KP_Delete` | `KP_Decimal` |
| `/` `*` `-` `+` `enter` | 106 63 82 86 104 | same in both states | same in both states |

The five operator keys and numpad Enter carry one keysym regardless of NumLock. They are still
usable, because the daemon grabs on **keycode + empty mask**, not on keysym: with NumLock on, `mod2`
is set, the grab does not match, and the key types `+`/`-`/`*`/`/`/Enter as normal.

## Approach

**Chosen: a single python-xlib daemon plus a curses CLI**, mirroring the sibling `clipboard_history`
project (`clip`) — event-driven, no polling in the daemon, no new packages, no `wmctrl`/`xdotool` at
runtime. Every mechanism it relies on is demonstrated working in `docs/probes/`.

**Rejected — `xbindkeys` / `sxhkd` plus shell scripts.** Needs a new package, and there is nowhere
to host the three-state toggle logic or the monitor-fraction geometry model. Each keypress would
fork a shell that re-parses config and re-queries X. Since probe `01` shows a short xlib grab loop
already does the job, the dependency buys nothing.

**Rejected for now — `keyd` at the evdev layer.** It would survive a move to Wayland, but today it
is strictly worse: it needs a root daemon, it cannot read window state (so the three-state toggle is
impossible), and it would have to track NumLock itself instead of reading `mod2` from X. Recorded as
the migration path if this machine ever moves to Wayland/COSMIC — `pop-cosmic` is already installed.

## Architecture

Package `dials`, four entry points:

| Entry point | Role | Dependencies |
| --- | --- | --- |
| `dialsd` | The daemon: grabs keys, drives windows | `python-xlib` only, no GTK |
| `dials` | CLI subcommands + curses TUI | stdlib only |
| `dials-tray` | Optional top-bar shell icon, separate systemd unit | GTK via system `gi` |
| `dials-confirm` | Transient modal dialog for destructive confirmations | GTK via system `gi` |

The tray and the dialog are separate processes so the always-on daemon never pays GTK's ~30 MB.
Same reasoning `clip` uses to keep `clipd` GTK-free while `clip-popup` is GTK.

### Modules

Kept small and single-purpose, with I/O injected so logic is testable without an X server —
following `clip`'s established style.

| Module | Responsibility | Pure? |
| --- | --- | --- |
| `keys.py` | Slot ↔ keycode table, slot name parsing | yes |
| `geometry.py` | Monitor rect + fractions → pixel rect; fallback chain; clamping | yes |
| `panels.py` | `DialController` — the state machine; decides SHOW/RAISE/HIDE | yes (injected ops) |
| `config.py` | XDG paths, TOML load/save, `Dial` dataclass, validation, defaults inheritance | mostly |
| `icons.py` | `.desktop` `Icon=` → Nerd Font glyph, with per-Dial override | yes |
| `monitors.py` | RandR 1.2 enumeration, cache, hotplug invalidation | I/O |
| `windows.py` | EWMH: find by class, read state, hide/show/raise, set hints, set geometry | I/O |
| `grab.py` | `XGrabKey` install/remove, `BadAccess` reporting, pause/resume | I/O |
| `assign.py` | Assign-mode arming, capture, overwrite confirmation, write-back | mixed |
| `launcher.py` | Confirm-then-launch, `.desktop` lookup, wait-for-window | mixed |
| `notify.py` | `gdbus` notification wrapper; best-effort, never raises | I/O |
| `cli.py` | `list`, `bind`, `unbind`, `capture`, `reload`, `pause`, `resume`, `status`, `config` | mixed |
| `tui.py` | curses numpad grid, window picker, per-Dial editor | I/O |
| `tray.py` | `Gtk.StatusIcon` shell icon with live/dormant/paused states | I/O |
| `confirm.py` | GTK modal used by `dials-confirm` | I/O |

### Keypress flow

```
KeyPress(keycode, state == 0)
  -> keys.slot_for(keycode)                     "9"
  -> config.dial("9")                           Dial(match_class="spotify", ...)
  -> windows.find(dial.match_class)             window id | None
       None  -> launcher.arm_confirm(dial)      notify, grab Return+KP_Enter for 5s
       found -> windows.read_state(win)
                  HIDDEN       -> SHOW   apply geometry + hints, then activate
                  not FOCUSED  -> RAISE  activate only
                  FOCUSED      -> HIDE   WM_CHANGE_STATE -> IconicState
```

### State machine

| Window state | Action | Geometry re-applied |
| --- | --- | --- |
| not found | confirm-then-launch | — |
| `_NET_WM_STATE_HIDDEN` (minimized) | apply geometry + hints, activate | yes |
| visible, **not the active window** | activate only | only if `pin_geometry` |
| visible and **is the active window** | iconify | — |

Three states rather than a strict two-state toggle, because a Dial with `on_focus_loss = "normal"`
can be buried: pressing its key while it is buried must raise it, not hide it. A strict toggle would
need two presses to surface a buried window.

**"Focused" means `_NET_ACTIVE_WINDOW`, not `_NET_WM_STATE_FOCUSED`.** EWMH defines
`_NET_WM_STATE_FOCUSED` as *"whether the window's decorations are drawn in an active state"* and
explicitly permits a window manager to set it on more than one window — a modal dialog and its
parent, for instance. It is a decoration hint, not an exclusive keyboard-focus flag, so it is the
wrong predicate for a toggle: a Dial whose own dialog is up could be read as focused and get hidden
out from under that dialog. `_NET_ACTIVE_WINDOW` is single-valued by definition and is used instead.

Probe `08` tested this on mutter with a real `WM_TRANSIENT_FOR` child and found `FOCUSED` *was*
exclusive here — the parent lost it when the child activated. So the concrete failure does not
reproduce on this WM today. The stricter predicate is still used, because it is free, it is correct
by specification rather than by observed behavior, and it does not depend on a mutter implementation
detail that EWMH does not oblige it to keep.

`_NET_WM_STATE_HIDDEN` remains the right test for "minimized" and is kept.

### Activation is a request, not a command

Sending `_NET_ACTIVE_WINDOW` asks the WM to activate a window; it can be refused, notably by
focus-stealing prevention. The design therefore:

- Sends the triggering **`KeyPress.time`** as the message timestamp, not `CurrentTime`. The daemon
  has a real user-activity timestamp in hand and EWMH asks for it; `CurrentTime` is what a client
  sends when it has nothing better, and it is likelier to be second-guessed. Probe `04` used
  `CurrentTime` and succeeded, which is weaker evidence than it looks.
- Uses **source indication 2** (pager). Dials acts on the user's behalf across other applications'
  windows, which is pager-like rather than app-like. This is now a stated choice rather than an
  accident of the probe.
- **Verifies the outcome** instead of assuming it. A window can be de-iconified yet not receive
  focus. Activation is confirmed by observing `_NET_ACTIVE_WINDOW` actually become the Dial window;
  if it does not within a short window, the Dial is left visible and the failure is logged rather
  than leaving the state machine believing something untrue.

### Asynchronous transition model

Because activation is asynchronous and refusable, a `hide` Dial needs an explicit state machine
rather than a single guard clause:

```
INACTIVE  --key-->  ACTIVATING  --observed active-->  ACTIVE
   ^                    |                               |
   |                    +--- refused / timed out -------+
   |                         (log, stay visible)        |
   +---------------- HIDING <--- lost active -----------+
```

The hide-on-focus-loss guard arms only on the `ACTIVATING → ACTIVE` edge — that is, only after
`_NET_ACTIVE_WINDOW` has actually been observed to equal the Dial's window. Arming merely because an
activation message was *sent* is not enough, and is the race an earlier draft's "held focus" wording
under-specified.

Focus events are **reconciled against current root state** rather than treated as an ordered stream:
on each `PropertyNotify` the daemon re-reads `_NET_ACTIVE_WINDOW` and compares, so a stale or
coalesced event cannot drive a wrong transition.

Races this model must absorb, each a test case:

| Race | Required behavior |
| --- | --- |
| Activation refused or times out | log, leave the Dial visible, return to `INACTIVE` |
| Window destroyed between find and act | swallow the X error, return to `INACTIVE` |
| Focus moves elsewhere before activation completes | do not hide; the guard never armed |
| Two Dial keys pressed in rapid succession | each Dial keeps independent state; the second activation supersedes |
| Focus moves to a transient owned by the Dial | treated as the Dial still being active, not as focus loss |
| Iconifying emits further focus/property events | reconciliation against root state makes them harmless |
| Config reload changes or removes a Dial mid-transition | in-flight transition is abandoned, not applied to the new Dial |

### Key autorepeat

Holding a Dial key down produces repeated `KeyPress` events, which would toggle a Dial show → hide →
show for as long as the key is held. The daemon therefore **ignores repeats**: a press of the same
keycode within a short debounce window of the previous one is discarded. Debouncing at the dispatch
layer also protects assign mode and launch confirmation from the same problem.

This is the one behavior in the design that no probe covers, because XTEST cannot faithfully
reproduce server-generated autorepeat — it needs a physically held key, so it is an explicit
first-run smoke test rather than a claim.

Geometry is **not** re-applied on a plain raise, so hand-nudging a window is not undone on every
keypress. `dials capture <slot>` saves the current position instead; `pin_geometry = true` opts a
Dial into strict enforcement.

### Focus-loss behavior

`on_focus_loss` is one setting with three values, not two independent flags. "Can be buried" and
"hides when focus leaves" are mutually exclusive — hiding on focus loss means it can never be
buried — so they are values of the same knob:

| Value | Behavior | Mechanism |
| --- | --- | --- |
| `normal` | Stays visible, ordinary stacking, can be buried | no hint |
| `above` | Stays visible, never buried | `_NET_WM_STATE_ABOVE` |
| `hide` | Hides as soon as focus leaves | root `PropertyNotify` on `_NET_ACTIVE_WINDOW` |

`hide` needs **no polling**: the daemon already has an X connection and subscribes to
`PropertyNotify` on the root window for `_NET_ACTIVE_WINDOW`. `above` and `normal` need no watching
at all — the WM does the work.

### Overlapping Dials

Dials are **not** mutually exclusive and there is no "only one Dial at a time" rule. Two Dials
whose rects overlap are resolved entirely by each Dial's own `on_focus_loss`, because showing a
Dial is an ordinary focus change and is treated exactly like clicking on any other window:

| Dial A is showing, then Dial B is shown over it | What happens to A |
| --- | --- |
| A is `hide` | A hides — same as clicking away from it |
| A is `normal` | A stays visible and gets buried under B |
| A is `above` | A stays visible; B is stacked above it only if B is also `above` |

This falls out of the mechanism already described rather than needing new code: B taking focus
changes `_NET_ACTIVE_WINDOW`, the daemon's existing watcher sees it, and A's configured behavior
applies. Nothing special-cases "another Dial" versus "any other window", which is the point — the
keypad becomes a set of independent windows, not a mode switcher.

The default layout deliberately avoids the question anyway: Spotify on the left half of `HDMI-0`
and the Firefox panel on the right half do not overlap, so both can be shown at once.

**Race to guard against.** A Dial must never hide itself during its own show sequence. Showing a Dial
writes geometry and hints and *then* activates it, and focus can move transiently in between. This is
handled by the `ACTIVATING → ACTIVE` edge of the transition model above: the hide guard arms only
once `_NET_ACTIVE_WINDOW` has been *observed* to equal the Dial's window, never merely because an
activation request was sent. The condition is watcher state, so it is unit-testable without X.

Two consequences of applying the rule uniformly, stated so they are not read as bugs:

- The `dials-confirm` dialog takes focus, so raising it hides any showing `hide` Dial. That is the
  same thing clicking on the dialog would do, and it is left consistent rather than special-cased.
- The launch-confirmation notification does **not** take focus, so it never disturbs a showing Dial.

`_NET_WM_STATE_STICKY`, `SKIP_TASKBAR` and `SKIP_PAGER` are applied to every Dial unconditionally:
Dials must be reachable from any workspace and must never appear in alt-tab.

**Accepted trade (confirmed by the user):** per the mutter source above, `skip_taskbar` is the same
flag the window list uses, so Dials get no taskbar entry either. Alt-tab exclusion is the
requirement; taskbar presence was optional.

## Monitors

Monitor handling is a first-class concern rather than a detail, because the layout on this machine
demonstrably changes: `DP-1-1` (the 768×1366 portrait panel) was connected when probe `01` ran and
gone by probe `06`, and `eDP-1-1` shifted from `+373+1440` to `+388+1440` in the same window. A
design that stored absolute pixels would have silently placed Dials off-screen.

Layout as of probe `06`:

| Output | Resolution | Position | Role |
| --- | --- | --- | --- |
| `HDMI-0` | 3440×1440 | +0+0 | ultrawide, default for Dials |
| `eDP-1-1` | 2560×1440 | +388+1440 | laptop, **primary** |
| `DP-1-1` | — | disconnected | portrait, may or may not return |

11 outputs are reported; only those with a CRTC are usable.

### Enumeration

Pure python-xlib, RandR **1.2** API — `get_screen_resources` → `get_output_info` → `get_crtc_info`,
plus `get_output_primary`. The 1.5 `get_monitors` call is *not* available in python-xlib 0.29 (probe
`06`), so it is not used. Outputs with `crtc == 0` are skipped as disconnected.

**RandR 1.2 enumerates outputs, not logical monitors**, which matters: mirrored outputs share one
CRTC and would otherwise appear as two "monitors" with identical rects. Results are therefore
**deduplicated by CRTC id**, keeping the first output name for each CRTC, and the surviving list is
**sorted deterministically** by `(x, y, name)` so that "first usable monitor" in the fallback chain
means the same thing on every run rather than depending on server reply order.

### Cache and invalidation

The monitor list is cached and invalidated **event-driven, never polled**:
`randr.select_input(root, RRScreenChangeNotifyMask | RRCrtcChangeNotifyMask | RROutputChangeNotifyMask)`
is confirmed working in probe `06`. On any such event the cache is dropped and the next Dial
activation re-resolves geometry. A Dial already on screen is not forcibly moved — moving windows out
from under the user on every hotplug is worse than leaving them.

### Resolution fallback chain

`geometry.py` resolves a Dial's target monitor in this order, and every step is a tested case:

1. The monitor named in the Dial, if present and has a CRTC.
2. Otherwise the monitor flagged primary.
3. Otherwise the first usable monitor.
4. Otherwise the root bounding box.

Each fallback past step 1 logs once and is reported by `dials status`, so a Dial silently landing on
the wrong screen is visible rather than mysterious. Resolved rects are clamped to the root bounding
box, so a negative offset or a partially-off-screen monitor cannot produce an unreachable window.

### Error cases explicitly handled

| Case | Behavior |
| --- | --- |
| Named monitor absent (`DP-1-1` today) | fall back to primary, log once, flag in `status` |
| No output flagged primary | fall back to first usable monitor |
| Zero usable monitors | use root bounding box |
| Monitor rect with negative offset | clamp to root box |
| Computed rect wider/taller than the monitor | clamp to the monitor |
| Fractions outside 0..1, or `w`/`h` of 0 | rejected at config validation, not at show time |
| RandR query itself raises | keep the previous cache; if none, root bounding box |
| Layout changes while a Dial is visible | leave it alone; re-resolve on next show |

### Screen space

Nothing on this system reserves screen space, so there is **no strut subtraction**. Probe `05` found
zero windows with `_NET_WM_STRUT` / `_NET_WM_STRUT_PARTIAL`, and `_NET_WORKAREA` is
`[0, 0, 3440, 2880]` — identical to the root bounding box, all four insets zero.

**An earlier draft intersected each monitor rect with `_NET_WORKAREA`, which is wrong**, and is
removed. `_NET_WORKAREA` is a single desktop-wide rectangle; it cannot express per-monitor reserved
regions. On a multi-monitor desktop, one panel reserving an edge shrinks that one rectangle, and
intersecting *every* monitor against it would wrongly shrink monitors the panel never touched. The
previous justification — "it is free and starts working automatically" — was wrong: it was not free,
it was latently incorrect, and only invisible because every inset is currently zero.

`geometry.py` therefore resolves against the **raw deduplicated monitor rect**. If reserved space
ever appears, the correct implementation is to aggregate `_NET_WM_STRUT_PARTIAL` from strut-setting
windows and subtract only the struts that actually intersect the monitor in question — not to consult
the desktop-wide workarea. That is written down as the known upgrade path rather than pre-built,
since nothing on this system reserves space today.

An earlier draft also had a per-Dial `ignore_struts` escape hatch; it was removed as configuration
surface for a problem that does not exist here.

The practical consequence: a Dial on `eDP-1-1` with `y = 0.0` sits under the GNOME top bar, because
the top bar is shell chrome and does not advertise itself as reserved space. Offset `y` by hand for
Dials on the primary monitor. Dials on `HDMI-0`, the default, are unaffected — there is no top bar
there.

### cosmic-dock

The Pop!_OS dock (`cosmic-dock@system76.com`, a Dash-to-Dock fork, configured under
`org.gnome.shell.extensions.dash-to-dock`) does **not** affect Dial geometry. Its current settings:

| Setting | Value | Consequence for Dials |
| --- | --- | --- |
| `dock-fixed` | `false` | the dock is not pinned open, so it never reserves space |
| `autohide` | `true` | it slides away when not hovered |
| `intellihide` | `true` | it hides even while a window merely overlaps it |
| `dock-position` | `'BOTTOM'` | if it ever did reserve, it would eat the bottom edge only |
| `extend-height` | `false` | it spans part of the edge, not the whole side |
| `multi-monitor` | `false` | one dock only |
| `preferred-monitor` | `-2` | that dock lives on the primary (`eDP-1-1`), not `HDMI-0` |
| `height-fraction` | `0.9` | irrelevant while unpinned |

Because `dock-fixed = false`, Dash-to-Dock runs in autohide mode and publishes no struts — which is
exactly what probe `05` measured. So a Dial with `h = 1.0` on any monitor will **not** be clipped,
and the dock slides out of the way when a Dial overlaps it.

Three consequences worth stating rather than discovering later:

- **A bottom-edge Dial on the primary monitor overlaps the dock's hover zone.** Because
  `intellihide` is on, the dock hides rather than covering the Dial, so the Dial wins visually.
  Moving the pointer to the very bottom edge will still summon the dock over it. This only affects
  `eDP-1-1`, since `multi-monitor = false` and `preferred-monitor = -2` put the single dock on the
  primary — the default `HDMI-0` Dials never meet it.
- **If `dock-fixed` is ever switched to `true`**, Dash-to-Dock begins publishing struts,
  `_NET_WORKAREA` grows a bottom inset, and the monitor∩workarea intersection described above starts
  subtracting it automatically. No code change needed. This is the concrete payoff for keeping the
  intersection instead of hardcoding zero insets.
- **A `skip_taskbar` Dial may still show as a running app in the dock**, because Dash-to-Dock tracks
  applications rather than windows, and `skip_taskbar` hides a *window* from the window list. This
  is cosmetic, is not a requirement in either direction, and is called out only so the behavior is
  not read as a bug. It is also the one place a Dial might remain visible in the shell UI despite
  being hidden from alt-tab.

## Configuration

Hand-editable TOML, and the single source of truth — the CLI and TUI only edit this file. Per-Dial
values inherit from `[defaults]`.

### Config location

There are **two copies, and only one of them is live**:

| Path | Role |
| --- | --- |
| `~/.config/dials/config.toml` | **The live config.** The only file the daemon, CLI and TUI ever read or write |
| `dials/config/config.reference.toml` | **Reference copy in the repo.** Read by nothing. Exists so the configuration is visible in the workspace to future agents |

No symlink. An earlier draft symlinked the XDG path into the repo; that is dropped because it made
the running system depend on the project folder's location, which meant a dangling-link failure
mode, a `--relink` repair command, and a health check in `dials status` — three pieces of machinery
existing only to support the link. Two plain files need none of it.

The reference copy carries a header making its status unambiguous, so nobody edits it expecting an
effect:

```toml
# REFERENCE COPY - NOT LIVE. Nothing reads this file.
# The live config is ~/.config/dials/config.toml
# Refresh this snapshot with:  dials config export
```

`dials config export` overwrites the reference copy from the live one. It is manual on purpose:
automatically mirroring every write would mean the daemon touching the repo, and a stale snapshot is
a much smaller problem than a background process making git commits' worth of noise. `dials config`
prints both paths and says whether the snapshot currently differs from the live file.

The config contains no secrets — app class names, launch commands, monitor names, fractions — so the
reference copy is safe to commit, and is deliberately not in `.gitignore`.

Runtime state — the pause flag and the tray PID file — stays in `~/.local/state/dials/` and is
**not** mirrored into the repo: it is machine state, not configuration. The last-good config is held
in memory by the running daemon and never written to disk.

### Schema

```toml
[defaults]
monitor       = "HDMI-0"              # ultrawide; falls back to primary if unplugged
rect          = [0.0, 0.0, 0.5, 1.0]  # x, y, w, h as fractions of the monitor
on_focus_loss = "hide"                # normal | above | hide
pin_geometry  = false

[dials."9"]
label         = "Spotify"
match_class   = "spotify"             # WM_CLASS *class* field (second field)
# must carry the scale flag, or the min-width workaround from spotify_width is lost
launch        = "/snap/bin/spotify --force-device-scale-factor=0.7"
icon          = ""                   # nerd font glyph; auto-guessed from .desktop if omitted
monitor       = "HDMI-0"
rect          = [0.0, 0.0, 0.5, 1.0]
on_focus_loss = "hide"

[dials."6"]
label         = "Firefox panel"
match_class   = "Dial6"
launch        = "firefox -P dial6 --class=Dial6 --no-remote --new-instance"
monitor       = "HDMI-0"
rect          = [0.5, 0.0, 0.5, 1.0]
on_focus_loss = "above"
```

Both Spotify and the Firefox panel live on `HDMI-0`, the ultrawide — Spotify on the left half,
Firefox on the right half, so they can be shown together without overlapping. The portrait monitor
is deliberately not referenced by any default, since it is currently disconnected.

`rect` fractions resolve against the raw deduplicated monitor rect (see *Screen space* above).

### Geometry limits

"Geometry is applied exactly" holds for the window types probed — a GTK app and Firefox both landed
on the requested rect with zero drift — but it is **not** a general guarantee, and the spec should not
be read as promising one. A `ConfigureRequest` is subject to the application's own size hints:
minimum sizes, maximum sizes, and resize increments. A terminal that resizes in character cells will
land on the nearest cell boundary rather than the exact pixel rect.

The concrete known case on this machine is **Spotify**, which enforces a minimum window width; the
sibling `spotify_width` project exists precisely because of it and works around it with
`--force-device-scale-factor=0.7`. A narrow Dial rect for Spotify may therefore come out wider than
requested. That launcher override is already in place, so the two projects have to stay consistent:
Spotify's Dial `launch` should go through the same `.desktop` override rather than bare
`/snap/bin/spotify`, or the minimum width will not be reduced.

Behavior when a window refuses the requested size: accept what the WM grants, log the discrepancy
once, and do not fight it in a loop. `dials status` reports Dials whose last applied geometry did not
match what was asked for.

## Window identification

Match on the **class** field of `WM_CLASS`. For most apps that is enough: Spotify's Snap sets
`StartupWMClass=spotify` (see the sibling `spotify_width` project).

### Which window wins

A class match can return several windows — two Slack windows, a browser with three windows, an app
plus its open dialog. Every branch of the state machine depends on picking one deterministically, so
the policy is explicit rather than left to whatever `_NET_CLIENT_LIST` order happens to be:

1. Consider only windows in `_NET_CLIENT_LIST` (WM-managed) whose `WM_CLASS` class field matches.
2. Exclude windows whose `_NET_WM_WINDOW_TYPE` is `DIALOG`, `UTILITY`, `SPLASH`, `MENU` or
   `TOOLTIP`, and exclude override-redirect windows. A dialog is never *the* Dial.
3. Among the rest, prefer the one that is currently `_NET_ACTIVE_WINDOW`.
4. Otherwise prefer the most recently active — tracked by the daemon as it watches
   `_NET_ACTIVE_WINDOW` anyway, so this costs nothing.
5. Otherwise fall back to the lowest window id, purely so the choice is stable across presses rather
   than arbitrary.

A **transient owned by the chosen window** (`WM_TRANSIENT_FOR` pointing at it) counts as part of that
Dial's group: if the transient is active, the Dial is considered active, and it is not treated as
focus loss. This is what keeps a Dial from hiding itself while its own dialog is open — the concern
that motivated dropping `_NET_WM_STATE_FOCUSED`.

Two further identity rules:

- **Window ids are never persisted.** They are resolved fresh on each press, because an id can be
  destroyed and reused. Only the class lives in config.
- **After a launch**, the waiter accepts a matching window only if it appeared *after* the launch was
  issued, so an older pre-existing window of the same class is not mistaken for the newly spawned
  one.

Where a class is genuinely ambiguous and the above is not enough, the intended answer is a dedicated
instance with its own class — exactly the Firefox approach below — rather than more elaborate
matching heuristics.

Firefox needs more care, because Firefox refuses to run two instances against one profile, so a
"panel" Firefox window cannot simply be a second window of the normal browser. Options weighed:

- **Chosen — dedicated panel profile with a custom class.**
  Probe `02` confirms `--class` works
  on Firefox 152, giving `WM_CLASS = "firefox", "<name>"` — unambiguous, stable across reboots,
  never confused with normal browsing. **Accepted cost (confirmed by the user):** separate cookies,
  history and extensions, plus a second Firefox process.
- Rejected — matching a normal-profile window by title. Firefox window titles track the active tab,
  so the binding breaks the moment the tab changes.
- Rejected — binding a hand-picked X window id. Not stable across reboots; the binding dies with the
  window.

The panel profile is created once, by hand. `install.sh` does not create it — silently creating
Firefox profiles is too surprising for an installer.

### The dial6 profile

The profile exists at `~/.mozilla/firefox/wcobxzqa.dial6` and is registered in `profiles.ini` as
`[Profile2] Name=dial6`, so Dial `6` references it **by name**:

```
firefox -P dial6 --class=Dial6 --no-remote --new-instance
```

Referencing by name rather than by `--profile <absolute path>` keeps the machine-specific directory
hash and an absolute `$HOME` out of the config, and survives the profile directory being recreated.

Worth recording, because it briefly looked like a defect and is a trap for anyone re-checking this:
when first inspected, the profile directory held only `times.json` and there was **no** `dial6` entry
in `profiles.ini` — which looks exactly like the known Firefox behavior where a running instance
rewrites `profiles.ini` from memory and drops entries added elsewhere. That diagnosis was wrong. The
profile simply had not been opened yet; Firefox writes the `profiles.ini` entry and populates the
directory (`prefs.js`, `compatibility.ini`, `places.sqlite`) on **first launch**. After one launch
both appeared. A freshly created, never-opened Firefox profile is invisible to `-P` — it is not
broken, just not yet realised.

If the entry ever does go missing, the fallback bypasses `profiles.ini` entirely and needs no repair
to Firefox's own configuration:

```
firefox --profile ~/.mozilla/firefox/wcobxzqa.dial6 --class=Dial6 --no-remote --new-instance
```

### Paths in launch commands

`launch` strings are run **without a shell** — `shlex.split` then `Popen` — so nothing would
expand `~` or `$HOME` on its own. Written naively, the fallback line above would hand Firefox a
literal directory named `~` and silently create a junk profile there.

The launcher therefore expands each argv token itself, via `os.path.expandvars` then
`os.path.expanduser`, including after an `=` so `--profile=~/x` works as well as
`--profile ~/x`. Both `~` and `$HOME`-style variables are supported, and an unset variable is
left untouched rather than becoming an empty string.

Config therefore uses `~` rather than absolute paths, which keeps a machine-specific home
directory out of a version-controlled, publicly visible repository.

`match_class` is `Dial6`, matching the profile name, so which Firefox window belongs to which Dial is
obvious from either side. Because the class is what identifies the window, it must stay in sync with
`--class` in the launch line; that pairing is the one thing not to break when editing this Dial.

First launch will be a bare profile — no extensions, no logins, default settings. That is the
intended isolation, not a fault.

## Assign mode

Pressing `.` (NumLock off) binds the **currently focused window** to the next numpad key:

1. `.` → read `_NET_ACTIVE_WINDOW`, notify *"Assign mode: press a numpad key"*, arm for 5 s.
2. Next numpad key:
   - **Slot empty** → write the Dial immediately and notify *"Bound 4 → Slack"*.
   - **Slot already assigned** → open the overwrite dialog below. Nothing is written until it is
     confirmed.
3. `.` again, or the 5 s timeout, cancels.

New Dials take `label` from `_NET_WM_NAME`, `match_class` from `WM_CLASS`, `monitor` and `rect` from
where the window already sits, and everything else from `[defaults]`.

`Escape` is deliberately **not** grabbed as a cancel key for assign mode — that would steal Escape
system-wide for those five seconds. Pressing `.` again is the cancel.

### Overwrite confirmation

Reassigning an occupied slot destroys an existing Dial, so it gets a real modal window rather than a
notification — a notification cannot show what is about to be lost, and cannot be refused.
`dials-confirm` shows both sides:

```
┌─ Dial 4 is already assigned ───────────────────┐
│                                                 │
│   Currently:     Spotify                        │
│                  class  spotify                 │
│                  HDMI-0   0,0   50% x 100%      │
│                                                 │
│   Replace with:  Slack                          │
│                  class  slack                   │
│                  HDMI-0   50,0  50% x 100%      │
│                                                 │
│   Enter = replace     Esc / Backspace = cancel  │
└─────────────────────────────────────────────────┘
```

Because the dialog is a real focused window, it handles its own keys — `Return`, `Escape` and
`BackSpace` — with **no global grabs at all**. That is strictly better than the launch-confirm
mechanism below, and it is why the two flows deliberately differ:

| Flow | Mechanism | Why |
| --- | --- | --- |
| Overwrite a Dial | modal dialog | destructive; must show what is being replaced; must be refusable |
| Launch a missing app | notification + brief key grab | harmless; must not steal focus from what you are doing |

Both `Escape` and `BackSpace` cancel. Escape is the convention; Backspace is included because it was
asked for, and inside a focused dialog supporting both costs nothing — there are no global grabs to
conflict with. The dialog is keyboard-driven but its buttons are clickable.

If the dialog cannot start (no GTK, no display), assign mode **refuses** the overwrite with a
notification rather than silently destroying the existing Dial.

### Capture and dispatch semantics

| Question | Answer |
| --- | --- |
| When is the target window captured? | At the moment `.` is pressed — window id, class and geometry are **snapshotted** then. Whatever gains focus afterwards is irrelevant, so the dialog itself taking focus cannot change what gets bound |
| Does a numpad press go to assign mode or run its Dial? | Assign mode takes precedence while armed. One dispatcher decides per press, so there is no grab juggling and no race |
| What if the captured window closes before confirmation? | abort with a notification; a Dial is never written from a dead window's snapshot |
| Does the 5 s timeout kill the dialog? | **No.** The timeout bounds only the *capture* phase — waiting for a slot key. Once a slot is chosen, capture is complete and the modal has its own lifetime; a confirmation dialog that vanished after 5 s would be unusable |
| `WM_CLASS` or `_NET_WM_NAME` missing or malformed | refuse to bind and say why. A Dial with an empty `match_class` would match unpredictably, so it is never written |
| Captured class matches several windows | allowed — the *Which window wins* policy resolves it at press time. The snapshot records the class, not the id |
| Concurrent edits from the CLI or TUI | config writes are **atomic**: write a temp file in the same directory, `fsync`, then `rename`. A writer re-reads and re-applies onto the current file rather than overwriting from a stale copy, so a TUI session open in another terminal cannot silently revert a Dial |
| After a successful write | the daemon reloads its own config in-process; no `SIGHUP` round trip and no window where the file and the running state disagree |

## Launch confirmation

An unbound-but-configured app is not launched on the first press, so a stray keypress never spawns
anything. Instead: notify *"Spotify not running — press Enter to launch"* and arm for 5 s. Either
main `Return` (keycode 36) or `KP_Enter` confirms.

While armed, `Return` and `KP_Enter` are grabbed over the **same tolerated lock-mask set as the Dial
keys** — `{0, LockMask}` — and ungrabbed immediately on the first press or at timeout. Using mask 0
alone here would reintroduce the probe `07` bug in miniature: confirmation would silently stop working
whenever CapsLock happened to be on. Modified Enter is still never touched, since `Mod2Mask`,
`ControlMask` and `ShiftMask` are excluded, so `Shift+Enter` and `Ctrl+Enter` pass through untouched.

`Return` must be `BadAccess`-checked at grab time like every other key, since it is far more likely to
be contended than the numpad keys. See *Confirmation edge cases* below for what happens when one or
both grabs fail.

If `enter` is itself bound to a Dial, that binding is suspended for the duration of the confirm
window. This is documented rather than designed around, because reserving Enter permanently would
waste one of only 15 slots.

After confirmation: run `launch`, wait up to 10 s for a window matching `match_class` **that appeared
after the launch was issued**, then apply geometry and hints and activate it. If no window appears,
notify and give up. The process is **not** killed — a slow-starting app that shows a window at 11 s is
a much better outcome than a killed one, and killing a process because it was merely slow is the more
dangerous behavior.

The waiter runs off the daemon's existing X event loop (arm a deadline, check on `MapNotify` and on
`_NET_CLIENT_LIST` changes) and **never blocks it**. A blocking 10 s wait would freeze every other
Dial.

### Confirmation edge cases

These were undefined in an earlier draft and are now specified, because a temporary global grab of
`Return` is the most intrusive thing in the design and its failure modes must be closed:

| Case | Behavior |
| --- | --- |
| Either `Return` or `KP_Enter` grab fails | arm with whichever succeeded; if **both** fail, refuse to arm and notify — never leave a confirmation pending with no way to confirm it |
| One grab succeeds, the other fails | roll back to a consistent state: keep the successful grab, report the other in `status` |
| A second missing-app Dial is pressed while armed | the newer request **replaces** the older one; there is only ever one pending confirmation, so Enter is never ambiguous |
| The originating Dial key is pressed again while armed | treated as confirmation, so the "press it twice" instinct also works |
| Daemon exits, is paused, or reloads while armed | both temporary grabs are released in a `finally`-equivalent path; grabs are process-scoped so a *crash* releases them with the X connection, but an orderly stop must not rely on that |
| Autorepeat from the initiating key | swallowed by the dispatch debounce, so holding a key cannot arm and confirm in one gesture |
| `enter` is bound to a Dial | that Dial is suspended for the confirm window by the **single dispatcher**, which decides per press whether Enter means "confirm" or "run the Dial" — not by ungrabbing and regrabbing, which would race |

**A note on this being the intrusive option.** Grabbing plain `Return` for 5 s can swallow an Enter
you meant for a terminal or a chat box, and because the notification deliberately does *not* take
focus, you may not realise a confirmation is pending — so an Enter pressed for an unrelated reason can
launch the app. That is a new accidental-launch path, which is mildly at odds with the reason
confirmation was wanted in the first place. Confirming with the **same Dial key again** avoids
grabbing `Return` at all and unambiguously identifies which pending launch is being confirmed. Enter
is implemented as specified because it was explicitly requested; the trade-off is recorded here so it
can be revisited after living with it.

## Interfaces

### CLI

```
dials list                 # table of all 15 slots, bound or not
dials unbind <slot>
dials capture <slot>       # save the matched window's current geometry into its Dial
dials reload               # SIGHUP the daemon
dials pause                # release all grabs; numpad behaves stock in both NumLock states
dials resume               # re-install grabs
dials status               # paused or active, how many Dials are bound, config health
dials config               # print the live path and the reference path; flag if the snapshot differs
dials config export        # overwrite the repo reference copy from the live config
dials                      # no args -> curses TUI
```

Interactive binding is the **TUI's `b` key** (window picker), plus assign mode's `.` hotkey — there is
no `dials bind <slot>` subcommand, and an earlier draft of this block wrongly listed one.
`dials status` reports only what a short-lived CLI process can see for itself; the grab-conflict,
monitor-fallback and geometry-mismatch reporting promised elsewhere in this document is **not built**
— see *Deferred*.

`pause` exists because these grabs are global: if a game or a remote-desktop session needs the raw
numpad keys, there has to be a way to stand down without stopping the service. It is a flag file in
the state dir, so it survives a daemon restart, and it is what the tray icon reflects.

### TUI

stdlib `curses`, no new dependency — matching `clip`'s lean dependency policy and carrying no risk
with the Python 3.10 / system-site-packages venv constraint. A numpad grid mirroring the physical
layout, arrow-key navigation, a per-Dial editor, and a window picker listing everything in
`_NET_CLIENT_LIST` so an open window can be bound without knowing its class name.

Icons are **Nerd Font glyphs**, auto-guessed from the app's `.desktop` `Icon=` name with a per-Dial
`icon` override. Ghostty ships JetBrains Mono Nerd Font by default, so glyphs render with no font
install. Real bitmap icons via the Kitty graphics protocol are deferred: Ghostty supports it, but
driving it from inside a curses screen is fiddly and degrades badly in other terminals.

### Tray

Optional `dials-tray` unit showing a shell icon in the top bar, next to the clock and Guake's icon.
Proven feasible because Guake does exactly this here with `Gtk.StatusIcon`. Opt-in via
`install.sh --tray`, because it costs a GTK process the daemon itself avoids.

The icon has three states, so a glance answers "are my Dials live right now?":

| State | Condition | Appearance |
| --- | --- | --- |
| **Live** | daemon running, not paused, **NumLock off** | shell icon lit / full colour |
| **Dormant** | daemon running, not paused, **NumLock on** | shell icon dimmed |
| **Paused** | `dials pause`, or daemon not running | shell icon greyed with a slash overlay |

Live vs dormant is the state that matters: with NumLock on the keypad types digits and no Dial can
fire, so the layer is genuinely inert and the icon should say so.

**How NumLock is tracked.** `get_keyboard_control().led_mask & 0x2` gives the state, verified against
`xset` in probe `05`. There is no event-driven route available: the server has `XKEYBOARD`, but
python-xlib 0.29 ships no XKB binding, so `XkbSelectEvents` is out of reach without ctypes. The tray
therefore reads `led_mask` on a **1 Hz GLib timer**. Probe `06` measured one read at **28.4 µs**,
i.e. **0.0028 % of one core** at 1 Hz — small enough to accept in an optional UI process.

Two alternatives were rejected. Grabbing `Num_Lock` in synchronous mode and replaying it would be
event-driven, but its failure mode is *NumLock stops working*, which is far worse than a 28 µs timer.
Reaching XKB through `ctypes` against `libX11` would also work, but adds a C-ABI dependency to a
cosmetic indicator.

The **daemon never polls anything** — this timer lives only in `dials-tray`, which is opt-in. That
keeps the always-on process purely event-driven per the AGENTS.md daemon rules.

## Resource budget

Lightweight is a hard requirement, not an aspiration, so it gets stated as numbers the build must hit
and design rules that produce them. The reference point is the sibling `clip` daemon: same stack
(python-xlib on Python 3.10, event-driven, no GTK), measured at ~14 MB idle.

| Process | Idle CPU | Idle RSS | Wakeups at idle |
| --- | --- | --- | --- |
| `dialsd` (always on) | **0.0 %** | **≤ 16 MB** | **0 / s** |
| `dials-tray` (opt-in) | ≤ 0.01 % | ≤ 45 MB (GTK) | 1 / s |
| `dials`, `dials-confirm` | transient — contribute nothing at idle | | |

### How the daemon reaches zero

- **Block, never poll.** The daemon owns one X connection and blocks in `select()` on its file
  descriptor. With no keys pressed and no focus changes, the process is not scheduled at all — which
  is what makes 0.0 % CPU and 0 wakeups/s achievable rather than merely small.
- **No periodic timer exists.** The three timeouts in the design — assign mode 5 s, launch
  confirmation 5 s, wait-for-window 10 s — are expressed as the `select()` timeout, and that timeout
  is `None` (infinite) whenever nothing is armed. Timers exist only while a Dial is mid-interaction,
  never in the steady state. This is the specific reason the 10 s launch waiter must not be a
  `sleep()` loop.
- **No caches to keep warm.** Window lookups are done on demand; probe `06` measured an X round trip
  at ~28 µs, so caching window state would trade real complexity and staleness bugs for microseconds.
  The only cache is the monitor list, and it is invalidated by RandR events rather than refreshed on a
  schedule.
- **Lazy, minimal imports.** The daemon must never import `curses`, `gi`/GTK, the TOML *writer*, or
  the TUI modules. Config reading uses `tomllib` (`tomli` on 3.10); writing lives only in the CLI
  path. Import graph is part of the budget: every module the daemon pulls in is resident for the
  session.
- **No subprocesses at idle.** `gdbus` is spawned only to show a notification and exits immediately.
  Nothing is kept alive to listen.
- **systemd limits as a backstop**, per the AGENTS.md daemon rules: `MemoryMax=64M`, `CPUQuota=5%`,
  `Nice=5`. These are guard rails against a regression, not the plan — a daemon that needs them has
  already broken the budget.

### What lightweight cost us

Two features were dropped or kept opt-in on these grounds, and it is worth recording that the
trade was made deliberately:

- **The config symlink and its machinery** (dangling-link handling, `--relink`, a health check in
  `status`) — removed entirely in favour of two plain files.
- **The tray is opt-in and measured separately.** A GTK status icon costs more than the daemon it
  reports on, which is exactly why it is a second process behind an `install.sh --tray` flag rather
  than folded in. Running without it costs nothing; `dials status` gives the same information from the
  CLI.

Verification is not optional: the numbers above get measured with the daemon idle for several minutes
and written into the README, and a build that misses them is not finished.

## Error handling

Follows `clip`'s degrade-never-crash rule — nothing in a keypress path may kill the daemon.

| Failure | Response |
| --- | --- |
| Window dies between find and act | X error logged and swallowed |
| Configured monitor absent | full fallback chain, log once, report in `status` |
| RandR query raises | keep previous monitor cache; if none, root bounding box |
| Zero usable monitors | root bounding box |
| Config fails to parse | keep last-good config in memory, notify once |
| Live config missing entirely | run with `[defaults]` only, notify once, flag in `status` |
| `launch` command fails | notify, give up |
| Window never appears within 10 s | notify, give up |
| `gdbus` notification fails | silent |
| `dials-confirm` cannot start | refuse the overwrite, notify; never destroy a Dial silently |
| Key grab returns `BadAccess` | log which slot, keep the other grabs, report in `status` |
| X server drops | `Restart=on-failure` plus `PartOf=graphical-session.target` |

`SIGHUP` re-reads config and re-installs grabs, so `dials reload` never needs a restart.

## Testing

pytest, with ops injected so no X server is required — mirroring `clip`'s
`runner=subprocess.run`-as-parameter style.

| Area | What is covered |
| --- | --- |
| `geometry.py` | fractions → pixels; the full monitor fallback chain (named → primary → first → root box); clamping negative offsets, oversized rects, and off-screen monitors; workarea intersection including the no-inset case |
| `monitors.py` | parsing a RandR reply into monitors; skipping `crtc == 0` outputs; **deduplicating mirrored outputs sharing one CRTC**; deterministic ordering; no output flagged primary; zero usable monitors; cache invalidation on a simulated `RRScreenChangeNotify`; a query that raises |
| `panels.py` transitions | the `INACTIVE → ACTIVATING → ACTIVE → HIDING` model against every race in the table above: refused activation, window destroyed mid-flight, focus moving before activation completes, two Dials in rapid succession, transient-of-Dial gaining focus, stale/coalesced focus events reconciled against root state, config reload mid-transition |
| `panels.py` | the state machine table, every state → action pair; the focus-loss watcher — a `hide` Dial hides when another Dial (or any window) takes focus, a `normal` one is left buried, an `above` one left alone; and the held-focus guard, so a Dial never hides itself during its own show sequence |
| `config.py` | load/save round trip, defaults inheritance, validation, bad values, rejecting fractions outside 0..1 and zero-size rects, reserved-slot rejection |
| `keys.py` | slot ↔ keycode mapping both directions, reserved-slot handling |
| `grab.py` | the tolerated-lock-mask cross product built from a fake modifier mapping — includes `LockMask`, **never** `Mod2Mask`, and grows correctly when ScrollLock is mapped; partial-failure rollback; autorepeat debounce |
| `windows.py` | the *which window wins* policy: type/override-redirect exclusion, active-window preference, most-recent tiebreak, lowest-id stability, transient-of-match treated as the same group, post-launch windows only |
| `icons.py` | `.desktop` → glyph mapping, override precedence |
| `assign.py` | arming, timeout, cancel, write-back, **occupied-slot path returns a confirmation request rather than writing**, and the refuse-on-dialog-failure path |
| `launcher.py` | confirm arming/timeout, wait-for-window with a fake clock |
| `cli.py` | each subcommand against an injected config, including `pause`/`resume` |
| `tray.py` | the state-selection function — (running, paused, numlock) → icon state — as a pure table |

Monitor handling gets the heaviest coverage, because it is the part most likely to break silently and
the part whose real-world inputs already changed once during design.

Not unit-tested, smoke-tested by hand: curses rendering, the GTK tray and dialog, and real X I/O.
Same split `clip` documents.

## Install and revert

`install.sh`, following `clip`'s pattern:

1. Check `python3.10`, `python-xlib`, `gdbus`, `uv`.
2. `uv venv --python /usr/bin/python3.10 --system-site-packages` at `~/.local/share/dials/venv`.
   The pinned interpreter and `--system-site-packages` matter for the optional GTK tray and dialog;
   the constraint is inherited from `clip`'s environment gotcha and kept for consistency. Use
   `uv pip`, never `uv run`/`uv sync`.
3. `uv pip install -e .`
4. Write `~/.config/dials/config.toml` from the reference copy **only if it does not already exist**;
   never overwrite a live config.
5. Install and enable `dialsd.service`; with `--tray`, also `dials-tray.service`.
6. Measure idle CPU, RSS and wakeups against the budget below and record them in the README, per the
   AGENTS.md daemon rules.

`uninstall.sh` stops and disables both units and removes the venv. It leaves both
`~/.config/dials/config.toml` and the repo's reference copy alone — one is the user's live
configuration, the other is version-controlled project content; neither is installer-owned state.

**Revert plan.** Nothing outside the project is modified except: two `systemd --user` units in
`~/.config/systemd/user/`, a venv at `~/.local/share/dials/`, a config file at
`~/.config/dials/config.toml`, and state under `~/.local/state/dials/`. No dconf keys, no system
packages, no changes to the X keymap, and no changes to Firefox's `profiles.ini`. Stopping `dialsd` releases every key grab immediately and
restores stock numpad behavior with NumLock off; nothing persists past process exit. The optional
Firefox panel profile, if created, is removed with `firefox -P` or by deleting its profile
directory.

## Deferred

- **Runtime diagnostics in `dials status`** — grab conflicts, monitor fallbacks and geometry
  mismatches. This document promises them in four places (*Resolution fallback chain*,
  *Geometry limits*, the *Confirmation edge cases* table, and the *Error handling* table) and the
  shipped `dials status` reports none of them. It reports what one short-lived process can see for
  itself: paused/active, how many Dials are bound, and whether the config parses. Everything else
  lives in the **daemon's** memory, and `status` is a separate process with no channel to it, so this
  is a missing feature rather than a missing print statement: it needs a daemon→CLI channel — a JSON
  state file written to `state_dir()` on each event, or D-Bus. What exists instead, per diagnostic:
  grab failures are printed to stderr at startup and on resume, so `journalctl --user -u dialsd` has
  them; a monitor fallback raises a desktop notification once per Dial (and `monitors.pick` already
  returns the reason string this feature would consume); **geometry mismatch is not detected at all**
  — nothing reads the granted geometry back, so the "log the discrepancy once" line in
  *Geometry limits* is unimplemented too.
- **Crosshair click-to-pick** binding (`xdotool selectwindow`) — the assign-mode hotkey and the TUI
  window picker cover the need; this is a small addition if it turns out to be wanted.
- **Real bitmap app icons** in the TUI via the Kitty graphics protocol.
- **Alt-tab-hidden but taskbar-visible** — impossible with EWMH hints alone, since mutter reads one
  flag for both. Would need a GNOME Shell extension.
- **Event-driven NumLock tracking** — needs an XKB binding python-xlib does not provide; revisit if
  the 1 Hz tray timer ever proves visible, or if `ctypes` becomes acceptable.
- **Wayland / COSMIC support** — would mean moving key handling to `keyd` at the evdev layer and
  replacing EWMH window control with a compositor-specific path.
- **Sliding animation.** An external client cannot animate a foreign window's geometry smoothly
  under mutter; Dials snap into place. Only a Shell extension could do better.
