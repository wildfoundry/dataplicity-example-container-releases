# Laundry machine family catalogue

Profiles are JSON under this directory. Timings come from **public OEM manuals**
and **payment-installer documentation** (PayRange, Kiosoft, Laundroworks,
FasCard, Airwallet). None of these entries claim Dataplicity OEM qualification
or a verified wiring harness for a specific SKU.

| Status | Meaning |
| --- | --- |
| `lab_bench` | Desk GPIO / scope work only |
| `field_candidate` | Published third-party or OEM behavioural docs; still host-timed GPIO |
| `catalogue_only` | Documented interface that is **not** host-pulseable yet (e.g. serial) |
| `qualified` | Reserved for OEM-evidence-backed profiles |

## How to add a family

1. Copy an existing `*-pulse-*.json`.
2. Set unique `machine_profile` + aliases.
3. Fill `default_duration_seconds` / `default_gap_seconds` from a cited source.
4. Keep `status` honest; add `sources[]` with title + URL.
5. Run `equipment-gateway --list-machine-profiles`.

## Pulse vs serial

`PulseActivated` accepts `start_pulse`, `credit_pulse`, and `enable_pulse`.
`serial_native` profiles appear in the list but fail closed if selected for GPIO.

## Credit-pulse backends

`credit_pulse.backend` selects how the edge is driven:

| Backend | Timer owner | Use |
| --- | --- | --- |
| `gpio` (default when `chip`/`line` set) | Host (CM5 hybrid wait) | Desk lab / scope |
| `modbus_waveshare_flash` | Waveshare RTU Relay (FC05 flash, 100 ms quantum) | Production path with Waveshare IO |
| `modbus` | Generic duration register + trigger coil | Other controller-timed I/O |

Waveshare flash requires `duration_seconds` to be an exact multiple of 0.1 s.
Profiles with shorter defaults (e.g. 50 ms Dexter) need an explicit override
only when the machine accepts the longer pulse.

Managed Softwares path (OCI site templates → dpdata state volume, no device
sudo): see [`docs/WAVESHARE_MODBUS.md`](../../docs/WAVESHARE_MODBUS.md).
