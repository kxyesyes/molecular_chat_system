# Scientific provenance reference routing

## Goal

Keep follow-up requests that ask for a real tool or calculation source inside
their scientific workflow, so an already confirmed candidate selection remains
available to the server-owned property calculation. Explicit knowledge-base,
literature, or retrieval requests must continue to use RAG.

## Design

The hybrid router will distinguish provenance wording from retrieval intent.
When a request contains an actionable molecular/property/activity calculation
and asks for tool/model/calculation provenance, the provenance phrase will not
add the higher-priority `rag_search` route. An explicit retrieval or literature
verb still wins and remains RAG. The existing server-owned `reference` and
`selection` payload is unchanged; the fix is routing-only.

## Verification

- Add a router regression for the exact candidate-property provenance wording.
- Add a scientific-reference regression proving the same wording resolves the
  selected candidate to its canonical SMILES.
- Run focused Python tests, `compileall`, and frontend JavaScript checks.
- Start the project with the real Conda environment and verify in the browser:
  generate candidates, select one, request properties with provenance wording,
  and observe a real property-tool result with evidence/provenance.

## Safety boundary

No SMILES is injected into browser prose or inferred from the card. The server
must continue to validate the confirmed pointer, selection, session ownership,
and scientific tool evidence before execution or display.
