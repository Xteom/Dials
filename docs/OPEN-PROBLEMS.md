# Open problems

Things known to be wrong, unconfirmed, or unresolved, with what is established and
what the next step is. Settled decisions live in `docs/IMPLEMENTATION-DECISIONS.md`;
this file is only for what is still open.

Last updated 2026-08-07.

---

## 1. ~~Dials reappear after a workspace swipe~~ — FIXED 2026-07-30

Sticky windows are `_NET_WM_DESKTOP = 0xFFFFFFFF`, i.e. present on *every*
workspace, so a four-finger swipe carried every Dial along with it. Replaced with
`_NET_WM_DESKTOP` set to `_NET_CURRENT_DESKTOP` on show, and `STICKY` explicitly
removed. See the design doc's *Workspaces: current-desktop placement, not sticky*.

Verified live: `sticky=True, desktop=4294967295` → `sticky=False, desktop=0`.

Kept here rather than deleted because the reasoning matters: the requirement was
"always in all workspaces", sticky implemented it literally, and that was the
wrong reading — "reachable from any workspace" was the actual wish.

## 2. Pop Shell's documented per-class rules are dead code — upstream bug

**Status: root cause found, working switch applied, CONFIRMED WORKING 2026-07-31.**
Dials no longer appear in alt-tab or the workspace overview. Kept in this file
because the upstream bug is still there and the workaround is still load-bearing:
anyone who re-enables `show-skip-taskbar`, or who reasons from Pop Shell's own
documentation, will land straight back here.

`SKIP_TASKBAR` is necessary but not sufficient here; `pop-shell` deliberately shows
skip-taskbar windows. Full mechanism with source excerpts: the design doc's *Pop
Shell interaction* section.

**The `skiptaskbarhidden` rules Pop Shell documents can never work in this
version.** `ext.conf` starts as `new Config.Config()`, whose constructor sets
`skiptaskbarhidden = []`, and `Config.reload()` — the only thing that ever
repopulates it — copies out `float` and `log_on_focus` and drops the third field:

```js
this.float = c.float;
this.log_on_focus = c.log_on_focus;     // skiptaskbarhidden never assigned
```

The JSON is parsed in full and then two of its three fields are used. So
`skiptaskbar_shall_hide()` only ever consults the hardcoded `SKIPTASKBAR_EXCEPTIONS`
— which is the real reason Guake escapes and nothing user-configured can.

**Applied instead:** `gsettings set org.gnome.shell.extensions.pop-shell
show-skip-taskbar false`. Supported, user-level, reversible, and Pop Shell watches
the key so it took effect immediately. Trade: system-wide, so genuine
tray-minimising apps lose their overview/alt-tab entry too.

The three inert `skiptaskbarhidden` rules are left in `config.json`. They cost
nothing and become correct if the bug is fixed — recorded here so their presence is
never read as evidence they do something.

**Not reported upstream.** Worth doing; nobody has. It is a two-line fix in
`config.js`: add `this.skiptaskbarhidden = c.skiptaskbarhidden;` to `reload()`.

### The method failure worth keeping

The rules were written, then *verified to match* by replicating
`skiptaskbar_shall_hide()` in Python against the live windows — Guake included as a
control — and they still did nothing. **Verifying that a rule matches is not
verifying that the rule is consulted.** The replication faithfully reproduced the
predicate and shared its input assumption: that `this.skiptaskbarhidden` contains
what the file contains. Reading the two lines of `reload()` would have settled it in
seconds, and `reload()` had already been read in this session for a different
question without noticing what it omitted.

## 3. `Alt+F2` then `r` cannot reload GNOME Shell on this install

**Status: understood, worked around, not fixed.**

`/usr/libexec/mutter-restart-helper` is not shipped by this Pop!_OS install, so the
restart fails and mutter logs `Failed to start restart helper` while carrying on
with the old process. The Shell had been running since 27 July across an attempted
restart.

Worse, the obvious check does not work: a *successful* Shell re-exec also preserves
the PID and process start time, so neither confirms nor denies a restart. The
journal line is the only evidence.

Use `gnome-extensions disable pop-shell@system76.com && gnome-extensions enable
pop-shell@system76.com` to reload that extension's config, or reboot. Whether the
missing helper is a packaging bug worth reporting to System76 has not been looked
into.

## 4. WhatsApp Web sometimes re-syncs after the Dial is hidden

**Status: explained, judged acceptable, not addressed.**

Hiding a Dial sends `WM_CHANGE_STATE` → `IconicState`. Firefox then treats the
tab's document as hidden, and WhatsApp Web re-synchronises with the server when it
becomes visible again — which presents as a brief refresh. "Sometimes" fits:
it depends on whether the connection actually dropped while hidden.

