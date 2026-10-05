"""Equipment-neutral bounded physical primitives. No semantic interpretation.

Simulators use this same validation/execution interface. Modbus pulse output
requires a controller-owned timer. Lab GPIO pulse output is an explicit
host-timed bench path (`host_timed=true`); it is not a fail-safe timer.
"""
from copy import deepcopy
import logging
import math
import time

from .adapters import Outcome
from .gpio_chip import GpioLineSession, hybrid_wait, pulse_line

LOG = logging.getLogger("equipment-gateway.physical")

_CLASSIC_PULSE_KEYS = frozenset({"duration_register", "trigger_coil", "max_seconds", "controller_timed"})
_WAVESHARE_PULSE_KEYS = frozenset({
    "kind", "relay", "flash", "max_seconds", "controller_timed", "time_quantum_seconds",
})


class PrimitiveError(ValueError):
    pass


ARGUMENTS = {
    "read_input": {"channel"}, "read_counter": {"channel"},
    "write_output": {"channel", "value"}, "pulse_output": {"channel", "duration_seconds"},
    "modbus_read": {"register", "count"}, "modbus_write": {"register", "values"},
    "serial_transfer": {"request_hex", "response_bytes"},
}


def validate_primitive(operation, arguments):
    if operation not in ARGUMENTS or set(arguments) != ARGUMENTS[operation]:
        raise PrimitiveError("Unknown primitive or invalid argument set")
    if "channel" in arguments and (not isinstance(arguments["channel"], str) or not arguments["channel"] or len(arguments["channel"]) > 128):
        raise PrimitiveError("An explicit I/O channel is required")
    if operation == "write_output" and type(arguments["value"]) is not bool:
        raise PrimitiveError("Output values must be boolean")
    if operation == "pulse_output":
        duration = arguments["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 10:
            raise PrimitiveError("Pulse duration must be from greater than zero to ten seconds")
    if operation in {"modbus_read", "modbus_write"}:
        register = arguments["register"]
        if type(register) is not int or not 0 <= register <= 65535:
            raise PrimitiveError("Invalid register address")
        values = arguments.get("values")
        count = arguments.get("count", len(values) if isinstance(values, list) else 0)
        if type(count) is not int or not 1 <= count <= 123 or register + count > 65536:
            raise PrimitiveError("Register request exceeds bounds")
        if operation == "modbus_write" and (not isinstance(values, list) or any(type(v) is not int or not 0 <= v <= 65535 for v in values)):
            raise PrimitiveError("Invalid register values")
    if operation == "serial_transfer":
        try:
            data = bytes.fromhex(arguments["request_hex"])
        except (ValueError, TypeError):
            raise PrimitiveError("Invalid serial request") from None
        size = arguments["response_bytes"]
        if not data or len(data) > 256 or type(size) is not int or not 0 <= size <= 256:
            raise PrimitiveError("Serial transfer exceeds bounded request/response sizes")


class SimulatedIO:
    """Explicit in-memory test endpoint, using real primitive validation."""
    def __init__(self, *, inputs=None, counters=None, registers=None):
        self.inputs, self.counters, self.registers = dict(inputs or {}), dict(counters or {}), dict(registers or {})
        self.outputs, self.operations = {}, []

    def validate(self, operation, arguments):
        validate_primitive(operation, arguments)

    def execute(self, operation, arguments):
        self.validate(operation, arguments)
        self.operations.append((operation, deepcopy(arguments)))
        if operation == "read_input": return self.inputs.get(arguments["channel"])
        if operation == "read_counter": return self.counters.get(arguments["channel"])
        if operation == "write_output": self.outputs[arguments["channel"]] = arguments["value"]
        elif operation == "pulse_output":
            # Simulation records the bounded timer operation; it does not sleep.
            self.outputs[arguments["channel"]] = False
        elif operation == "modbus_write":
            for offset, value in enumerate(arguments["values"]): self.registers[arguments["register"] + offset] = value
        elif operation == "modbus_read":
            return [self.registers.get(arguments["register"] + offset, 0) for offset in range(arguments["count"])]
        elif operation == "serial_transfer": return bytes(arguments["response_bytes"])
        return Outcome("accepted", "simulated_physical_operation")


class ModbusIO:
    """Declared channels over an isolated remote I/O controller, lazily opened."""
    def __init__(self, transport, channels, resources=None):
        self.transport, self.channels = deepcopy(transport), deepcopy(channels)
        self.resources = resources if resources is not None else {}

    def _bus(self):
        from .modbus import ModbusTransport
        key = self.transport["device"]
        if key not in self.resources:
            self.resources[key] = ModbusTransport(self.transport)
        return self.resources[key]

    def validate(self, operation, arguments):
        validate_primitive(operation, arguments)
        if operation == "serial_transfer":
            raise PrimitiveError("Raw serial transfer requires a serial transport adapter")
        if "channel" in arguments:
            channel = self.channels.get(arguments["channel"], {})
            if operation not in channel:
                raise PrimitiveError("I/O channel does not declare this operation")
            if operation == "pulse_output":
                mapping = channel[operation]
                # Controller-owned timers survive host failure; no host-timed coils.
                if mapping.get("controller_timed") is not True:
                    raise PrimitiveError("Pulse output requires an explicitly declared controller-owned timer")
                if not 0.001 <= arguments["duration_seconds"] <= mapping["max_seconds"]:
                    raise PrimitiveError("Pulse exceeds configured controller timer")
                if mapping.get("kind") == "waveshare_flash":
                    keys = set(mapping)
                    if keys != _WAVESHARE_PULSE_KEYS and keys != _WAVESHARE_PULSE_KEYS - {"time_quantum_seconds"}:
                        raise PrimitiveError(
                            "Waveshare flash pulse_output requires kind, relay, flash, "
                            "max_seconds, controller_timed (optional time_quantum_seconds)"
                        )
                    if type(mapping["relay"]) is not int or not 0 <= mapping["relay"] <= 31:
                        raise PrimitiveError("Invalid Waveshare relay index")
                    if mapping.get("flash") not in {"on", "off"}:
                        raise PrimitiveError("Waveshare flash mode must be 'on' or 'off'")
                    quantum = mapping.get("time_quantum_seconds", 0.1)
                    if isinstance(quantum, bool) or not isinstance(quantum, (int, float)) or not 0 < float(quantum) <= 1:
                        raise PrimitiveError("Invalid Waveshare time_quantum_seconds")
                    from .modbus import waveshare_flash_units
                    try:
                        waveshare_flash_units(arguments["duration_seconds"], time_quantum_seconds=quantum)
                    except ValueError as exc:
                        raise PrimitiveError(str(exc)) from None
                elif set(mapping) == _CLASSIC_PULSE_KEYS:
                    for key in ("duration_register", "trigger_coil"):
                        if type(mapping[key]) is not int or not 0 <= mapping[key] <= 65535:
                            raise PrimitiveError("Invalid pulse timer mapping")
                else:
                    raise PrimitiveError("Pulse output requires an explicitly declared controller-owned timer")
            elif type(channel[operation]) is not int or not 0 <= channel[operation] <= 65535:
                raise PrimitiveError("Invalid channel register mapping")

    def execute(self, operation, arguments):
        self.validate(operation, arguments)
        bus = self._bus()
        channel = self.channels.get(arguments.get("channel"), {})
        if operation == "read_input": return bus.read_input(channel[operation], address=self.transport["address"])
        if operation == "read_counter":
            words = bus.read({"register": channel[operation], "function": 3, "datatype": "uint32"}, address=self.transport["address"])
            return (words[0] << 16) | words[1]
        if operation == "write_output": bus.write_output(channel[operation], arguments["value"], address=self.transport["address"])
        elif operation == "pulse_output":
            mapping = channel[operation]
            if mapping.get("kind") == "waveshare_flash":
                quantum = mapping.get("time_quantum_seconds", 0.1)
                LOG.info(
                    "modbus_waveshare_flash channel=%s relay=%s flash=%s duration_s=%.3f quantum_s=%.3f controller_timed=true",
                    arguments["channel"],
                    mapping["relay"],
                    mapping["flash"],
                    arguments["duration_seconds"],
                    quantum,
                )
                bus.flash_output(
                    mapping["relay"],
                    arguments["duration_seconds"],
                    flash=mapping["flash"],
                    address=self.transport["address"],
                    time_quantum_seconds=quantum,
                )
                return Outcome("accepted", "waveshare_controller_timed_flash_acknowledged")
            duration_ms = round(arguments["duration_seconds"] * 1000)
            if duration_ms < 1: raise PrimitiveError("Pulse timer must be at least one millisecond")
            bus.write_registers(mapping["duration_register"], [duration_ms], address=self.transport["address"])
            bus.write_output(mapping["trigger_coil"], True, address=self.transport["address"])
        elif operation == "modbus_write": bus.write_registers(arguments["register"], arguments["values"], address=self.transport["address"])
        elif operation == "modbus_read":
            # Read count can exceed the datatypes used by observation profiles.
            values = []
            for offset in range(arguments["count"]):
                values.extend(bus.read({"register": arguments["register"] + offset, "function": 3, "datatype": "uint16"}, address=self.transport["address"]))
            return values
        return Outcome("accepted", "physical_transport_acknowledged")

    def read_point(self, point):
        return self._bus().read(point, address=self.transport["address"])


class SerialIO:
    def __init__(self, transport, resources=None):
        self.transport = deepcopy(transport)
        self.resources = resources if resources is not None else {}

    def validate(self, operation, arguments):
        validate_primitive(operation, arguments)
        if operation != "serial_transfer":
            raise PrimitiveError("This endpoint declares a raw serial transport")

    def execute(self, operation, arguments):
        from .serial_transport import SerialTransport
        self.validate(operation, arguments)
        key = self.transport["device"]
        if key not in self.resources:
            self.resources[key] = SerialTransport(self.transport)
        return self.resources[key].transfer(bytes.fromhex(arguments["request_hex"]), arguments["response_bytes"])


class GpioIO:
    """Declared GPIO lines on an isolated gpiochip node.

    `pulse_output` is host-timed and must set `host_timed=true` in the channel
    mapping. Use this for bench/scope verification on a CM5 IO header pin until
    remote I/O with a controller-owned timer is available. Default wait timing
    is hybrid (sleep + short busy-wait tail); set ``timing`` to ``sleep`` for
    plain sleep (useful in tests).
    """

    def __init__(self, channels, *, session=None, sleep=None, wait=None, timing="hybrid"):
        self.channels = deepcopy(channels)
        self.session = session if session is not None else GpioLineSession()
        self._sleep = sleep or time.sleep
        self._timing = timing
        if wait is not None:
            self._wait = wait
        elif sleep is not None:
            # Explicit sleep injection (tests) replaces the whole wait path.
            self._wait = sleep
        elif timing == "sleep":
            self._wait = self._sleep
        else:
            self._wait = lambda seconds: hybrid_wait(seconds, sleep=self._sleep)
        self.operations = []

    def validate(self, operation, arguments):
        validate_primitive(operation, arguments)
        if operation not in {"write_output", "pulse_output"}:
            raise PrimitiveError("GPIO transport only supports write_output and pulse_output")
        channel = self.channels.get(arguments["channel"], {})
        if operation not in channel:
            raise PrimitiveError("I/O channel does not declare this operation")
        mapping = channel[operation]
        if operation == "write_output":
            required = {"chip", "line", "active_high"}
            if set(mapping) != required and set(mapping) != required | {"consumer"}:
                raise PrimitiveError("GPIO write_output requires chip, line, active_high")
        else:
            required = {"chip", "line", "active_high", "host_timed", "max_seconds"}
            allowed = (
                required,
                required | {"consumer"},
                required | {"timing"},
                required | {"consumer", "timing"},
            )
            if set(mapping) not in allowed:
                raise PrimitiveError("GPIO pulse_output requires chip, line, active_high, host_timed, max_seconds")
            if mapping.get("host_timed") is not True:
                raise PrimitiveError("GPIO pulse_output requires explicit host_timed=true for bench actuation")
            timing = mapping.get("timing", "hybrid")
            if timing not in {"hybrid", "sleep"}:
                raise PrimitiveError("GPIO pulse timing must be hybrid or sleep")
            max_seconds = mapping["max_seconds"]
            if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds):
                raise PrimitiveError("Invalid GPIO pulse max_seconds")
            if not 0.001 <= arguments["duration_seconds"] <= float(max_seconds) <= 10:
                raise PrimitiveError("Pulse exceeds configured GPIO host timer")
        chip = mapping["chip"]
        line = mapping["line"]
        if not isinstance(chip, str) or not chip.startswith("/dev/gpiochip"):
            raise PrimitiveError("GPIO chip must be an explicit /dev/gpiochip* path")
        if type(line) is not int or not 0 <= line < 512:
            raise PrimitiveError("GPIO line offset is out of bounds")
        if type(mapping["active_high"]) is not bool:
            raise PrimitiveError("GPIO active_high must be boolean")
        consumer = mapping.get("consumer", "equipment-gateway")
        if not isinstance(consumer, str) or not 0 < len(consumer) <= 31:
            raise PrimitiveError("GPIO consumer label must be 1-31 characters")

    def execute(self, operation, arguments):
        self.validate(operation, arguments)
        self.operations.append((operation, deepcopy(arguments)))
        mapping = self.channels[arguments["channel"]][operation]
        handle = self.session.handle(
            chip=mapping["chip"],
            line=mapping["line"],
            consumer=mapping.get("consumer", "equipment-gateway"),
            active_high=mapping["active_high"],
        )
        if operation == "write_output":
            LOG.info(
                "gpio_write_output channel=%s chip=%s line=%s value=%s active_high=%s",
                arguments["channel"],
                mapping["chip"],
                mapping["line"],
                arguments["value"],
                mapping["active_high"],
            )
            handle.set_active(arguments["value"])
            return Outcome("accepted", "gpio_write_acknowledged")
        duration = float(arguments["duration_seconds"])
        timing = mapping.get("timing", self._timing)
        LOG.info(
            "gpio_pulse_activate channel=%s chip=%s line=%s duration_s=%.3f active_high=%s host_timed=true timing=%s",
            arguments["channel"],
            mapping["chip"],
            mapping["line"],
            duration,
            mapping["active_high"],
            timing,
        )
        wait = self._wait
        if timing == "sleep" and wait is not self._sleep:
            # Channel requested plain sleep while constructor defaulted to hybrid.
            wait = self._sleep
        try:
            pulse_line(handle, duration_seconds=duration, wait=wait)
        except Exception:
            LOG.exception(
                "gpio_pulse_failed channel=%s chip=%s line=%s duration_s=%.3f",
                arguments["channel"],
                mapping["chip"],
                mapping["line"],
                duration,
            )
            raise
        LOG.info(
            "gpio_pulse_complete channel=%s chip=%s line=%s duration_s=%.3f",
            arguments["channel"],
            mapping["chip"],
            mapping["line"],
            duration,
        )
        return Outcome("accepted", "gpio_host_timed_pulse_complete")

    def close(self):
        self.session.close()
