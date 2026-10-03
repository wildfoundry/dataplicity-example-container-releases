#!/usr/bin/env python3
"""Generate the standard immutable equipment-gateway OCI release bundle."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "equipment-gateway/pyproject.toml").read_text())["project"]["version"]
    if args.version != version:
        raise ValueError("Release version must match the source package version")
    archive = args.directory / f"equipment-gateway_{version}_oci.tar"
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    sha = digest.hexdigest()
    (args.directory / f"{archive.name}.sha256").write_text(f"{sha}  {archive.name}\n")
    manifest = dict(schema_version=1, service="equipment-gateway", version=version,
                    tag=f"equipment-gateway/v{version}",
                    source_repository="wildfoundry/dataplicity-example-container-releases",
                    source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                    built_at=datetime.now(timezone.utc).isoformat(),
                    oci_digest=json.loads(args.metadata.read_text())["containerimage.digest"],
                    platforms=["linux/amd64", "linux/arm64"],
                    asset=dict(name=archive.name, media_type="application/vnd.oci.image.layout.v1.tar",
                               size=archive.stat().st_size, sha256=sha))
    (args.directory / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.directory / "THIRD_PARTY_NOTICES.txt").write_bytes((root / "THIRD_PARTY_NOTICES.txt").read_bytes())


if __name__ == "__main__":
    main()
