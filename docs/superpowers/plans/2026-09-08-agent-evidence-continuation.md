# A2：证据输入绑定与显式澄清恢复

> **For agentic workers:** Use executing-plans and test-driven-development; request independent code review before delivery.

**Goal:** 让逐轮 Agent 使用本任务已验证 SMILES 观察，并在用户明确补充完整输入后安全恢复。

**Architecture:** 保留单一 StateGraph 和 WorkflowRunSession。引用经过 EvidenceLedger、输出摘要、完整 SMILES 校验，再复用 BindingResolver；SQLite 原子领取等待状态，使用原预算和授权恢复，不建立第二个状态库。

**Tech Stack:** Python、RDKit、LangGraph、Pydantic 2、SQLite、pytest。

## 已批准设计下的具体范围

- 本批仍仅四工具；分子性质/类药性/活性可以消费同 trace 的分子结果。靶点到生成、反向寻靶和 docking 绑定属于后续能力迁移，不在本批开放。
- 参数只允许 `{"input_ref":"user"}` 或 `{"input_ref":"evidence-..."}`，不接受模型新添 SMILES/任意 JSONPath。
- 新增 `run(..., continuation_id=..., clarified_query=...)`：用户提交**完整修正后输入**，不让模型自行合并或修补结构。原始目标及 required_tools 不被模型修改；模型仍见原历史和用户补充。
- 恢复要求原 user_id/session_id 均非空且匹配；原始上下文、权限、工具/主模型配置及预算边界一致。未确认执行、旧版本快照、错误输入、非等待态、错误 continuation ID 均拒绝。
- 澄清等待不消耗人的思考时间，但已用调用次数、工具预算和执行时间不能重置。同一个 continuation 只能领取一次，拒绝隐式 crash replay。
- 当前输入变化后，旧输入的工具结果不能替代新输入完成义务；历史证据/错误继续保留。相同输入的已完成动作可以复用。
- 不改 Web/默认入口，不运行真实外部 API，不激活生产模型，不合并 main。

## Task 1：证据绑定（先 RED 再 GREEN）

Files: `src/agent/harness/decision_inputs.py`, `decision_loop.py`, `decision_policy.py`, `src/agent/runtime/run_session.py`, `tests/agent/test_decision_inputs.py`。

- [x] 测试属性观察 → 类药性实际收到上游 SMILES，而不是原 query。
- [x] 测试未知/跨 trace 引用、demo、失败观察、缺输出摘要、篡改结果、无效完整 SMILES 均在工具开始前拒绝。
- [x] 实现引用解析，先检查来源再调用既有 BindingResolver；记录 input_evidence_ids、operation_key 和 request_input_digest。
- [x] 科学 finish 只用当前输入的证据完成 required_tools；历史工具结果仍在完整轨迹中。

```python
decision = tool('drug_likeness_assessment', {'input_ref': evidence_id})
assert downstream.inputs == [upstream_smiles]
assert result.tool_results[-1].quality['input_evidence_ids'] == [evidence_id]
```

## Task 2：可恢复快照与原子领取

Files: `src/agent/harness/decision_continuation.py`, `decision_loop.py`, `src/agent/runtime/run_session.py`, `src/agent/persistence/{base,sqlite_store}.py`, `tests/agent/test_decision_continuation.py`。

- [x] RED：重新实例化 loop/store 后恢复 waiting；原调用计数继续，已完成动作不再次执行。
- [x] RED：错误用户/会话/nonce、扩大授权、换工具版本、预算配置变更、已领取、损坏快照都不改变原等待记录。
- [x] 保存有界且脱敏的消息、观察、输入摘要、缓存、剩余预算和配置指纹；不保存 key/私有思维链。
- [x] 原子领取同时比较 owner、status、continuation_id 及快照摘要；完成校验后才能领取。
- [x] 复用既有校验/ledger 重建恢复结果；只跳过新建 run，不 upsert 覆盖原运行。
- [x] 恢复后的新输入必须真实计算；旧输入不能冒充新输入的结果。

```python
waiting = await loop.run(context, **permissions)
result = await fresh_loop.run(context, **permissions,
    continuation_id=waiting.metadata['continuation_id'], clarified_query='SMILES: CCO')
assert result.metadata['model_requests'] > waiting.metadata['model_requests']
```

## Task 3：回归与交接

- [x] `python -B -m pytest tests/agent/test_decision_loop.py tests/agent/test_decision_inputs.py tests/agent/test_decision_continuation.py -q -p no:cacheprovider --tb=short`
- [x] `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short`
- [x] 旧 contract 验收、compileall、diff check；独立审查并补齐有效发现的 RED/GREEN。
- [x] 更新 `docs/handoff/agent-evidence-continuation.md`；精确暂存、独立提交，保留原项目工作树。

命令使用既有 MedChat Conda Python。模型为明确测试替身，真实计算只标注实际执行的 RDKit 用例；不能计为真实外部主模型通过。
