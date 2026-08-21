#!/usr/bin/env python3
"""Validate release-repository metadata without third-party dependencies."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "README.md",
    "LICENSE.txt",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "THIRD_PARTY_NOTICES.txt",
    "docs/RELEASE_FORMAT.md",
    "docs/PUBLISHING.md",
    "schemas/release-manifest.schema.json",
    "examples/release-manifest.example.json",
)
FORBIDDEN_TRACKED_SUFFIXES = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".oci",
    ".zip",
)
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


def load_json(relative: str) -> dict:
    path = ROOT / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{relative}: {exc}")
    if not isinstance(value, dict):
        fail(f"{relative}: root must be an object")
    return value


def tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def validate_manifest(manifest: dict) -> None:
    required = {
        "schema_version",
        "service",
        "version",
        "tag",
        "source_repository",
        "source_commit",
        "built_at",
        "oci_digest",
        "platforms",
        "asset",
    }
    missing = required - manifest.keys()
    if missing:
        fail(f"example manifest missing: {', '.join(sorted(missing))}")
    if manifest["schema_version"] != 1:
        fail("example manifest schema_version must be 1")
    service = manifest["service"]
    version = manifest["version"]
    if service not in SERVICES:
        fail(f"unknown example service: {service}")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        fail(f"invalid semantic version: {version!r}")
    if manifest["tag"] != f"{service}/v{version}":
        fail("example manifest tag does not match service/version")
    if manifest["source_repository"] != "wildfoundry/dataplicity-prelude":
        fail("example manifest source_repository is not authoritative")
    if not GIT_SHA_RE.fullmatch(str(manifest["source_commit"])):
        fail("example manifest source_commit must be a full lowercase Git SHA")
    digest = str(manifest["oci_digest"])
    if not digest.startswith("sha256:") or not SHA_RE.fullmatch(digest.removeprefix("sha256:")):
        fail("example manifest oci_digest must be sha256:<64 lowercase hex>")
    platforms = manifest["platforms"]
    if not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms)):
        fail("example manifest platforms must be a non-empty unique list")
    asset = manifest["asset"]
    if not isinstance(asset, dict):
        fail("example manifest asset must be an object")
    expected_name = f"{service}_{version}_oci.tar"
    if asset.get("name") != expected_name:
        fail(f"example manifest asset name must be {expected_name}")
    if not isinstance(asset.get("size"), int) or asset["size"] < 1:
        fail("example manifest asset size must be a positive integer")
    if not SHA_RE.fullmatch(str(asset.get("sha256", ""))):
        fail("example manifest asset sha256 must be 64 lowercase hex characters")


def main() -> None:
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            fail(f"required file missing: {relative}")

    schema = load_json("schemas/release-manifest.schema.json")
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("manifest schema must use JSON Schema draft 2020-12")
    validate_manifest(load_json("examples/release-manifest.example.json"))

    for relative in tracked_files():
        if relative.endswith(FORBIDDEN_TRACKED_SUFFIXES):
            fail(f"release payload must not be tracked in Git: {relative}")

    print("repository metadata verified")


if __name__ == "__main__":
    main()
