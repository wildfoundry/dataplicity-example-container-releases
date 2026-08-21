#!/usr/bin/env python3
"""Verify a downloaded example-container GitHub release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SERVICES = {
    "tank-level-sim",
    "water-table-sim",
    "grain-silo-sim",
    "fridge-temp-sim",
    "hvac-sim",
    "video-gen-sim",
}
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    root = args.directory.resolve()
    if not root.is_dir():
        fail(f"release directory does not exist: {root}")

    manifest_path = root / "release-manifest.json"
    notices_path = root / "THIRD_PARTY_NOTICES.txt"
    if not manifest_path.is_file():
        fail("release-manifest.json is missing")
    if not notices_path.is_file() or notices_path.stat().st_size == 0:
        fail("THIRD_PARTY_NOTICES.txt is missing or empty")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid release manifest: {exc}")
    if not isinstance(manifest, dict):
        fail("release manifest root must be an object")

    service = manifest.get("service")
    version = manifest.get("version")
    if service not in SERVICES:
        fail(f"unsupported service: {service!r}")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        fail(f"invalid version: {version!r}")
    expected_tag = f"{service}/v{version}"
    if args.tag != expected_tag or manifest.get("tag") != expected_tag:
        fail(f"tag mismatch: expected {expected_tag}")
    if manifest.get("schema_version") != 1:
        fail("schema_version must be 1")
    if manifest.get("source_repository") != "wildfoundry/dataplicity-prelude":
        fail("source_repository is not authoritative")
    if not GIT_SHA_RE.fullmatch(str(manifest.get("source_commit", ""))):
        fail("source_commit must be a full lowercase Git SHA")

    digest = str(manifest.get("oci_digest", ""))
    if not digest.startswith("sha256:") or not SHA_RE.fullmatch(digest.removeprefix("sha256:")):
        fail("oci_digest must be sha256:<64 lowercase hex>")
    platforms = manifest.get("platforms")
    if not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms)):
        fail("platforms must be a non-empty unique list")

    asset = manifest.get("asset")
    if not isinstance(asset, dict):
        fail("asset must be an object")
    archive_name = f"{service}_{version}_oci.tar"
    archive_path = root / archive_name
    checksum_path = root / f"{archive_name}.sha256"
    if asset.get("name") != archive_name:
        fail("manifest asset name does not match service/version")
    if not archive_path.is_file() or archive_path.stat().st_size == 0:
        fail(f"archive missing or empty: {archive_name}")
    if not checksum_path.is_file():
        fail(f"checksum file missing: {checksum_path.name}")
    if asset.get("size") != archive_path.stat().st_size:
        fail("manifest asset size does not match archive")

    actual_sha = sha256(archive_path)
    if asset.get("sha256") != actual_sha:
        fail("manifest checksum does not match archive")
    checksum_line = checksum_path.read_text(encoding="utf-8").strip()
    if checksum_line != f"{actual_sha}  {archive_name}":
        fail("checksum file is not canonical or does not match archive")

    expected_files = {
        archive_name,
        f"{archive_name}.sha256",
        "release-manifest.json",
        "THIRD_PARTY_NOTICES.txt",
    }
    actual_files = {path.name for path in root.iterdir() if path.is_file()}
    extras = actual_files - expected_files
    missing = expected_files - actual_files
    if missing:
        fail(f"missing assets: {', '.join(sorted(missing))}")
    if extras:
        fail(f"unexpected assets: {', '.join(sorted(extras))}")

    print(f"verified {expected_tag}: {archive_name} sha256:{actual_sha}")


if __name__ == "__main__":
    main()
