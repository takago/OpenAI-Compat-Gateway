#!/usr/bin/env bash
set -euo pipefail

# Install from an ordinary clone with: sudo ./install.sh
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run: sudo ./install.sh' >&2; exit 1
fi
SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEST=/opt/openai-compat-gateway
SERVICE=openai-compat-gateway
USERNAME=openai-gateway

for cmd in git python3 systemctl runuser; do
  command -v "$cmd" >/dev/null || { echo "Missing command: $cmd" >&2; exit 1; }
done
if ! id "$USERNAME" >/dev/null 2>&1; then
  useradd --system --home-dir "$DEST" --shell /usr/sbin/nologin "$USERNAME"
fi
install -d -o root -g root -m 0755 "$DEST"
install -d -o "$USERNAME" -g "$USERNAME" -m 0700 /var/lib/openai-compat-gateway
# No overwrite of persistent database. Keep source root-owned to reduce attack surface.
if [[ "$SRC" != "$DEST" ]]; then
  for f in gateway.py management.py endpoints.py admin.html requirements.txt LICENSE README.md README.ja.md; do
    install -m 0644 "$SRC/$f" "$DEST/$f"
  done
else
  echo 'Installing directly from /opt checkout (source files remain owned by their current owner).'
fi
if [[ ! -x "$DEST/.venv/bin/python" ]]; then
  python3 -m venv "$DEST/.venv"
fi
"$DEST/.venv/bin/python" -m pip install -r "$DEST/requirements.txt"
install -m 0644 "$SRC/openai-compat-gateway.service" /etc/systemd/system/openai-compat-gateway.service
systemctl daemon-reload
systemctl enable --now "$SERVICE"
systemctl restart "$SERVICE"
echo
printf 'Installed. Check: sudo systemctl status %s\n' "$SERVICE"
echo 'Admin setup: http://127.0.0.1:8765/admin (from server itself)'
echo 'If accessing remotely, use HTTPS via Caddy and finish setup before public exposure.'
