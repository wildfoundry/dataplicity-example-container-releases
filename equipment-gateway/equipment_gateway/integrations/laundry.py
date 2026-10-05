"""LaundryMachine adapters. Simulation stays explicit; pulse profiles come from the catalogue."""
import json
import logging
import time
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from ..adapters import Outcome
from ..laundry_catalogue import get_machine_profile, list_machine_profiles
from ..physical import GpioIO, ModbusIO, SimulatedIO
from ..contract import capabilities as contract_capabilities, state_envelope

PULSE_ACTIVATIONS = frozenset({"start_pulse", "credit_pulse", "enable_pulse"})
CREDIT_PULSE_BACKENDS = frozenset({"gpio", "modbus_waveshare_flash", "modbus"})

CONTRACT = json.loads((Path(__file__).parents[1] / "contracts/laundry-machine.v1.json").read_text())
LOG = logging.getLogger("equipment-gateway.laundry")


class _MachineProfileMap(dict):
    """Lazy compatibility view for callers that import MACHINE_PROFILES."""

    def _refresh(self):
        self.clear()
        for row in list_machine_profiles():
            profile = get_machine_profile(row["machine_profile"])
            self[row["machine_profile"]] = {
                key: value for key, value in profile.items() if not key.startswith("_")
            }

    def __contains__(self, key):
        if not dict.__len__(self):
            self._refresh()
        return dict.__contains__(self, key)

    def __getitem__(self, key):
        if not dict.__len__(self):
            self._refresh()
        return dict.__getitem__(self, key)

    def keys(self):
        if not dict.__len__(self):
            self._refresh()
        return dict.keys(self)

    def items(self):
        if not dict.__len__(self):
            self._refresh()
        return dict.items(self)

    def values(self):
        if not dict.__len__(self):
            self._refresh()
        return dict.values(self)


MACHINE_PROFILES = _MachineProfileMap()


def capabilities(**kwargs):
    return contract_capabilities(CONTRACT, **kwargs)


def validate_action(action, payload):
    if action not in CONTRACT["actions"]:
        raise ValueError("Unknown LaundryMachine action")
    Draft202012Validator(CONTRACT["actions"][action]["input_schema"]).validate(payload)


def _profile(name):
    profile = get_machine_profile(name)
    return {key: value for key, value in profile.items() if not key.startswith("_")}


def _resolve_backend(credit_pulse):
    backend = credit_pulse.get("backend")
    if backend is None:
        if "chip" in credit_pulse or "line" in credit_pulse:
            return "gpio"
        if credit_pulse.get("kind") == "waveshare_flash" or "relay" in credit_pulse:
            return "modbus_waveshare_flash"
        if "duration_register" in credit_pulse:
            return "modbus"
        raise ValueError(
            "credit_pulse must set backend to gpio, modbus_waveshare_flash, or modbus "
            "(or declare chip/line for gpio)"
        )
    if backend not in CREDIT_PULSE_BACKENDS:
        raise ValueError(
            f"credit_pulse.backend must be one of {sorted(CREDIT_PULSE_BACKENDS)}"
        )
    return backend


def _validate_modbus_transport(transport):
    if not isinstance(transport, dict):
        raise ValueError("credit_pulse.transport must be a Modbus RTU transport object")
    if transport.get("kind") != "modbus_rtu":
        raise ValueError("credit_pulse.transport.kind must be modbus_rtu")
    for key in ("device", "address", "baudrate", "parity", "stopbits"):
        if key not in transport:
            raise ValueError(f"credit_pulse.transport requires {key}")
    if not isinstance(transport["device"], str) or not transport["device"].startswith("/dev/"):
        raise ValueError("credit_pulse.transport.device must be an explicit /dev/* path")
    if type(transport["address"]) is not int or not 1 <= transport["address"] <= 247:
        raise ValueError("credit_pulse.transport.address must be 1-247")


def _build_gpio_io(credit_pulse, *, channel, active_high, max_seconds, consumer, io, sleep):
    chip = credit_pulse.get("chip")
    line = credit_pulse.get("line")
    timing = credit_pulse.get("timing", "hybrid")
    if timing not in {"hybrid", "sleep"}:
        raise ValueError("credit_pulse.timing must be hybrid or sleep")
    channels = {
        channel: {
            "pulse_output": {
                "chip": chip,
                "line": line,
                "active_high": active_high,
                "host_timed": True,
                "max_seconds": max_seconds,
                "consumer": consumer,
                "timing": timing,
            }
        }
    }
    return io if io is not None else GpioIO(channels, sleep=sleep, timing=timing)


