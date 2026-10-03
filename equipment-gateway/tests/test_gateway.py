from copy import deepcopy
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from equipment_gateway.integrations.laundry import Simulator, manufacturer_adapter, validate_action, CONTRACT
from equipment_gateway.agent import AgentClient
from equipment_gateway.integrations.profile import ProfileAdapter
from equipment_gateway.contract import state_envelope
from equipment_gateway.journal import Journal
from equipment_gateway.modbus import crc16, parse_response, request_frame, TransportError
from equipment_gateway.profiles import observe, validate_profile, validate_bus_assignments
from equipment_gateway.runtime import Gateway, load_config

ROOT = Path(__file__).resolve().parents[1]


def payload():
    return dict(equipment_id="washer-1", effect_id="effect-1", machine_id="washer-1", order_id="order-1", vend_id="vend-1", process_id="process-1",
                operation_id="operation-1", amount_minor=500, currency="GBP")


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "journal.sqlite3"
        self.journal = Journal(self.path)
        self.adapter = Simulator()

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def execute(self, command="command-1", data=None):
        return self.journal.execute(command, "vend", data or payload(), self.adapter, instance_id="instance-1")

    def test_duplicate_command_and_duplicate_service_after_restart(self):
        self.assertEqual(self.execute()["certainty"], "accepted")
        self.execute()
        self.assertEqual(self.adapter.calls, 1)
        self.journal.close()
        self.journal = Journal(self.path)
        row = self.execute("command-2")
        self.assertEqual(row["invocation_id"], "command-2")
        self.assertEqual(row["certainty"], "accepted")
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(len(self.journal.unreported()), 2)

    def test_identity_cannot_be_reused_with_different_input(self):
        self.execute()
        with self.assertRaises(ValueError):
            self.execute(data={**payload(), "amount_minor": 900})
        with self.assertRaises(ValueError):
            self.execute("command-2", {**payload(), "amount_minor": 900})
        self.assertEqual(self.adapter.calls, 1)

    def test_crash_after_dispatch_is_durable_unknown_never_repeated(self):
        def crash(*args):
            self.adapter.calls += 1
            raise SystemExit("power loss")
        self.adapter.execute = crash
        with self.assertRaises(SystemExit):
            self.execute()
        self.journal.close()
        self.journal = Journal(self.path)
        self.assertEqual(self.execute()["certainty"], "unknown")
        self.assertEqual(self.execute("new-invocation")["certainty"], "unknown")
        self.assertEqual(self.adapter.calls, 1)

    def test_reject_and_uncertain_are_distinct(self):
        self.adapter = Simulator(outcome="definitely_rejected")
        self.assertEqual(self.execute()["certainty"], "definitely_rejected")
        self.adapter = Simulator(outcome="unknown")
        self.assertEqual(self.execute("command-2", {**payload(), "vend_id": "vend-2", "effect_id": "effect-2"})["certainty"], "unknown")

    def test_full_journal_fails_before_effect(self):
        self.journal.max_commands = 0
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(self.adapter.calls, 0)

    def test_second_gateway_cannot_share_the_same_physical_journal(self):
        with self.assertRaises(ValueError):
            Journal(self.path)

    def test_unsupported_action_does_not_dispatch(self):
        self.journal.max_commands = 2
        self.adapter = Simulator(mode="pulse")
        row = self.journal.execute("c", "reset", {k:v for k,v in payload().items() if k not in {"currency", "amount_minor"}}, self.adapter, instance_id="i")
        self.assertEqual(row["certainty"], "definitely_rejected")
        self.assertEqual(self.adapter.calls, 0)


