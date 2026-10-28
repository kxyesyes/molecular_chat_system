# Reverse-target result presentation plan

**Goal:** Deliver truthful, source-bound ranked results and truthful failures.

**Architecture:** Existing evidence boundary renders legacy and concise views;
the summary transport adds an optional display-kind marker. The browser restores
one safe entity-escape layer only for the marked seven-column ranking table.
Explicit no-display requests receive clarification and retain their full evidence.

**Verification:** RED-first tests in `test_reverse_summary_delivery.py`,
`test_reverse_unavailable_summary.py` and `home_comparison_table_test.js`;
related session/rank/transport regression; Node suites; compileall; final-code
real browser with local source, plus independent review. Do not stage the mixed
worktree or claim prediction results when source is unavailable.

## Current validation record

- Full 831,120-row source prewarm completed in the earlier running instance.
  Real aspirin request succeeded but its summary omitted the ranked rows; the
  unmodified detailed observation contained seven results.
- Earlier negative-source instance produced a truthful missing-source reason;
  the revised failure summary removed the incorrect success sentence and internal
  warning token on a real browser. Its later unavailable-details fallback is
  covered by a RED-first unit regression.
- An intermediate revised instance fully validated a separate 43,465-row local
  source. The same real request displayed the one actually returned candidate,
  with its similarity caveats and details; no missing rows were fabricated.
- Independent reviews caught unsupported count scopes, entity double escaping,
  and explicit no-display commands. Each received new RED-first cases and fixes;
  a structured optional `display_kind` replaced unreliable prose sniffing.
- A final-source browser request using an English full stop exposed a missing
  punctuation boundary in the no-display parser. A RED-first regression
  reproduced it; the repaired parser passed the 40-test focused suite. The
  restarted real browser request with the same wording displayed no ranks and
  retained the details control. A separate earlier final-code browser request
  showed the single actual returned candidate from the 43,465-row source.
- The eight-file Python regression passed after the punctuation fix: 459 passed,
  seven existing deprecation warnings. Twenty-two home Node suites and static
  XSS checks passed after the structured-render changes; compileall passed after
  the final fix. `display_kind` is an optional, additive transport marker; the
  unmarked rendering and the full evidence record remain compatible.
