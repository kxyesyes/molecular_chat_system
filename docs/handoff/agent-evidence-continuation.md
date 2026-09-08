# A2 第二批：证据引用与显式澄清恢复

日期：2026-09-08。分支：`codex/agent-evidence-continuation`；基线 `cb1cc39`。
工作位置为独立 scientific-assets worktree。原项目工作树未修改。

## 本批结果

仍是模型逐轮决定 tool / clarify / finish，不使用静态 Planner。复用既有 LangGraph、WorkflowRunSession、ToolAdapter、领域校验器、EvidenceLedger 和 SQLite，不另建执行框架。

- `input_ref=user` 绑定当前完整用户输入；`input_ref=evidence-...` 只绑定本 trace、当前输入、科学可用的分子观察。服务端校验输出摘要、已登记 provenance、完整 SMILES，再用既有 BindingResolver 提取下游参数。
- 下游引用记录 `input_evidence_ids`；缓存键包含工具、版本和实际绑定输入，不能仅按工具名复用。
- 结果被后续修改（包括修改后重新计算摘要、改标成功、重分配输入归属）时，引用和最终科研输出拒绝它，不能把改动后的值当成已验证结果。Ledger 深复制记录并将动态 input_binding 纳入证据身份。
- 明确进入 waiting 的任务可以在重新实例化 loop/store 后恢复。要求可信调用方提供原始 context、相同 user/session、权限/配置/预算和一次性 continuation_id。配置指纹同时包含工具版本和适配器版本。
- 用户提交完整修正输入；模型不自行补结构。新输入不能用旧分子的结果完成 required_tools；历史失败与证据仍保留在 tool_results 中。
- 恢复保留原 trace、消息、调用 ID、累计调用次数、工具预算和剩余执行时限。等待用户的时间不扣减执行时限；不会重置执行预算。
- SQLite `BEGIN IMMEDIATE` 原子领取等待快照。并发请求只有一个能领取；领取提交状态不确定时不重试、不派发工具，保留 running 供人工核查。新 waiting 发布可幂等重试，不能覆盖已经领取的状态。
- 恢复不是 crash replay：running、未知执行结果、非等待终态、损坏快照、权限变更一律拒绝。没有 Web 登录/身份认证新实现，服务端调用方的身份信息必须可信。

## 调用方式与范围

```python
waiting = await loop.run(original_context, request_kind='scientific',
    allowed_tools={'property_calculator'}, required_tools={'property_calculator'})

result = await fresh_loop.run(original_context, request_kind='scientific',
    allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
    continuation_id=waiting.metadata['continuation_id'],
    clarified_query='SMILES: CCO')
```

`original_context` 是第一次调用的上下文，而非自行替换 query 后的新上下文。`clarified_query` 是用户完整修正输入。user_id/session_id 必须均非空；匿名任务保留旧等待提示但不开放恢复。调用方不可把浏览器或模型提交的 allowed_tools/required_tools/identity 直接当成可信授权。

当前仍仅开放四工具：property_calculator、drug_likeness_assessment、activity_predictor、target_database_search。分子引用仅用于前三者；没有新增候选生成、反向寻靶或 docking 引用适配。工具级 required_tools 也不等于已经支持所有自然语言科研目标验收。

快照在已有 agent_runs.metadata_json 中，最多 512 KiB；不另建数据库。持久化内容必须经既有脱敏器检查，凭据/私有推理不入快照。checksum 用于检测损坏，不是抵御恶意数据库管理员的数字签名；数据库仍属于可信部署边界。

## 文件

新增：

- `src/agent/harness/decision_inputs.py`
- `src/agent/harness/decision_continuation.py`
- `tests/agent/test_decision_inputs.py`
- `tests/agent/test_decision_continuation.py`
- `docs/superpowers/plans/2026-09-08-agent-evidence-continuation.md`
- 本交接文档。

修改：

- `src/agent/harness/decision_loop.py`
- `src/agent/harness/decision_policy.py`
- `src/agent/runtime/run_session.py`
- `src/agent/evidence/ledger.py`
- `src/agent/persistence/base.py`
- `src/agent/persistence/sqlite_store.py`

关键阅读：仓库规范、上一批 A1/A2 交接、已批准设计、工具契约/注册/绑定/校验、持久化与事件实现、原项目 Git 状态。没有读取密钥文件。

## 验证

解释器：既有 MedChat Conda Python。命令在本 worktree 执行。

| 命令 | 结果 |
|---|---|
| `python -B -m pytest tests/agent/test_decision_inputs.py tests/agent/test_decision_continuation.py tests/agent/test_decision_loop.py -q -p no:cacheprovider --tb=short` | 76 passed |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1820 passed、1 skipped、7 warnings，101.55 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract` | 34/34 passed；报告在忽略目录 outputs/agent_evaluation/agent_acceptance_report.json |
| `python -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

TDD：先观察澄清恢复 13 个用例因缺 continuation_id 失败，再实现；结果篡改新增两项先失败再修复。真实 RDKit 通过性质→类药性及无效输入经用户修正后重算。模型决策使用测试替身；既有 HTTPX MockTransport 覆盖协议，不是外部 LLM 实测。没有调用生产权重、Ollama、Vina 或真实外部 API。

唯一 skipped 为未启用的 harness shadow 性能测试；7 条 warning 为既有 SWIG/FastAPI 弃用提示。负例 RDKit parse error 是预期无效输入拒绝。

独立代码审查复现并推动修复：可变输入归属、失败观察改标成功、适配器版本漏检，均先建立失败回归再修复。另外自检修复恢复后的累计 step_count 和未知 evidence_id 引发的错误收尾异常。最终独立复审 10 项通过，未发现本批范围内剩余阻断；不代表已完成生产网站迁移验收。

## 下一步

细化可信任务义务（数量、指标、禁止动作）并继续迁移有限工具的结构化输入适配；然后为 Supervisor/ChatHandler 准备隔离接入和真实主模型验收。当前网站入口未切换，旧业务工作流未删除，生产模型未启用。

本批不 push、不创建大型历史 PR、不合并 main、不重启实例。发布仍需先整理依赖提交范围。
