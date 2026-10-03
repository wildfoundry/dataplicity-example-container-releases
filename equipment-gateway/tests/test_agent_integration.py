"""Run with PYTHONPATH=<Prelude checkout> to exercise the real agent API."""
from pathlib import Path
import tempfile
import unittest

from equipment_gateway.agent import AgentClient
from equipment_gateway.journal import Journal
from equipment_gateway.runtime import Gateway, load_config

try:
    from agent_runtime.tests.product_runtime_helpers import make_runtime
except ImportError:
    make_runtime = None


@unittest.skipIf(make_runtime is None, "Set PYTHONPATH to a Prelude checkout for the real-agent contract test")
class RealAgentIntegrationTests(unittest.TestCase):
    def test_real_command_event_and_service_deduplication(self):
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config.example.json")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = make_runtime(Path(tmp) / "agent")
            runtime.start_oem_server()
            gateway = Gateway(config, AgentClient(str(runtime.cfg.socket_path)), Journal(Path(tmp) / "gateway.sqlite3"))
            command = dict(invocation_id="actual-agent-command", product_instance_id=config["product_instance_id"],
                           command_key="vend", device_action_key="vend", safety="unsafe_to_duplicate",
                           input=dict(equipment_id="washer-1", effect_id="effect-1", machine_id="washer-1", order_id="order-1", vend_id="vend-1", process_id="process-1",
                                      operation_id="operation-1", amount_minor=500, currency="GBP"))
            try:
                runtime.receive_command(command)
                gateway.tick()
                result = runtime.get_command_status(command["invocation_id"])
                self.assertEqual(result.status, "succeeded")
                self.assertEqual(result.result["outcome"], "accepted")
                runtime.receive_command(command)
                gateway.tick()
                runtime.receive_command({**command, "invocation_id":"another-invocation"})
                gateway.tick()
                self.assertEqual(gateway.adapters["washer-1"].calls, 1)
                self.assertEqual(runtime.get_command_status("another-invocation").result["outcome"], "accepted")
            finally:
                gateway.close()
                runtime.stop_oem_server()

    def test_same_agent_runtime_with_non_laundry_profile(self):
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config.access-control.example.json")
        with tempfile.TemporaryDirectory() as tmp:
            runtime = make_runtime(Path(tmp) / "agent")
            runtime.start_oem_server()
            gateway = Gateway(config, AgentClient(str(runtime.cfg.socket_path)), Journal(Path(tmp) / "effects.sqlite3"))
            command = dict(invocation_id="access-agent-command", product_instance_id=config["product_instance_id"],
                command_key="grant_access", safety="unsafe_to_duplicate", correlation_id="access-audit-1",
                input=dict(equipment_id="entrance-1", effect_id="access-effect-1", parameters={"duration_seconds": 2}))
            try:
                runtime.receive_command(command)
                gateway.tick()
                self.assertEqual(runtime.get_command_status(command["invocation_id"]).status, "succeeded")
                runtime.receive_command({**command, "invocation_id": "access-alias"})
                gateway.tick()
                self.assertEqual(gateway.adapters["entrance-1"].calls, 1)
                self.assertEqual(runtime.get_command_status("access-alias").result["outcome"], "accepted")
            finally:
                gateway.close()
                runtime.stop_oem_server()