class ProfileTests(unittest.TestCase):
    def test_large_profile_observation_contains_only_its_immutable_point(self):
        profile = self.profile()
        point = deepcopy(profile["points"][0])
        point.pop("scale", None)
        point["conversion"] = {"version": "calibration-1", "table": [[i, i / 10] for i in range(128)]}
        profile["device_class"] = "MeasurementBank"
        profile["points"] = [{**deepcopy(point), "key": f"quantity_{i}", "semantic_property": f"quantity_{i}"}
                             for i in range(64)]
        self.assertGreater(len(json.dumps(profile).encode()), 65536)
        observation = observe(profile, "quantity_0", [10], 100)
        original = observation.derive(100)
        self.assertLess(len(json.dumps(original).encode()), 32768)
        self.assertEqual(original["profile_snapshot_scope"], "point")
        self.assertEqual([p["key"] for p in original["profile"]["points"]], ["quantity_0"])
        profile["points"][0]["conversion"]["version"] = "calibration-2"
        self.assertEqual(observation.derive(100), original)
        self.assertEqual(original["value"], 1)

    def test_oversized_equipment_state_is_rejected_before_opening_hardware(self):
        profile = self.profile()
        profile["device_class"] = "MeasurementBank"
        point = deepcopy(profile["points"][0])
        point["conversion"] = {"version": "calibration-1", "table": [[i, i / 10] for i in range(128)]}
        profile["points"] = [{**deepcopy(point), "key": f"quantity_{i}", "semantic_property": f"quantity_{i}"}
                             for i in range(64)]
        with patch("equipment_gateway.integrations.profile.SimulatedIO") as io:
            with self.assertRaisesRegex(ValueError, "state profile exceeds"):
                ProfileAdapter(profile=profile)
            io.assert_not_called()

    def test_agent_refuses_oversized_payload_before_socket_call(self):
        agent = AgentClient()
        with patch.object(agent, "call") as call:
            with self.assertRaisesRegex(ValueError, "byte budget"):
                agent.event("equipment.observation", {"raw": "x" * 49152})
            call.assert_not_called()

    def test_oversized_point_provenance_is_rejected_before_polling(self):
        profile = self.profile()
        profile["points"][0]["unit"] = "x" * 32768
        with self.assertRaisesRegex(ValueError, "byte budget"):
            validate_profile(profile)

    def profile(self, variant="scaled"):
        return json.loads((ROOT / f"profiles/simulated-energy-{variant}.json").read_text())

    def test_two_meter_maps_identical_semantics(self):
        scaled = observe(self.profile(), "active_power", [12500], 100).derive(100)
        floating = observe(self.profile("float"), "active_power", list(struct.unpack(">2H", struct.pack(">f", 1250))), 100).derive(100)
        self.assertEqual(scaled["value"], floating["value"])
        self.assertEqual(scaled["semantic_property"], floating["semantic_property"])

    def test_profile_edits_do_not_reinterpret_history(self):
        profile = self.profile()
        original = observe(profile, "active_power", [100], 100)
        profile["version"] = "2.0.0"
        profile["points"][0]["scale"] = 1
        updated = observe(profile, "active_power", [100], 100)
        self.assertEqual(original.derive(100)["value"], 10)
        self.assertEqual(updated.derive(100)["value"], 100)
        self.assertNotEqual(original.derive(100)["profile_sha256"], updated.derive(100)["profile_sha256"])

    def test_missing_stale_invalid_disconnected_crc_and_timeout(self):
        for raw, now, error, expected in [(None,100,"","missing"),([1],131,"","stale"),([-1],100,"","invalid"),
                                          (None,100,"disconnected","disconnected"),(None,100,"crc_error","crc_error"),
                                          (None,100,"timeout","timeout")]:
            self.assertEqual(observe(self.profile(), "active_power", raw, 100, error=error).derive(now)["quality"], expected)

    def test_byte_and_word_order_signed_scaling(self):
        from equipment_gateway.profiles import decode
        self.assertEqual(decode({"datatype":"int16", "scale":0.1}, [65526]), -1)
        self.assertEqual(decode({"datatype":"uint32", "word_order":"little"}, [2,1]), 65538)
        self.assertEqual(decode({"datatype":"uint16", "byte_order":"little"}, [256]), 1)

    def test_duplicate_addresses_are_rejected_and_unverified_evidence_cannot_claim_verified(self):
        profile = self.profile()
        profile["transport"] = dict(kind="modbus_rtu", device="/dev/ttyUSB0", address=1, baudrate=9600, parity="N", stopbits=1)
        profile["points"][0].update(register=0, function=3)
        with self.assertRaises(ValueError):
            validate_bus_assignments([profile, deepcopy(profile)])
        profile["verification"]["status"] = "physical_verified"
        with self.assertRaises(Exception):
            validate_profile(profile)

    def test_rtu_wire_frames_crc_exception_and_wrong_address(self):
        self.assertEqual(request_frame(1, 3, 0, 1).hex(), "010300000001840a")
        data = bytes.fromhex("0103021234")
        frame = data + crc16(data).to_bytes(2, "little")
        self.assertEqual(parse_response(frame, address=1, function=3, count=1), [0x1234])
        for broken in [frame[:-1] + b"\x00", frame]:
            with self.assertRaises(TransportError):
                parse_response(broken, address=2, function=3, count=1)
        data = bytes([1, 0x83, 2])
        with self.assertRaises(TransportError) as raised:
            parse_response(data + crc16(data).to_bytes(2, "little"), address=1, function=3, count=1)
        self.assertEqual(raised.exception.quality, "device_error")