def _build_waveshare_io(credit_pulse, *, channel, max_seconds, io):
    _validate_modbus_transport(credit_pulse.get("transport"))
    relay = credit_pulse.get("relay")
    flash = credit_pulse.get("flash", "on")
    quantum = credit_pulse.get("time_quantum_seconds", 0.1)
    if type(relay) is not int or not 0 <= relay <= 31:
        raise ValueError("credit_pulse.relay must be an integer 0-31")
    if flash not in {"on", "off"}:
        raise ValueError("credit_pulse.flash must be 'on' or 'off'")
    channels = {
        channel: {
            "pulse_output": {
                "kind": "waveshare_flash",
                "relay": relay,
                "flash": flash,
                "max_seconds": max_seconds,
                "controller_timed": True,
                "time_quantum_seconds": quantum,
            }
        }
    }
    return io if io is not None else ModbusIO(deepcopy(credit_pulse["transport"]), channels)


def _build_modbus_timer_io(credit_pulse, *, channel, max_seconds, io):
    _validate_modbus_transport(credit_pulse.get("transport"))
    duration_register = credit_pulse.get("duration_register")
    trigger_coil = credit_pulse.get("trigger_coil")
    if type(duration_register) is not int or not 0 <= duration_register <= 65535:
        raise ValueError("credit_pulse.duration_register must be 0-65535")
    if type(trigger_coil) is not int or not 0 <= trigger_coil <= 65535:
        raise ValueError("credit_pulse.trigger_coil must be 0-65535")
    channels = {
        channel: {
            "pulse_output": {
                "duration_register": duration_register,
                "trigger_coil": trigger_coil,
                "max_seconds": max_seconds,
                "controller_timed": True,
            }
        }
    }
    return io if io is not None else ModbusIO(deepcopy(credit_pulse["transport"]), channels)


class Simulator:
    """Two capability subsets bind to the same contract; never a compatibility claim."""
    def __init__(self, *, mode="native", outcome="accepted", service_delivered=None):
        if mode not in {"native", "pulse"}:
            raise ValueError("Simulator mode must be native or pulse")
        self.mode = mode
        self.outcome = Outcome(outcome)
        if service_delivered is not None and type(service_delivered) is not bool:
            raise ValueError("Simulated delivery evidence must be boolean or absent")
        self.service_delivered = service_delivered
        self.calls = 0
        self.io = SimulatedIO()
        self.capabilities = capabilities(
            properties=("availability", "operating_state", "remaining_seconds", "door_locked")
            if mode == "native" else ("availability",),
            actions=("vend", "start", "add_time", "free_vend", "reset", "set_out_of_service", "set_price", "set_programme")
            if mode == "native" else ("vend",),
        )

    def validate(self, action, payload):
        validate_action(action, payload)
        if payload["machine_id"] != payload["equipment_id"]:
            raise ValueError("Semantic machine target differs from configured equipment")

    def state(self, *, observed_at, now):
        return state_envelope(CONTRACT, self.capabilities, self.observe(), observed_at=observed_at, now=now)

    def execute(self, action, payload):
        self.calls += 1
        if self.outcome.certainty == "accepted":
            # Fictional simulator mappings, never an OEM protocol/wiring claim.
            if self.mode == "pulse":
                self.io.execute("pulse_output", {"channel": "simulated-credit", "duration_seconds": 0.1})
            else:
                self.io.execute("serial_transfer", {"request_hex": ("simulated:" + action).encode().hex(), "response_bytes": 0})
        if self.outcome.certainty == "accepted" and self.service_delivered is not None:
            return Outcome("accepted", result={"service_delivered": self.service_delivered,
                "evidence": {"kind": "simulated", "action": action}})
        return self.outcome

    def observe(self):
        # Simulated process restarts cannot prove a real machine's cycle state.
        return {"availability": "unknown"} if self.mode == "pulse" else {
            "availability": "available", "operating_state": "idle",
            "remaining_seconds": 0, "door_locked": False,
        }


