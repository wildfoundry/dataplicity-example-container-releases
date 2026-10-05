"""Credit-pulse backends: GPIO hybrid timing and Waveshare Modbus flash."""
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from equipment_gateway.gpio_chip import hybrid_wait
from equipment_gateway.integrations.laundry import PulseActivated
from equipment_gateway.modbus import crc16, waveshare_flash_frame, waveshare_flash_units
from equipment_gateway.physical import ModbusIO, PrimitiveError
from equipment_gateway.runtime import load_config
from test_gateway import payload

ROOT = Path(__file__).resolve().parents[1]


class WaveshareFlashFrameTests(unittest.TestCase):
    def test_flash_on_700ms_matches_waveshare_manual(self):
        # Wiki example: 01 05 02 00 00 07 8D B0  (700 ms = 7 × 100 ms)
        frame = waveshare_flash_frame(1, 0, 0.7, flash="on")
        self.assertEqual(frame.hex(), "0105020000078db0")
        self.assertEqual(crc16(frame[:-2]), int.from_bytes(frame[-2:], "little"))

    def test_flash_on_100ms_is_one_quantum(self):
        self.assertEqual(waveshare_flash_units(0.1), 1)
        frame = waveshare_flash_frame(1, 0, 0.1, flash="on")
        self.assertEqual(frame[:6].hex(), "010502000001")

    def test_sub_quantum_duration_rejected(self):
        with self.assertRaises(ValueError):
            waveshare_flash_units(0.05)


class ModbusWaveshareIoTests(unittest.TestCase):
    def test_controller_timed_flash_does_not_host_sleep(self):
        bus = MagicMock()
        transport = {
            "kind": "modbus_rtu",
            "device": "/dev/ttyUSB0",
            "address": 1,
            "baudrate": 9600,
            "parity": "N",
            "stopbits": 1,
        }
        io = ModbusIO(
            transport,
            {
                "credit-pulse": {
                    "pulse_output": {
                        "kind": "waveshare_flash",
                        "relay": 0,
                        "flash": "on",
                        "max_seconds": 2.0,
                        "controller_timed": True,
                        "time_quantum_seconds": 0.1,
                    }
                }
            },
            {"/dev/ttyUSB0": bus},
        )
        outcome = io.execute("pulse_output", {"channel": "credit-pulse", "duration_seconds": 0.1})
        self.assertEqual(outcome.reason, "waveshare_controller_timed_flash_acknowledged")
        bus.flash_output.assert_called_once_with(
            0, 0.1, flash="on", address=1, time_quantum_seconds=0.1
        )
        bus.write_registers.assert_not_called()
        bus.write_output.assert_not_called()

    def test_host_timed_flag_rejected_for_waveshare(self):
        io = ModbusIO(
            {"kind": "modbus_rtu", "device": "/dev/ttyUSB0", "address": 1, "baudrate": 9600, "parity": "N", "stopbits": 1},
            {
                "credit-pulse": {
                    "pulse_output": {
                        "kind": "waveshare_flash",
                        "relay": 0,
                        "flash": "on",
                        "max_seconds": 2.0,
                        "controller_timed": False,
                        "time_quantum_seconds": 0.1,
                    }
                }
            },
        )
        with self.assertRaises(PrimitiveError):
            io.validate("pulse_output", {"channel": "credit-pulse", "duration_seconds": 0.1})


