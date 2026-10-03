"""Architecture acceptance: alternate semantics, unchanged runtime and journal."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from equipment_gateway.journal import Journal
from equipment_gateway.physical import SimulatedIO, PrimitiveError
from equipment_gateway.profiles import observe, validate_profile
from equipment_gateway.runtime import Gateway, load_config
from equipment_gateway.integrations.profile import ProfileAdapter
from test_gateway import FakeAgent

ROOT = Path(__file__).resolve().parents[1]


class ReuseTests(unittest.TestCase):
    def test_profile_poll_cadence_preserves_timestamp_and_marks_stale(self):
        profile = json.loads((ROOT / "profiles/simulated-energy-scaled.json").read_text())
        point = profile["points"][0]
        point["register"] = 0
        point["poll_seconds"] = 5
        point["max_age_seconds"] = 10
        adapter = ProfileAdapter(profile=profile, registers={point["register"]: 100})
        property_key = point["semantic_property"]
        first = adapter.state(observed_at=100, now=100)["properties"][property_key]
        adapter.io.registers[point["register"]] = 200
        cached = adapter.state(observed_at=103, now=103)["properties"][property_key]
        self.assertEqual((cached["raw"], cached["observed_at"]), (first["raw"], 100))
        with patch("equipment_gateway.integrations.profile.time.monotonic", side_effect=[0, 3]):
            self.assertEqual(adapter.state(observed_at=111, now=111)["properties"][property_key]["quality"], "stale")
        refreshed = adapter.state(observed_at=160, now=160)["properties"][property_key]
        self.assertEqual(refreshed["raw"], [200])
        self.assertEqual(refreshed["observed_at"], 160)

    def test_missing_simulator_registers_are_missing_observations(self):
        profile = json.loads((ROOT / "profiles/simulated-energy-scaled.json").read_text())
        adapter = ProfileAdapter(profile=profile)
        values = adapter.state(observed_at=100, now=100)["properties"]
        self.assertTrue(all(value["quality"] == "missing" and value["value"] is None for value in values.values()))

    def test_equipment_state_budget_resumes_at_next_adapter(self):
        config = load_config(ROOT / "config.access-control.example.json")
        config["equipment"].append({**deepcopy(config["equipment"][0]), "equipment_id": "entrance-2"})
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "effects.sqlite3"))
            try:
                with patch.object(gateway.adapters["entrance-1"], "state", return_value={}), patch("equipment_gateway.runtime.time.monotonic", side_effect=[0, 0, 3, 3]):
                    gateway.tick()
                states = [data for kind, data, _ in agent.events if kind == "equipment.state"]
                self.assertEqual([data["equipment_id"] for data in states], ["entrance-1"])
                gateway.tick()
                states = [data for kind, data, _ in agent.events if kind == "equipment.state"]
                self.assertEqual(states[1]["equipment_id"], "entrance-2")
            finally:
                gateway.close()

    def test_access_profile_uses_same_dispatch_io_and_reboot_deduplication(self):
        config = load_config(ROOT / "config.access-control.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effects.sqlite3"
            gateway = Gateway(config, agent, Journal(path))
            command = {"invocation_id": "access-1", "product_instance_id": config["product_instance_id"],
                "command_key": "grant_access", "safety": "unsafe_to_duplicate",
                "input": {"equipment_id": "entrance-1", "effect_id": "access-effect-1", "parameters": {"duration_seconds": 2}}}
            gateway.handle(command)
            adapter = gateway.adapters["entrance-1"]
            self.assertEqual(adapter.io.operations, [("pulse_output", {"channel": "release-relay", "duration_seconds": 2})])
            self.assertFalse(adapter.io.outputs["release-relay"])
            gateway.handle(command)
            self.assertEqual(len(adapter.io.operations), 1)
            gateway.tick()
            states = [data for kind, data, _ in agent.events if kind == "equipment.state"]
            self.assertEqual(states[-1]["state"]["contract"], "AccessController")
            self.assertTrue(states[-1]["state"]["properties"]["door_closed"]["value"])
            gateway.close()
            restarted = Gateway(config, agent, Journal(path))
            try:
                restarted.handle({**command, "invocation_id": "access-alias"})
                self.assertEqual(restarted.adapters["entrance-1"].io.operations, [])
                self.assertEqual(restarted.journal.lookup("access-alias")["certainty"], "accepted")
                restarted.handle({**command, "invocation_id": "indicator", "command_key": "set_indicator",
                    "input": {"equipment_id": "entrance-1", "effect_id": "indicator-1", "parameters": {"values": [42]}}})
                io = restarted.adapters["entrance-1"].io
                self.assertEqual(io.execute("modbus_read", {"register": 10, "count": 1}), [42])
            finally:
                restarted.close()

    def test_profile_invalid_arguments_fail_before_dispatch(self):
        config = load_config(ROOT / "config.access-control.example.json")
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, FakeAgent(), Journal(Path(tmp) / "effects.sqlite3"))
            try:
                gateway.handle({"invocation_id": "invalid", "product_instance_id": config["product_instance_id"],
                    "command_key": "grant_access", "safety": "unsafe_to_duplicate", "input": {"equipment_id": "entrance-1", "effect_id": "e", "parameters": {"duration_seconds": 999}}})
                self.assertEqual(gateway.adapters["entrance-1"].io.operations, [])
                self.assertIsNone(gateway.journal.lookup("invalid"))
            finally:
                gateway.close()

    def test_primitives_are_bounded_and_have_no_domain_methods(self):
        io = SimulatedIO(inputs={"input-1": True}, counters={"counter-1": 10})
        self.assertTrue(io.execute("read_input", {"channel": "input-1"}))
        self.assertEqual(io.execute("read_counter", {"channel": "counter-1"}), 10)
        io.execute("write_output", {"channel": "relay-1", "value": True})
        self.assertTrue(io.outputs["relay-1"])
        for operation, args in [("pulse_output", {"channel": "relay-1", "duration_seconds": float('nan')}),
            ("write_output", {"channel": "relay-1", "value": 1}), ("modbus_write", {"register": 65535, "values": [1, 2]})]:
            with self.assertRaises(PrimitiveError): io.execute(operation, args)
        self.assertFalse(hasattr(io, "vend"))

    def test_conversion_version_preserves_raw_evidence(self):
        profile = json.loads((ROOT / "profiles/simulated-energy-scaled.json").read_text())
        point = profile["points"][0]
        point.pop("scale", None)
        point["conversion"] = {"version": "calibration-1", "table": [[0, 0], [1000, 100]]}
        original = observe(profile, point["key"], [500], 100)
        point["conversion"] = {"version": "calibration-2", "table": [[0, 0], [1000, 200]]}
        updated = observe(profile, point["key"], [500], 100)
        self.assertEqual(original.derive(100)["value"], 50)
        self.assertEqual(updated.derive(100)["value"], 100)
        self.assertEqual(original.derive(100)["raw"], [500])
        self.assertEqual(original.derive(100)["profile"]["points"][0]["conversion"]["version"], "calibration-1")
        point["conversion"]["table"] = [[1, 0], [0, 1]]
        with self.assertRaises(ValueError): validate_profile(profile)

    def test_old_development_journal_fails_closed_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE commands (invocation_id TEXT, service_key TEXT)")
                db.execute("INSERT INTO commands VALUES ('old-command','old-effect')")
            with self.assertRaisesRegex(ValueError, "reviewed migration"): Journal(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 1)

    def test_core_has_no_vertical_imports_or_identifiers(self):
        for filename in ("runtime.py", "journal.py", "contract.py", "adapters.py", "physical.py", "profiles.py", "modbus.py"):
            source = (ROOT / "equipment_gateway" / filename).read_text()
            for forbidden in ("laundry", "washer", "dryer", "vend_id", "machine_id", "order_id", "LaundryMachine"):
                self.assertNotIn(forbidden, source, f"Vertical leakage in {filename}")


class ModbusActuationTests(unittest.TestCase):
    def test_register_write_frames_and_acknowledgement(self):
        from equipment_gateway.modbus import write_frame, parse_write_response, crc16, TransportError
        single = write_frame(1, 10, [42])
        self.assertEqual(single[:6].hex(), "0106000a002a")
        self.assertTrue(parse_write_response(single, request=single))
        multiple = write_frame(2, 10, [42, 43])
        echo = multiple[:6]
        frame = echo + crc16(echo).to_bytes(2, "little")
        self.assertTrue(parse_write_response(frame, request=multiple))
        with self.assertRaises(TransportError): parse_write_response(single, request=multiple)
        with self.assertRaises(ValueError): write_frame(1, 65535, [1, 2])

    def test_shared_bus_write_respects_each_equipment_address_and_controller_timer(self):
        from unittest.mock import MagicMock
        from equipment_gateway.physical import ModbusIO, PrimitiveError
        transport = {"kind": "modbus_rtu", "device": "/dev/ttyUSB0", "address": 2, "baudrate": 9600, "parity": "N", "stopbits": 1}
        bus = MagicMock()
        io = ModbusIO(transport, {"relay": {"write_output": 10, "pulse_output": {"duration_register": 11, "trigger_coil": 12, "max_seconds": 5, "controller_timed": True}}}, {"/dev/ttyUSB0": bus})
        io.execute("modbus_write", {"register": 10, "values": [42]})
        bus.write_registers.assert_called_with(10, [42], address=2)
        io.execute("write_output", {"channel": "relay", "value": False})
        bus.write_output.assert_called_with(10, False, address=2)
        io.execute("pulse_output", {"channel": "relay", "duration_seconds": 2})
        bus.write_registers.assert_called_with(11, [2000], address=2)
        bus.write_output.assert_called_with(12, True, address=2)
        io.channels["relay"]["pulse_output"]["controller_timed"] = False
        with self.assertRaises(PrimitiveError): io.execute("pulse_output", {"channel": "relay", "duration_seconds": 2})

    def test_unverified_profile_cannot_enable_physical_actuation(self):
        from equipment_gateway.integrations.profile import ProfileAdapter
        profile = json.loads((ROOT / "profiles/simulated-access-control.json").read_text())
        profile["verification"]["status"] = "unverified"
        profile["transport"] = {"kind": "modbus_rtu", "device": "/dev/ttyUSB0", "address": 1, "baudrate": 9600, "parity": "N", "stopbits": 1}
        profile["points"] = []
        with self.assertRaises(ValueError): ProfileAdapter(profile=profile, backend="modbus")


class OutcomeIsolationTests(unittest.TestCase):
    def test_opaque_parameters_cannot_overwrite_outcome_and_replay_correlation(self):
        config = load_config(ROOT / "config.access-control.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "j.sqlite3"))
            try:
                gateway.handle({"invocation_id": "actual", "product_instance_id": config["product_instance_id"],
                    "correlation_id": "access-audit", "command_key": "grant_access", "safety": "unsafe_to_duplicate",
                    "input": {"equipment_id": "entrance-1", "effect_id": "effect", "parameters": {"duration_seconds": 1},
                        "outcome": "unknown", "invocation_id": "spoofed", "service_delivered": True}})
                events = [(data, kwargs) for kind, data, kwargs in agent.events if kind == "equipment.command_outcome"]
                self.assertEqual(events[0][0]["outcome"], "accepted")
                self.assertEqual(events[0][0]["invocation_id"], "actual")
                self.assertNotIn("service_delivered", events[0][0])
                self.assertNotIn("service_delivered", events[0][0]["adapter_result"])
                self.assertEqual(events[0][1]["correlation_id"], "access-audit")
                gateway.handle({"invocation_id": "actual", "product_instance_id": config["product_instance_id"],
                    "command_key": "grant_access", "safety": "unsafe_to_duplicate", "input": {"equipment_id": "entrance-1", "effect_id": "effect", "parameters": {"duration_seconds": -1}}})
                report = [params for method, params in agent.calls if method == "ReportCommandResult"][-1]
                self.assertEqual(report["result"]["outcome"], "unknown")
                self.assertEqual(gateway.adapters["entrance-1"].calls, 1)
            finally:
                gateway.close()


class SerialPrimitiveTests(unittest.TestCase):
    def test_bounded_request_and_partial_reply_are_distinct(self):
        from unittest.mock import MagicMock
        from equipment_gateway.serial_transport import SerialTransport
        from equipment_gateway.modbus import TransportError
        bus = SerialTransport.__new__(SerialTransport)
        bus.transport = {"timeout_seconds": 1}
        bus.port = MagicMock()
        bus.port.write.return_value = 2
        bus.port.read.side_effect = [b"A", b"B"]
        self.assertEqual(bus.transfer(b"01", 2), b"AB")
        bus.port.read.side_effect = [b"A", b""]
        with self.assertRaises(TransportError): bus.transfer(b"01", 2)
        with self.assertRaises(ValueError): bus.transfer(b"X" * 257, 0)

    def test_serial_bus_cannot_be_shared_with_modbus(self):
        from equipment_gateway.profiles import validate_bus_assignments
        profile = json.loads((ROOT / "profiles/simulated-access-control.json").read_text())
        profile["points"] = []
        profile["transport"] = {"kind": "serial", "device": "/dev/ttyUSB0", "baudrate": 9600,
            "parity": "N", "stopbits": 1, "electrical_standard": "RS232", "timeout_seconds": 1}
        other = deepcopy(profile)
        other["transport"].update(kind="modbus_rtu", address=1)
        for profiles in ([profile, other], [other, profile]):
            with self.assertRaisesRegex(ValueError, "exclusive port"): validate_bus_assignments(profiles)

    def test_adapter_serial_reply_is_journalled_and_replayed_without_io(self):
        from equipment_gateway.integrations.profile import ProfileAdapter
        profile = json.loads((ROOT / "profiles/simulated-access-control.json").read_text())
        profile["points"] = []
        profile["commands"] = {"query_status": {"input_schema": {"type": "object", "additionalProperties": False},
            "steps": [{"operation": "serial_transfer", "arguments": {"request_hex": "0102", "response_bytes": 2}}]}}
        adapter = ProfileAdapter(profile=profile)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.sqlite3"
            journal = Journal(path)
            payload = {"equipment_id": "controller", "effect_id": "query"}
            first = journal.execute("query-1", "query_status", payload, adapter, instance_id="site")
            journal.close()
            journal = Journal(path)
            try:
                second = journal.execute("query-2", "query_status", payload, adapter, instance_id="site")
                self.assertEqual(first["result"], second["result"])
                self.assertEqual(json.loads(second["result"])["operations"][0]["value"], {"response_hex": "0000"})
                self.assertEqual(adapter.calls, 1)
            finally: journal.close()
