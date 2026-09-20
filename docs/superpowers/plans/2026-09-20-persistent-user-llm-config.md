# Persistent user LLM configuration implementation plan

> **For agentic workers:** Use subagent-driven-development for the independent UI task and requesting-code-review for specification then quality review. Parent implements the tightly coupled storage/application tasks using TDD.

**Goal:** DeepSeek empty-key defaults, persistent per-user configuration outside every Git checkout, no legacy credential adoption.

**Architecture:** Add a small user-configuration policy module around existing env serialization/atomic replacement/locking primitives. The Web app uses this authoritative store instead of repository env/runtime caches. Keep external acceptance CLI configuration and molecular generation independent.

**Tech Stack:** Python/FastAPI, existing trusted-files helpers, pathlib, pytest, vanilla JS/Node.

## Task 1 — Storage and credential boundary (parent)

Files: add `src/web/user_llm_config.py`, `tests/test_user_llm_config.py`; update fixed masking in `src/web/llm_runtime_config.py`.

- [x] RED: temporary-directory tests for DeepSeek defaults; no inherited env key; stable path across cwd; save/reload in a new process; blank-key preservation on same endpoint; explicit clear; endpoint/provider changes; failed replace preserves bytes; malformed/unsafe files; path outside repositories; single-line values; fixed masking.
- [x] Run `python -B -m pytest tests/test_user_llm_config.py -q -p no:cacheprovider` and record expected failures.
- [x] GREEN: implement `default_user_llm_config()`, `user_llm_config_path()`, `load_user_llm_config(path)`, `save_user_llm_config(path, raw, clear_api_key=False)` returning `(config, signature)`, `user_llm_signature(path)` and `resolve_user_llm_request(raw, current, clear_api_key=False)`.

```python
assert default_user_llm_config() == {
    'provider': 'openai_compatible',
    'base_url': 'https://api.deepseek.com/chat/completions',
    'model_name': 'deepseek-v4-pro', 'api_key': '', 'stream': True,
}
# Save must lock, read authoritative current state, resolve same-endpoint key reuse,
# atomically replace private env contents and return the exact saved fingerprint.
# Read/signature/save reject untrusted paths; absence is defaults, corruption is error.
```

- [x] Re-run tests; independently review path trust and secret isolation before integration completion.

## Task 2 — Web integration (parent)

Files: `src/web/app.py`, `tests/test_user_llm_routes.py`, existing runtime/wiring test expectations affected by deliberate precedence change.

- [x] RED: build isolated lightweight app fixtures; GET default ignores old env/cache; POST survives reconstructed app; test endpoint never saves; changed endpoint never inherits a key; storage failure is a sanitized error; cross-worker refresh handles update/clear/removal; `/api/switch_model` persists to same store.
- [x] Replace UI store path/load/save/refresh functions with Task 1 functions. Remove only obsolete UI env/cache priority logic and duplicate startup model initialization. Keep generator construction local Ollama.

```python
self.runtime_llm_env_path = user_llm_config_path()
self.active_llm_config = load_user_llm_config(self.runtime_llm_env_path)
# Request credentials are resolved against current persisted config, never os.environ.
# Write succeeds before applying model; reload treats a deleted file as defaults.
```

- [x] Focused regression: `python -B -m pytest tests/test_user_llm_routes.py tests/test_user_llm_config.py tests/test_llm_runtime_config.py tests/test_agent_llm_wiring.py tests/test_admin_auth_routes.py -q -p no:cacheprovider`.
- [x] Ensure any existing tests that construct full apps use temporary user directories, never the real home config.

## Task 3 — Settings UI (independent worker)

Files only: `src/web/static/js/home/main.js`, `src/web/templates/index.html`, new `tests/home_llm_settings_test.js`.

- [x] RED Node behavior tests extract actual settings functions: empty config uses DeepSeek; key input always clears; saved hint contains no key fragments; successful save says saved, not connected; clear and persistent-location text is accurate.
- [x] Set provider fallback openai_compatible, explicit DeepSeek model/URL fallback; preserve configured other providers. Static markup initially selects compatible provider, shows DeepSeek placeholders, and describes per-user persistent configuration. Do not store keys in browser storage.
- [x] Run Node tests, `node --check src/web/static/js/home/main.js`, existing frontend safety tests. Report red/green evidence; no backend edits.

## Task 4 — Integration, docs, review and handoff

- [x] Update `.env.example`, README and handoff: new user path, plaintext/private boundary, old UI env precedence intentionally removed, acceptance CLI still uses env, generator unchanged. No real key values.
- [x] Run combined tests, `tests/agent`, relevant Node scripts and `python -m compileall -q src scripts`.
- [x] Independent specification review then independent quality review; fix actionable issues with tests.
- [ ] Commit only scoped files; create draft PR targeting main. Do not merge without specific PR authorization.
- [ ] Local activation is a separate bounded operation after reviewed code is ready: preserve non-LLM runtime fields; do not inspect/output credentials; do not use user history keys. Never claim external connectivity without an actual separately authorized request.

Tests use synthetic credentials and temporary directories only. Runtime activation status must be reported separately from code completion.