class ContractTests(unittest.TestCase):
    def test_owner_control_has_effect_correlation_without_payment_order(self):
        validate_action("reset", {"equipment_id": "m", "effect_id": "effect-1",
                                  "machine_id": "m", "process_id": "process-1", "operation_id": "operation-1"})

    def test_adapter_subsets_state_freshness_and_no_oem_claims(self):
        native, pulse = Simulator(), Simulator(mode="pulse")
        self.assertEqual(native.capabilities["contract"], pulse.capabilities["contract"])
        self.assertEqual(pulse.capabilities["actions"]["start"]["support"], "unsupported")
        report = state_envelope(CONTRACT, native.capabilities, {"remaining_seconds":10}, observed_at=100, now=131)
        self.assertEqual(report["properties"]["remaining_seconds"]["quality"], "stale")
        self.assertEqual(report["properties"]["availability"]["quality"], "missing")
        for brand in ["Alliance", "Electrolux", "Dexter", "Girbau"]:
            with self.assertRaises(ValueError):
                manufacturer_adapter(brand)
        with self.assertRaises(Exception):
            validate_action("vend", {**payload(), "oem_register": 42})


class FakeAgent:
    def __init__(self):
        self.events, self.calls = [], []
        self.status = "executing"
        self.fail_report = False

    def event(self, kind, data, **kwargs):
        self.events.append((kind, data, kwargs))

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "GetPendingCommands":
            return {"commands": []}
        if method == "ReportCommandResult" and self.fail_report:
            raise OSError("agent unavailable")
        return {"record": {"status": self.status}}


