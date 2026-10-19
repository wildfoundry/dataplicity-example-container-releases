"""Local simulator is a labelled cycle on the real command path, with no I/O."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from equipment_gateway import controller_protocol
from equipment_gateway.integrations.laundry import LOCAL_SIMULATOR_PULSE_SECONDS, LocalSimulator
from equipment_gateway.journal import Journal
from equipment_gateway.runtime import Gateway, load_config
from test_gateway import FakeAgent, payload

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def parameter(tmp, value):
    """Write the agent's mounted parameters file the way the supervisor does."""
    root = Path(tmp) / "config"
    root.mkdir(exist_ok=True)
    (root / "parameters.json").write_text(json.dumps({
        "generation": 1,
        "settings": {"controller_protocol": {"key": "controller_protocol", "value": value, "value_kind": "enum"}},
    }))
    with patch.dict(os.environ, {"DP_CONFIG_DIR": str(root)}):
        yield


class LocalSimulatorTests(unittest.TestCase):
    def test_pulse_ack_names_equipment_and_duration_without_opening_io(self):
        adapter = LocalSimulator()
        outcome = adapter.execute("vend", payload())
        self.assertEqual(outcome.certainty, "accepted")
        self.assertEqual(outcome.reason, "local_simulator_pulse_acknowledged")
        self.assertTrue(outcome.result["service_delivered"])
        self.assertTrue(outcome.result["simulated"])
        self.assertEqual(outcome.result["equipment_id"], "washer-1")
        self.assertEqual(outcome.result["duration_seconds"], LOCAL_SIMULATOR_PULSE_SECONDS)
        self.assertEqual(outcome.result["evidence"]["label"], "simulated")
        snapshots = adapter.cycle_snapshots("washer-1", observed_at=1, now=1)
        stages = [snap["state"]["properties"]["stage"]["value"] for snap in snapshots]
        self.assertEqual(stages, ["simulated-pulse", "simulated-cycle", "simulated-complete"])
        self.assertTrue(all(snap["simulated"] is True for snap in snapshots))

    def test_gateway_simulator_does_not_pulse_the_configured_line(self):
        config = load_config(ROOT / "config.speed-queen-pulse.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "effects.sqlite3"))
            try:
                physical = gateway.adapters["washer-1"]
                command = {
                    "invocation_id": "vend-sim",
                    "product_instance_id": config["product_instance_id"],
                    "command_key": "vend",
                    "safety": "unsafe_to_duplicate",
                    "input": payload(),
                }
                with parameter(tmp, "local-simulator"):
                    gateway.handle(command)
                self.assertEqual(physical.calls, 0)
                states = [data for kind, data, _ in agent.events if kind == "equipment.state"]
                self.assertEqual(
                    [row["state"]["properties"]["stage"]["value"] for row in states],
                    ["simulated-pulse", "simulated-cycle", "simulated-complete"],
                )
                self.assertTrue(all(row["simulated"] is True for row in states))
                reported = [params for method, params in agent.calls if method == "ReportCommandResult"]
                self.assertEqual(len(reported), 1)
                result = json.loads(reported[0]["result"]["adapter_result"]) if isinstance(
                    reported[0]["result"]["adapter_result"], str
                ) else reported[0]["result"]["adapter_result"]
                self.assertEqual(result["equipment_id"], "washer-1")
                self.assertEqual(result["duration_seconds"], LOCAL_SIMULATOR_PULSE_SECONDS)
                self.assertTrue(result["service_delivered"])
                self.assertTrue(reported[0]["success"])
            finally:
                gateway.close()

    def test_unknown_protocol_is_rejected_without_using_the_configured_output(self):
        config = load_config(ROOT / "config.speed-queen-pulse.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "effects.sqlite3"))
            try:
                physical = gateway.adapters["washer-1"]
                with parameter(tmp, "girbau-serial"):
                    gateway.handle({
                        "invocation_id": "vend-unknown",
                        "product_instance_id": config["product_instance_id"],
                        "command_key": "vend",
                        "safety": "unsafe_to_duplicate",
                        "input": payload(),
                    })
                self.assertEqual(physical.calls, 0)
                reported = [params for method, params in agent.calls if method == "ReportCommandResult"]
                self.assertEqual(len(reported), 1)
                self.assertTrue(reported[0]["rejected"])
                self.assertIn("not implemented", reported[0]["error"])
            finally:
                gateway.close()

    def test_relay_protocol_refuses_when_no_relay_is_configured(self):
        config = load_config(ROOT / "config.speed-queen-pulse.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "effects.sqlite3"))
            try:
                physical = gateway.adapters["washer-1"]
                with parameter(tmp, "modbus-relay-pulse"):
                    gateway.handle({
                        "invocation_id": "vend-relay",
                        "product_instance_id": config["product_instance_id"],
                        "command_key": "vend",
                        "safety": "unsafe_to_duplicate",
                        "input": payload(),
                    })
                self.assertEqual(physical.calls, 0)
                reported = [params for method, params in agent.calls if method == "ReportCommandResult"]
                self.assertIn("not configured", reported[0]["error"])
            finally:
                gateway.close()

    def test_vend_payload_cannot_select_the_protocol(self):
        config = load_config(ROOT / "config.speed-queen-pulse.example.json")
        agent = FakeAgent()
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, agent, Journal(Path(tmp) / "effects.sqlite3"))
            try:
                physical = gateway.adapters["washer-1"]
                with patch.dict(os.environ, {"DP_CONFIG_DIR": str(Path(tmp) / "no-parameters")}):
                    gateway.handle({
                        "invocation_id": "vend-payload",
                        "product_instance_id": config["product_instance_id"],
                        "command_key": "vend",
                        "safety": "unsafe_to_duplicate",
                        "input": {**payload(), "controller_protocol": "local-simulator"},
                    })
                self.assertEqual(physical.calls, 0)
                reported = [params for method, params in agent.calls if method == "ReportCommandResult"]
                self.assertTrue(reported[0]["rejected"])
                states = [data for kind, data, _ in agent.events if kind == "equipment.state"]
                self.assertEqual(states, [])
            finally:
                gateway.close()

    def test_declared_choices_are_exactly_the_implemented_protocols(self):
        declared = json.loads((ROOT / "device-class-parameters.json").read_text())["settings"]
        self.assertEqual([row["key"] for row in declared], [controller_protocol.PARAMETER_KEY])
        self.assertEqual(declared[0]["value_kind"], "enum")
        self.assertEqual(
            [option["value"] for option in declared[0]["options"]],
            [
                controller_protocol.LOCAL_SIMULATOR,
                controller_protocol.MODBUS_RELAY_PULSE,
                controller_protocol.HOST_TIMED_COIN_PULSE,
            ],
        )
