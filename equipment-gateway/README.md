# Equipment gateway

Reusable, credential-free managed Software for physical-world Product
Applications. The existing Dataplicity agent owns authentication, command
transport and durable event upload. The gateway supplies equipment-neutral
transport/I/O, profile execution, adapter hosting and a bounded physical-effect
journal. Device Classes and Product Processes supply semantic/business meaning.

## Architectural boundary

The runtime, physical primitives, journal, configuration and generic events do
not interpret vertical state, transactions or service delivery. Installed
integrations live under `equipment_gateway.integrations`; configuration names
an explicit plugin and its options. No plugin is downloaded at runtime.

`config.example.json` selects the LaundryMachine simulator adapter.
`config.access-control.example.json` selects a declarative AccessController
profile. `config.site.example.json` + `equipment.example/*.json` show how to
scale to many laundry endpoints: one JSON file per machine, each naming a
catalogue `machine_profile`. Bundled families cover common commercial brands
(Alliance/Speed Queen/Huebsch/UniMac/IPSO, Dexter, Maytag, ADC, LG, Girbau,
Primus/MXR, Electrolux dryer pulse, plus serial-native catalogue markers).
Timings cite public OEM/payment-installer docs — see
`laundry_profiles/CATALOGUE.md`. Extend on-device via `machine_profiles_dir`
or `LAUNDRY_PROFILES_DIR` without rebuilding the image.

All examples use the **same executable/container**, runtime, primitive engine,
agent connection and journal. The access example exercises a controller-timed
relay pulse, digital observation and register operations without core changes.
The Speed Queen GPIO example is a bench/lab path for oscilloscope verification;
it is not an OEM wiring claim and is not a fail-safe remote I/O timer. GPIO
credit pulses default to hybrid wait (sleep + short busy-wait tail) on the CM5
header. When the site runs Waveshare Modbus RTU Relay / IO boards, set
`credit_pulse.backend` to `modbus_waveshare_flash` so the **controller** owns
the pulse timer (Waveshare FC05 flash-on, 100 ms quantum) — see
`config.waveshare-modbus.example.json`. Sub-100 ms machine profiles need an
explicit `duration_seconds` override that is a multiple of 0.1 s (only when the
OEM accepts the longer pulse).

```sh
# Track configured families and site machines
equipment-gateway --list-machine-profiles
equipment-gateway --config config.site.example.json --list-equipment
equipment-gateway --config config.site.example.json --check
```

To add another washer/dryer on a controller: drop
`/etc/equipment-gateway/equipment/<id>.json` (see `equipment.example/`), point
`equipment_dir` at that folder, and re-run `--check`. To add another actuation
family: copy `laundry_profiles/speed-queen-pulse-activated.json`, set a unique
`machine_profile`, then `--list-machine-profiles`.

## Commands and persistence

Configuration schema version 2 declares `equipment` endpoints with unique
`equipment_id`, `adapter` and `options`. Commands use the existing ProductCommand
envelope with `product_instance_id`, `device_action_key` or `command_key`, and
`safety=unsafe_to_duplicate`. Input must contain generic `equipment_id` and
immutable `effect_id`. An adapter validates the remaining opaque semantic input.
A declarative profile places its typed values under `parameters`.

Deduplication uses instance, equipment, effect and action. A retry retains its
effect ID even if the cloud assigns a new invocation ID. Separate physical
effects receive separate IDs. Input fingerprints reject changed parameters.
SQLite reserves and commits before I/O. Interrupted dispatch remains unknown;
restarts replay outcomes, never physical operations. Capacity exhaustion fails
closed, and an exclusive writer lock prevents concurrent gateways sharing state.

The unreleased development prototype used a different journal schema. Opening
that prototype journal fails closed and preserves the file. A reviewed migration
is required; deleting evidence to make an upgrade start is unsafe. This is not an
upgrade path for an already published gateway release.

Generic events are `equipment.gateway_inventory`, `equipment.state`,
`equipment.observation` and `equipment.command_outcome`. Command acceptance proves
only the physical/adapter operation was acknowledged. Business service delivery
requires separate evidence interpreted by its Device Class/Product Process.

