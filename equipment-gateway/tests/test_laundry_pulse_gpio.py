"""Speed Queen pulse-activated laundry profile over host-timed GPIO."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from equipment_gateway.gpio_chip import LineHandle
from equipment_gateway.integrations.laundry import MACHINE_PROFILES, PulseActivated, Simulator
from equipment_gateway.journal import Journal
from equipment_gateway.physical import GpioIO, PrimitiveError
from equipment_gateway.runtime import Gateway, load_config
from test_gateway import FakeAgent, payload

ROOT = Path(__file__).resolve().parents[1]


class FakeLine(LineHandle):
    def __init__(self):
        super().__init__(fd=3, offset=17, chip="/dev/gpiochip0", consumer="test", active_high=True)
        self.values = []

    def set_active(self, active: bool) -> None:
        self.values.append(bool(active))

    def close(self) -> None:
        self.fd = -1


class LaundryPulseGpioTests(unittest.TestCase):
    def test_speed_queen_profile_is_configured_by_name(self):
        self.assertIn("Speed Queen - Pulse Activated", MACHINE_PROFILES)

    def test_pulse_activated_drives_host_timed_gpio_and_logs(self):
        line = FakeLine()
        sleeps = []

        def open_line(chip, offset, consumer, active_high):
            self.assertEqual((chip, offset, consumer, active_high), ("/dev/gpiochip0", 17, "rinsepilot-credit", True))
            return line

        from equipment_gateway.gpio_chip import GpioLineSession

        io = GpioIO(
            {
                "credit-pulse": {
                    "pulse_output": {
                        "chip": "/dev/gpiochip0",
                        "line": 17,
                        "active_high": True,
                        "host_timed": True,
                        "max_seconds": 2.0,
                        "consumer": "rinsepilot-credit",
                    }
                }
            },
            session=GpioLineSession(open_line=open_line),
            sleep=lambda seconds: sleeps.append(seconds),
        )
        with self.assertLogs("equipment-gateway.laundry", level="INFO") as laundry_logs:
            with self.assertLogs("equipment-gateway.physical", level="INFO") as physical_logs:
                adapter = PulseActivated(
                    machine_profile="Speed Queen - Pulse Activated",
                    credit_pulse={
                        "chip": "/dev/gpiochip0",
                        "line": 17,
                        "active_high": True,
                        "consumer": "rinsepilot-credit",
                    },
                    io=io,
                )
                outcome = adapter.execute("vend", payload())
        self.assertEqual(outcome.certainty, "accepted")
        self.assertEqual(outcome.result["machine_profile"], "Speed Queen - Pulse Activated")
        self.assertEqual(outcome.result["evidence"]["kind"], "gpio_host_timed_pulse")
        self.assertEqual(sleeps, [0.1])
        self.assertEqual(line.values, [True, False])  # host-timed pulse edge
        self.assertTrue(any("laundry_vend_pulse_begin" in row for row in laundry_logs.output))
        self.assertTrue(any("gpio_pulse_activate" in row for row in physical_logs.output))
        self.assertTrue(any("gpio_pulse_complete" in row for row in physical_logs.output))

    def test_gpio_pulse_requires_explicit_host_timed_flag(self):
        io = GpioIO({"credit-pulse": {"pulse_output": {
            "chip": "/dev/gpiochip0", "line": 17, "active_high": True,
            "host_timed": False, "max_seconds": 2.0,
        }}})
        with self.assertRaises(PrimitiveError):
            io.validate("pulse_output", {"channel": "credit-pulse", "duration_seconds": 0.1})

    def test_example_speed_queen_config_loads_and_dispatches_once(self):
        config = load_config(ROOT / "config.speed-queen-pulse.example.json")
        self.assertIn("washer-1", {entry["equipment_id"] for entry in config["equipment"]})
        line = FakeLine()
        from equipment_gateway.gpio_chip import GpioLineSession

        session = GpioLineSession(open_line=lambda *args, **kwargs: line)
        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway(config, FakeAgent(), Journal(Path(tmp) / "effects.sqlite3"))
            try:
                adapter = gateway.adapters["washer-1"]
                adapter.io.session = session
                adapter.io._sleep = lambda seconds: None
                command = {
                    "invocation_id": "vend-1",
                    "product_instance_id": config["product_instance_id"],
                    "command_key": "vend",
                    "safety": "unsafe_to_duplicate",
                    "input": payload(),
                }
                with patch.object(gateway.agent, "call", side_effect=gateway.agent.call):
                    gateway.handle(command)
                self.assertEqual(adapter.calls, 1)
                self.assertEqual(line.values.count(True), 1)
                gateway.handle(command)
                self.assertEqual(adapter.calls, 1)
            finally:
                gateway.close()

    def test_simulator_remains_available_for_non_gpio_paths(self):
        adapter = Simulator(mode="pulse")
        outcome = adapter.execute("vend", payload())
        self.assertEqual(outcome.certainty, "accepted")
        self.assertEqual(adapter.io.operations[0][0], "pulse_output")


if __name__ == "__main__":
    unittest.main()
