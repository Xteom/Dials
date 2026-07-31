# Open problems

Things known to be wrong, unconfirmed, or unresolved, with what is established and
what the next step is. Settled decisions live in `docs/IMPLEMENTATION-DECISIONS.md`;
this file is only for what is still open.

Last updated 2026-07-30.

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

## 2. Whether the Pop Shell rules actually fix alt-tab and the overview

**Status: cause established, fix applied, effect unverified.**

`SKIP_TASKBAR` is necessary but not sufficient here; `pop-shell` deliberately shows
skip-taskbar windows and has to be told not to. The full mechanism, with source
excerpts, is in the design doc's *Pop Shell interaction* section, and the three
`skiptaskbarhidden` rules are in `~/.config/pop-shell/config.json`.

What is confirmed: the rules match. Pop Shell's `skiptaskbar_shall_hide()` was
replicated against the live windows and returns "hide" for `Spotify`, `Dial6` and
`Slack`, with Guake as a control (it matches Pop Shell's own built-in rule and is
known to stay out of alt-tab on this machine).

What is not confirmed: that alt-tab and the overview are now clean. The one report
after applying them turned out to be about §1 instead, so the switcher and window
picker have still not actually been looked at since the config was loaded.

**Next step:** open alt-tab, and open the window-picker overview, and look. If a
Dial is still listed, do not add more hints — establish which code path builds that
particular list first. `cosmic-workspaces` overrides `Workspace.Workspace.prototype`
but only replaces `_init`, so `_isOverviewWindow` still resolves to Pop Shell's
patched copy; that was checked, but only by reading, not by observation.

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