class RuntimeTests(unittest.TestCase):
    def test_inventory_is_bounded_and_republished_without_reobserving_values(self):
        config = load_config(ROOT / "config.access-control.example.json")
        template = config["equipment"][0]
        for point in template["options"]["profile"]["points"]:
            point.update(poll_seconds=200, max_age_seconds=300)
        config["equipment"] = [{**deepcopy(template), "equipment_id": f"entrance-{i}"} for i in range(128)]
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "j.sqlite3"))
            try:
                with patch("equipment_gateway.runtime.time.monotonic", return_value=100), patch("equipment_gateway.runtime.time.time", return_value=100):
                    gateway.tick()
                inventories = [data for kind, data, _ in agent.events if kind == "equipment.gateway_inventory"]
                self.assertEqual(sum(len(data["equipment"]) for data in inventories), 128)
                self.assertTrue(all(len(data["equipment"]) <= 100 and len(json.dumps(data).encode()) <= 49152
                                    for data in inventories))
                with patch("equipment_gateway.runtime.time.monotonic", return_value=120), patch("equipment_gateway.runtime.time.time", return_value=120):
                    gateway.tick()
                self.assertEqual(sum(kind == "equipment.gateway_inventory" for kind, _, _ in agent.events), len(inventories))
                with patch("equipment_gateway.runtime.time.monotonic", return_value=131), patch("equipment_gateway.runtime.time.time", return_value=131):
                    gateway.tick()
                self.assertEqual(sum(kind == "equipment.gateway_inventory" for kind, _, _ in agent.events), len(inventories) * 2)
                latest = [data for kind, data, _ in agent.events if kind == "equipment.state"][-1]
                self.assertTrue(all(value["observed_at"] == 100 for value in latest["state"]["properties"].values()))
            finally:
                gateway.close()

    def test_command_budget_preserves_pending_work_and_observation_polling(self):
        config = load_config(ROOT / "config.access-control.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "j.sqlite3"))
            clock = [100]
            handled = []
            def handle(command):
                handled.append(command)
                clock[0] += 3
            original_call = agent.call
            def call(method, **params):
                if method == "GetPendingCommands":
                    self.assertEqual(params["limit"], 8)
                    return {"commands": ["first", "second"]}
                return original_call(method, **params)
            try:
                with patch.object(agent, "call", side_effect=call), patch.object(gateway, "handle", side_effect=handle), patch("equipment_gateway.runtime.time.monotonic", side_effect=lambda: clock[0]):
                    gateway.tick()
                self.assertEqual(handled, ["first"])
                self.assertTrue(any(kind == "equipment.state" for kind, _, _ in agent.events))
            finally:
                gateway.close()

    def test_missing_optional_serial_hardware_preserves_other_equipment(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(ROOT / "config.example.json")
            real = deepcopy(config["instruments"][0])
            real["instrument_id"] = "missing-meter"
            real.pop("source")
            real["profile"]["transport"] = dict(kind="modbus_rtu", device="/dev/serial/by-id/missing",
                                                address=1, baudrate=9600, parity="N", stopbits=1)
            real["profile"]["points"][0].update(register=0, function=3)
            config["instruments"].insert(0, real)
            agent = FakeAgent()
            gateway = Gateway(config, agent, Journal(Path(tmp) / "j.sqlite3"))
            try:
                with patch("equipment_gateway.runtime.ModbusReader", side_effect=OSError("unplugged")):
                    gateway.tick()
                observations = {data["instrument_id"]:data for kind,data,_ in agent.events if kind=="equipment.observation"}
                self.assertEqual(observations["missing-meter"]["quality"], "disconnected")
                self.assertEqual(observations["meter-1"]["quality"], "good")
                self.assertTrue(any(kind=="equipment.state" for kind,_,_ in agent.events))
            finally:
                gateway.close()

    def test_rpc_unix_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "agent.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)
            received = []
            def serve():
                conn, _ = server.accept()
                with conn, conn.makefile("rb") as stream:
                    received.append(json.loads(stream.readline()))
                    conn.sendall(b'{"jsonrpc":"2.0","id":1,"result":{"commands":[]}}\n')
            thread = threading.Thread(target=serve)
            thread.start()
            self.assertEqual(AgentClient(path).call("GetPendingCommands", limit=10), {"commands":[]})
            thread.join()
            server.close()
            self.assertEqual(received[0]["params"], {"limit":10})

    def test_no_effect_on_cancel_foreign_instance_or_expired_command_and_replay_after_agent_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / "j.sqlite3")
            agent = FakeAgent()
            config = load_config(ROOT / "config.example.json")
            gateway = Gateway(config, agent, journal)
            command = dict(invocation_id="c", product_instance_id=config["product_instance_id"], command_key="vend",
                           input=payload(), safety="unsafe_to_duplicate")
            agent.status = "cancelled"
            gateway.handle(command)
            agent.status = "executing"
            gateway.handle({**command, "product_instance_id":"foreign"})
            gateway.handle({**command, "deadline":"2000-01-01T00:00:00Z"})
            self.assertEqual(gateway.adapters["washer-1"].calls, 0)
            agent.fail_report = True
            with self.assertRaises(OSError):
                gateway.handle(command)
            self.assertEqual(gateway.adapters["washer-1"].calls, 1)
            agent.fail_report = False
            gateway.tick()
            gateway.handle(command)
            self.assertEqual(gateway.adapters["washer-1"].calls, 1)
            self.assertEqual(journal.unreported(), [])
            gateway.close()


if __name__ == "__main__":
    unittest.main()
