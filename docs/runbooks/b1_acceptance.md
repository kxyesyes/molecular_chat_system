# B1 scientific chat acceptance

Run from a checkout with the MedChat Python environment and the locked Pi Node dependencies installed. These offline checks exercise the current decision binding, Web admission, continuation and worker lifecycle contracts. The decisions are scripted. Molecular weights and drug-likeness results in the scientific tests come from RDKit.

Use a temporary directory outside every Git checkout. The Web tests create a user configuration directory beneath it, and MedChat rejects configuration paths inside a repository.

```console
npm ci --prefix runtime/pi --ignore-scripts --no-audit --no-fund
python scripts/run_b1_acceptance.py --list
python scripts/run_b1_acceptance.py --rows 28-34 --collect-only
python scripts/run_b1_acceptance.py --rows 28-34
python scripts/run_b1_acceptance.py
```

The runner processes rows sequentially. Each row gets a fresh Python child, temporary databases, configuration and current repository evaluation fixtures. The child denies outbound Python socket connections and explicitly loads the AnyIO pytest plugin. The parent uses the command owner to enforce a 720-second row deadline and check settlement of the Windows Job or POSIX process group. A missing result, deselection, skip, pytest error or uncertain cleanup makes the command fail. Collection results are labelled separately. The JSON lines contain counts, failure locations without parameter values and elapsed time.

## Historical matrix

The checked-in selectors in `scripts/b1_acceptance_cases.json` retain the 57 row numbers. Run `--list --rows N` to see the current selectors for any row.

| Rows | Original coverage | Current coverage |
|---|---|---|
| 1–25, 38, 42–57 | Decision, evidence, Web and model lifecycle modules | Same module paths in this checkout |
| 26 | B1 Web admission | Current Web requirements and Pi obligation admission |
| 27 | Web runtime before scientific dispatch | Pi input budgets, capability advertisement, RAG count, mounted hit/empty retrieval receipts, confirmed-reference ownership and display-injection rejection |
| 28–34 | Two molecular pairs in native/JSON, plain, profiled, phase-only and functional variants | Mounted Web entry, observed molecular weights, tool errors and stored executions. Plain runs use the ordinary fixture observer. Profiled runs additionally measure adapter calls. Phase-only measures Web turn duration. Functional runs also verify the original four-tool order in isolated B1 and both traces’ persisted inputs and provenance. |
| 35 | Post-science source checks | Cold RAG/reverse source setup, failure/cancellation rollback, no pre-execution in a mounted property turn, a reverse prediction record driving the next isolated B1 model decision and a lookup in a real local SQLite database |
| 36 | Continuations | Current Pi step identity and checkpoint validation, same-socket and reconnect clarification, expiry at claim, pinned model budgets and validation before resumed dispatch |
| 37 | Web lifecycle | Atomic application assembly and physical worker retention |
| 39–41 | Lifecycle before, during and after a permanently failed join | Original split function selections and the current isolated permanent-join regression |

The former Web profile accepted a single request for molecular weight, drug likeness, ADMET and activity. Current Web admission rejects that combination. A three-step comparison of molecular weight, ADMET and PDE5A activity supports independent compiled steps. The mounted campaign uses server-issued step references in both wire modes. The isolated B1 tests continue to cover observation-bound input references. An isolated activity registry and unavailable ADMET backend exercise failure handling; they supply no activity or ADMET measurements.

Each row receipt includes a digest of the runner, selectors, selected test module contents and the three evaluation fixtures. It identifies these files rather than an installed interpreter or a deployed system. Timing values describe this run and interpreter. Old profiler snapshots, private frame hashes and historical Windows image identities are retained in the donor checkout.
