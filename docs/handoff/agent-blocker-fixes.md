# Agent 分批集成阻塞修复

日期：2026-09-09。分支 `codex/agent-integration-blockers`，起点 `eb93c43`。
本文件记录隔离实验入口的修复，不代表启用生产 Agent、真实模型验收或批准合并。

## 范围

- 保持原工作树、生产配置、真实数据及模型权重不变。
- `decision_continuation.py`、`decision_loop.py`、`decision_lab.py`：初始输入、澄清认领、等待快照使用共享的凭据检测；拒绝时不把文本发给模型，也不消费澄清 nonce。
- `persistence/redaction.py`：增加仅检测凭据/敏感赋值的递归 helper，不把合法 SMILES 斜线当机器路径。不是对所有未标注字符串的通用 DLP 保证。
- `decision_inputs.py`：同时绑定用户输入和证据引用的活性靶点；使用原始请求及历次用户澄清，绝不拼回旧无效 SMILES，也不从模型消息推断靶点。
- `tools/activity_input.py`：在已有受控标签语法上补充英文 Predict/Assess/Evaluate TARGET activity/potency。未知或冲突靶点明确拒绝。
- 活性观察的 `request_input_digest` 同时覆盖 query 和靶点；补充靶点后，即使再次输入同一 SMILES，也不能重新启用旧的无靶点活性证据。其他工具保持原 query 身份。
- 证据完整性同时检查 ledger 中的 evidence 和 artifacts；覆盖下游引用、重复工具缓存、finish、clarify 和异常终态。
- 拒绝被修改的结果时，清除污染的结果及共享状态输出；异常 artifact/非有限值收敛为结构化失败。
- 模型观察在持久化回调前完成隔离、校验和编码；终态从已校验结果及 ledger 重建状态，避免工具保留的旧引用在持久化回调中再次污染输出。不能仅复制已有 state，因为它本身可能保留旧引用。

## 兼容性

- 实验续接协议 revision 从 2 升为 3。旧等待快照不能继续使用，需重新发起请求；不承诺旧快照无缝迁移。
- 未指定靶点的 legacy 活性调用保持原输入形式；有明确靶点时使用结构化 query/target。
- 不更换工具、阈值、科学模型，不接管生产入口，不新增工作流框架。
- 待合并源分支包含历史实现，禁止直接将此分支全部合入 main。后续应按集成清单抽取本任务提交/必要差异。

## TDD 与审查记录

- 初轮有效 RED：16 failed、4 passed；其中 artifact 测试随后修正必填 label，避免把测试构造异常误当拒绝成功。
- 扩展缓存分支 RED：2 failed；扩展共享 state.outputs 污染 RED：4 failed。
- 独立规格审查复现延迟修改、英文换靶、异常 artifact/NaN：持久回归 11 failed，之后完成修复。
- 原无靶点证据在补充靶点后被重新启用：1 failed，之后用靶点绑定的输入摘要修复。
- 独立质量审查又复现旧 state 引用污染及新观察回调注入：3 项持久回归先全部 failed，再修复。
- Gibbs 完成规格复审：无剩余规格阻塞；43 项回归及 4 个独立生命周期场景通过。
- Dirac 完成质量复审：原两项 P1 关闭，未发现本次范围内新增 P0–P2 或直接相关回归；46 项 blocker、14 项相关回归及 6 个独立场景通过。审查是代码范围判断，不代表绝无潜在缺陷或生产启用授权。

## 已执行命令

解释器为本机 MedChat Conda 环境。测试使用显式 scripted 模型、合成工具和临时 SQLite；真实 RDKit 回归不等于真实外部模型或活性模型验收。没有读取或写入真实密钥。

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent/test_decision_merge_blockers.py tests/agent/test_decision_inputs.py tests/agent/test_decision_continuation.py tests/agent/test_decision_lab.py -q -p no:cacheprovider --tb=short
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -m pytest tests/agent tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B scripts/run_agent_acceptance.py --mode contract
node tests/decision_lab_ui_test.js
git diff --check
```

最终聚焦（上述第一条命令）：**112 passed**。
最终全 Agent/反幻觉/健康：**2097 passed、1 skipped、7 deprecation warnings**，118.35 秒。
跳过项为 `test_langgraph_shadow_plan_simulation_performance`（未启用 `MEDCHAT_RUN_PERF_TESTS`）；不是功能验收失败。警告来自现有 SWIG 类型及 FastAPI `on_event` 弃用提示，本任务未扩大到生命周期迁移。
此前中间全量结果 2074/2093/2094 passed 反映修复/用例逐步补充，不与最终结果相加。

Contract：34/34 passed；报告保留在本地 `outputs/agent_evaluation/agent_acceptance_report.json`，不提交。
Node 隔离页面：27 passed。`src`、`scripts` compileall 已通过，完整编译使用短临时 pycache_prefix，避免 Windows 深路径问题。

## 合并状态

本轮尚未 push 或合并。PR #7 已合并的状态不因本轮修复改变；PR #8 为独立解析变更，不与这里的实验 Agent/活性模型改动混合。
独立规格与质量审查已通过。后续按小批 PR 验证和明确授权执行合并，不将此历史分支整体发布。
