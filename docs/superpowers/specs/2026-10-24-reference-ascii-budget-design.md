# Reference JSON ASCII budget fast path

## Approved scope

The user approved the minimal ASCII byte-counting approach on 2026-10-24.
This follow-up addresses avoidable CPU cost in the Windows default environment;
it does not claim to eliminate all scheduler, machine-load, or Pi-startup variance.
Keep the existing isolated PR branch and preserve the original checkout's edits.

## Evidence and alternatives

Payload-free timings measured 164 reference revalidations at approximately
3.2–4.2 seconds, with Pi startup at approximately 1.8–2.1 seconds. Individual
pipe frame reads took milliseconds, so pipe buffering is not the chosen fix.
A ten-revalidation CPU profile identified the per-character JSON budget walk
as one avoidable hotspot. The measurements include instrumentation overhead.

On one persisted source, three alternating blocks of 40 complete revalidations
took 0.7710/0.7480/0.6884 seconds with the original code and
0.6069/0.5681/0.5764 seconds with the in-memory ASCII prototype. All checks
returned valid; no check was cached or removed. These are local diagnostics,
not timing guarantees. An unmodified three-file Windows baseline still passed
52 tests, consistent with the intermittent nature of the earlier failure.

Choose an allocation-free ASCII fast path over broader UTF-8 byte encoding,
Pi lifecycle changes, connection reuse, or permission-handling changes. The
existing decision-bounds validator provides a compatible local pattern.

## Minimal implementation

Modify only `src/agent/contracts/scientific_references.py` and its existing
contract tests, plus task documentation. Inside `_check_json_bounds`, retain
the plain-string type and remaining-budget guards. When `item.isascii()` is
true and a compiled pattern finds no control character, quote, or backslash,
charge `len(item) + 2` bytes. Every other string uses the original loop.

This is exact for unescaped ASCII JSON strings, including DEL. Non-ASCII,
surrogates, short escapes, other control characters, and molecular backslashes
retain their existing handling. Node/depth/cycle checks, sensitive-data scans,
canonical JSON, revisions, ownership, source freshness, ACLs, and RDKit
validation are unchanged. There is no validation-result cache, dependency
change, API/schema change, or scientific-algorithm change.

## Verification and delivery

1. Add a deterministic failing regression showing safe ASCII strings do not
   enter the Python per-character loop; do not use a flaky wall-clock threshold.
2. Add exact-boundary/equivalence cases for ordinary ASCII, DEL, all JSON
   escapes, mixed Unicode, and invalid surrogates, retaining preflight-before-
   serializer/secret-scan assertions and custom-hook rejection.
3. Implement the minimal fast path and run contract, complete reference, and
   security-boundary regressions, compileall, and diff checks.
4. Repeat the unmodified Windows browser scenario in separate processes and
   run all three affected browser/integration test files sequentially. Retain
   every failure in the report; later passes do not erase earlier failures.
   Compare payload-free phase timings and confirm all 164 revalidations remain.
5. Independently review the code, commit only scoped files, update draft PR
   #216, and inspect actual CI results. Do not merge or deploy.

The 10-second receive deadline, frame budgets, assertions, scientific tools,
security checks, and physical worker settlement must remain unchanged. If the
same timeout persists, report it and investigate further rather than increasing
timeouts, adding retries, or treating the optimization as complete resolution.
Rollback is limited to reverting this optimization and its dedicated tests.
