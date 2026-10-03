"""Equipment-neutral bounded physical primitives. No semantic interpretation.

Simulators use this same validation/execution interface. Hardware pulse output
requires a controller-owned timer; host sleeps are never a fail-safe output timer.
"""
from copy import deepcopy
import math

from .adapters import Outcome


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
                if set(mapping) != {"duration_register", "trigger_coil", "max_seconds", "controller_timed"} or mapping["controller_timed"] is not True:
                    raise PrimitiveError("Pulse output requires an explicitly declared controller-owned timer")
                if not 0.001 <= arguments["duration_seconds"] <= mapping["max_seconds"]:
                    raise PrimitiveError("Pulse exceeds configured controller timer")
                for key in ("duration_register", "trigger_coil"):
                    if type(mapping[key]) is not int or not 0 <= mapping[key] <= 65535:
                        raise PrimitiveError("Invalid pulse timer mapping")
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
