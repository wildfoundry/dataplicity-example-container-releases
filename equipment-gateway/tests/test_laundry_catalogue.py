"""Machine-family catalogue and per-machine equipment inventory."""
import json
from pathlib import Path
import tempfile
import unittest

from equipment_gateway.laundry_catalogue import get_machine_profile, list_machine_profiles, set_extra_profile_dirs
from equipment_gateway.runtime import load_config
from equipment_gateway.site_inventory import merge_equipment

ROOT = Path(__file__).resolve().parents[1]


class LaundryCatalogueTests(unittest.TestCase):
    def tearDown(self):
        set_extra_profile_dirs([])

    def test_bundled_speed_queen_profile_and_aliases(self):
        rows = list_machine_profiles()
        names = {row["machine_profile"] for row in rows}
        self.assertGreaterEqual(len(names), 15)
        self.assertIn("Speed Queen - Pulse Activated", names)
        self.assertIn("Dexter - Pulse Activated", names)
        self.assertIn("Electrolux Compass/Clarus Washer - Serial", names)
        profile = get_machine_profile("SQ-pulse")
        self.assertEqual(profile["machine_profile"], "Speed Queen - Pulse Activated")
        self.assertEqual(profile["status"], "field_candidate")
        self.assertEqual(profile["default_gap_seconds"], 0.1)

    def test_serial_native_profiles_are_not_host_pulseable(self):
        from equipment_gateway.integrations.laundry import PulseActivated
        with self.assertRaisesRegex(ValueError, "not host-pulseable"):
            PulseActivated(
                machine_profile="Electrolux Compass/Clarus Washer - Serial",
                credit_pulse={"chip": "/dev/gpiochip0", "line": 17},
            )

    def test_site_profiles_dir_can_add_families_without_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "acme-pulse.json"
            path.write_text(json.dumps({
                "machine_profile": "Acme - Pulse Activated",
                "manufacturer": "Acme",
                "model": "Pulse Activated",
                "activation": "credit_pulse",
                "default_duration_seconds": 0.05,
                "default_gap_seconds": 0.2,
                "default_pulses_per_vend": 2,
                "channel": "credit-pulse",
                "status": "lab_bench",
                "aliases": ["acme-pulse"],
            }))
            set_extra_profile_dirs([tmp])
            self.assertEqual(get_machine_profile("acme-pulse")["manufacturer"], "Acme")

    def test_equipment_dir_merges_many_machine_files(self):
        config = load_config(ROOT / "config.site.example.json")
        ids = [entry["equipment_id"] for entry in config["equipment"]]
        self.assertGreaterEqual(len(ids), 8)
        self.assertIn("washer-1", ids)
        self.assertIn("dryer-2", ids)
        washer = next(entry for entry in config["equipment"] if entry["equipment_id"] == "washer-1")
        self.assertEqual(washer["bay"], "A1")
        self.assertEqual(washer["options"]["machine_profile"], "Speed Queen - Pulse Activated")

    def test_duplicate_equipment_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.json").write_text(json.dumps({
                "equipment_id": "washer-1",
                "adapter": "equipment_gateway.integrations.laundry:Simulator",
                "options": {"mode": "pulse"},
            }))
            (root / "b.json").write_text(json.dumps({
                "equipment_id": "washer-1",
                "adapter": "equipment_gateway.integrations.laundry:Simulator",
                "options": {"mode": "pulse"},
            }))
            with self.assertRaisesRegex(ValueError, "Duplicate equipment_id"):
                merge_equipment([], equipment_dir=root)


if __name__ == "__main__":
    unittest.main()
