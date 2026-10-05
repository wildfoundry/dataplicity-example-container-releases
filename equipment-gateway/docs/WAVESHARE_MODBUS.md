# Waveshare Modbus RTU Relay — managed Softwares path

Production credit-pulse path when a Waveshare Modbus RTU Relay (or compatible
Module A DO flash) is on RS485. The **controller owns the pulse timer**
(FC05 flash-on, 100 ms quantum).

**Operators do not SSH or `sudo` on the device.** Site layout ships inside the
equipment-gateway OCI image and is materialized into the Softwares state volume
on dpdata at container start.

## How config reaches the device

| Piece | Where |
| --- | --- |
| Bay / relay map | OCI `site_templates/waveshare/` (Softwares release) |
| Working tree | `/var/lib/dataplicity/equipment-gateway/` → container `/var/lib/equipment-gateway/` |
| Serial node | Granted `--device` mounts (`ttyUSB*` / `ttyACM*` / `ttyAMA*`); absent nodes skipped until plugged in |
| Permissions | Container `group_add: dialout` from managed Softwares overrides |
| Selection | `EQUIPMENT_SITE=auto` → Waveshare when a serial node is present, else GPIO lab |

Bootstrap runs every Softwares start (`EQUIPMENT_BOOTSTRAP=1`). Updating the
bay map is a Softwares release, not a host edit.

## Defaults (match Waveshare wiki)

| Setting | Value |
| --- | --- |
| Baud | 9600 |
| Data / parity / stop | 8 / N / 1 |
| Modbus address | 1 |
| Flash | FC05 subcommand `0x02` (flash on) |
| Time quantum | 100 ms |
| Relays | 0–7 (8CH site template) |

## Desk plug-in (no host shell)

1. **Staging cloud** (once): seed Softwares + provision grants for serial + GPIO:
   ```sh
   python manage.py seed_dataplicity_microservices
   python manage.py provision_laundry_diagnostic_staging --io-backend both
   ```
   Import / assign equipment-gateway **≥ 0.2.4**.
2. **Plug in** USB-RS485 + Waveshare board. Agent reconcile bind-mounts the
   serial node (skipped while absent) and restarts Softwares.
3. Container bootstrap selects `waveshare`, writes
   `/var/lib/equipment-gateway/config.json`, patches `transport.device`.
4. **Probe** (from Softwares logs / exec if your ops path allows container exec —
   not host sudo):
   ```sh
   equipment-gateway --probe
   ```
5. **Vend** once; logs show `modbus_waveshare_flash`.

Optional env (Device Class / Softwares env, not device SSH):

| Env | Purpose |
| --- | --- |
| `EQUIPMENT_SITE` | `auto` (default), `waveshare`, or `gpio` |
| `WAVESHARE_MODBUS_DEVICE` | Force serial path (default: first present candidate) |
| `PRODUCT_INSTANCE_ID` | Override instance id in materialized config |
| `EQUIPMENT_BOOTSTRAP` | `1` managed (default); `0` lab host-override only |

## Lab break-glass only

`scripts/install-waveshare-host-config.sh` writes `/etc/dataplicity/…` for a
desk unit you intentionally shell into. Set `EQUIPMENT_BOOTSTRAP=0` to honour
that host file. **Not** the fleet path.

## Duration constraint

Flash delay must be an exact multiple of 100 ms. Bundled Waveshare site uses
100 ms / 1.0 s families only.
