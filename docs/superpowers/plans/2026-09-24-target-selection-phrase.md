# 英文候选筛选短语 Implementation Plan

> **For agentic workers:** Use executing-plans for the tightly coupled parser/TDD steps; use requesting-code-review for independent specification and quality checks.

**Goal:** 正确处理句尾有界 `and/, select top N`，不放宽未知靶点或科学执行门禁。

**Architecture:** Router 和 Planner 继续共享 `analyze_target_request`。只在现有 `_FOLLOWING` 循环的动作边界增加完整后缀匹配，不重写查询或全局动作词。

**Tech Stack:** Python、标准库 re、pytest、既有 Router/Planner/WorkflowExecutor。

## 范围和发布顺序

用户已确认书面设计并要求进入 TDD。本地独立分支先完成测试与最小修复；不推送、不创建 PR、不合并 #53。发布前仍须在 #53 获授权合并后对齐基线，并更新其保留旧 select 澄清行为的特征测试。当前基线没有该测试，不能声称已完成这项集成。

## Task 1：可复现失败与回归矩阵

Files:
- Create: `tests/agent/test_target_selection_phrase.py`
- Read: `tests/agent/test_target_identity_alignment.py`

- [x] 添加实际 Router→Planner 正向测试，覆盖 and/中英文逗号、大小写、空格/tab、1–100、candidates/molecules、句末标点。

核心断言：

```python
query = 'Design 2 molecules for PDE5A and select top 3'
decision = HybridSkillRouter().decide(query)
assert decision.selected_skill == 'target_driven_design'
assert not decision.requires_confirmation
plan = TaskPlanner().plan(AgentContext(query=query, active_skill=decision.selected_skill, trace_id='selection-test'))
assert plan.metadata == {'target_hint': 'PDE5A', 'requested_count': 2, 'docking_top_n': 3}
assert len(plan.steps) == 6
assert plan.steps[1].preconditions == ('target_evidence',)
```

- [x] 添加负向矩阵：未知/多靶点、否定/切换/选择性、尾部新条件、or、换行、非法数字、长数字。断言澄清、无计划步骤，原全局动作词无 select。
- [x] 用既有隔离 runner 运行新文件，确认 RED 是目标澄清误判，不是收集/依赖失败。

## Task 2：最小解析修复

Files:
- Modify: `src/agent/contracts/target_request.py`

- [x] 新增完整句尾模式及 helper，数字最多三位，ASCII 大小写匹配，不跨行，不创建查询副本：

```python
_CANDIDATE_SELECTION = re.compile(
    r'[ \t]*(?:[,，]|\band\b)[ \t]*select[ \t]+top[ \t]+'
    r'(?:100|[1-9][0-9]?)(?:[ \t]+(?:candidates|molecules))?'
    r'(?:[ \t]*[.!?。！？])?(?u:\s*)', re.I | re.ASCII,
)

def _is_candidate_selection(query: str, following: re.Match[str]) -> bool:
    return (
        following[1].casefold() == 'select'
        and _CANDIDATE_SELECTION.fullmatch(query, following.start()) is not None
    )
```

- [x] 仅修改最终后续标识循环的退出条件：既有动作词或 `_is_candidate_selection(query, following)`。不变更列表起点发现、原文靶点扫描、限定词检查。
- [x] 新测试 GREEN；既有 target identity、planner、routing 回归保持通过，保留线性操作次数门禁。

## Task 3：执行安全、联合回归及交接

Files:
- Create: `tests/agent/test_target_selection_execution.py`
- Create: `docs/handoff/target-selection-phrase.md`

- [x] 用真实 WorkflowExecutor.prepare 验证合法输入可编译；真实 execute 配合明确标注的受控无证据服务验证 target not_found/unavailable 时禁止生成，不能算作科学验收。
- [x] 非法短语强制指定设计 skill 仍返回 invalid_input，工具未执行。
- [x] 独立规格审查后独立质量审查；发现问题按 RED→GREEN 修复。
- [x] 全 Agent/相关模块联合回归、断网 contract、内存源码编译、diffcheck。记录实际失败/跳过，不使用真实 API 或模型。
- [x] 只暂存本批文件，本地提交；记录 #53 基线对齐与旧特征测试更新尚待完成。

## 实际运行方式

复用 `2026-09-24-rag-service-extraction.md` 中隔离 runner：环境白名单清空继承凭据、TemporaryDirectory 配置和数据库、复制三份已跟踪案例并校验 hash；只替换 worktree 路径为当前目录。

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/target-selection-phrase-pr')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_target_selection_phrase.py
exit $LASTEXITCODE
```

聚焦回归参数换为新两文件及 `tests/agent/test_target_identity_alignment.py`、`tests/agent/test_task_planner.py`、`tests/agent/test_routing_prompt_matrix.py`；联合回归沿用已验证的23路径隔离命令。
