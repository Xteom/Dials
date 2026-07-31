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

echo "==> checking Pop Shell's skip-taskbar rules"
# Advisory only, never fatal, and it deliberately does NOT edit the file.
#
# _NET_WM_STATE_SKIP_TASKBAR is necessary but not sufficient on this desktop.
# GNOME Shell honours it in both the overview and alt-tab, but pop-shell
# monkey-patches both to keep minimise-to-tray apps reachable, and its predicate
# matches any NORMAL window with skip_taskbar and a real WM_CLASS - i.e. every
# Dial window. Its own config can exempt classes again. See the design doc's
# "Pop Shell interaction" section.
#
# Not edited automatically because config.json belongs to another extension and
# malformed JSON there would take pop-shell's tiling down with it.
POP_CONF="${XDG_CONFIG_HOME:-$HOME/.config}/pop-shell/config.json"
if gnome-extensions list --enabled 2>/dev/null | grep -q "pop-shell@system76.com"; then
  missing=""
  for cls in $("$VENV/bin/python" - <<'PY'
from dials.config import load
print("\n".join(sorted({d.match_class for d in load().dials.values() if d.match_class})))
PY
  ); do
    grep -q "$cls" "$POP_CONF" 2>/dev/null || missing="$missing $cls"
  done
  if [ -n "$missing" ]; then
    echo "  NOTE: pop-shell is enabled and will show these Dials in alt-tab and the"
    echo "        overview despite SKIP_TASKBAR:$missing"
    echo "        Add one rule per class to \"skiptaskbarhidden\" in"
    echo "        $POP_CONF, e.g. { \"class\": \"^Spotify\$\" }, then reload"
    echo "        GNOME Shell (X11: Alt+F2, then r). Details: docs/superpowers/specs/."
  else
    echo "  all Dial classes are already exempted"
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
