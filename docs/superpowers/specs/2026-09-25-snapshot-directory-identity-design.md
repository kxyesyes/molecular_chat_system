# Snapshot directory identity, not sibling content versions

Bounded stability fix discovered during package5 regression. User delegated recommended choices for completing packages1–8. Separate from Planner changes. No deployment, external model, secret or original dirty-tree access.

An actual controlled invocation of the unchanged docking contract test rejected an unchanged pose because an ancestor directory's mtime changed during unrelated sibling creation/removal. Two full Agent runs also failed valid-hash cases, but their historical low-level cause was not captured; do not claim unique attribution or erase those failures after reruns.

Options: retries/timeouts would mask an incorrect comparison; dropping ancestor checks weakens the trust boundary. Chosen: separate directory boundary identity from the target file's content version. Keep file `_identity` and SHA-256 checks unchanged.

Add a private directory snapshot helper validating directory type/no reparse, existing platform identity, mode and uid/gid. Do not include size/mtime/ctime: sibling changes legitimately alter them. Check every captured ancestor again. On POSIX also compare each pinned directory descriptor with its still-named component using its pinned parent descriptor and `follow_symlinks=False`; otherwise removing ctime checks could miss rename/replacement of an open ancestor. The root descriptor is compared with its named anchor. Reject replacement, links, permission/owner changes, missing components and I/O errors. Windows retains before/after lstat boundary comparison and existing handle/path semantics. This is not a claim of atomic defense against all possible malicious namespace races.

Preserve target regular-file checks, descriptor/path identity+content version, size bound, bounded read, digest, exception sanitization and finally descriptor close. Do not change config._stat_identity/_stat_version, atomic writes, docking trust policy or assertion expectations. No retries, new permissive fallback or dependency.

TDD includes sibling activity at direct and higher ancestors, file mutation/replacement, ancestor identity/mode changes, symlink/reparse rejection and descriptor cleanup on success/failure. Execute original docking snapshot tests plus secure-I/O consumers. Native Windows coverage locally; POSIX actual descriptor/named checks in Linux CI, never claimed from a mocked Windows run. Independent SPEC then QUALITY and exact-head8-check CI before merge.
