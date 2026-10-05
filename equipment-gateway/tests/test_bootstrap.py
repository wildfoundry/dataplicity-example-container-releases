"""Softwares-managed site bootstrap (no host sudo)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from equipment_gateway.bootstrap import (
    materialize_site,
    resolve_config_path,
    select_site,
)


class BootstrapTests(unittest.TestCase):
    def test_select_site_prefers_waveshare_when_serial_present(self):
        self.assertEqual(select_site(site="auto", serial_device="/dev/ttyUSB0"), "waveshare")
        self.assertEqual(select_site(site="waveshare", serial_device=None), "waveshare")
        self.assertEqual(select_site(site="gpio", serial_device="/dev/ttyUSB0"), "gpio")

    def test_materialize_waveshare_rewrites_serial_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = materialize_site(
                site="waveshare",
                state_dir=Path(tmp),
                product_instance_id="instance-from-env",
                serial_device="/dev/ttyACM0",
            )
            payload = json.loads(config.read_text())
            self.assertEqual(payload["product_instance_id"], "instance-from-env")
            self.assertEqual(payload["equipment_dir"], "equipment")
            washer = json.loads((Path(tmp) / "equipment" / "washer-1.json").read_text())
            self.assertEqual(
                washer["options"]["credit_pulse"]["transport"]["device"],
                "/dev/ttyACM0",
            )
            self.assertEqual(washer["options"]["credit_pulse"]["backend"], "modbus_waveshare_flash")

    def test_materialize_gpio_keeps_chip_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            materialize_site(site="gpio", state_dir=Path(tmp))
            washer = json.loads((Path(tmp) / "equipment" / "washer-1.json").read_text())
            self.assertEqual(washer["options"]["credit_pulse"].get("backend", "gpio"), "gpio")
            self.assertEqual(washer["options"]["credit_pulse"]["chip"], "/dev/gpiochip0")

    def test_resolve_config_path_materializes_into_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "EQUIPMENT_BOOTSTRAP": "1",
                "EQUIPMENT_SITE": "waveshare",
                "EQUIPMENT_STATE_DIR": tmp,
                "PRODUCT_INSTANCE_ID": "pi-1",
                "WAVESHARE_MODBUS_DEVICE": "/dev/ttyUSB0",
            }
            with patch.dict(os.environ, env, clear=False):
                path = resolve_config_path()
            self.assertEqual(path, Path(tmp) / "config.json")
            self.assertTrue(path.is_file())

    def test_bootstrap_disabled_uses_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "manual.json"
            existing.write_text('{"schema_version": 2, "product_instance_id": "x", "equipment": []}')
            with patch.dict(
                os.environ,
                {"EQUIPMENT_BOOTSTRAP": "0", "EQUIPMENT_CONFIG": str(existing)},
                clear=False,
            ):
                self.assertEqual(resolve_config_path(), existing)


if __name__ == "__main__":
    unittest.main()
