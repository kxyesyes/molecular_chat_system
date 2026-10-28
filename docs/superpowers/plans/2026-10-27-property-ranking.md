# Property-only candidate ranking implementation

1. Add failing routing and plan tests for the exact compound request and adversarial unsupported criteria/operations.
2. Add a narrowly bounded shared intent classifier; let only its reviewed requests bypass the property-only admission profile.
3. Bind the existing target-design plan to candidate and property evidence only, excluding unrequested prediction steps for this reviewed request; keep other workflows unchanged.
4. Render actual ranking and explain the existing property heuristic without implying an activity or experimental result.
5. Verify evidence identity and failure boundaries, run focused and related tests, then test the same request through a running real frontend.

The narrower classifier deliberately leaves other phrasing and explicit metrics on the existing fail-closed path. A real two-candidate frontend run completed with the verified property-only ranking and an accessible detail view; a six-candidate synthetic integration test checks the full requested count. Review-driven checks also cover altered scores and ordering, missing or conflicting acceptance checks, and unaccepted ranking data in modern and legacy presentations. These checks do not establish general natural-language coverage or verify all possible tool failures.
