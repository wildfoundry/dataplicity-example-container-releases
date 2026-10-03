"""Declarative equipment integration over reusable physical primitives."""
from copy import deepcopy

from jsonschema import Draft202012Validator

from ..adapters import Outcome
from ..physical import SimulatedIO, ModbusIO, SerialIO
from ..modbus import TransportError
from ..profiles import validate_profile, observe


class ProfileAdapter:
    def __init__(self, *, profile, inputs=None, counters=None, registers=None, backend="simulated"):
        self.profile = validate_profile(profile)
        self.backend = backend
        if backend == "simulated" and self.profile["verification"]["status"] == "simulated":
            self.io = SimulatedIO(inputs=inputs, counters=counters, registers=registers)
        elif backend == "modbus" and self.profile["transport"]["kind"] == "modbus_rtu" and self.profile["verification"]["status"] in {"bench_verified", "physical_verified"}:
            self.io = ModbusIO(self.profile["transport"], self.profile.get("io_channels", {}))
        elif backend == "serial" and self.profile["transport"]["kind"] == "serial" and self.profile["verification"]["status"] in {"bench_verified", "physical_verified"}:
            self.io = SerialIO(self.profile["transport"])
        else:
            raise ValueError("Physical actuation requires an evidence-backed transport profile; simulation must be explicit")
        self.calls = 0
        self.capabilities = {"contract": self.profile["device_class"], "version": self.profile["version"],
            "failure_certainty": "acknowledged_reject_and_unknown", "reconciliation": "unsupported",
            "actions": {key: {"support": "supported", "provenance": "simulated" if backend == "simulated" else "machine_native", "capability_version": self.profile["version"]} for key in self.profile.get("commands", {})},
            "properties": {point["semantic_property"]: {"support": "supported", "provenance": "simulated" if backend == "simulated" else "machine_native", "capability_version": self.profile["version"], "max_age_seconds": point["max_age_seconds"]} for point in self.profile["points"]}}

    def bind_resources(self, resources):
        if isinstance(self.io, (ModbusIO, SerialIO)):
            self.io.resources = resources

    def _arguments(self, step, parameters):
        return {key: parameters[value["input"]] if isinstance(value, dict) and set(value) == {"input"} else deepcopy(value)
                for key, value in step["arguments"].items()}

    def validate(self, action, payload):
        spec = self.profile.get("commands", {}).get(action)
        if spec is None:
            raise ValueError("Unsupported profile command")
        parameters = payload.get("parameters", {})
        Draft202012Validator(spec["input_schema"]).validate(parameters)
        for step in spec["steps"]:
            self.io.validate(step["operation"], self._arguments(step, parameters))

    def execute(self, action, payload):
        self.validate(action, payload)
        self.calls += 1
        results = []
        for index, step in enumerate(self.profile["commands"][action]["steps"]):
            value = self.io.execute(step["operation"], self._arguments(step, payload.get("parameters", {})))
            if isinstance(value, bytes): value = {"response_hex": value.hex()}
            elif isinstance(value, Outcome): value = {"certainty": value.certainty, "reason": value.reason}
            results.append({"index": index, "operation": step["operation"], "value": value})
        return Outcome("accepted", "profile_operations_complete", {"operations": results})

    def state(self, *, observed_at, now):
        values = {}
        for point in self.profile["points"]:
            error, raw = "", None
            try:
                if isinstance(self.io, ModbusIO):
                    raw = self.io.read_point(point)
                else:
                    channel = point.get("source_key", point["key"])
                    raw = self.io.inputs.get(channel) if point["datatype"] == "boolean" else self.io.counters.get(channel) if point["datatype"] == "counter" else [self.io.registers.get(point.get("register", 0) + n, 0) for n in range(2 if point["datatype"] in {"uint32", "int32", "float32"} else 1)]
            except TransportError as exc:
                error = exc.quality
            except OSError:
                error = "disconnected"
            values[point["semantic_property"]] = observe(self.profile, point["key"], raw, observed_at, error=error).derive(now)
        return {"contract": self.profile["device_class"], "version": self.profile["version"], "properties": values}
