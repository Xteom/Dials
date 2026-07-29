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

A passive `XGrabKey` on a keycode with an **empty modifier mask** therefore matches only when no
modifiers are active — including no `mod2`. NumLock-on presses never match the grab and are
delivered to the focused application as normal digits.

This is the load-bearing assumption of the whole design, so it was verified empirically rather than
assumed.

## Verified findings

Every claim below was measured on this machine. The probe scripts live in `docs/probes/` so they can
be re-run if GNOME, Firefox, or the monitor layout changes.

| Claim | Result | Probe |
| --- | --- | --- |
| All 16 numpad keycodes grabbable with modifier mask 0 | Yes — no `BadAccess`, so mutter, pop-shell and Guake contend for none of them | `01` |
| NumLock **off** → grab fires | Yes, delivered as `(keycode 91, state 0)` | `01` |
| NumLock **on** → grab does not fire, digit types normally | Yes, nothing delivered | `01` |
| `firefox --class=NAME` sets WM_CLASS on Firefox 152 | Yes → `WM_CLASS = "firefox", "FFPanelProbe"`; the **class** (second) field carries the custom value | `02` |
| Geometry can be forced on a foreign window | Yes, pixel-exact via `configure()` | `02`, `04` |
| mutter honours `_NET_WM_STATE_ABOVE`, `STICKY`, `SKIP_TASKBAR`, `SKIP_PAGER` | Yes, all four, with source indication 1 or 2 | `03` |
| Dial hints survive a minimize/restore round trip | Yes, 4/4 | `03` |
| `_NET_WM_STATE` reports `HIDDEN` when minimized, `FOCUSED` when focused | Yes | `03` |
| Hide/show without `xdotool` | Yes — `WM_CHANGE_STATE`→`IconicState` and `_NET_ACTIVE_WINDOW` | `04` |
| Desktop notifications without a new apt package | Yes — `gdbus` → `org.freedesktop.Notifications` | `04` |
| Anything on this system reserves screen space | **No** — `_NET_WORKAREA` equals the full root box, all insets zero | `05` |
| NumLock state readable | Yes — `get_keyboard_control().led_mask & 0x2`, agrees with `xset` | `05` |
| python-xlib exposes XKB (for event-driven NumLock) | **No** — server has `XKEYBOARD`, the binding does not | `05` |
| Monitors enumerable in pure python-xlib | Yes, via the RandR **1.2** path; `get_monitors` (1.5) is absent from the binding | `06` |
| Monitor hotplug observable without polling | Yes — `randr.select_input(RRScreenChangeNotifyMask …)` succeeds | `06` |
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
| `_NET_WM_STATE_HIDDEN` | apply geometry + hints, activate | yes |
| visible, no `_NET_WM_STATE_FOCUSED` | activate only | only if `pin_geometry` |
| visible and `_NET_WM_STATE_FOCUSED` | iconify | — |

Three states rather than a strict two-state toggle, because a Dial with `on_focus_loss = "normal"`
can be buried: pressing its key while it is buried must raise it, not hide it. A strict toggle would
need two presses to surface a buried window.

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

**Race to guard against.** A Dial must never hide itself during its own show sequence. Showing a
Dial writes geometry and hints and *then* activates it, and focus can move transiently in between.
The rule is therefore: a `hide` Dial hides only on a transition from *it held focus* to *it does
not* — never on a focus event for a window that had not yet gained focus. Without that guard, a
`hide` Dial could hide itself the instant it appeared. The condition is a property of the watcher's
state, so it is unit-testable without X.

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

`geometry.py` still intersects the monitor rect with `_NET_WORKAREA` rather than hardcoding "no
insets": it is the same amount of code, it is correct by construction, and it starts working
automatically if a reserved-space panel is ever added. Today the intersection is a no-op. An earlier
draft of this spec had a per-Dial `ignore_struts` escape hatch; it was removed as configuration
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

The canonical file lives **in this repository**, at `config/config.toml`, and
`~/.config/dials/config.toml` is a **symlink** to it:

```
~/.config/dials/config.toml -> ~/Documents/personal/pc_tweaks/dials/config/config.toml
```

The real file is kept in the repo rather than in `~/.config` so that the live configuration sits in
the workspace where it is version-controlled, diffable, and visible to future agents working on this
project. The symlink direction matters: a symlink committed *into* the repo pointing out to `$HOME`
would be an absolute path that breaks on any other machine and reads as a dangling link in
`git status`. Pointing inward keeps the repo self-contained.

Consequences, all intentional:

- The daemon and CLI open the XDG path as normal and never need to know about the symlink.
- Editing either path edits the same file.
- Config changes made through the TUI show up as ordinary git diffs in this project, so Dial changes
  get committed alongside code.
- The config contains no secrets — app class names, launch commands, monitor names and fractions —
  so it is safe to commit. It is deliberately **not** in `.gitignore`.
- If the project folder is moved or deleted the symlink dangles. `dials status` reports this, and
  `install.sh --relink` repairs it after a move.

Runtime state — the pause flag and the tray PID file — stays in `~/.local/state/dials/` and is
**not** moved into the repo: it is machine state, not configuration. The last-good config is held in
memory by the running daemon and never written to disk.

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
launch        = "/snap/bin/spotify"
icon          = ""                   # nerd font glyph; auto-guessed from .desktop if omitted
monitor       = "HDMI-0"
rect          = [0.0, 0.0, 0.5, 1.0]
on_focus_loss = "hide"

