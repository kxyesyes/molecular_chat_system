# 分子对接结果明细表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让普通单次对接、持久化对接和历史恢复都展示来自已验证 Vina 结果文件的逐 Pose 明细表，并支持安全地查看指定 Pose。

**Architecture:** 服务端新增有归属校验的结果明细投影，复用现有旧版/持久化结果读取和 `MolecularDockingService` 的严格 Vina 解析器；前端在 `ui_manager.js` 中使用一个统一表格渲染函数，三条链路只负责提供同一数据契约。失败、缺失或历史受体未恢复时保留真实边界，不补造数值、不拼接不匹配复合物。

**Tech Stack:** FastAPI、Jinja2、原生 JavaScript/CSS、Python pytest、Node.js 直接 DOM 契约测试、AutoDock Vina PDBQT。

---

### Task 0: 增加 PDE4A apo + 罗氟司特一键示例

**Files:**
- Modify: `data/REGISTRY.md`
- Create: `data/samples/PDE4A_apo_6FG5.pdb`
- Modify: `src/web/routes/docking_routes.py` near `setup_docking_routes()`
- Modify: `src/web/templates/molecular_docking.html` near the protein upload controls
- Modify: `src/web/static/js/docking/main.js`
- Test: `tests/test_docking_demo_route.py`
- Test: `tests/docking_demo_ui_test.js`

- [ ] **Step 1: Record and verify the real public sample asset**

Download only the RCSB PDB entry `6FG5` to `data/samples/PDE4A_apo_6FG5.pdb`, verify it contains a PDB `HEADER` and `ATOM` records, and record the download date and SHA256 in `data/REGISTRY.md`. The fixed demo SMILES is:

```text
C1CC1COC2=C(C=CC(=C2)C(=O)NC3=C(C=NC=C3Cl)Cl)OC(F)F
```

- [ ] **Step 2: Write failing route and browser-contract tests**

In `tests/test_docking_demo_route.py`, assert that an owned GET to `/api/docking/demo/pde4a-apo` returns `chemical/x-pdb` bytes containing `6FG5` and `ATOM`, and that a missing sample path returns 404 without falling back to another PDB. In `tests/docking_demo_ui_test.js`, assert that `loadDockingDemo()` requests the endpoint, assigns the returned file to `protein-file`, fills the exact roflumilast SMILES, and does not call `startDocking()`.

- [ ] **Step 3: Run the new tests and verify they fail**

Run:

```powershell
$env:TEMP='D:\MedChat\molecular_chat_system\.pytest-temp'; $env:TMP=$env:TEMP
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_demo_route.py -q --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-demo-red
node tests/docking_demo_ui_test.js
```

Expected: FAIL because the demo route, button, and `loadDockingDemo()` do not yet exist.

- [ ] **Step 4: Implement the local demo route**

Add `GET /api/docking/demo/pde4a-apo` to `setup_docking_routes`. Require the existing browser session, resolve only the fixed project-relative sample path, read it in a worker thread, and return it as `chemical/x-pdb`. Return 404 with a Chinese actionable message if the registered asset is missing; do not download from the network during a browser click and do not use another protein as fallback.

- [ ] **Step 5: Implement the small button and input preparation flow**

Add a compact button beside the protein upload control with accessible text `载入示例：PDE4A apo（6FG5）+ 罗氟司特`. Implement `loadDockingDemo()` in `main.js` to fetch the local route, create a `File` named `PDE4A_apo_6FG5.pdb`, assign it through `DataTransfer` to the existing protein input, dispatch the existing change event, set the exact SMILES in `#smiles-input`, dispatch its input event, and call the existing SMILES preview helper. The function must never call `startDocking()`; it should leave box confirmation and submission to the user.

