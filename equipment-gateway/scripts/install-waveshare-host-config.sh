#!/usr/bin/env bash
# LAB BREAK-GLASS ONLY.
# Fleet devices get Waveshare site config from the Softwares OCI bootstrap
# (see docs/WAVESHARE_MODBUS.md). Do not use this on customer devices.
#
# Writes /etc/dataplicity/equipment-gateway for a desk unit you intentionally
# shell into. Pair with EQUIPMENT_BOOTSTRAP=0 on that unit.
set -euo pipefail

echo "WARNING: lab break-glass host install. Fleet path is Softwares bootstrap." >&2

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/equipment_gateway/site_templates/waveshare"
DEST="${DEST:-/etc/dataplicity/equipment-gateway}"
STATE="${STATE:-/var/lib/dataplicity/equipment-gateway}"
DEVICE_NODE="${WAVESHARE_MODBUS_DEVICE:-}"

if [[ ! -d "$SRC/equipment" || ! -f "$SRC/config.json" ]]; then
  echo "missing bundled site template at $SRC" >&2
  exit 1
fi

if [[ -z "$DEVICE_NODE" ]]; then
  if [[ -e /dev/ttyUSB0 ]]; then DEVICE_NODE=/dev/ttyUSB0
  elif [[ -e /dev/ttyACM0 ]]; then DEVICE_NODE=/dev/ttyACM0
  elif [[ -e /dev/ttyAMA0 ]]; then DEVICE_NODE=/dev/ttyAMA0
  else DEVICE_NODE=/dev/ttyUSB0
  fi
fi

echo "installing lab host override → $DEST (modbus device=$DEVICE_NODE)"
install -d -m 0755 "$DEST/equipment" "$STATE"
install -m 0644 "$SRC/config.json" "$DEST/config.json"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -a "$SRC/equipment/." "$tmp/"
python3 - "$tmp" "$DEVICE_NODE" <<'PY'
import json, sys
from pathlib import Path
root, device = Path(sys.argv[1]), sys.argv[2]
for path in sorted(root.glob("*.json")):
    payload = json.loads(path.read_text())
    credit = payload.setdefault("options", {}).setdefault("credit_pulse", {})
    transport = credit.setdefault("transport", {})
    transport["device"] = device
    path.write_text(json.dumps(payload, indent=2) + "\n")
PY
install -m 0644 "$tmp"/*.json "$DEST/equipment/"
echo "Set EQUIPMENT_BOOTSTRAP=0 on this lab unit to honour $DEST/config.json"