[dials."6"]
label         = "Firefox panel"
match_class   = "FFPanel"
launch        = "firefox -P panel --class=FFPanel --no-remote --new-instance"
monitor       = "HDMI-0"
rect          = [0.5, 0.0, 0.5, 1.0]
on_focus_loss = "above"
```

Both Spotify and the Firefox panel live on `HDMI-0`, the ultrawide — Spotify on the left half,
Firefox on the right half, so they can be shown together without overlapping. The portrait monitor
is deliberately not referenced by any default, since it is currently disconnected.

`rect` fractions resolve against the monitor rect intersected with `_NET_WORKAREA` (today a no-op;
see *Screen space* above).

## Window identification

Match on the **class** field of `WM_CLASS`. For most apps that is enough: Spotify's Snap sets
`StartupWMClass=spotify` (see the sibling `spotify_width` project).

Firefox needs more care, because Firefox refuses to run two instances against one profile, so a
"panel" Firefox window cannot simply be a second window of the normal browser. Options weighed:

- **Chosen — dedicated panel profile with a custom class.**
  `firefox -P panel --class=FFPanel --no-remote --new-instance`. Probe `02` confirms `--class` works
  on Firefox 152, giving `WM_CLASS = "firefox", "FFPanel"` — unambiguous, stable across reboots,
  never confused with normal browsing. **Accepted cost (confirmed by the user):** separate cookies,
  history and extensions, plus a second Firefox process.
- Rejected — matching a normal-profile window by title. Firefox window titles track the active tab,
  so the binding breaks the moment the tab changes.
- Rejected — binding a hand-picked X window id. Not stable across reboots; the binding dies with the
  window.

The panel profile is created once, by hand, and documented in the README. `install.sh` does not
create it — silently creating Firefox profiles is too surprising for an installer.

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

## Launch confirmation

An unbound-but-configured app is not launched on the first press, so a stray keypress never spawns
anything. Instead: notify *"Spotify not running — press Enter to launch"* and arm for 5 s. Either
main `Return` (keycode 36) or `KP_Enter` confirms.

While armed, `Return` and `KP_Enter` are grabbed with **mask 0 only**, and ungrabbed immediately on
the first press or at timeout. So plain Enter is intercepted only inside that short window, and only
unmodified — `Shift+Enter` and `Ctrl+Enter` are never touched. `Return` must be `BadAccess`-checked
at grab time like every other key, since it is more likely to be contended than the numpad keys; if
the grab fails, fall back to `KP_Enter` alone and log it.

If `enter` is itself bound to a Dial, that binding is suspended for the duration of the confirm
window. This is documented rather than designed around, because reserving Enter permanently would
waste one of only 15 slots.

After confirmation: run `launch`, wait up to 10 s for a window matching `match_class`, then apply
geometry and hints and activate it. If no window appears, notify and give up.

## Interfaces

### CLI

```
dials list                 # table of all 15 slots, bound or not
dials bind <slot>          # bind interactively (opens the window picker)
dials unbind <slot>
dials capture <slot>       # save the matched window's current geometry into its Dial
dials reload               # SIGHUP the daemon
dials pause                # release all grabs; numpad behaves stock in both NumLock states
dials resume               # re-install grabs
dials status               # daemon state, grab conflicts, monitor fallbacks, config link health
dials config               # print both the XDG path and the repo path it resolves to
dials                      # no args -> curses TUI
```

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

## Error handling

Follows `clip`'s degrade-never-crash rule — nothing in a keypress path may kill the daemon.

| Failure | Response |
| --- | --- |
| Window dies between find and act | X error logged and swallowed |
| Configured monitor absent | full fallback chain, log once, report in `status` |
| RandR query raises | keep previous monitor cache; if none, root bounding box |
| Zero usable monitors | root bounding box |
| Config fails to parse | keep last-good config in memory, notify once |
| Config symlink dangles (project moved) | treat as no config, run with `[defaults]` only, notify once, flag in `status` |
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
| `monitors.py` | parsing a RandR reply into monitors; skipping `crtc == 0` outputs; no output flagged primary; zero usable monitors; cache invalidation on a simulated `RRScreenChangeNotify`; a query that raises |
| `panels.py` | the state machine table, every state → action pair; the focus-loss watcher — a `hide` Dial hides when another Dial (or any window) takes focus, a `normal` one is left buried, an `above` one left alone; and the held-focus guard, so a Dial never hides itself during its own show sequence |
| `config.py` | load/save round trip, defaults inheritance, validation, bad values, rejecting fractions outside 0..1 and zero-size rects, reserved-slot rejection |
| `keys.py` | slot ↔ keycode mapping both directions, reserved-slot handling |
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
4. Seed `config/config.toml` in the repo if absent, then symlink `~/.config/dials/config.toml` to it.
   If a **real file** already exists at the XDG path, move it into the repo as the seed and replace
   it with the symlink; never clobber an existing config silently. `--relink` repairs the symlink
   after the project folder moves.
5. Install and enable `dialsd.service`; with `--tray`, also `dials-tray.service`.
6. Measure idle CPU and RSS and record them in the README, per the AGENTS.md daemon rules.

`uninstall.sh` stops and disables both units, removes the venv, and removes the `~/.config/dials/`
symlink. It leaves `config/config.toml` in the repo untouched — that is version-controlled project
content, not installed state.

**Revert plan.** Nothing outside the project is modified except: two `systemd --user` units in
`~/.config/systemd/user/`, a venv at `~/.local/share/dials/`, a symlink at
`~/.config/dials/config.toml`, and state under `~/.local/state/dials/`. No dconf keys, no system
packages, no changes to the X keymap. Stopping `dialsd` releases every key grab immediately and
restores stock numpad behavior with NumLock off; nothing persists past process exit. The optional
Firefox panel profile, if created, is removed with `firefox -P` or by deleting its profile
directory.

## Deferred

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