class PulseActivated:
    """Pulse-activated laundry family with pluggable credit-pulse backends.

    Backends:

    - ``gpio`` — host-timed CM5/Pi header GPIO (bench/scope). Default timing is
      hybrid sleep + busy-wait tail. Not a fail-safe remote I/O timer.
    - ``modbus_waveshare_flash`` — Waveshare Modbus RTU Relay flash-on/off
      (controller-timed, 100 ms quantum). Preferred when the site uses Waveshare
      RS485 I/O boards.
    - ``modbus`` — generic controller-timed duration register + trigger coil.
    """

    def __init__(
        self,
        *,
        machine_profile,
        credit_pulse,
        pulses_per_vend=None,
        duration_seconds=None,
        gap_seconds=None,
        io=None,
        sleep=None,
    ):
        self.profile = _profile(machine_profile)
        if self.profile["activation"] not in PULSE_ACTIVATIONS:
            raise ValueError(
                f"{self.profile['machine_profile']!r} activation={self.profile['activation']!r} "
                "is not host-pulseable; choose a start_pulse/credit_pulse/enable_pulse family"
            )
        self.machine_profile = self.profile["machine_profile"]
        self.channel = self.profile["channel"]
        duration = self.profile["default_duration_seconds"] if duration_seconds is None else duration_seconds
        gap = self.profile.get("default_gap_seconds", 0.0) if gap_seconds is None else gap_seconds
        pulses = self.profile["default_pulses_per_vend"] if pulses_per_vend is None else pulses_per_vend
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < float(duration) <= 2:
            raise ValueError("Pulse duration_seconds must be greater than zero and at most two seconds")
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not 0 <= float(gap) <= 2:
            raise ValueError("Pulse gap_seconds must be from zero to two seconds")
        if type(pulses) is not int or not 1 <= pulses <= 20:
            raise ValueError("pulses_per_vend must be an integer from 1 to 20")
        if not isinstance(credit_pulse, dict):
            raise ValueError("credit_pulse must declare the physical backend for the start/credit pulse")

        self.backend = _resolve_backend(credit_pulse)
        if "active_high" in credit_pulse:
            active_high = credit_pulse.get("active_high")
        else:
            active_high = self.profile.get("default_active_high", True)
        consumer = credit_pulse.get("consumer", "rinsepilot-credit")
        max_seconds = credit_pulse.get("max_seconds", 2.0)
        if active_high is not True and active_high is not False:
            raise ValueError("credit_pulse.active_high must be boolean")

        self.duration_seconds = float(duration)
        self.gap_seconds = float(gap)
        self.pulses_per_vend = pulses
        self.calls = 0
        self._sleep = sleep or time.sleep
        self.credit_pulse = deepcopy(credit_pulse)

        if self.backend == "gpio":
            self.io = _build_gpio_io(
                credit_pulse,
                channel=self.channel,
                active_high=active_high,
                max_seconds=max_seconds,
                consumer=consumer,
                io=io,
                sleep=sleep,
            )
            self._evidence_kind = "gpio_host_timed_pulse"
            self._backend_detail = {
                "chip": credit_pulse.get("chip"),
                "line": credit_pulse.get("line"),
                "timing": credit_pulse.get("timing", "hybrid"),
                "active_high": active_high,
            }
        elif self.backend == "modbus_waveshare_flash":
            self.io = _build_waveshare_io(
                credit_pulse, channel=self.channel, max_seconds=max_seconds, io=io
            )
            self._evidence_kind = "modbus_waveshare_controller_timed_flash"
            self._backend_detail = {
                "transport_device": credit_pulse["transport"]["device"],
                "address": credit_pulse["transport"]["address"],
                "relay": credit_pulse.get("relay"),
                "flash": credit_pulse.get("flash", "on"),
                "time_quantum_seconds": credit_pulse.get("time_quantum_seconds", 0.1),
            }
        else:
            self.io = _build_modbus_timer_io(
                credit_pulse, channel=self.channel, max_seconds=max_seconds, io=io
            )
            self._evidence_kind = "modbus_controller_timed_pulse"
            self._backend_detail = {
                "transport_device": credit_pulse["transport"]["device"],
                "address": credit_pulse["transport"]["address"],
                "duration_register": credit_pulse.get("duration_register"),
                "trigger_coil": credit_pulse.get("trigger_coil"),
            }

        # Validate channel mapping at construction so --check fails closed.
        self.io.validate("pulse_output", {"channel": self.channel, "duration_seconds": self.duration_seconds})
        self.capabilities = capabilities(
            properties=("availability", "manufacturer", "model"),
            actions=("vend",),
            provenance="machine_native",
            version=CONTRACT["version"],
        )
        LOG.info(
            "laundry_pulse_profile_ready machine_profile=%r manufacturer=%s model=%s status=%s "
            "activation=%s backend=%s duration_s=%.3f gap_s=%.3f pulses_per_vend=%s detail=%s",
            self.machine_profile,
            self.profile["manufacturer"],
            self.profile["model"],
            self.profile["status"],
            self.profile["activation"],
            self.backend,
            self.duration_seconds,
            self.gap_seconds,
            self.pulses_per_vend,
            self._backend_detail,
        )

    def bind_resources(self, resources):
        if isinstance(self.io, ModbusIO):
            self.io.resources = resources

    def validate(self, action, payload):
        if action != "vend":
            raise ValueError("Pulse-activated laundry profile only supports vend")
        validate_action(action, payload)
        if payload["machine_id"] != payload["equipment_id"]:
            raise ValueError("Semantic machine target differs from configured equipment")

    def state(self, *, observed_at, now):
        return state_envelope(CONTRACT, self.capabilities, self.observe(), observed_at=observed_at, now=now)

    def execute(self, action, payload):
        self.validate(action, payload)
        self.calls += 1
        LOG.info(
            "laundry_vend_pulse_begin machine_profile=%r equipment_id=%s vend_id=%s "
            "order_id=%s backend=%s pulses=%s duration_s=%.3f",
            self.machine_profile,
            payload.get("equipment_id"),
            payload.get("vend_id"),
            payload.get("order_id"),
            self.backend,
            self.pulses_per_vend,
            self.duration_seconds,
        )
        pulses = []
        for index in range(self.pulses_per_vend):
            result = self.io.execute(
                "pulse_output",
                {"channel": self.channel, "duration_seconds": self.duration_seconds},
            )
            pulses.append({
                "index": index,
                "channel": self.channel,
                "duration_seconds": self.duration_seconds,
                "gap_seconds": self.gap_seconds if index + 1 < self.pulses_per_vend else 0.0,
                "certainty": getattr(result, "certainty", "accepted"),
                "reason": getattr(result, "reason", ""),
            })
            LOG.info(
                "laundry_vend_pulse_edge machine_profile=%r equipment_id=%s pulse_index=%s/%s backend=%s",
                self.machine_profile,
                payload.get("equipment_id"),
                index + 1,
                self.pulses_per_vend,
                self.backend,
            )
            if index + 1 < self.pulses_per_vend and self.gap_seconds > 0:
                self._sleep(self.gap_seconds)
        LOG.info(
            "laundry_vend_pulse_complete machine_profile=%r equipment_id=%s vend_id=%s pulses=%s backend=%s",
            self.machine_profile,
            payload.get("equipment_id"),
            payload.get("vend_id"),
            self.pulses_per_vend,
            self.backend,
        )
        return Outcome(
            "accepted",
            "pulse_activated_credit_dispatched",
            {
                "machine_profile": self.machine_profile,
                "activation": self.profile["activation"],
                "pulses": pulses,
                "evidence": {
                    "kind": self._evidence_kind,
                    "backend": self.backend,
                    "channel": self.channel,
                    "duration_seconds": self.duration_seconds,
                    "pulses_per_vend": self.pulses_per_vend,
                    **self._backend_detail,
                    # Bench evidence only; not OEM machine cycle confirmation.
                    "service_delivered": False,
                },
            },
        )

    def observe(self):
        return {
            "availability": "unknown",
            "manufacturer": self.profile["manufacturer"],
            "model": self.profile["model"],
        }

    def close(self):
        close = getattr(self.io, "close", None)
        if callable(close):
            close()


def manufacturer_adapter(manufacturer):
    raise ValueError(
        f"{manufacturer}: no verified protocol/profile is bundled; "
        "add a laundry_profiles/*.json family or select PulseActivated machine_profile"
    )
