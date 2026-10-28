# Home chat responsive containment

## Evidence and scope

Real browser acceptance at a 390px layout viewport found a 348px assistant row
containing a 756px message box. The page hides horizontal overflow, so scientific
columns disappear instead of becoming reachable. The composer also places the
three decision buttons in the input's flex row, reducing its usable width.

Keep the native-JS/Jinja architecture, existing colors and scientific data. Fix
layout ownership only. Existing desktop typography and the message-avatar
relationship remain. Do not shrink scientific values or remove result columns.
Natural-language admission and the singleton task-panel lifecycle are separate
findings and are not part of this patch.

## Design

Use an explicit shared message-container class for the existing user, assistant,
streaming and RAG message shells. Let every flex ancestor shrink (`min-width: 0`)
and constrain the message to its row. On small screens, the shell may use the
available row width rather than the desktop 70% limit. Text can wrap; tables must
retain all cells and scroll within a bounded, keyboard-focusable region. Existing
result-detail table scrolling stays intact.

Decision buttons belong to a separate composer row, not the input's `.fill` row.
Keep existing cancel/resume/abandon enablement and transport behavior unchanged.
Give controls discernible spacing, disabled and focus states using current theme
tokens. Reserve enough mobile space that the fixed composer does not permanently
cover the last message or detail disclosure. Respect hidden decision mode.

## Alternatives

- Hiding columns, reducing font size or clipping overflow would conceal evidence
  and is rejected.
- Page-wide horizontal scrolling would make the chat and controls difficult to
  use and is rejected.
- Bounded message shells and local table scrolling address the actual flex
  intrinsic-size cause while preserving scientific contents; this is selected.

## Acceptance

- Real 390px and 768px layouts keep message boxes and composer controls within
  the viewport; 1280px retains readable desktop layout.
- Property/activity/ADMET tables preserve values and expose horizontal scrolling
  where needed, including keyboard access. Details open without widening the row.
- Stop, Continue and Start-new-request use the same protocol and authority gates.
- Add failing Node contracts first, then run affected Node suites and syntax
  checks. Browser geometry and screenshots, not static CSS tests alone, prove
  responsive acceptance. No backend, data or model changes.

User's standing instruction permits selecting the solution and default approval.
Preserve the existing dirty task branch; no unrelated staging, commit or PR.