- [ ] **Step 6: Run the demo tests and relevant input regressions**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_demo_route.py tests/test_docking_configuration.py -q --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-demo-green
node tests/docking_demo_ui_test.js
node tests/docking_box_state_test.js
node tests/docking_ligand_upload_layout_test.js
```

Expected: all PASS; missing assets produce a visible error and the example never auto-submits.

### Task 1: 为 Vina 结果建立可复用的服务端逐 Pose 投影

**Files:**
- Modify: `src/docking/molecular_docking_service.py:1151-1280`
- Modify: `src/web/routes/docking_routes.py:304-430`
- Test: `tests/test_docking_result_summary.py`

- [ ] **Step 1: Write the failing parser tests**

在 `tests/test_docking_result_summary.py` 添加真实 PDBQT 文本夹具，至少包含两个连续 `MODEL`，每个模型包含有限结合能、RMSD 下限/上限和一个合法 ATOM 记录。添加以下断言：

```python
def test_parse_vina_results_text_returns_all_verified_pose_rows():
    rows = MolecularDockingService.parse_vina_results_text(TWO_POSE_PDBQT)
    assert [row["pose"] for row in rows] == [1, 2]
    assert rows[0]["binding_energy"] == -8.4
    assert rows[1]["rmsd_ub"] == 2.7

def test_parse_vina_results_text_rejects_incomplete_pose():
    assert MolecularDockingService.parse_vina_results_text(INCOMPLETE_PDBQT) == []
```

- [ ] **Step 2: Run the parser tests and verify the expected failure**

Run:

```powershell
$env:TEMP='D:\MedChat\molecular_chat_system\.pytest-temp'; $env:TMP=$env:TEMP
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_result_summary.py -k parse_vina_results_text --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-result-red -q
```

Expected: FAIL because `parse_vina_results_text` does not yet exist.

- [ ] **Step 3: Implement the text parser without changing the existing path API**

Extract the current strict parsing loop into `MolecularDockingService.parse_vina_results_text(text: str) -> list[dict]`, returning only `pose`, `binding_energy`, `rmsd_lb`, and `rmsd_ub`. Keep `parse_vina_results(path)` as a compatibility wrapper that reads UTF-8 text and delegates to the new method. Preserve sequential MODEL checks, finite-number checks, atom validation, and rejection of malformed score remarks.

- [ ] **Step 4: Run the parser tests and verify they pass**

Run the same command from Step 2. Expected: both parser tests PASS; then run the existing parser/history tests:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_p1_scientific_integrity.py tests/test_docking_history_index.py -q --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-result-parser -x
```

- [ ] **Step 5: Add a failing route projection test**

Extend `tests/test_docking_result_summary.py` with a small FastAPI route harness using the existing `setup_docking_routes` dependency pattern. Assert that an owned legacy or durable job returns:

```python
assert response.json() == {
    "success": True,
    "job_id": "job-1",
    "total_poses": 2,
    "results": [
        {"pose": 1, "binding_energy": -8.4, "rmsd_lb": 0.0, "rmsd_ub": 0.0,
         "ligand_efficiency": None},
        {"pose": 2, "binding_energy": -6.2, "rmsd_lb": 0.9, "rmsd_ub": 2.7,
         "ligand_efficiency": None},
    ],
}
```

Also assert that an unowned job returns 404 and malformed PDBQT returns a non-success response without a `results` array.

- [ ] **Step 6: Run the route test and verify it fails for the missing endpoint**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_result_summary.py -k summary_route --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-result-route-red -q
```

Expected: FAIL because `/api/docking/result/{job_id}/summary` is not registered.

- [ ] **Step 7: Implement the owned summary route**

Add `GET /api/docking/result/{job_id}/summary` before the existing raw-result route. Reuse `read_owned_docking_pose(job_id, request)`, decode the returned bytes strictly as UTF-8, delegate to `parse_vina_results_text`, and return the bounded row projection. Do not include raw PDBQT, absolute paths, task internals, or fabricated ligand efficiency. Return 404 for ownership/artifact failures and 422/422-equivalent domain error for invalid result content.

- [ ] **Step 8: Run the route and regression tests**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_result_summary.py tests/test_docking_pdbqt_viewer.py tests/test_docking_history_ownership.py -q --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-result-route-green
```

Expected: PASS with no route ownership regression.

### Task 2: 抽取统一的前端结果表渲染器

