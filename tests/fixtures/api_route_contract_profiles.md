# API route contract profiles

These fixtures freeze the **pre-extraction registrar at 0430ad8**, not the
current domain modules. They protect all 30 ordered operations, endpoint names,
status codes, source-level parameter signatures and the complete OpenAPI document.
There is no schema-field normalization, conditional omission, or automatic
snapshot update in the test.

## Exact supported versions

| Profile | FastAPI | Pydantic | Fixed fixture |
| --- | --- | --- | --- |
| Host | 0.135.3 | 2.12.5 | `api_route_contract.json` |
| CI pinned web dependencies | 0.104.1 | 2.5.0 | `api_route_contract_ci_deployment.json` |
| Deployment pinned web dependencies | 0.115.6 | 2.10.4 | `api_route_contract_ci_deployment.json` |

The CI and deployment snapshots were captured independently from the old
registrar and compared in full; they are identical, so they share one data file.
This does **not** imply that every runtime error response is identical across
those frameworks (see below). Selection uses only the exact verified
FastAPI/Pydantic pair; other patch versions and mixed pairs fail explicitly.
The host fixture has not been changed.

Capture date: 2026-09-25. The old registrar was saved before extraction and loaded
from a temporary Python module. Its UTF-8/LF source with one final newline has
SHA-256 `0b3bdaff13a5d43b1fe3ad5459617f455200024f8a55d5e8a272eb64839c93dc`.
No second implementation or copy of its algorithms is stored in these fixtures.

The first capture's provenance remains `0430ad8`. For future profile maintenance,
use the stable main commit `aa86377c60ff4c8a0457dc28ab6a9d6b4856c194` instead:
`git rev-parse <commit>:src/web/routes/api_routes.py` confirmed that both revisions
reference the same blob. This avoids depending on the reachability of a branch
commit after squash merging.
Verified blob: `f8db94a0c2839a387b92b46390622ae4bd02d0c5`.

Fixture byte SHA-256:

- Host: `acf013a93673ec19e422753fcb191b25bf67ed8c4559f717992c76fd59f4ff28`.
- CI/deployment: `a11ee3627de74c3debdd35efe428a16c09d821cf1aea9b6a617a8ed39c229ab0`.

Recorded auxiliary versions (not additional snapshot-selection rules):

| Profile | Starlette | python-multipart | HTTPX | AnyIO | pytest |
| --- | --- | --- | --- | --- | --- |
| Host | 1.0.0 | 0.0.24 | 0.28.1 | 3.7.1 | 9.1.0 |
| CI web target | 0.27.0 | 0.0.6 | 0.25.2 | 3.7.1 | 9.1.0 |
| Deployment web target | 0.41.3 | 0.0.20 | 0.28.1 | 4.15.1 | 9.1.0 |

## Observed framework differences; none discarded

Relative to host, both CI and deployment have exactly 24 schema-field
differences:

1. Nine upload schema nodes use `format: binary` instead of
   `contentMediaType: application/octet-stream` (18 added/removed fields).
   These nodes are the file fields of activity batch, reverse-target batch,
   activity training, and the protein/ligand fields of durable, synchronous and
   batch docking, including batch ligand-array items.
2. `ValidationError.properties` does not declare `input` or `ctx` (2 fields).
   The older frameworks still return those values in relevant runtime errors.
3. Four arbitrary-dictionary request schemas omit the host's explicit
   `additionalProperties: true` (4 fields): activity model switching, docking
   report, SMILES-to-3D and molecule properties.

No other differences were found in the complete OpenAPI comparison, including
operationId, required, alias, ranges, defaults, nullability, response status,
media type, docstring, title, summary, or application metadata. All these remain
in the exact snapshot comparison.

Four controlled invalid requests (missing protein upload, image width below its
minimum, invalid training epochs/learning rate, and a list instead of a JSON
object) produce five validation errors. Old and new registrars return identical
responses **within each profile**. Across profiles, CI adds a Pydantic
`errors.pydantic.dev/2.5/v/...` URL to each error; host and deployment omit it.
All other observed response values match. These URLs are framework-produced
metadata, not a production response changed by extraction. They were not
removed or rewritten. The shared schema fixture must not be mistaken for a
cross-profile wire-response identity guarantee.

