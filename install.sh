#!/usr/bin/env bash
# Install Dials. Idempotent: safe to re-run.
set -euo pipefail

VENV="$HOME/.local/share/dials/venv"
UNIT_DIR="$HOME/.config/systemd/user"
LIVE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dials/config.toml"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WANT_TRAY=0

for arg in "$@"; do
  case "$arg" in
    --tray) WANT_TRAY=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> checking dependencies"
for cmd in uv gdbus pgrep /usr/bin/python3.10; do
  command -v "$cmd" >/dev/null 2>&1 || [ -x "$cmd" ] || {
    echo "missing required command: $cmd" >&2; exit 1; }
done

echo "==> creating venv (python3.10, system site-packages)"
# --system-site-packages is required so the tray and confirm dialog can reach
# the system PyGObject, which is ABI-locked to python3.10. Use `uv pip` only:
# `uv run`/`uv sync` would recreate .venv without system site-packages.
# --allow-existing is what makes a re-run work at all. Without it uv aborts with
# "A virtual environment already exists ... Use --clear to replace it", and under
# `set -euo pipefail` that kills the script before it reaches the unit-enable
# step - so the "re-run with --tray" advice this script prints would fail.
# --allow-existing reuses the venv and PRESERVES the editable install (verified);
# --clear would wipe it and force a full reinstall.
uv venv --python /usr/bin/python3.10 --system-site-packages --allow-existing "$VENV"
VIRTUAL_ENV="$VENV" uv pip install -e "$REPO"

echo "==> verifying the venv can reach system GTK"
"$VENV/bin/python" -c "import gi; gi.require_version('Gtk','3.0'); print('  gi ok')"
"$VENV/bin/python" -c "import Xlib; print('  python-xlib', Xlib.__version__)"

echo "==> seeding live config (never overwriting an existing one)"
mkdir -p "$(dirname "$LIVE_CONFIG")"
if [ -e "$LIVE_CONFIG" ]; then
  echo "  keeping existing $LIVE_CONFIG"
else
  cp "$REPO/config/config.reference.toml" "$LIVE_CONFIG"
  # Strip the whole reference-only header so the live file is not labelled "NOT
  # LIVE" and does not carry instructions for refreshing the *other* file. The
  # range has to run to the end of that paragraph: stopping at "Refresh this
  # snapshot" left the "WARNING: that command REGENERATES this file" lines
  # behind, which in the live config referred to a file it is not.
  sed -i '/^# REFERENCE COPY/,/^# them with `git checkout/d' "$LIVE_CONFIG"
  # ...and the separator line that paragraph left behind at the very top.
  sed -i '1{/^#[[:space:]]*$/d}' "$LIVE_CONFIG"
  echo "  wrote $LIVE_CONFIG"
fi

echo "==> installing tray icons"
# The tray looks these up by NAME through the GTK icon theme, so they have to
# live in a theme directory: a path inside the repo is not discoverable, and
# would break the moment the checkout moved. hicolor is the fallback theme every
# other theme inherits from, so this works whichever icon theme is active.
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DIR"
install -m 644 "$REPO"/icons/dials-shell*.svg "$ICON_DIR/"
# Refresh the cache only if one exists. A user hicolor dir usually has no
# index.theme and therefore no cache, in which case GTK reads the directory
# directly and this would be a no-op that prints a confusing error.
HICOLOR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
if [ -f "$HICOLOR/index.theme" ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -f -t "$HICOLOR" || true
fi
echo "  installed $(ls "$REPO"/icons/dials-shell*.svg | wc -l) icons into $ICON_DIR"

echo "==> installing systemd units"
mkdir -p "$UNIT_DIR"
install -m 644 "$REPO/systemd/dialsd.service" "$UNIT_DIR/dialsd.service"
install -m 644 "$REPO/systemd/dials-tray.service" "$UNIT_DIR/dials-tray.service"
systemctl --user daemon-reload
systemctl --user enable --now dialsd.service

if [ "$WANT_TRAY" = 1 ]; then
  systemctl --user enable --now dials-tray.service
  echo "  tray enabled"
else
  echo "  tray NOT enabled (re-run with --tray to enable it)"
fi

echo "==> checking Pop Shell's skip-taskbar override"
# Advisory only, never fatal, and it changes nothing by itself.
#
# _NET_WM_STATE_SKIP_TASKBAR is necessary but not sufficient here. GNOME Shell
# honours it in both the overview and alt-tab, but pop-shell monkey-patches both
# to keep minimise-to-tray apps reachable, and its predicate matches any NORMAL
# window with skip_taskbar and a real WM_CLASS - i.e. every Dial window.
#
# The per-class `skiptaskbarhidden` rules pop-shell documents CANNOT exempt them:
# its Config.reload() copies out `float` and `log_on_focus` and silently drops
# `skiptaskbarhidden`, so that list is permanently empty and only the hardcoded
# SKIPTASKBAR_EXCEPTIONS ever apply. That is an upstream bug - do NOT send anyone
# to edit config.json, which is what an earlier version of this check did. The
# gsettings key below is the switch that works, and pop-shell watches it, so it
# takes effect with no reload. See the design doc's "Pop Shell interaction".
if gnome-extensions list --enabled 2>/dev/null | grep -q "pop-shell@system76.com"; then
  if [ "$(gsettings get org.gnome.shell.extensions.pop-shell show-skip-taskbar 2>/dev/null)" = "true" ]; then
    echo "  NOTE: pop-shell is enabled with show-skip-taskbar=true, so it will show"
    echo "        your Dials in alt-tab and the workspace overview even though they"
    echo "        set SKIP_TASKBAR. To stop that:"
    echo "          gsettings set org.gnome.shell.extensions.pop-shell \\"
    echo "            show-skip-taskbar false"
    echo "        Applies immediately, no reload. Trade: apps that genuinely"
    echo "        minimise to the tray are then hidden from those views too."
  else
    echo "  show-skip-taskbar is already false; Dials stay out of alt-tab"
  fi
else
  echo "  pop-shell not enabled; nothing to do"
fi

echo "==> status"
systemctl --user --no-pager --lines=0 status dialsd.service || true
"$VENV/bin/dials" list

cat <<'EOF'

Dials installed.

  NumLock OFF -> the numpad drives your windows
  NumLock ON  -> the numpad types digits, exactly as before

  dials list     show every slot
  dials status   paused or active, how many Dials are bound, config health
  dials pause    stand down (for a game or remote desktop)

  '.' arms assign mode: focus a window, press '.', then press a numpad key.

EOF