**Files:**
- Modify: `src/web/static/js/docking/ui_manager.js:1185-1335`
- Modify: `src/web/templates/molecular_docking.html:273-307`
- Modify: `src/web/static/css/docking.css` (the existing docking stylesheet containing `.results-table`)
- Test: `tests/docking_result_table_ui_test.js`

- [ ] **Step 1: Write failing DOM tests for the shared renderer**

Create `tests/docking_result_table_ui_test.js` with the existing vm harness style. Load `ui_manager.js`, provide `results-content`, `start-btn`, `pose-select`, and viewer stubs, then assert:

```javascript
test("shared renderer shows every verified pose and real numeric fields", () => {
  renderDockingResultsTable({job_id: "job-1", total_poses: 2, best_pose: {pose: 1}, results: rows}, {restored: false});
  assert.equal(get("results-content").querySelectorAll("tbody tr").length, 2);
  assert.match(get("results-content").textContent, /-8\.4/);
  assert.match(get("results-content").textContent, /2\.7/);
  assert.match(get("results-content").innerHTML, /viewPose\(2\)/);
});

test("shared renderer never turns missing values into zero", () => {
  renderDockingResultsTable({job_id: "job-1", total_poses: 1, results: [{pose: 1, binding_energy: null, rmsd_lb: null, rmsd_ub: null, ligand_efficiency: null}]});
  assert.match(get("results-content").textContent, /未计算/);
  assert.doesNotMatch(get("results-content").textContent, /0\.0/);
});
```

- [ ] **Step 2: Run the new UI test and verify it fails**

Run:

```powershell
node tests/docking_result_table_ui_test.js
```

Expected: FAIL because `renderDockingResultsTable` is not yet exposed by the production module.

- [ ] **Step 3: Implement the renderer with safe numeric formatting**

Add `renderDockingResultsTable(data, options = {})` to `ui_manager.js`. It must:

- accept only an array of row objects and positive integer Pose numbers;
- use `Number.isFinite` for every numeric field;
- render `未计算` for missing values;
- escape all text and inline Pose arguments through `Safe` helpers;
- include the screenshot columns and a “查看” action only when the task can safely view a matching receptor;
- render a bounded empty/error state instead of an empty successful table;
- preserve existing download/report/rerun actions.

Replace the duplicated table-building block inside `showRealResults()` with this renderer. Use `data.best_pose.pose` when auto-loading the first complex so a non-Pose-1 best result is not silently changed.

- [ ] **Step 4: Add the result-table CSS and responsive behavior**

Extend the existing docking stylesheet, not a new framework, with scoped classes for the result summary, table wrapper, energy badge, and mobile overflow. Keep the current colors but make the header row and completed status visually match the reference: light blue completion strip, compact table header, orange/red energy badge, and indigo action button. Add `aria-label` text to action buttons and keep keyboard focus visible.

- [ ] **Step 5: Run the UI test and all existing docking UI tests**

Run:

```powershell
node tests/docking_result_table_ui_test.js
node tests/docking_task_ui_behavior_test.js
node tests/docking_history_best_pose_ui_test.js
node tests/docking_interaction_regressions_test.js
```

Expected: all commands PASS; no legacy result or history test may regress.

### Task 3: Connect durable completion and history restoration to the shared table

**Files:**
- Modify: `src/web/static/js/docking/ui_manager.js:834-940, 2030-2180`
- Modify: `tests/docking_task_ui_behavior_test.js`
- Modify: `tests/docking_history_best_pose_ui_test.js`

- [ ] **Step 1: Write failing durable and history integration assertions**

Add a durable-success test whose record contains `result_job_id` and `best_pose.pose`, whose summary fetch returns the two-row JSON projection, and assert that `renderDurableTaskSuccess()` eventually renders both rows and passes the explicit best Pose to `loadComplexStructure`. Add a history test that restores the same two rows and asserts that the table remains visible while `viewPose(...)` is absent when the receptor is not restored.

- [ ] **Step 2: Run the integration assertions and verify the expected failure**

Run:

```powershell
node tests/docking_task_ui_behavior_test.js
node tests/docking_history_best_pose_ui_test.js
```