**Not caused by the profile tuning.** `browser.tabs.unloadOnLowMemory` is
deliberately *not* set for this profile precisely because it would cause a real
reload rather than a re-sync — see `docs/FIREFOX-DIAL6.md`. Memory pressure makes it
more likely (see §5): an iconified content process is a prime candidate for having
its pages compressed into zram and its timers throttled.

**If it becomes annoying**, the direction is to stop iconifying: hide by moving the
window off-screen instead, so the document never becomes hidden and nothing
re-syncs. That is a real change to the hide path with its own costs — an off-screen
window still occupies a workspace and still renders — and it should be measured
rather than assumed to help.

## 5. dial6 feels slow, and the machine is deep in swap

**Status: measured; the browser is not the problem.**

Measured while the complaint was live:

| | |
| --- | --- |
| RAM | 31 GiB total, 8.7 GiB available (**28 %**) |
| zram / swap | **13.8 GiB in use** of 19 GiB |
| Load average | 4.00 on 32 cores — CPU is idle by comparison |
| dial6 Firefox | 148 MB PSS, 10 processes |
| VS Code | 4114 MB PSS |
| this agent | 2976 MB PSS |
| main Firefox | 1198 MB + 2413 MB isolated web content |
| Spotify | 1159 MB PSS |

**13.8 GiB sitting in zram is the finding.** Every memory access that misses is a
decompress, and that is felt as latency everywhere, including in a browser window
that costs almost nothing itself. dial6 is 148 MB against a machine that has
overcommitted by more than 13 GiB.

So the honest answer to "is it the laptop or something we misconfigured" is: the
laptop, and specifically memory. The profile tuning did apply — `prefs.js` confirms
`media.av1.enabled=false`, `accessibility.force_disabled=1` and
`browser.newtabpage.enabled=false` are live — and there is no further browser-side
win of that magnitude available.

**One observation for the sibling `firefox_performance` project**, not for this one:
the main profile's tab-unloading threshold is
`browser.low_commit_space_threshold_percent = 15`, so unloading fires below 15 %
available. At 28 % available the safety net is not engaging even though 13.8 GiB is
already swapped. The note in that project's `user.js` warns against setting the
threshold near the typical available percentage (~32 %) to avoid latching into
permanent low-memory mode, so the gap between 15 % and 28 % looks deliberate — but
it does mean nothing intervenes in exactly this state.

---

## 6. ~~Every Dial opened on the laptop panel~~ — FIXED 2026-08-07

**A monitor's name is not a property of the monitor. It is a property of which
GPU is driving X**, and nothing in this project assumed that.

Every Dial was configured for `HDMI-0`, the ultrawide. All three opened on the
laptop panel instead. `pick()` was not broken — it did exactly what it
documents, and the config named an output that no longer existed:

| `system76-power graphics` | X is driven by | ultrawide | internal panel |
| --- | --- | --- | --- |
| `nvidia` | the NVIDIA X driver, owning every output | `HDMI-0` | `eDP-1-1` |
| `hybrid` | `modesetting` (Intel); NVIDIA attaches as a PRIME **sink** provider | `HDMI-1-0` | `eDP-1` |

The box was in `nvidia` when the config was written on 2026-07-30 and in
`hybrid` on 2026-08-07. In hybrid mode the NVIDIA-attached outputs arrive
through a second RandR provider and gain its index as a prefix — `HDMI-0`
becomes `HDMI-1-0` — so the configured name matched nothing, `pick()` fell
through to primary, and primary is the laptop. Confirm the mode with
`system76-power graphics` and `xrandr --listproviders`.

This is **not** the RandR hotplug case the README lists as unverified. Hotplug
changes which monitors are present; this changes what the same monitor is
called, and no hotplug event is involved.

### The part that was actually a bug

`pick()` returns a fallback reason, and its docstring says that reason exists
"for `dials status` so a Dial landing on the wrong screen is visible rather than
mysterious". Nothing ever surfaced it. `daemon._rect_for` raised a desktop
notification, which is transient and easy to miss, and `dials status` — the
command the README advertises as reporting config health — resolved no monitors
at all and printed only a count. The diagnostic written specifically to stop
this being mysterious had never been wired to a caller.

`dials status` now lists the live outputs and names every Dial that is not
landing where it was configured to:

```
state:     active
dials:     3 bound of 15 slots
monitors:  HDMI-1-0, eDP-1
monitor:   WARNING slot 9: monitor 'HDMI-0' absent; using primary 'eDP-1'
```

It stays exit-0 and degrades to `monitors:  unreadable (...)` when there is no
display, because `dials status` has to answer over ssh.

### Still open

The name remains a single exact string, so switching graphics mode back to
`nvidia` breaks it again in the same way — the reverse direction, silently, and
now with a warning line that says so. A monitor selector that survives a rename
(match on resolution, or on geometry, or accept a list of candidate names)
is the real fix and has not been designed.
