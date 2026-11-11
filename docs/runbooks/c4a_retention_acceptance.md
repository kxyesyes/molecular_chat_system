# C4a physical command and retention acceptance

From the repository root with the MedChat Python environment active:

```powershell
python -m pytest tests/test_c4a_portable_acceptance.py -q
python scripts/run_c4a_acceptance.py
```

The first command checks bounded guard and report parsing, altered source rejection and negative controls on any platform. The second requires Windows with nested, kill-on-close Job Objects and the repository's local pytest/runtime dependencies. Use `--finite-only` to run the three owner prerequisites and six containment controls. Run one native suite at a time. The CLI returns 0 for a complete pass, 1 for failed qualification or cleanup and 2 for an unsupported platform.

| Case | Observation required |
| --- | --- |
| Clean | The guarded root exits, the Job empties, capture readers join, and handles close. |
| Pipe-holding descendant | The root exits with status zero while its Job still has a live descendant and a capture reader. The owner cancels and drains the tree. |
| Invalid guard | The child rejects the packet before producing proof. |
| Guard write fails | The suspended root never resumes. The acquiring stack retains the original write exception and closes the assigned Job. |
| Guard write and cleanup both fail | The original write and secondary close failures are both observed. A separate teardown releases the actual acquisition. |
| Job close fails after root exit | Exit alone cannot certify containment. The containment receipt must fail before independent teardown. |
| Receptor Job close fails permanently | The analysis command closed successfully. The fault targets the next receptor acquisition and the command scope remains unresolved. |
| Owned output deletion and quarantine fail | The receiver observes the two attempts on its own output. The command scope settles while its cleanup remains unresolved. |

The runner first qualifies clean settlement, root exit with a live descendant, and a suspended Popen returning after cancellation. Both permanent cases run only after the six finite controls pass in the same invocation. They use the current `_c4a_runtime_rig`, local execution factory, physical-resource observer, SQLite store, input lease and worker. The child checks pinned descriptors, occupied capacity, cancellation reason and repeated unresolved shutdown, with zero validation, finalization, lease exit or input close. The parent independently reads the committed state and staged files before terminating its Job and again after containment. The CLI reports production retention and test-child containment separately. All scientific executable boundaries use fixed Python fixtures.

`scripts/c4a_source_contract.json` records reviewed repository-relative files and their SHA-256 digests after CRLF-to-LF normalization. The runner and guarded children verify these files. A changed digest requires review of the affected assertions or fixture, updating the corresponding digest, and rerunning the complete native suite. Each receipt includes the digest of the sorted-key JSON contract. Source identity alone does not qualify a run.

The runner discards raw pytest and adapter output. Failures identify the case, exception class and source basename/line when available. Each invocation owns a temporary directory and its suspended root, assigned before resume. The 300-second execution deadline includes startup. The supervisor requests cancellation at expiry and reports ownership uncertainty if settlement takes another five seconds. Ctrl+C requests cancellation. The CLI retains the actual owner and private directory until the owner signals that settlement or independent cleanup has finished, then propagates the interrupt. Default pytest discovery cannot start a permanently retained worker.