Expected: the new durable assertion fails because the current durable path renders only the summary card and never requests the summary endpoint.

- [ ] **Step 3: Implement durable summary loading**

After the durable task passes `durableDockingResult()` validation, request `/api/docking/result/${encodeURIComponent(result.result_job_id)}/summary`. On success, pass the returned `results` and explicit best pose into `renderDockingResultsTable`. Until the request resolves, keep the validated summary visible. On failure, keep that summary and add “结果明细暂不可用” with a retry action; never render placeholder rows.

- [ ] **Step 4: Route history restoration through the same renderer**

Keep the existing strict PDBQT parsing for legacy history as the source of rows, but pass its normalized output to `renderDockingResultsTable({job_id, total_poses, results, best_pose}, {restored: true})`. Do not re-enable Pose viewing or complex viewing unless the current receptor identity is verified for that historical task.

- [ ] **Step 5: Run the focused integration and security tests**

Run:

```powershell
node tests/docking_task_ui_behavior_test.js
node tests/docking_history_best_pose_ui_test.js
node tests/frontend_safe_render_test.js
node tests/docking_p0_frontend_test.js
```

Expected: all PASS; no task result may display a score when the server response omitted it.

### Task 4: Verify the complete application with a real started project

**Files:**
- Modify only if a real run exposes a defect in: `src/web/routes/docking_routes.py`, `src/web/static/js/docking/ui_manager.js`, or the existing docking CSS.
- Test: `tests/test_docking_result_summary.py`, `tests/docking_result_table_ui_test.js`, and the already-existing docking regression suites.

- [ ] **Step 1: Run static and focused checks before startup**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src/docking src/task_runtime src/web
node --check src/web/static/js/docking/ui_manager.js
node --check src/web/static/js/docking/main.js
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/test_docking_result_summary.py -q --basetemp D:\MedChat\molecular_chat_system\.pytest-temp-result-final
```

- [ ] **Step 2: Start the real service in a persistent terminal session**

Use the MedChat environment and the configured real tools, with a fresh port and the existing environment variables:

```powershell
$env:MEDCHAT_CHAT_DOCKING_ENABLED='1'
$env:MEDCHAT_USER_CONFIG_DIR="$env:LOCALAPPDATA\MedChat\config"
$env:MEDCHAT_LLM_LOCK_DIR='D:\MedChat\molecular_chat_system\.medchat-ui-locks'
$env:REVERSE_TARGET_DATA_DIR='D:\MedChat\runtime_data\reverse_target'
$env:MEDCHAT_DOCKING_VINA_EXE='D:\Chemicaltools\Dock\autodock\Vina\vina.exe'
$env:MEDCHAT_DOCKING_ADFR_BIN='D:\Chemicaltools\Dock\autodock\ADFRsuite\bin'
$env:MEDCHAT_DOCKING_MEEKO_EXE='D:\Python\Python311\Scripts\mk_prepare_ligand.exe'
& 'C:\Users\xkx52\.conda\envs\MedChat\python.exe' -u main.py --port 6061 --no-reload
```

Confirm the service reaches a live HTTP response before interacting with the page; do not use a cached browser tab as evidence.

- [ ] **Step 3: Exercise the single-task frontend flow with real Vina**

In the browser, open the running service, use a verified local receptor and ligand/SMILES, set the docking box, start the task, wait for the terminal success state, and inspect the result table. Record only values visible in the actual result response. Click a non-best Pose row and confirm the viewer request uses that exact Pose number.

- [ ] **Step 4: Exercise history restore and the failure boundary**

Open the history panel, restore the same completed task, confirm all rows remain visible, and confirm the UI does not pair the historical artifact with a different current receptor. Also verify that a failed task displays no fabricated energy or Pose rows.

- [ ] **Step 5: Run the complete applicable regression set and report evidence**

Run the focused Python suites, docking JavaScript suites, `compileall`, and `node --check`. Report the actual service port, real task outcome, table row count, clicked Pose, any unavailable checks, branch, and commit state. Do not claim full end-to-end success if the real Vina task or browser service is unavailable.
