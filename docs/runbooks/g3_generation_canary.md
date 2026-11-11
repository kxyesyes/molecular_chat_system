# G3 molecular generation acceptance

Use the active MedChat environment with HTTPX, Pydantic and RDKit installed. From the checkout root, run the default offline check.

```console
python scripts/run_g3_generation_canary.py
```

The launcher runs one `GenerationToolAdapter.execute` call, the production `LLMMolecularGenerator`, strict synchronous `OllamaModel.generate`, and RDKit parsing and canonicalization. The default transport supplies a fixed set of ten synthetic SMILES in memory. Its report carries `transport_kind=scripted` and `remote_state=not_contacted`. Use it to check the integration. The fixture rows provide no evidence of model quality.

For an authorized model run, use the following command with an existing local Ollama service and the installed `gmm-llama:latest` model. Run this sequentially with other local model work according to the machine's memory and commit requirements. Ollama may load the model into memory when the request arrives.

```console
python scripts/run_g3_generation_canary.py --real
```

The real mode builds a direct, proxy-free HTTP transport inside the owned child. The observer admits only non-streaming POSTs to `http://127.0.0.1:11434/api/generate` with the fixed model, temperature 0.7, `num_ctx=512` and `num_predict=1000`. The adapter has one attempt. Within that call the generator can use up to five rounds to obtain the ten candidates. Each response body is limited to 1 MiB. Each report is limited to 64 KiB. It contains prompt digests, bounded provider counts and typed result summaries. Prompts, SMILES, response bodies, logs, paths and exception messages are excluded.

## Reading the result

- Exit 0 requires `status=complete10`, one adapter invocation, at least one successful response, and ten returned rows. Every row must parse in RDKit, have a distinct canonical SMILES and identify the expected generator model and source. Child resources and the parent process owner must settle.
- A `partial` report retains the number of valid, unique rows and warning counts. Partial generation, unrecovered protocol failures, timeouts and uncertain cleanup return nonzero. If a later response completes the request, the earlier failed attempt remains visible in the report. Argument errors return 2. A real run published by the launcher carries `transport_kind=local_ollama`. Test-only injected transports carry `injected` with an unverified remote state, and the launcher rejects them.
- `provider_terminal_reported` describes completed HTTP response bodies, including completed HTTP errors. Inspect per-attempt protocol, HTTP status and `provider_model`. Every otherwise-successful live response must echo `gmm-llama:latest`. A missing, invalid or different model stops the campaign even if another round matched. A connection failure after dispatch leaves remote completion uncertain. Requested options, model-name echoes and usage counts do not attest tokenizer identity, token enforcement or model-weight identity.

## Scope of the portable check

The parent uses its current Python interpreter, a private temporary working directory and the repository's command owner. Windows starts the child suspended and assigns a Job Object before resuming. POSIX uses the process group created for the child. The 180-second admission deadline and 210-second outer deadline share a monotonic origin set before owner setup. Late work cannot produce a successful report. Waiting for OS process creation and settlement can extend wall-clock time beyond the deadline. The parent removes the temporary directory after proven settlement. An unresolved owner returns failure and leaves its directory in place.

The report records digests for seven checked-out source files and the parent compares them to the same checkout. These are reproducibility details. The historical `G3Bootstrap` manifest pinned a particular Windows/Python installation, DLL identities, import inventory and load paths. This portable command does not reproduce that host qualification, attest native-code isolation, or verify Ollama's downstream state. The separate metadata probe remains available in `scripts/g3_ollama_metadata.py`.

The historical cold-readiness run stopped at `system_membership/unmatched` before scientific imports, client construction or any generation POST. An unlisted `python3.DLL` was considered as a possible cause and remained unconfirmed. In a separate live smoke on 2026-09-30, one adapter invocation requested ten candidates from the local `gmm-llama:latest` model. The saved report recorded ten valid, unique candidates and completed local cleanup in a run lasting 29.125 seconds. Host qualification remained unverified.

Run the focused offline regressions with the active interpreter.

```console
python -m pytest -q tests/test_g3_generation_canary.py tests/test_g3_generation_launcher.py
```
