# Home Chat Responsive Implementation Plan

> Use subagent-driven-development for the isolated frontend patch, followed by
> specification and code-quality review. The parent owns actual browser tests.

**Goal:** Keep scientific messages, tables and decision controls usable at mobile
and desktop widths without altering scientific data or execution contracts.

**Architecture:** Add a scoped home-chat stylesheet and explicit message-shell
classes. Mount decision controls as a composer row. Reuse existing table markup
and scrolling, adding keyboard access to comparison table scroll regions.

**Tech Stack:** Jinja2, native JavaScript/CSS, existing Node contract harness.

## Tasks

- [x] Add failing tests in `tests/home_decision_runtime_test.js` for controls
  outside the input row with unchanged cancellation/resume behavior; use a
  realistic composer fixture. In `tests/home_comparison_table_test.js`, assert
  a bounded, focusable table wrapper preserves all original values.
- [x] Add a scoped `src/web/static/css/home_chat_layout.css`, loaded by
  `src/web/templates/index.html`; give all message containers a shared class in
  `src/web/static/js/home/main.js`. Remove conflicting inline max-width only
  for those owned shells. Enforce `min-width: 0` at the flex shell and message,
  preserve desktop sizing and allow full-width mobile shells.
- [x] Move decision controls outside `.fill` into `.chat-composer`; preserve all
  authority guards and explicit hidden behavior. Style the row and reserve
  mobile bottom space. Give comparison-table wrappers a scoped class, tab stop
  and descriptive accessible name in `src/web/static/js/home/formatters.js`.
- [x] Run `node tests/home_decision_runtime_test.js`,
  `node tests/home_comparison_table_test.js`,
  `node tests/home_result_details_test.js`, plus new layout contracts;
  run `node --check` on changed JS and `git diff --check`.
- [x] Obtain independent spec review, fix any gaps, then quality review. Keep
  all frontend changes bounded to the listed files and new layout contract file.
- [x] On the actual running page, replay a real scientific result, inspect at
  390/768/1280px, open details and scroll tables using the keyboard. Verify
  cancellation and post-cancel requests remain usable. Save ignored screenshots,
  reset temporary viewport overrides, and record observed results below.

No backend restart is required for static-only changes; refresh an idle test tab
and confirm the new resource version loaded. Do not reload a running request.
No implementation staging/commit/PR before scope review; preserve other work.

## Verification

### Automated and independent checks

- RED: initial runtime suite had 78 passing / six expected new failures;
  comparison and layout contracts also failed before implementation. The
  additional DOM-order regression failed once before moving controls before
  `.fill`, then passed.
- Final focused runtime suite: **85 passed**; result details: **38 passed**;
  model-settings suite: **50 passed**. All **22 related Node suites** passed.
  These counts overlap and must not be summed into a fabricated total.
- Both independent specification and quality reviews passed. No Python/backend,
  scientific values, model configuration, execution authority or transport
  protocol changed.
- Four existing test expectations were updated to the newly served main/formatter
  cache versions: `home_llm_settings_test.js`, `home_reference_controls_test.js`,
  `home_tool_toggle_visibility_test.js`, and `frontend_safe_render_test.js`.
- The expanded suite also exposed a pre-existing stale assertion in
  `home_structured_molecule_render_test.js`: it expected client close codes
  1002/1009 although the current implementation already maps them to 4002/4009.
  The test now agrees with the existing decision-runtime contract; production
  close-code behavior was not changed by this follow-up.
- Production JS syntax checks, `python -m compileall -q src/web` and
  `git diff --check` passed. Git reports only
  the existing unrelated controller CRLF-normalization warning.

The 22 executed suites were `home_scientific_references`, `home_result_details`,
`home_renderer_boundary`, `home_reference_controls`, `home_protocol`,
`home_llm_settings`, `home_generation_count`, `home_evidence_report`,
`home_docking_source_selection`, `home_docking_consent`, `home_decision_runtime`,
`home_connection`, `home_comparison_table`, `home_chat_layout`,
`home_agent_task_panel`, `frontend_safe_render`, `home_task_state`,
`home_task_panel`, `home_structured_molecule_render`, `home_task_status`,
`home_tool_toggle_visibility`, and `home_workflow_completion_behavior`.
Each was run as `node tests/<name>_test.js`. The evidence-report suite's positive
same-frame Python driver was not rerun; that Node invocation covers its hostile
preflight contract only.

### Real browser observations

- Fresh actual page loaded all three `20261026-responsive-v1` resources and
  completed the established aspirin/paracetamol property + ADMET + BuChE
  comparison. Original probabilities/pIC50 values and requested tables remained.
- At **390px**, a 348px message row now contains a 296px message box, rather than
  the previously observed 756px overflow. The assistant box ends at x=364.
  Tables retain all cells and scroll locally (approximately 255px visible vs
  805px content for the activity table). Real ArrowRight keypresses moved the
  table to its right edge and exposed full pIC50 values.
- Actual expanded ADMET details reached the 49-endpoint collection without
  widening the message; detail width was approximately 255px. Last disclosure
  and long identifier text remained reachable above the fixed composer.
- At **768px**, message boxes were 674px wide and ended within the viewport;
  details opened with Enter at approximately 633px. Controls stayed between
  x=10 and x=758. At the restored **1280px** desktop viewport, message boxes
  and the 860px composer row remained contained.
- Real Stop on the new layout reached `cancelled`; a missing-structure request
  enabled Continue, and clicking it with CCO produced the real property result
  at phone width. Start-new-request released another waiting continuation;
  its subsequent independent CCN property request completed at 18:05. All
  controls returned to the idle state with zero streaming message boxes.
- Temporary viewport overrides were reset to the default 1280px layout. The
  latest acceptance page remains open; three intermediate test-only pages were
  closed after their requests had reached terminal states.

### Cancellation semantics observed before the static-layout refresh

The test clicked Stop approximately 48ms after the real generator's tool-start
event. The already-running `gmm-llama:latest` call drained successfully; the turn
then became cancelled approximately 5.9s after the click. No candidate result
was published to the page, and the next CCN property request succeeded. This
proves cooperative cancellation/publication gating, **not immediate physical
termination of an in-flight model call**. A separate planning-phase cancellation
and post-tool cancellation also reached cancelled.

### Remaining findings outside this layout patch

- Comparison prose asking to retain original predictions and consistency checks
  is incorrectly rejected by the exact presentation-clause vocabulary. The
  earlier approved English-period and unrequested-ADMET-column fixes remain
  accepted; this newly observed wording is a separate unresolved case.
- The singleton task panel remains at the first task's historical position while
  showing the latest task's status. The scientific result messages keep their
  own status, but the panel placement is misleading after multiple turns.
- Reverse-target prewarm remains unavailable. The configured data-directory
  helper reports no directory or required ChEMBL/fingerprint files in this
  environment. A nonblocking question asks for the user's existing asset path;
  no large downloads, data generation or deployment changes were performed.

Runtime screenshots are ignored artifacts, not committed assets. Branch remains
`codex/feat/pi-baseline-migrate-worktree`, HEAD `95c29f8b`; no staging, implementation
commit, PR or merge. This is scoped UI acceptance, not whole-goal completion.
