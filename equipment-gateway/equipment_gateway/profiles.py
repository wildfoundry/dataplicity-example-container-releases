"""Generic field profiles and immutable conversion evidence. Original observations remain self-describing."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct

from jsonschema import Draft202012Validator

SCHEMA = json.loads((Path(__file__).parent / "contracts/field-device-profile.v1.schema.json").read_text())
MAX_POINT_SNAPSHOT_BYTES = 32768


def point_snapshot(profile, point):
    """Preserve conversion provenance without copying unrelated points/commands."""
    result = {key: deepcopy(profile[key]) for key in
              ("profile_id", "version", "device_class", "applicability", "transport", "verification")}
    result["points"] = [deepcopy(point)]
    return result


def validate_profile(profile):
    Draft202012Validator(SCHEMA).validate(profile)
    json.dumps(profile, allow_nan=False)
    points = profile["points"]
    if len({p["key"] for p in points}) != len(points) or len({p["semantic_property"] for p in points}) != len(points):
        raise ValueError("Duplicate point key or semantic property")
    if profile["verification"]["status"] in {"bench_verified", "physical_verified"} and not profile["verification"]["evidence"]:
        raise ValueError("Verified profiles require evidence")
    for point in points:
        if len(json.dumps(point_snapshot(profile, point), allow_nan=False).encode()) > MAX_POINT_SNAPSHOT_BYTES:
            raise ValueError("Profile point observation snapshot exceeds its byte budget")
        if point["max_age_seconds"] < point["poll_seconds"]:
            raise ValueError("Freshness must allow polling interval")
        limits = point.get("valid_range")
        if limits and limits[0] > limits[1]:
            raise ValueError("Reversed validity range")
        if profile["transport"]["kind"] == "modbus_rtu":
            if not {"register", "function"} <= point.keys() or point["datatype"] in {"counter", "boolean"}:
                raise ValueError("Modbus points require a numeric register mapping")
            if point["register"] + register_count(point) > 65536:
                raise ValueError("Register mapping exceeds address range")
    for command in profile.get("commands", {}).values():
        Draft202012Validator.check_schema(command["input_schema"])
        if command["input_schema"].get("type") != "object" or command["input_schema"].get("additionalProperties") is not False:
            raise ValueError("Profile commands require a closed object input schema")
    for point in points:
        if "conversion" in point:
            table = point["conversion"]["table"]
            if any(table[i][0] >= table[i + 1][0] for i in range(len(table) - 1)):
                raise ValueError("Conversion table inputs must increase strictly")
            if point["datatype"] == "boolean":
                raise ValueError("Boolean values cannot use numeric conversion tables")
    return deepcopy(profile)


def register_count(point):
    return 2 if point["datatype"] in {"uint32", "int32", "float32"} else 1


def decode(point, raw):
    datatype = point["datatype"]
    if datatype == "boolean":
        if type(raw) is not bool:
            raise ValueError("Digital observation must be boolean")
        return raw
    if datatype == "counter":
        if type(raw) is not int or raw < 0:
            raise ValueError("Counter observation must be nonnegative integer")
        value = raw
    else:
        if not isinstance(raw, (list, tuple)) or len(raw) != register_count(point):
            raise ValueError("Wrong register count")
        if any(type(v) is not int or not 0 <= v <= 65535 for v in raw):
            raise ValueError("Invalid register value")
        words = list(raw)
        if point.get("word_order", "big") == "little":
            words.reverse()
        data = b"".join(word.to_bytes(2, point.get("byte_order", "big")) for word in words)
        value = struct.unpack({"uint16": ">H", "int16": ">h", "uint32": ">I", "int32": ">i", "float32": ">f"}[datatype], data)[0]
    if "conversion" in point:
        table = point["conversion"]["table"]
        if not table[0][0] <= value <= table[-1][0]:
            raise ValueError("Reading outside conversion calibration range")
        for left, right in zip(table, table[1:]):
            if left[0] <= value <= right[0]:
                value = left[1] + (value - left[0]) / (right[0] - left[0]) * (right[1] - left[1])
                break
    else:
        value = value * point.get("scale", 1) + point.get("offset", 0)
    if not math.isfinite(value):
        raise ValueError("Non-finite reading")
    limits = point.get("valid_range")
    if limits and not limits[0] <= value <= limits[1]:
        raise ValueError("Reading outside validity range")
    return value


@dataclass(frozen=True)
class Observation:
    """Serialized profile and raw values are immutable, even after profile edits."""
    profile_json: str
    raw_json: str
    point_key: str
    observed_at: float
    error: str = ""

    def derive(self, now):
        profile = json.loads(self.profile_json)
        point = next(p for p in profile["points"] if p["key"] == self.point_key)
        raw = json.loads(self.raw_json)
        result = {"profile_id": profile["profile_id"], "profile_version": profile["version"],
                  "profile_sha256": hashlib.sha256(self.profile_json.encode()).hexdigest(),
                  "profile": point_snapshot(profile, point), "profile_snapshot_scope": "point",
                  "point": self.point_key, "raw": raw,
                  "observed_at": self.observed_at, "semantic_property": point["semantic_property"],
                  "unit": point["unit"], "quality": "good", "value": None}
        if self.error:
            result.update(quality=self.error)
        elif raw is None:
            result.update(quality="missing")
        else:
            try:
                result["value"] = decode(point, raw)
                if now - self.observed_at > point["max_age_seconds"] or self.observed_at > now:
                    result["quality"] = "stale"
            except (ValueError, TypeError, OverflowError):
                result["quality"] = "invalid"
        return result


def observe(profile, point_key, raw, observed_at, *, error=""):
    validated = validate_profile(profile)
    if point_key not in {p["key"] for p in validated["points"]}:
        raise ValueError("Unknown profile point")
    return Observation(json.dumps(validated, sort_keys=True, separators=(",", ":"), allow_nan=False),
                       json.dumps(raw, allow_nan=False), point_key, observed_at, error)


def validate_bus_assignments(profiles):
    seen = set()
    settings = {}
    for profile in profiles:
        transport = validate_profile(profile)["transport"]
        if transport["kind"] not in {"modbus_rtu", "serial"}:
            continue
        key = (transport["device"], transport.get("address"))
        if transport["kind"] == "serial" and any(port == key[0] for port, address in seen):
            raise ValueError("Raw serial endpoints require an exclusive port")
        if (key[0], None) in seen:
            raise ValueError("Raw serial endpoints require an exclusive port")
        if key in seen:
            raise ValueError("Modbus address collision")
        seen.add(key)
        serial_settings = (transport["kind"], transport["baudrate"], transport["parity"], transport["stopbits"], transport.get("electrical_standard"))
        if key[0] in settings and settings[key[0]] != serial_settings:
            raise ValueError("Inconsistent settings on one Modbus bus")
        settings[key[0]] = serial_settings
