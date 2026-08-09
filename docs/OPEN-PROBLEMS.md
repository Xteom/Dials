# Open problems

Things known to be wrong, unconfirmed, or unresolved, with what is established and
what the next step is. Settled decisions live in `docs/IMPLEMENTATION-DECISIONS.md`;
this file is only for what is still open.

Last updated 2026-08-09.

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

## 6. ~~Every Dial opened on the laptop panel~~ — FIXED 2026-08-07, root cause corrected 2026-08-09

**A monitor's name is not a property of the monitor. It is a property of which
GPU is driving X**, and nothing in this project assumed that.

Every Dial was configured for `HDMI-0`, the ultrawide. All three opened on the
laptop panel instead. `pick()` was not broken — it did exactly what it
documents, and the config named an output that no longer existed.

**Corrected 2026-08-09: the table below and the `system76-power graphics`
attribution that used to sit here were wrong.** `system76-power graphics`
reported `hybrid` on *both* the boot that broke and the boot that worked, so
the graphics mode never changed and was never the trigger. That was checked at
the time and misread — see the design doc's *Evidence* for the actual
measurement.

What changes the name is which GPU the firmware marks `boot_vga`, and
therefore which one X treats as primary. Both GPUs supply outputs; the
primary GPU's outputs keep their plain connector names, and the other GPU
attaches as a secondary RandR provider whose outputs get that provider's
index spliced in:

| X primary | ultrawide | internal panel |
| --- | --- | --- |
| NVIDIA | `HDMI-0` | `eDP-1-1` |
| Intel (`modesetting`) | `HDMI-1-0` | `eDP-1` |

Same cable, same panel, both times. Verified from the `*` marking the primary
PCI device in `~/.local/share/xorg/Xorg.1.log`, which moved between the two
boots, and from `/sys/bus/pci/devices/0000:01:00.0/boot_vga` in sysfs, which
currently reads `1` on the NVIDIA card. **What flips `boot_vga` itself is not
settled** — whether the external monitor is connected at power-on is the
leading hypothesis, from two data points; that is not proof, and is stated
here as a hypothesis, not a mechanism. `pick()` fell through to primary
exactly as documented once the configured name matched nothing, and primary
was the laptop.

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

### Fixed for real, 2026-08-09: EDID-based selection

The warning line bought visibility, not survival — the name was still a
single exact connector string, so a `boot_vga` flip in either direction broke
it again the same way. The actual fix is to stop naming the socket: `monitor`
now accepts `edid:NAME` (the display's own EDID model name, parsed from its
`0xFC` descriptor) and `internal` (the built-in panel, by connector type, since
panels don't carry a model name), either of which resolves to the same
physical display regardless of which GPU X treats as primary this boot. A bare
string, or `connector:NAME`, still means an exact connector, unchanged, for
anyone who wants that.

This config now uses `edid:AW3425DWM` for the ultrawide. Full design, including
why a candidate list of connector names is not a sound alternative and how
ambiguity (two monitors sharing a name) is handled:
`docs/superpowers/specs/2026-08-09-monitor-identity-design.md`.

---

## 7. `monitor_warnings` is never cleared

**Status: found while designing the EDID fix (§6), not addressed — pre-existing
and independent of that change.**

`daemon.py:144` writes `self.monitor_warnings[dial.slot] = warning` whenever a
Dial's resolution falls back, and nothing ever deletes an entry. So: a monitor
disappears, the Dial warns, the monitor comes back and the Dial resolves
cleanly again, the monitor disappears a second time — the second, identical
warning is suppressed, because the dict still holds the first one and the
write at `daemon.py:143` is gated on the value having *changed*, not on it
having been re-observed.

**Next step:** clear (or re-set) the slot's entry on a successful resolution,
not only write it on failure, so "still broken" and "broken again" are
distinguishable from "fixed since last checked."

## 8. `select_events` omits `RROutputPropertyNotifyMask`

**Status: found while designing the EDID fix (§6), not addressed.**

EDID is exposed as an output property, but `monitors.py`'s `select_events`
(around `daemon.py:625`, `monitors.py:298`) only asks for
`RRScreenChangeNotifyMask | RRCrtcChangeNotifyMask | RROutputChangeNotifyMask`.
A hotplug normally *also* fires an output-change event, so in practice a
display's EDID becoming readable (or unreadable) tends to be noticed anyway —
but "normally" is exactly the word a design with no polling should not lean
on. If a compositor or driver ever updates the EDID property in isolation,
without an accompanying output-change event, `dials status` and `pick()` would
keep using a stale cached read until some other event happened to trigger a
refresh.

**Next step:** add `RROutputPropertyNotifyMask` to the event mask and confirm
with a real hotplug that the cache actually refreshes on an EDID-only change,
not just on the output-change event that usually rides along with it.

## 9. `dedupe_and_sort` can drop the identity that would have matched

**Status: found while designing the EDID fix (§6), not addressed.**

`dedupe_and_sort` (`monitors.py:149`) keeps one `RawOutput` per CRTC. That is
correct for a *mirrored* pair — one CRTC, one picture, one `Monitor` — but the
two connectors feeding that CRTC can carry different EDIDs (or one may read
successfully while the other fails), and only one of the two survives
deduplication. If the dropped connector was the one whose EDID matched
`edid:NAME`, selection reports "no display named …" against a display that is,
physically, right there — a false negative indistinguishable from the display
being genuinely absent.

**Next step:** a logical `Monitor` should retain the EDIDs (and connector
names) of every `RawOutput` sharing its CRTC, and `pick()`'s name match should
check all of them, not just the one that happened to survive dedup. No mirrored
setup exists on this hardware to reproduce it against, so this needs either
borrowed mirrored hardware or a targeted unit test against a synthetic
CRTC-sharing pair.

## 10. The first Dial keypress after startup pays ~146 ms for the monitor cache

**Status: found while designing the EDID fix (§6), not addressed.**

`MonitorSource` builds its cache lazily (`monitors.py:274`) — the first call to
`monitors()` does the full RandR walk, EDID reads included. Measured on this
machine at 20 iterations, a full refresh costs ~146 ms regardless of whether
EDID is read (the EDID reads themselves are noise against that). Today, the
first thing that triggers a refresh is the first Dial keypress, so that
keypress — not daemon startup — eats the 146 ms, and a user pressing a Dial
right after login gets a visibly delayed first show.

**Next step:** call `monitors()` once at daemon start, immediately after
`select_events()` (`daemon.py:625`), purely to prime the cache. This adds no
polling — it is one extra read at a point that already does one-time setup —
and moves the cost off the interactive path.
