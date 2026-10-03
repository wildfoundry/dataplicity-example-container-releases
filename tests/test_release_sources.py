from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


class ReleaseSourceTests(unittest.TestCase):
    def test_gateway_uses_public_source_and_existing_examples_keep_private_source(self):
        manifest = json.loads((ROOT / "examples/release-manifest.example.json").read_text())
        schema = Draft202012Validator(json.loads((ROOT / "schemas/release-manifest.schema.json").read_text()))
        spec = importlib.util.spec_from_file_location("verify_repository", ROOT / "scripts/verify_repository.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        schema.validate(manifest)
        module.validate_manifest(manifest)
        gateway = deepcopy(manifest)
        gateway.update(service="equipment-gateway", tag=f"equipment-gateway/v{manifest['version']}",
                       source_repository="wildfoundry/dataplicity-example-container-releases")
        gateway["asset"]["name"] = f"equipment-gateway_{manifest['version']}_oci.tar"
        schema.validate(gateway)
        module.validate_manifest(gateway)
        for candidate in (manifest, gateway):
            invalid = deepcopy(candidate)
            invalid["source_repository"] = "wildfoundry/dataplicity-prelude" if candidate is gateway else "wildfoundry/dataplicity-example-container-releases"
            with self.assertRaises(ValidationError):
                schema.validate(invalid)
            with self.assertRaises(SystemExit):
                module.validate_manifest(invalid)
