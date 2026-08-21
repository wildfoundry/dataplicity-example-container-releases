# Security policy

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue, discussion,
pull request, or social-media post.

Use GitHub's private vulnerability reporting:

<https://github.com/wildfoundry/dataplicity-example-container-releases/security/advisories/new>

Include:

- affected example and release tag;
- affected platform or architecture;
- expected and observed behaviour;
- reproduction steps or a proof of concept;
- potential impact; and
- any suggested mitigation.

Do not include real device credentials, provisioning keys, API keys, customer
data, or camera footage. Use synthetic data and redact identifiers.

We will acknowledge the report, assess severity, and coordinate remediation
and disclosure through the private advisory.

## Supported versions

Only the newest published version of each example is actively supported.
Superseded releases remain immutable for reproducibility but may not receive
fixes. Security fixes are published as new versions; existing assets are never
silently replaced.

## Artifact verification

Verify both the release checksum and provenance before loading an archive:

```sh
sha256sum --check <service>_<version>_oci.tar.sha256
gh attestation verify <service>_<version>_oci.tar \
  --repo wildfoundry/dataplicity-prelude
```

The attestation is produced by the private source repository's release
workflow. A missing or invalid attestation should be treated as a release
failure.
