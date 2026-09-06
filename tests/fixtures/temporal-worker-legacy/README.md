# Temporal worker legacy fixtures

These files are the byte-exact flat deployment assets from local predecessor
commit `0c54b2f`. They keep legacy migration tests self-contained in fresh CI
clones, where that unpushed historical commit is intentionally unavailable.

The expected SHA-256 digests remain defined by
`deployment/libexec/install-temporal-worker-bundle.py`. Update these fixtures
only when intentionally changing the recognized legacy migration contract.
