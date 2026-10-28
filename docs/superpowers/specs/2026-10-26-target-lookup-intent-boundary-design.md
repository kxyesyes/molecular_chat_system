# Target structure lookup versus generation and docking

## Observed failure

In the real Web UI, `帮我查找人源 EGFR 的靶点结构，给我适合小分子对接的结构。` selected target-driven design and actually invoked the generation model, property and ADMET tools. This query requests existing protein structures, not molecule generation or a docking run. The test was cancelled; an in-flight ADMET operation finished before cancellation settled.

## Boundaries

Treat a structurally bounded target/protein lookup and anaphoric request to provide an existing structure for small-molecule docking as lookup intent only. A docking suitability phrase is a selection criterion, not authorization to execute docking or generate molecules. Preserve explicit independent requests for generating molecules, property prediction, retrieval, actual docking, or clarifications; never silently discard an unknown clause. Do not bypass target validation or suggest that a structure is docking-ready without actual source evidence.

## Verification

RED-first coverage should exercise the exact observed query, Chinese and English variations, target identifiers, independent explicit generation and docking actions, negation, numeric counts, and unknown extra operations. Verify routed tool permissions and Web requirements as well as actual configured-model UI with no generator, property, ADMET or docking dispatch. Keep source truth and failed/empty results visible, and run affected parser/router/planner and Web regressions.
