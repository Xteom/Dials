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
uv venv --python /usr/bin/python3.10 --system-site-packages "$VENV"
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
  # Strip the reference header so the live file is not labelled "NOT LIVE".
  sed -i '/^# REFERENCE COPY/,/^# Refresh this snapshot/d' "$LIVE_CONFIG"
  echo "  wrote $LIVE_CONFIG"
fi

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

echo "==> status"
systemctl --user --no-pager --lines=0 status dialsd.service || true
"$VENV/bin/dials" list

cat <<'EOF'

Dials installed.

  NumLock OFF -> the numpad drives your windows
  NumLock ON  -> the numpad types digits, exactly as before

  dials list     show every slot
  dials status   health, grab conflicts, monitor fallbacks
  dials pause    stand down (for a game or remote desktop)

  '.' arms assign mode: focus a window, press '.', then press a numpad key.

EOF