class PulseActivatedBackendTests(unittest.TestCase):
    def test_gpio_backend_default_and_evidence(self):
        bus_ops = []

        class FakeIo:
            def validate(self, operation, arguments):
                pass

            def execute(self, operation, arguments):
                bus_ops.append((operation, arguments))
                return type("O", (), {"certainty": "accepted", "reason": "ok"})()

        adapter = PulseActivated(
            machine_profile="Speed Queen - Pulse Activated",
            credit_pulse={"backend": "gpio", "chip": "/dev/gpiochip0", "line": 17},
            io=FakeIo(),
        )
        outcome = adapter.execute("vend", payload())
        self.assertEqual(outcome.result["evidence"]["kind"], "gpio_host_timed_pulse")
        self.assertEqual(outcome.result["evidence"]["backend"], "gpio")
        self.assertEqual(bus_ops[0][0], "pulse_output")

    def test_waveshare_backend_builds_modbus_io_and_evidence(self):
        bus = MagicMock()
        adapter = PulseActivated(
            machine_profile="Speed Queen - Pulse Activated",
            credit_pulse={
                "backend": "modbus_waveshare_flash",
                "transport": {
                    "kind": "modbus_rtu",
                    "device": "/dev/ttyUSB0",
                    "address": 1,
                    "baudrate": 9600,
                    "parity": "N",
                    "stopbits": 1,
                },
                "relay": 3,
                "flash": "on",
            },
        )
        adapter.bind_resources({"/dev/ttyUSB0": bus})
        outcome = adapter.execute("vend", payload())
        self.assertEqual(outcome.result["evidence"]["kind"], "modbus_waveshare_controller_timed_flash")
        self.assertEqual(outcome.result["evidence"]["relay"], 3)
        bus.flash_output.assert_called_once()

    def test_waveshare_rejects_non_quantum_profile_duration(self):
        with self.assertRaises(PrimitiveError):
            PulseActivated(
                machine_profile="Dexter - Pulse Activated",  # 50 ms default
                credit_pulse={
                    "backend": "modbus_waveshare_flash",
                    "transport": {
                        "kind": "modbus_rtu",
                        "device": "/dev/ttyUSB0",
                        "address": 1,
                        "baudrate": 9600,
                        "parity": "N",
                        "stopbits": 1,
                    },
                    "relay": 0,
                },
            )

    def test_waveshare_example_config_loads(self):
        config = load_config(ROOT / "config.waveshare-modbus.example.json")
        ids = {entry["equipment_id"] for entry in config["equipment"]}
        self.assertEqual(len(ids), 8)
        self.assertIn("washer-1", ids)
        self.assertIn("dryer-2", ids)
        relays = set()
        for entry in config["equipment"]:
            credit = entry["options"]["credit_pulse"]
            self.assertEqual(credit["backend"], "modbus_waveshare_flash")
            self.assertEqual(credit["transport"]["device"], "/dev/ttyUSB0")
            self.assertEqual(credit["transport"]["baudrate"], 9600)
            relays.add(credit["relay"])
        self.assertEqual(relays, set(range(8)))

    def test_duplicate_waveshare_relay_fails_closed(self):
        from equipment_gateway.modbus_site import validate_credit_pulse_buses

        transport = {
            "kind": "modbus_rtu",
            "device": "/dev/ttyUSB0",
            "address": 1,
            "baudrate": 9600,
            "parity": "N",
            "stopbits": 1,
        }
        credit = {
            "backend": "modbus_waveshare_flash",
            "transport": transport,
            "relay": 0,
            "flash": "on",
        }
        equipment = [
            {"equipment_id": "a", "options": {"credit_pulse": credit}},
            {"equipment_id": "b", "options": {"credit_pulse": {**credit}}},
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate Waveshare relay"):
            validate_credit_pulse_buses(equipment)

    def test_two_washers_share_one_modbus_bus_resource(self):
        from equipment_gateway.adapters import load_adapter

        transport = {
            "kind": "modbus_rtu",
            "device": "/dev/ttyUSB0",
            "address": 1,
            "baudrate": 9600,
            "parity": "N",
            "stopbits": 1,
        }
        resources = {}
        adapters = []
        for relay, equipment_id in enumerate(("washer-1", "washer-2")):
            entry = {
                "equipment_id": equipment_id,
                "adapter": "equipment_gateway.integrations.laundry:PulseActivated",
                "options": {
                    "machine_profile": "Speed Queen - Pulse Activated",
                    "credit_pulse": {
                        "backend": "modbus_waveshare_flash",
                        "transport": transport,
                        "relay": relay,
                        "flash": "on",
                    },
                },
            }
            adapters.append(load_adapter(entry, resources))
        bus = MagicMock()
        resources["/dev/ttyUSB0"] = bus
        for equipment_id, adapter in zip(("washer-1", "washer-2"), adapters):
            body = payload()
            body["equipment_id"] = equipment_id
            body["machine_id"] = equipment_id
            adapter.execute("vend", body)
        self.assertEqual(bus.flash_output.call_count, 2)
        self.assertIs(adapters[0].io.resources, adapters[1].io.resources)
        self.assertEqual(bus.flash_output.call_args_list[0].args[0], 0)
        self.assertEqual(bus.flash_output.call_args_list[1].args[0], 1)

    def test_hybrid_wait_sleeps_then_spins(self):
        sleeps = []
        ticks = [0.0, 0.0, 0.05, 0.098, 0.099, 0.100]

        def monotonic():
            return ticks.pop(0) if ticks else 0.100

        hybrid_wait(0.1, sleep=sleeps.append, monotonic=monotonic, spin_tail_seconds=0.002)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.098, places=3)


if __name__ == "__main__":
    unittest.main()
