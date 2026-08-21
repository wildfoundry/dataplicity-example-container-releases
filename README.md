# Dataplicity example container releases

Pre-built OCI archives for Dataplicity's example device workloads.

This repository publishes **release artifacts only**. Development and source
code stay in the private Dataplicity control-plane repository. The containers
are intentionally credential-free: they communicate with the local
Dataplicity agent, which owns cloud authentication and transfer.

## Available examples

| Example | Purpose |
| --- | --- |
| `tank-level-sim` | Tank volume, operating marks, and alarm states |
| `water-table-sim` | Water-table depth telemetry |
| `grain-silo-sim` | Grain-silo fill height and threshold states |
| `fridge-temp-sim` | Refrigeration temperature and alarm states |
| `hvac-sim` | HVAC temperature and operating mode |
| `video-gen-sim` | Test-pattern or real CSI/V4L2 camera capture, live preview, and archive chunks |

Use the Dataplicity **Device class → Software → Use an example** flow. The
platform selects the correct immutable release and supplies the local runtime
configuration. Do not copy credentials into these containers.

## Releases

Each GitHub release is scoped to one example:

```text
<service>/v<version>
```

For example:

```text
video-gen-sim/v0.1.10
```

A release contains:

| Asset | Purpose |
| --- | --- |
| `<service>_<version>_oci.tar` | Multi-platform OCI archive |
| `<service>_<version>_oci.tar.sha256` | SHA-256 checksum for the archive |
| `release-manifest.json` | Machine-readable source commit, digest, platforms, size, and checksum |
| `THIRD_PARTY_NOTICES.txt` | Notice that bundled operating-system packages retain their own licences |

The Dataplicity control plane verifies the archive checksum before registering
or installing a release. Manual consumers should do the same:

```sh
sha256sum --check video-gen-sim_0.1.10_oci.tar.sha256
nerdctl load --input video-gen-sim_0.1.10_oci.tar
```

`docker load` may also accept the archive, but Dataplicity-managed devices use
the runtime selected by the management agent.

## Trust and provenance

- Artifacts are built from a pinned source commit by GitHub Actions.
- Published archives are immutable. A changed build receives a new version.
- Every archive has a SHA-256 checksum and OCI digest in its manifest.
- The release workflow generates GitHub artifact attestations.
- Secret scanning and push protection are enabled on this public repository.
- Source and release repositories never contain device or cloud credentials.

See [the release format](docs/RELEASE_FORMAT.md) for the full contract.
Maintainers should also follow [the publishing runbook](docs/PUBLISHING.md).

## Support and security

For product documentation, visit <https://docs.dataplicity.com/>.

Do not open a public issue for a suspected vulnerability. Follow
[SECURITY.md](SECURITY.md) to report it privately.

## Licence

Dataplicity example containers are distributed under the same modified-BSD
licence used for Dataplicity Agent release packages. See
[LICENSE.txt](LICENSE.txt).

Third-party packages inside an OCI archive remain subject to their respective
licences; the Dataplicity licence does not replace those terms.
