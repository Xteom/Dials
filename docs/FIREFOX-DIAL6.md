# The Firefox Dial (slot 6) and its profile

Why the Firefox Dial needs its own Firefox profile, why that profile is tuned, and
why it is tuned *differently* from the main one.

The per-pref rationale is not repeated here — it lives beside each pref in
`config/firefox-dial6-user.js.reference`, which is a copy of the live
`~/.mozilla/firefox/wcobxzqa.dial6/user.js`. This document covers what that file
cannot: the investigation, the measurements, and the options rejected.

Related: the sibling project `~/Documents/personal/pc_tweaks/firefox_performance/`
holds the main profile's tuning and the GPU-decode investigation this depends on.

---

## Why a separate profile at all

Firefox refuses to run two instances against one profile, and a Dial needs a
window it can identify. `--class=Dial6` gives the window a distinct `WM_CLASS`,
which is what `match_class` matches on, but `--class` applies per *instance* —
there is no way to give one window in a running Firefox its own class. Hence
`-P dial6 --no-remote --new-instance`, and hence a second Firefox.

`match_class` must stay in sync with `--class`. That pairing is the one thing not
to break when editing this Dial.

## The symptom, and what it actually was

The Dial worked but felt slow. The cause was not Dials and not the separate
instance: **the dial6 profile was stock, while the main profile is tuned.** The
main profile carries a `user.js` with documented performance prefs and two ad
blockers; `user.js` is per-profile, so none of it reached dial6.

Measured on 2026-07-30, both instances running, WhatsApp Web open in dial6:

| | processes | PSS | RSS |
| --- | --- | --- | --- |
| dial6 | 10 | **148 MB** | 874 MB |
| main (~15 tabs) | 25 | 2700 MB | 4681 MB |

That reframed the problem. dial6 is ~5 % of the main instance, so the complaint
was never about memory — it was CPU and latency. The tuning targets that.

## Why not something lighter than Firefox

The obvious instinct is that a whole Firefox is too much for three social sites.
Measurement says otherwise, for a reason that is easy to miss:

**A second Firefox shares the first one's code pages.** libxul and friends are
mapped once and charged half to each instance, which is a large part of why dial6
costs only 148 MB PSS. A different engine shares nothing — it brings its own
~150 MB of code pages plus its own GPU, network and utility service processes.
The overhead being optimised is the small part; the sites dominate.

| Option | Verdict |
| --- | --- |
| **Firefox, tuned** (chosen) | Cheapest in practice because of page sharing. Keeps one engine, one set of logins, and the built-in tracking protection. |
| Chrome `--app=URL --class=Dial6` | Viable — Chrome is installed, and each `--app` window is separate, so it would allow one Dial per site. But a second engine resident, no page sharing, and every login redone. Try only if the tuning proves insufficient. |
| WebKitGTK single-site browser | Rejected. Only webkit2gtk **4.0** is installed, old enough that WhatsApp Web will likely refuse it, and no `epiphany` to host it. Lightest on paper, highest risk in practice. |
| Electron wrappers (Ferdium, Rambox…) | Rejected without measuring: Electron is a Chromium plus a Node runtime per app. Strictly worse than the thing being replaced. |

## What is tuned, and the one thing that is not

The prefs are in `config/firefox-dial6-user.js.reference`, each with its reason.
In summary they do four things: stop AV1 software decode, turn up Firefox's own
tracking protection, blank the new tab page, and stop background telemetry and
disk writes. Plus `accessibility.force_disabled`, which is likely the largest CPU
win, because Firefox maintains an accessibility tree on every DOM change and
WhatsApp Web mutates the DOM constantly.

**The important part is what was deliberately left out.** The main profile sets
`browser.tabs.unloadOnLowMemory` with a 15 % threshold. Copying it here would
have been a bug: unloading a tab tears down WhatsApp Web's WebSocket, so it must
reconnect and re-sync every time you look at the panel. Tab unloading is right for
a browser holding eighty tabs and wrong for a messaging panel holding two.

Also left alone, each for its own reason:

- **Safebrowsing** stays on. Disabling it saves a few periodic blocklist
  downloads — the smallest win available, and the worst trade: this is the window
  where links arrive from other people.
- **`dom.ipc.processCount`** stays at its default. Fission is on in Firefox 152,
  so content processes are already allocated per site and a two-site profile keeps
  very few. Capping the pool would trade memory for cross-site contention with no
  measurement behind it.
- **Every VA-API / dmabuf / hardware-decode pref**, for the reason the sibling
  project established on 2026-07-24: the external monitors are wired to the dGPU,
  so Firefox composites on the NVIDIA render node and the Intel iGPU cannot
  engage regardless of prefs. The real fix is NVDEC — see that project's
  `NEXT_STEPS_nvdec_decode.md`.
- **Ad blockers**, by choice. `privacy.trackingprotection` covers the part of the
  problem that costs page-load time with nothing resident in memory.

## Every pref was checked against the installed build first

A misspelled pref is silently ignored and is indistinguishable from a tweak that
did not help. So each one was confirmed to exist in Firefox 152.0.5 before being
written — as a string in `libxul.so` for statically-defined prefs, or in the
default pref files inside `omni.ja` for the rest. Three candidates that could not
be confirmed were dropped rather than written on faith.

This matters more than it sounds: several prefs commonly recommended for "Firefox
performance" do not exist in current builds, and a `user.js` full of them looks
like it is doing something.

## Extensions are installed by hand, not side-loaded

A Dark Mode xpi was briefly copied in from the main profile with
`extensions.autoDisableScopes = 0` so it would start enabled. It worked in the
sense that mattered least: Firefox registered it as active and correctly signed.
But **a side-loaded extension gets no toolbar button**, so there was nothing
visible to click and it read as a total failure.

Removed, and `autoDisableScopes` is back at its default of 15 — which is the
safer value regardless, since it stops anything dropped into the profile
directory from enabling itself silently. Extensions in this profile get installed
from addons.mozilla.org like anywhere else, which also keeps them updating.

## Two loose ends in the surrounding setup

Neither is caused by Dials; both were found while investigating and are recorded
so they are not rediscovered from scratch.

**The main profile's `user.js` and `prefs.js` disagree.** The `user.js` states in
capitals that no VA-API or hardware-decode pref is to be set, because iGPU decode
cannot engage on this hardware. But `prefs.js` still carries
`media.ffmpeg.vaapi.enabled = true` and
`media.hardware-video-decoding.force-enabled = true`, live, from before that
conclusion was reached. `user.js` cannot unset a pref it does not mention, so
they persist. Worth reconciling so the file matches reality.

**`profiles.ini` names dial6 as the install default:**

```
[Install4F96D1932A9F858E]
Default=wcobxzqa.dial6
Locked=1
```

That normally means a bare `firefox` opens the panel profile rather than the main
one. The main windows show "Original profile" in their titles, which suggests
they are being reached another way, so this is flagged rather than asserted —
but it is also why the profile name began appearing in Firefox titlebars once a
second profile existed.
