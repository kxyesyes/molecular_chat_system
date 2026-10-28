# Home task panel ownership

## Evidence and scope

Real frontend multi-turn testing showed the first chat task panel being overwritten by the latest property calculation. `HomeTaskPanel.create` searches the whole chat for an existing panel; `main.js` resets that singleton for each run. Scientific messages remain correct, but the displayed task history is misleading.

User authorization: ongoing real-frontend Agent testing and fixes, with autonomous choice/default approval. Preserve the existing dirty branch; no unrelated changes, commits, publication, or merge in this bounded fix.

## Decision

Moving the singleton would discard history. Keying by workflow trace would still overwrite waiting history when a continuation uses the same trace. Give each accepted chat/resume turn its own panel instead. Keep the renderer DOM-only and backward compatible by adding an explicit fresh-create option; runtime retains the panel on the turn object. Insert it immediately after that turn's user message. Default renderer reuse and legacy mode behavior remain unchanged.

Only validated active-turn events may create/update that panel. Keep socket, turn, trace and post-result guards. Reset lifecycle state on accepted turns, not from a previous terminal's state. Multiple internal terminal/planning events within the same turn must retain earlier steps. The validated waiting-result correction must target the current turn's panel, never a historical panel sharing its trace.

## Acceptance

- Two completed requests produce two separately positioned panels; the first is unchanged.
- Cancellation followed by a fresh request leaves cancelled history unchanged.
- Resume uses a new panel even with the same trace; old waiting history is unchanged.
- Late, foreign-trace, stale-socket and post-result events do not modify history.
- A turn without lifecycle events does not borrow an older panel.
- Legacy renderer idempotence, status vocabulary, result/detail rendering and scientific values are unchanged.
- Automated RED/GREEN contracts plus live frontend multi-turn, waiting/resume and cancellation recovery checks; no substituted scientific fixtures in live acceptance.
