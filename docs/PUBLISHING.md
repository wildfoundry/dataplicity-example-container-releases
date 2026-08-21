# Publishing

Example containers are built in the private
`wildfoundry/dataplicity-prelude` repository. This public repository is a
distribution boundary and must not receive private source code.

## Authentication

Use a dedicated GitHub App installed only on:

- `wildfoundry/dataplicity-prelude` with Actions metadata read access; and
- `wildfoundry/dataplicity-example-container-releases` with Contents write
  access.

Store the App ID and private key in the protected `prod` environment of the
source repository. Do not use a maintainer's personal access token, a deploy
key, or a broadly scoped organisation token.

The source workflow should mint a short-lived installation token with
`actions/create-github-app-token`, create a draft release, upload and verify all
required assets, then publish the draft.

## Publication sequence

1. Build the multi-platform image from a pinned source commit.
2. Export one multi-platform OCI archive.
3. Generate an SBOM and GitHub artifact attestation.
4. Compute the archive SHA-256 and byte size.
5. Register the same digest and checksum in the Dataplicity Software registry.
6. Generate `release-manifest.json` and the canonical `.sha256` file.
7. Create a draft release using `<service>/v<version>`.
8. Upload exactly the four required release assets.
9. Download the draft assets and run `scripts/verify_release.py`.
10. Publish the release only after every verification succeeds.

If a step fails before publication, delete the draft and retry. Never repair a
published release by replacing assets.

## Required source-repository environment

Recommended protected environment: `prod`

| Name | Type | Purpose |
| --- | --- | --- |
| `EXAMPLE_RELEASES_APP_ID` | variable | Dedicated publisher GitHub App ID |
| `EXAMPLE_RELEASES_APP_PRIVATE_KEY` | secret | Dedicated publisher private key |
| `EXAMPLE_RELEASES_REPOSITORY` | variable | `dataplicity-example-container-releases` |

Environment protection should require approval for production publication.

## Release notes

Release notes should name:

- example and version;
- source commit;
- supported platforms;
- OCI digest;
- archive SHA-256;
- user-visible changes; and
- security or migration considerations.

Do not include private issue links, customer identifiers, internal hostnames,
credentials, or token-bearing URLs.
