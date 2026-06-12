# MedChat Agentization Phase 2-3 Implementation Notes

## Phase 2: Service Layer and Async Task Runtime

Implemented a lightweight, local-first async task foundation without changing existing module APIs.

- `src/task_runtime/`
  - SQLite-backed task table at `scratch/tasks.sqlite` by default.
  - Configurable with `MEDCHAT_TASK_DB_PATH` and `MEDCHAT_TASK_WORKERS`.
  - Records `queued / running / succeeded / failed / canceled` style task states.
  - Stores input payload, result JSON, error text, timestamps, and artifacts metadata.
- `src/web/api_response.py`
  - Provides a standard response envelope for new APIs.
  - Existing legacy APIs are not rewritten yet to avoid regressions.
- `src/system/data_versions.py`
  - Reports target database, structure cache, reverse-target data, activity models, and RAG index status.
- New APIs:
  - `GET /api/tasks`
  - `GET /api/tasks/{task_id}`
  - `POST /api/tasks/demo`
  - `GET /api/system/data-versions`
  - `GET /api/system/runtime`
- `scripts/health_check.py`
  - Adds checks for Task Runtime and Supervisor Agent.

## Phase 2: Molecular Design BRICS Path Decoupling

`brics/fragments_labeled.csv` is still kept in the original location to avoid a large data-file move in this phase.

The Web route now resolves the fragment database through:

- `src/molecular_design/fragment_repository.py`

The resolver prefers, in order:

1. `MEDCHAT_FRAGMENT_DB_PATH`
2. `src/molecular_design/data/fragments_labeled.csv`
3. `brics/fragments_labeled.csv`

This lets a future data migration move the file without changing route code.

## Phase 3: Supervisor Agent and Workflow API

Implemented the first Supervisor Agent layer on top of the existing planner and workflow orchestrator.

- `src/agent/supervisor.py`
  - Routes or accepts an explicit `skill_name`.
  - Builds deterministic workflow plans with `TaskPlanner`.
  - Executes plans through `WorkflowOrchestrator`.
  - Returns structured plan, summary, tool results, warnings, evidence, and artifacts.
- New APIs:
  - `POST /api/agent/workflows/plan`
  - `POST /api/agent/workflows/run`

`/api/agent/workflows/run` submits a background task and returns a `task_id`; the frontend can poll `/api/tasks/{task_id}`.

## Next Migration Targets

The next practical step is to convert the existing long-running module APIs one by one:

1. Molecular docking submit and batch submit.
2. Reverse-target batch prediction and 3D pharmacophore refinement.
3. Activity batch prediction and model training.
4. Molecular generation batch runs.
5. Frontend task progress components that poll `/api/tasks/{task_id}`.

Keep the old direct endpoints during migration, and add async variants first.