## Physical primitives and profiles

Supported primitives are `read_input`, `write_output`, `pulse_output`,
`read_counter`, `modbus_read`, `modbus_write` and simulated `serial_transfer`.
Simulation is explicit. Modbus RTU supports read functions 03/04, digital inputs
01/02, coil writes 05 and register writes 06/16 with CRC, responder and echo
validation. Profiles fix I/O channels/register mappings; command parameters have
closed JSON schemas and bounded values. Serial request/response sizes and pulse
durations are bounded.

Physical actuation through the declarative Modbus adapter requires an
`io_channels` mapping and an evidence-backed profile. A timed physical output
requires an explicitly declared controller-owned timer; no host sleep holds a
relay on. Raw physical RS232/RS485 protocol adapters remain a separate integration
step; simulated serial transfer does not claim a manufacturer protocol.

Field profiles are canonical in Prelude and exported to this repository. They
include model/revision applicability, transport, semantic points and optional
validated command steps. Initial instrument vocabularies constrain EnergyMeter,
FlowMeter, TemperatureSensor, AmbientSensor and LeakSensor; another Device Class
can supply its own vocabulary unchanged. Every observation preserves raw values
and the exact profile/conversion version and digest. Scale/offset or versioned
piecewise calibration tables derive values without rewriting historical evidence.
The embedded `profile` is a point-scoped snapshot (`profile_snapshot_scope=point`),
containing the selected conversion and its applicability/verification evidence;
`profile_sha256` identifies the complete original profile, including its other
points and commands. Individual snapshots must fit 32 KiB. This prevents a large
multi-point profile from being repeated in every observation.

Inventory declarations are republished every 30 seconds and split into events
of at most 48 KiB and 100 entries per section. Republishing capabilities does
not refresh the timestamps of cached physical measurements. Equipment state is
an atomic projection, so profiles whose aggregate state exceeds the event budget
are rejected before opening hardware; declare large measurement banks as separate
instruments. All outgoing events have a 48 KiB byte limit. Each tick fetches at
most eight commands and starts no further effects after its two-second command
budget. An effect already underway finishes and is journalled before polling
resumes; transport timeouts still bound that individual effect.

Modbus addresses are zero-based protocol addresses, not `40001` notation. Bus
addresses must be unique and serial settings consistent. Observations distinguish
missing, stale, invalid, disconnected, CRC/timeout and device errors. Missing
optional instrumentation does not stop other configured endpoints.

## Development and qualification

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/equipment-gateway --config config.example.json --check
.venv/bin/equipment-gateway --config config.access-control.example.json --check
.venv/bin/equipment-gateway --config config.site.example.json --check
.venv/bin/equipment-gateway --config config.waveshare-modbus.example.json --check
.venv/bin/equipment-gateway --list-machine-profiles
```

Waveshare Modbus RTU Relay (controller-timed flash) is managed Softwares:
OCI `site_templates/` materialize into `/var/lib/equipment-gateway` on start
(dpdata). No device-side sudo. See [docs/WAVESHARE_MODBUS.md](docs/WAVESHARE_MODBUS.md).

```sh
# After Softwares ≥0.2.4 is on the device and the USB-RS485 adapter is present:
equipment-gateway --probe
```

Set `PYTHONPATH` to a reviewed Prelude checkout to run the real agent Unix-socket
contract test. Tests cover duplicate effects, reboot/acknowledgement loss,
immutable request conflicts, non-laundry reuse, conversion history and wire frames.

Use Dataplicity-OS containerd/nerdctl and normal managed Software lifecycle. Bind
approved isolated devices, read-only config, persistent application storage and
`/run/dataplicity` for the agent socket. Workload UID/GID is 65532; grant actual
numeric socket/serial supplementary groups. No privileged containers, direct
unprotected GPIO or embedded cloud/Stripe credentials.

The OS host recipe only prepares persistent storage after dpdata mounts; it does
not contain application source. Release through the existing verified OCI
Software mechanism. `contracts/provenance.json` must identify the reviewed cloud
contract commit before publication. Physical boot, OTA/rollback, isolated I/O and
OEM qualification remain explicit evidence gates.
