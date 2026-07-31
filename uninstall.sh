#!/usr/bin/env bash
# Remove Dials. Leaves your config and the repo untouched.
set -euo pipefail

VENV="$HOME/.local/share/dials/venv"
UNIT_DIR="$HOME/.config/systemd/user"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/dials"

echo "==> stopping and disabling units"
for unit in dials-tray.service dialsd.service; do
  systemctl --user disable --now "$unit" 2>/dev/null || true
  rm -f "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload

echo "==> removing venv and runtime state"
rm -rf "$VENV" "$STATE"

echo "==> removing tray icons"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
# Named one by one rather than globbed: a glob here is one typo away from
# deleting somebody else's icons out of a shared theme directory.
for i in dials-shell dials-shell-dormant dials-shell-paused; do
  rm -f "$ICON_DIR/$i.svg"
done

cat <<'EOF'

Dials removed. Every key grab was released when the daemon stopped, so the
numpad is back to stock behaviour with NumLock off.

Deliberately NOT deleted:
  ~/.config/dials/config.toml        your live configuration
  <repo>/config/config.reference.toml  version-controlled project content

Nothing else was touched: no packages, no dconf keys, no X keymap changes, and
no changes to Firefox's profiles.ini.
EOF
