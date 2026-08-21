# Release format

This document is the contract between the private build workflow, this public
artifact repository, the Dataplicity control plane, and manual verifiers.

## Tag

```text
<service>/v<version>
```

`service` must be one of:

- `tank-level-sim`
- `water-table-sim`
- `grain-silo-sim`
- `fridge-temp-sim`
- `hvac-sim`
- `video-gen-sim`

Versions use semantic versioning without a leading `v` in filenames and
manifest fields.

## Required assets

```text
<service>_<version>_oci.tar
<service>_<version>_oci.tar.sha256
release-manifest.json
THIRD_PARTY_NOTICES.txt
```

The checksum file uses the portable `sha256sum --check` format:

```text
<64 lowercase hex characters>  <service>_<version>_oci.tar
```

## Manifest

`release-manifest.json` validates against
[`schemas/release-manifest.schema.json`](../schemas/release-manifest.schema.json).

Required identity and integrity fields:

- `schema_version`
- `service`
- `version`
- `tag`
- `source_repository`
- `source_commit`
- `built_at`
- `oci_digest`
- `platforms`
- `asset.name`
- `asset.media_type`
- `asset.size`
- `asset.sha256`

Timestamps use UTC RFC 3339. The source commit is a full 40-character Git SHA.
The archive checksum is lowercase SHA-256. The OCI digest includes the
`sha256:` prefix.

## Immutability

Published tags and assets are immutable:

- never delete and recreate a release to replace an archive;
- never upload a second asset with the same name;
- never move a release tag;
- publish a new semantic version for every changed build.

If publication partially fails, delete the **draft** release and retry. Once a
release is public, corrections require a new version.

## Provenance

The private source workflow must create a GitHub artifact attestation for the
OCI archive before publication. Consumers can verify it with:

```sh
gh attestation verify <archive> --repo wildfoundry/dataplicity-prelude
```

The manifest's `source_commit`, `asset.sha256`, and `oci_digest` must match the
build workflow and Dataplicity Software registry.

## Third-party software

OCI archives include Debian/Raspberry Pi packages and other open-source
components. Their original licences continue to apply. The release must carry
`THIRD_PARTY_NOTICES.txt`; source-package offers and copyright files remain
available through the upstream distributions included in the image.