## TDD and validation evidence

All runs used the isolated PowerShell `$runner` pattern documented in
`docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, with this worktree
as repo, a temporary cwd, cleared/non-secret environment, isolated configuration
and data paths, real-service switches disabled, and the MedChat interpreter:

`$runner | & $python -B -c "import sys; exec(sys.stdin.read())"`

The target file was `tests/test_api_route_boundary.py`; pytest options were
`-q -p no:cacheprovider --tb=short -rs`. CI/deployment web packages were installed
with `pip --isolated install --no-cache-dir --only-binary=:all: --target <temporary-target>`,
using the exact four pinned FastAPI/Pydantic/python-multipart/HTTPX versions
above, then prepended to `sys.path` in separate processes. No global packages
were installed or replaced. Service/network connections were blocked during
tests; the Windows socketpair fallback used by asyncio was allowed.

| Run | Actual result | Exit |
| --- | --- | --- |
| CI, unchanged host-only snapshot (RED) | 1 failed, 67 passed, 1 warning in 5.85s | 1 |
| Selector assertions before implementation (RED) | 1 failed, 67 deselected in 0.61s; selector missing | 1 |
| Host, final suite after stable-main anchor update | 68 passed in 5.38s | 0 |
| CI target, final suite after stable-main anchor update | 68 passed, 1 warning in 5.94s | 0 |
| Deployment target, final suite after stable-main anchor update | 68 passed, 1 warning in 5.27s | 0 |
| Simulated unknown FastAPI 0.135.4 with Pydantic 2.12.5 | 1 failed, 67 deselected in 0.56s; explicit baseline instructions | 1 (expected) |

The initial CI failure was only the complete OpenAPI assertion, not collection
or an endpoint behavior test. Each final suite still executes the original 68
cases, with selection/rejection assertions added inside the registration test.
For all three profiles, independently captured old/new full contracts and the
four controlled response probes matched.

CI emitted the existing Pydantic warning for `model_provenance` versus protected
namespace `model_`. Deployment emitted Starlette's deprecated
`anyio.abc.BlockingPortal` alias warning with the resolved AnyIO version.
Warnings were not suppressed and production code was not changed to fix them.

These are Windows/Python 3.10 **web-dependency profile** checks, not full Linux CI
or CUDA deployment acceptance. pytest and scientific libraries came from the
host MedChat environment. Runtime fixtures use synthetic inputs and temporary
files; there were no real model, database or service calls. Historical properties
heuristics are characterized only, never accepted as scientific validation.

## Adding another profile

1. Do not regenerate a fixture from the current implementation and do not skip
   the assertion or fall back to the closest known version.
2. Recover `src/web/routes/api_routes.py` from the pre-extraction revision
   `aa86377c60ff4c8a0457dc28ab6a9d6b4856c194` (stable main) into an isolated
   temporary location. Do not overwrite the worktree.
   Verify its provenance/source hash; retain the old source, not a reconstruction
   from current domain modules.
3. Install the proposed dependency profile into a temporary target or isolated
   environment, record actual versions, and retain the same environment/cwd/
   configuration/data isolation as the documented runner.
4. Register the old implementation on a bare FastAPI app and capture
   `registration_contract(app)`: all ordered operations/signatures plus
   `app.openapi()`. The helper in the test serializes observations; it must be
   invoked on the **old** registrar to obtain expected data.
5. Separately register the new implementation under the same versions. Compare
   the full old/new contracts and controlled runtime responses. Investigate
   differences; never absorb a migration regression into the expected snapshot.
6. Review framework schema and runtime differences against existing profiles.
   Save a fixed `api_route_contract*.json` file, or share an existing file only
   after independently proving complete equality. Preserve every parameter,
   required flag, alias, range, request/response schema and operationId.
7. Add the exact FastAPI/Pydantic pair to both the selector and its fixed mapping
   assertions; retain unknown-version negative controls. Run the complete
   boundary suite under every supported profile and record results here.
