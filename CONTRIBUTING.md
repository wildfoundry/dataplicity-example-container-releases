# Contributing

This is an artifact-distribution repository, not the development repository.

## Appropriate changes

Pull requests may improve:

- release documentation;
- manifest schemas;
- checksum and manifest verification;
- repository security policy; and
- release-consumer guidance.

Container source changes, feature requests, and product bugs belong in the
private Dataplicity development workflow.

## Pull requests

1. Keep the change focused.
2. Never commit OCI archives or other generated release assets to Git.
3. Never include credentials, customer data, provisioning keys, or production
   URLs containing tokens.
4. Run `python3 scripts/verify_repository.py`.
5. Use a pull request; do not rewrite published release tags or assets.

Release assets are published only by the approved source-repository workflow.
Maintainers must not upload replacement files to an existing release.
