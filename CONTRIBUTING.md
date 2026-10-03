# Contributing

This distributes example artifacts and owns public `equipment-gateway` source.

## Appropriate changes

Pull requests may improve:

- release documentation;
- manifest schemas;
- checksum and manifest verification;
- repository security policy; and
- release-consumer guidance.

Changes to `equipment-gateway/` include profiles, transports, machine adapters,
tests and build assets. Its cloud semantic contracts remain owned by
`dataplicity-prelude`; sync the versioned contract snapshots and record their
source provenance. Other container source changes belong in the private workflow.

## Pull requests

1. Keep the change focused.
2. Never commit OCI archives or other generated release assets to Git.
3. Never include credentials, customer data, provisioning keys, or production
   URLs containing tokens.
4. Run `python3 scripts/verify_repository.py`.
   For equipment changes, install `equipment-gateway/` and run its unittest suite.
5. Use a pull request; do not rewrite published release tags or assets.

Release assets are published only by the approved source-repository workflow.
Maintainers must not upload replacement files to an existing release.
