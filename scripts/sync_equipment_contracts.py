#!/usr/bin/env python3
"""Export canonical contracts from a reviewed Prelude checkout, or check parity."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prelude", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = args.prelude / "product_logic/contracts"
    target = Path(__file__).resolve().parents[1] / "equipment-gateway/equipment_gateway/contracts"
    names = ("laundry-machine.v1.json", "laundry-capabilities.v1.schema.json", "field-device-profile.v1.schema.json")
    hashes = {}
    for name in names:
        data = (source / name).read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        if args.check:
            if data != (target / name).read_bytes():
                raise ValueError(f"Cloud/edge contract drift: {name}")
        else:
            (target / name).write_bytes(data)
    if not args.check:
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "product_logic/contracts"], cwd=args.prelude, text=True).strip()
        provenance = {"source_repository":"wildfoundry/dataplicity-prelude", "source_path":"product_logic/contracts",
                      "source_commit": None if dirty else subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.prelude, text=True).strip(),
                      "status": "pending_cloud_contract_merge" if dirty else "exported",
                      "sha256":hashes}
        (target / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


if __name__ == "__main__":
    main()
