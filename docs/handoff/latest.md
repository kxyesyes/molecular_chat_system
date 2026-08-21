# 最新任务交接

## 2026-08-21 Temporal docking canary Stage 3A

### 状态与范围

- 分支：`codex/industrial-agent-temporal-docking-canary`，位于隔离 worktree；原始混杂工作树未被修改、回退或清理，未直接修改或合并 `main`。
- 默认仍为 `MEDCHAT_TASK_BACKEND=local`、`MEDCHAT_TEMPORAL_CANARY_PERCENT=0`；Temporal 仅承接 deterministic docking canary，现有同步 `/api/docking/submit` 保持兼容。
- 本阶段完成 Temporal workflow/activity、独立单并发 worker、进程树取消、completion authority、SQLite 查询投影、异步管理 API、事件/取消、健康检查、指标和 contract/real 发布门禁。
- 未使用或记录外部 API key；Temporal/Vina 验收不需要主聊天模型。报告和日志未保存 receptor、ligand、SMILES、manifest 内容、环境变量值、绝对路径或凭据。

### 本阶段提交

```text
032bdb5 feat(runtime): execute docking with temporal
01775d7 fix(runtime): harden temporal workflow semantics
bc35e2c fix(runtime): close temporal execution races
ec2f267 fix(runtime): make terminal projection completion safe
551477c fix(runtime): isolate temporal docking processes
b809032 feat(runtime): route docking through temporal canary
01aeb49 feat(web): expose durable docking task API
e931bda test(runtime): gate temporal docking canary
63c3399 fix(runtime): preserve verified docking provenance
```

### 真实 Temporal/Vina 验收

- 使用本地 Temporal SDK 开发服务器、独立 worker、仓库 `MAGL_5zun.pdb`、`5.sdf` 和固定 docking box，最终 `repeat=3` 为 `3/3 passed`。
- 三次均为一个 Vina attempt、一个终态事件、10 个 pose；最佳能量分别为 `-3.5`、`-3.6`、`-3.5`，均来自真实 Vina 产物。
- artifact 文件存在且 SHA-256 重算一致；tool/model provenance 完整，`demo_mode=false`、`fallback_used=false`；重复执行数和终态冲突数均为 0。
- 最终 p50 为 `27611 ms`，p95 为 `30464 ms`。报告位于本地忽略路径 `outputs/agent_evaluation/temporal_docking_acceptance.json`，不提交 Git。
- 预验收曾如实失败两轮：第一轮真实工具完成后，`AutoDock Vina` 显示名被过严 provenance 规则拒绝；第二轮任务 3/3 succeeded，但验收器未从严格 artifact 投影解析 pose，且持久化层丢失非 demo/fallback 标志。均先补失败测试，再修复根因并重跑。
- 安全复审进一步关闭了空格拆分科学结构、非布尔科学模式标志、大小写键碰撞和 artifact 类型别名冲突；独立审查最终结论为 `APPROVED`。

### 最终验证

```text
tests/agent：647 passed, 1 skipped
反幻觉、平台健康、real checks：48 passed
task runtime + docking + routes：1029 passed, 14 skipped, 9 subtests passed
Temporal workflow/store/acceptance 聚焦：389 passed
python -m compileall -q src scripts：passed
run_agent_acceptance.py --mode contract：passed
Temporal docking contract repeat 3：passed，raw executor calls=3，duplicate Vina=0
Temporal docking real repeat 3：passed，3/3
git diff --check：passed
```

现有 5 条 warning 为 SWIG 类型和 FastAPI `on_event` 弃用提示；本阶段没有 RuntimeWarning。skip 均为已有环境/平台条件测试，不是伪造成功。

`scripts/health_check.py --strict` 实际为 `20/23`，未通过项是本地缺少 reverse-target ChEMBL 表、activity model 文件和 RAG FAISS index；Temporal Runtime、Vina、ADFRsuite、配体准备、Ollama、任务运行时和样例资产均通过。这 3 项不是本阶段伪装成成功的依赖，也不影响上述真实 docking 证据。仓库长凭据形状正则命中 1 个正常任务名中的 `task-runtime` 子串，人工脱敏核验为误报；未发现真实密钥值。

### 发布与回滚

- 固定晋级顺序：`0% -> 5% -> 10% -> 25%`；每档至少 20 个已接受任务或一个完整观察窗。
- 晋级硬门禁：无重复 Vina、无多终态、无 artifact/hash 失配、取消可追踪、worker heartbeat 新鲜、p95 无显著退化。
- 任一硬门禁失败，将 `MEDCHAT_TASK_BACKEND` 改回 `local`、比例设为 `0`，停止新流量；已接受 Temporal workflow 继续到终态，不得在 local 重放。
- 不删除 SQLite、staging completion、Temporal history 或 artifact。生产 Temporal 服务、备份与高可用方式仍为“待确认”。

### Stage 3B 明确排除

PostgreSQL、Redis、NATS、对象存储、Kubernetes、多 worker/多节点调度、完整 Agent workflow 或 LangGraph state 迁移、人工审批、删除同步 docking API、长期双写，以及 Vina 算法/参数/评分重写均未包含在本阶段。后续必须单独设计和评审。

## 2026-08-11 LangGraph 权威 canary 执行阶段

### 状态与范围

- 当前分支：`codex/industrial-agent-langgraph-canary`，位于隔离 worktree；原始混杂工作树未被修改、回退或清理，未直接修改或合并 `main`。
- 默认仍为 `AGENT_HARNESS_MODE=legacy`、`AGENT_LANGGRAPH_CANARY_PERCENT=0`。只有固定 allowlist、受支持的直接 `WorkflowExecutor` 和稳定 hash 分桶同时满足时才使用 LangGraph；delegated specialist 始终留在 legacy control cohort。
- 权威科学语义仍由 `WorkflowRunSession`、统一 ToolResult、Validator、Evidence Ledger 和真实工具产物决定。LangGraph 只负责受约束的节点调度，不能自行终结 session、设置权威失败或伪造成功。
- 真实 API key 仅从运行时环境变量读取。本轮只确认 `OPENAI_COMPATIBLE_API_KEY/BASE_URL/MODEL` 均为 present，未输出、保存、提交或写入报告。

### 关键实现

1. 提取并统一 `WorkflowRunSession` 生命周期，legacy loop 与 LangGraph driver 共享同一权威状态机，确保每一步最多执行一次。
2. 新增 deterministic canary selector：固定 allowlist、0–100 整数比例、稳定 SHA-256 bucket、非法配置和非法 key fail closed，metadata 不保存原始 trace/idempotency key。
3. LangGraph 0.2.76 driver 支持 100 步长计划、空计划、required/optional terminal、精确递归上限和内部安全节点名。
4. 任一 semantic/checkpoint/session 进度发生后，即使真实工具尝试数仍为 0，也禁止回退重放；验证器、runner 或图异常被收敛为稳定错误码。
5. 注入 runner 使用受限 session façade，不能调用 `finish()`/`fail_runtime()`；共享 runner 通过 runner 级弱引用锁跨 harness 隔离，避免并发 session 串线。
6. `WorkflowExecution` 保留执行前的精确 compiled dependencies 与 session 真实 `tool_attempt_count`。checkpoint 复用为 0 次尝试，不再按 ToolResult 数量误计。
7. Supervisor 将脱敏 `harness_execution` 写入 AgentResult 和状态存储；持久化失败只追加 `harness_metadata_persistence_failed` warning，不改变科研结果状态。
8. acceptance 报告保留单轮或逐轮 canary metadata，并验证 backend、64 位 fingerprint 与工具尝试数；完整 prompt/idempotency key 在 mapping key/value 中均被脱敏。
9. contract 保持 34 个原案例，canary 探针独立统计；探针 failed，或明确请求 canary 但 unavailable，都会使发布门禁失败。

### 本阶段提交

```text
fb5b324 feat(agent): add deterministic langgraph canary selector
0689418 fix(agent): harden canary selector inputs
1a5a824 feat(agent): execute allowlisted workflows with langgraph
14dc1c9 fix(agent): harden langgraph fallback metadata
704d907 fix(agent): harden langgraph driver boundaries
0d20f83 feat(agent): route safe workflows through canary
92125c0 test(agent): gate langgraph canary releases
```

### 最终验证

- 默认 legacy：`638 passed, 1 skipped`。
- `AGENT_HARNESS_MODE=langgraph_canary`、`AGENT_LANGGRAPH_CANARY_PERCENT=100`：`638 passed, 1 skipped`。
- 反幻觉、平台健康与真实验收辅助测试：`46 passed`。
- LangGraph/Shadow 性能与边界测试（含 `MEDCHAT_RUN_PERF_TESTS=1`）：`39 passed`。
- `python -m compileall -q src scripts`：通过。
- `pip check`：`No broken requirements found`。
- canary contract：`passed`，34/34；探针 backend=`langgraph`、version=`0.2.76`、tool attempts=`1`、实际调用=`1`。
- changed-file 敏感形状扫描：0 命中；`git diff --check`：通过。

### 真实 golden 三轮结果

- 报告：`outputs/agent_evaluation/agent_acceptance_canary_real.json`（本地忽略产物，不提交）。
- 总状态：`partial`；12 个案例 × 3 轮，共 36 个结果。
- 每轮均为 `5 passed / 7 partial / 0 failed / 0 skipped`；严格 passed rate 41.67%，partial rate 58.33%。报告中的 `pass_rate=1.0` 表示 36/36 均为 passed 或诚实 partial，不表示全部严格通过。
- 稳定性：三轮案例状态和失败类型完全一致；p50=`100.65 ms`，p95=`15721.59 ms`。
- 外部主模型：`gemini-3.1-pro` passed，约 `5287 ms`；报告未记录 key。
- 本地分子生成：Ollama `gmm-llama:latest` passed，约 `5312 ms`。
- AutoDock Vina：passed，真实 pose 与数值 binding energy 检查通过，约 `14897 ms`。
- 靶点搜索探针：failed；相关 EGFR/PDE5A/设计案例保持 partial。
- RG-MPNN 真实推理探针：failed；未输出模拟 pIC50，相关案例保持 partial。
- 反向寻靶缺少 `data/reverse_target/chembl_training_data.tsv`；相关案例保持 partial，未编造靶点证据。

### 回滚与后续优先级

- 一键回滚：保持或设置 `AGENT_HARNESS_MODE=legacy`、`AGENT_LANGGRAPH_CANARY_PERCENT=0`，无需修改代码或科研工具。
- P0：补齐并校验靶点数据库资产，使 target search 能返回可追溯结构证据后再扩大 target workflow canary。
- P0：恢复 RG-MPNN registry、权重、scaler 与 provenance 完整性；在此之前活性相关结果必须继续 partial/failed。
- P1：补齐反向寻靶训练数据并建立版本/hash 注册，禁止用 demo/fallback 代替。
- P1：生产 rollout 从低比例开始，监控 backend 分布、fallback_before_execution、tool_attempt_count、terminal event 唯一性和 p95；delegated workflow 暂不进入 LangGraph。

## 2026-08-11 LangGraph Shadow Harness 与科学信任验收收尾

### 状态与范围

- 当前分支：`codex/industrial-agent-scientific-trust`；实施位于隔离 worktree，未覆盖原工作区的混杂改动，未直接修改或合并 `main`。
- 本阶段在既有 Supervisor/Orchestrator 上增加可选 harness 边界，采用“最终平台化、分阶段迁移”方式；默认仍为 legacy 权威执行，`shadow` 只做无副作用对照。
- 真实 API key 仅从运行时环境读取；没有写入源码、测试、日志或报告。报告凭据形状扫描为 0 命中。

### 已完成能力

1. 新增统一 `WorkflowHarness`，支持 `legacy` 与可选 `shadow`；Supervisor 直接执行和专家委派均经过同一边界。
2. LangGraph shadow 是有界、超时、无工具副作用的状态对照；依赖不可用或持久化失败时保留权威结果并记录 warning。
3. shadow fingerprint/diff 排除时间戳等非确定字段，拒绝凭据形状数据；科学验收报告保留 backend、版本、耗时、匹配结果和权威工具调用次数。
4. 目标驱动设计增加真实证据门：无可用靶点证据时禁止分子生成，下游 ADMET/活性检查标记为前置门禁阻断，不再误判为伪造成功。
5. 新增候选集身份闭环检查，确保下游结果对齐已生成且经 RDKit 验证的 canonical SMILES。
6. 收紧 SMILES 提取：先过滤明显的自然语言标签，再在屏蔽 RDKit 解析日志的情况下做真实结构验证，防止验收文本被当成 SMILES 送入解析器。
7. acceptance `contract` 模式增加 harness 探针；权威工具每案例仍只执行一次，报告 case 数与 legacy 断言保持兼容。

### 依赖与运行边界

- MedChat Conda 环境已对齐为 `langgraph 0.2.76`、`langchain-core 0.2.43`、`langchain 0.2.16`、`langchain-community 0.2.17`、`langsmith 0.1.147`；`pip check` 报告无损坏依赖。
- `AGENT_HARNESS_MODE` 默认为 `legacy`；当前只允许用 `shadow` 做比对。不得将 LangGraph 切换为权威执行器，直到具备完整工具调度、恢复、幂等性和生产容量证据。
- 分子生成仍由本地 Ollama `gmm-llama:latest` 执行；外部 OpenAI-compatible 模型仅用于主模型连通与理解边界测试。

### 最终验证结果

- Agent 全量测试：`474 passed, 1 skipped`；唯一 skip 是需显式设置 `MEDCHAT_RUN_PERF_TESTS=1` 的性能阈值测试。
- 反幻觉/平台健康/真实检查聚焦集：`43 passed`。
- `compileall` 通过；legacy contract 与 shadow contract 均为 `passed`，旧有 34/34 contract 案例保持通过。
- shadow 热身后 100 次基准：p50 `1.826 ms`、p95 `2.046 ms`、max `2.414 ms`；跨进程冷启动约 `317–397 ms`。
- 12 个 golden 真实案例重复 3 轮：每轮 `5 passed / 7 partial / 0 failed`，共36 个结果的 passed-or-partial rate 为 `1.0`，p50 `113.16 ms`，p95 `14315.53 ms`。
- 36/36 个真实案例的 shadow 与权威结果匹配；shadow p50 `4.5 ms`、p95 `10.0 ms`；forbidden pattern 命中数为 0。
- 真实外部主模型连通成功；本地 `gmm-llama:latest` 生成检查成功（3/3 有效且唯一）；Vina 真实对接成功（3 poses，最佳能量 `-3.5`，pose 文件存在）。
- 最终代码再跑 1 轮保持 `5 passed / 7 partial / 0 failed`，12/12 shadow 匹配，forbidden pattern 命中为 0。

### 如实保留的 partial 原因

- 本地靶点数据库未返回可用记录，相关靶点检索和靶点驱动生成案例保持 `partial`；生成器被科学证据门正确阻断。
- RG-MPNN 真实模型路径未配置，活性案例保持 `partial`，未输出伪 pIC50。
- 反向寻靶缺少 `data/reverse_target/chembl_training_data.tsv`，相关证据检查保持 `partial`。
- 这些缺失不会被 demo/fallback 包装成科学成功，也不会阻碍已具备真实依赖的 RDKit、Ollama 和 Vina 检查如实通过。

### 报告与后续建议

- 三轮稳定性报告：`outputs/agent_evaluation/agent_acceptance_report.json`。
- 最终单轮报告：`outputs/agent_evaluation/agent_acceptance_post_smiles_report.json`。
- 最终 contract 报告：`outputs/agent_evaluation/agent_acceptance_contract_final.json` 与 `outputs/agent_evaluation/agent_acceptance_contract_shadow_final.json`。
- `outputs/` 为本地验收产物，不应提交。下一阶段优先补齐靶点数据、真实 RG-MPNN 权重与反向寻靶训练数据，再评估 shadow 执行器的恢复/幂等能力，不建议直接切为权威执行。

## 2026-08-10 工业级 Agent 科学信任基础

### 任务状态

- 当前实现分支：`codex/industrial-agent-scientific-trust`，基于已批准计划 `docs/superpowers/plans/2026-08-10-industrial-agent-scientific-trust-foundation.md`。
- 实现位于隔离 worktree，未覆盖原工作区中的前端、Supervisor、报告层等未提交改动，未直接修改或合并 `main`。
- 下一阶段计划名：`industrial-agent-langgraph-harness`。本轮没有引入 LangGraph，而是先完成框架无关的科学状态、能力、编译、绑定、证据和真实性门禁。
- API key 仅从运行时环境读取；报告只确认 `API key present: true`。三份验收报告的 `sk-*` 长凭据扫描结果均为 0 命中。

### 已完成的架构能力

1. 新增 `ObservationStatus`、`RunOutcome`、`ToolProvenance` 和 `ScientificClaim`；`ToolResult`/`AgentResult` 在保留 legacy 字典兼容性的同时输出明确状态和 provenance。
2. 新增版本化科学能力目录，工具注册表可按能力解析真实实现，未知能力不会隐式落到任意工具。
3. 新增工作流计划编译器，在执行前拒绝工具越权、能力错配、重复步骤/输出和不存在的上游绑定。
4. 新增受限 Binding Resolver。靶点驱动设计强制绑定 `target search -> generation`，hit-to-lead 强制绑定 `baseline properties -> generation`，候选性质、ADMET、活性步骤消费生成候选。
5. 生成候选在进入下游或前端前由 RDKit 校验、canonicalize 和去重；无有效候选时失败，数量不足时为 `partial`。
6. 新增 Evidence Ledger 和 claim-safe renderer。demo/fallback 结果不可作为可用科学证据，未知或不可用 evidence 不能支撑 claim。
7. Orchestrator 为每个工具观察登记 evidence，最终 metadata 保留账本；部分结果发出 `task_partial`，不再伪装为 `task_completed`。
8. 真实验收新增候选有效性、claim evidence 和显式数据流的确定性 truth gates；LLM 自评不参与硬性科学通过判定。

### 本轮提交

```text
135b8df feat(agent): add scientific outcome contracts
e856f64 feat(agent): preserve tool truth status and provenance
301ecce feat(agent): add scientific capability catalog
aa29290 feat(agent): compile workflow plans before execution
9e6d012 feat(agent): bind downstream steps to upstream outputs
e566cd4 fix(agent): reject invalid generated molecules
a25c787 feat(agent): add evidence ledger and claim renderer
db6ba85 feat(agent): persist evidence-backed run outcomes
64cf3ec test(agent): gate releases on scientific evidence
97ff892 test(agent): enforce generated molecule output contract
88e175e fix(agent): synchronize validated status and provenance
```

### 修改文件范围

- 契约与能力：`src/agent/contracts/`、`src/agent/capabilities/`、`src/agent/tooling/registry.py`。
- 计划与执行：`src/agent/planning/`、`src/agent/orchestrators/base.py`、`src/agent/orchestrators/workflow.py`、`src/agent/runtime/workflow_executor.py`、`src/agent/runtime/task_state.py`。
- 科学真实性：`src/agent/validators/`、`src/agent/evidence/`、`src/agent/evaluation/scientific.py`。
- 测试：`tests/agent/test_scientific_contracts.py`、`test_capability_catalog.py`、`test_plan_compiler.py`、`test_binding_resolver.py`、`test_generated_candidate_validation.py`、`test_evidence_ledger.py`、`test_scientific_trust_runtime.py` 及相关既有回归测试。
- 文档：`docs/PROJECT_STANDARDS.md`、`docs/handoff/latest.md`。

### 验证命令与结果

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
# 272 passed, 5 warnings

C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
# 34 passed, 5 warnings

C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
# passed

C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract --output outputs\agent_evaluation\agent_acceptance_contract_report.json
# passed

C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode real --case-set golden --repeat 1
# 启动命令在 124 秒上限处 exit 124；此前已写出完整、可解析的 real 报告，报告状态为 partial。
# 无遗留 Python 验收进程。该退出生命周期问题不得记为命令成功。

C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode replay --replay-input outputs\agent_evaluation\agent_acceptance_report.json --output outputs\agent_evaluation\agent_acceptance_replay_report.json
# exit 0，partial；离线复现 real truth-check 决策。
```

现有 5 条 warning 为 3 条 SWIG 类型弃用提示和 2 条 FastAPI `on_event` 弃用提示，不影响本轮科学契约结论。

### 真实 golden 案例结果

| Case | 状态 | 分数 | 真实结论 |
|---|---:|---:|---|
| GOLD-001 | passed | 100 | 阿司匹林 RDKit 性质与类药性通过 |
| GOLD-002 | passed | 100 | 咖啡因性质、类药性、ADMET 方法记录通过 |
| GOLD-003 | partial | 90 | RG-MPNN、反向寻靶数据和下游靶点证据不可用 |
| GOLD-004 | partial | 90 | EGFR 本地靶点结构记录为空 |
| GOLD-005 | partial | 90 | PDE5A 本地靶点结构记录为空 |
| GOLD-006 | partial | 90 | 反向寻靶训练数据缺失 |
| GOLD-007 | partial | 100 | 低可信反向寻靶未伪造结果，但工作流没有成功工具输出 |
| GOLD-008 | partial | 90 | 10/10 有效唯一候选及数据流通过；靶点证据为空且 RG-MPNN 不可用 |
| GOLD-009 | partial | 90 | 基线到候选数据流通过；RG-MPNN 不可用 |
| GOLD-010 | passed | 100 | 真实 Vina 生成 3 个 pose，报告中 pose 文件存在、结合能为数值 |
| GOLD-011 | passed | 100 | 无效 SMILES 被拒绝，未返回伪性质 |
| GOLD-012 | passed | 100 | 缺 docking 参数时拒绝计算，未返回伪结合能 |

稳定性：12 个案例中 5 passed、7 partial、0 failed、0 skipped；严格 passed rate 为 41.67%，partial rate 为 58.33%。验收器的非灾难完成率为 100%，不代表所有案例严格通过。p50 为 254.75 ms，p95 为 40104.95 ms。

真实依赖探针：

- 外部主模型 `gemini-3.1-pro`：passed，4252 ms；未记录密钥值。
- 本地 Ollama `gmm-llama:latest`：passed，3/3 有效唯一候选，53708 ms。
- AutoDock Vina：项目适配器真实执行 passed；虽然系统 PATH 未发现 `vina` 命令，但适配器找到可用后端并生成 pose。
- RG-MPNN：failed，真实模型未加载，未输出模拟 pIC50。
- 靶点搜索：failed，本地探针返回 0 条记录。
- 反向寻靶：训练数据 `data/reverse_target/chembl_training_data.tsv` 缺失。

本地运行产物位于 `outputs/agent_evaluation/` 和 `temp_docking/`，受忽略规则保护，不提交 Git。

### 已发现、下一阶段优先处理的问题

1. **P0：关键上游结果只有“绑定存在”校验，没有“科学证据可用”校验。** GOLD-008 的靶点搜索没有记录，但生成器仍收到 `Validated target evidence: None` 并继续生成。下一阶段应为 required evidence 增加 semantic gate；空证据时停止、请求补充或明确降级为非靶点约束生成。
2. **P0：候选身份与下游结果缺少一一对应清单。** GOLD-008 生成 10 个候选，但性质/ADMET 输出摘要显示 11 条。应引入带 `candidate_id` 的 CandidateSet，按 ID 连接性质、ADMET、活性和排序，拒绝基数漂移。
3. **P1：长任务生命周期与资源治理仍不足。** real 报告已写完但 CLI 未在 124 秒内干净退出；本地生成探针 53.7 秒、docking 案例 76.7 秒。`industrial-agent-langgraph-harness` 应加入 durable execution、每节点 time budget、取消传播、增量报告和任务队列并发限制。

部署侧还需恢复并校验 RG-MPNN registry/权重、靶点数据库和反向寻靶训练数据；在这些资产可用前，相关案例必须继续保持 `partial`/`failed`。

## 2026-07-13 科学运行时加固与真实科研验收

### 任务状态

- 当前分支：`codex/scientific-runtime-hardening`，未直接修改、合并或提交到 `main`。
- 实施依据：
  - `docs/superpowers/specs/2026-07-11-scientific-runtime-hardening-design.md`
  - `docs/superpowers/plans/2026-07-11-scientific-runtime-hardening.md`
- 计划中的 17 个任务均已实施。行为修改均先建立失败契约，再完成最小修复。
- 本轮从 `b1527e9` 到 `f68dc1f` 共 34 个主题化提交；最终交接文档单独提交。
- 真实运行产物保存在本地 `outputs/agent_evaluation/` 和 `temp_docking/`，均不提交 Git。

### 已完成的加固

1. 前端与管理面
   - 收敛 activity、chat、docking 页面动态内容的安全渲染路径，补齐静态 XSS 契约。
   - 管理 API 调用统一携带管理 token；任务失败状态不再被前端包装为成功。
2. Agent 与状态恢复
   - 每个请求使用独立 EventBus 和 Orchestrator 扩展状态，避免跨请求事件、回调和可变状态串线。
   - 增加反向寻靶 specialist 所有权，保留 Supervisor 作为聊天唯一 Agent 入口。
   - 修复 checkpoint 在相同 `created_at` 下错误选中旧 `running` 记录的竞态，幂等请求和重启恢复不再偶发重复执行工具。
3. Docking 与异步边界
   - 每个 docking job 使用独立目录；取消/超时时终止完整进程树；Windows Job Object 和跨进程历史锁生效。
   - Vina 不可用或失败时不再返回合成能量；历史持久化失败作为 warning 传播。
   - 性质、反向寻靶冷启动、docking 报告等重任务移出 FastAPI 事件循环；上传大小、图片类型和临时训练文件清理均有契约。
4. RAG、活性模型与科学真实性
   - RAG index 使用 manifest、源数据 hash、index hash、row mapping、原子写入和不可变快照；能力不可用时在执行边界失败。
   - 活性模型路径限制在 registry 根目录，注册、切换、删除和训练生命周期使用原子状态更新。
   - ADME、反向寻靶、靶点结构、docking 和分子生成保留结构化 provenance；数据流检查使用真实 canonical SMILES 和持久化工具执行记录。
5. 部署与代码所有权
   - 靶点 SQLite 使用 WAL、busy timeout 和外键；结构下载具备大小上限、格式校验、临时文件和原子替换。
   - 日志改为追加式滚动文件；CI/CPU 与 CUDA 部署依赖 profile 明确分离。
   - 新增 `.github/workflows/quality.yml`，离线运行全量 pytest、compileall、6 个 Node 契约和只输出文件名的凭据扫描。
   - 删除四个已证明无生产导入的旧实现：`src/web/rag_service.py`、`src/rag/molecular_rag.py`、`src/web/models.py`、`src/web/static/js/script.legacy.backup.js`。
   - `src/web/models/ollama_model.py` 是唯一 Ollama 类实现；聊天适配器兼容本地同步生成 API 与外部异步模型 API。

### 本轮精确文件范围

共 77 个文件（相对 `4e69de4`），其中删除 4 个旧文件：

```text
.github/workflows/quality.yml
data/REGISTRY.md
deployment/README.md
deployment/requirements.txt
docs/PROJECT_STANDARDS.md
docs/superpowers/plans/2026-07-11-scientific-runtime-hardening.md
docs/superpowers/specs/2026-07-11-scientific-runtime-hardening-design.md
main.py
requirements.txt
src/activity/model_registry.py
src/activity/predictor.py
src/activity/trainer.py
src/agent/contracts/context.py
src/agent/evaluation/scientific.py
src/agent/orchestrators/workflow.py
src/agent/persistence/sqlite_store.py
src/agent/runtime/workflow_executor.py
src/agent/specialists/__init__.py
src/agent/specialists/agents.py
src/agent/supervisor.py
src/agent/tooling/factory.py
src/agent/tools/__init__.py
src/agent/tools/activity_predictor_tool.py
src/agent/tools/admet_predictor.py
src/agent/tools/molecular_docking.py
src/agent/tools/reverse_target_tool.py
src/agent/tools/target_database_tool.py
src/docking/adapters/base.py
src/docking/adapters/vina_adapter.py
src/docking/history_index.py
src/docking/molecular_docking_service.py
src/rag/molecular_rag.py (deleted)
src/target_search/database.py
src/target_search/downloader.py
src/target_search/routes.py
src/target_search/seed.py
src/task_runtime/manager.py
src/web/app.py
src/web/chat_handler.py
src/web/models.py (deleted)
src/web/models/__init__.py
src/web/rag_index.py
src/web/rag_service.py (deleted)
src/web/routes/api_routes.py
src/web/static/js/activity_prediction/main.js
src/web/static/js/activity_prediction/model_manager.js
src/web/static/js/activity_prediction/preflight.js
src/web/static/js/activity_prediction/results_renderer.js
src/web/static/js/activity_prediction/training_monitor.js
src/web/static/js/docking/ui_manager.js
src/web/static/js/home/chat_renderer.js
src/web/static/js/script.legacy.backup.js (deleted)
src/web/templates/activity_prediction.html
src/web/templates/molecular_docking.html
tests/activity_prediction_safe_render_test.js
tests/admin_fetch_test.js
tests/agent/test_chat_handler_agent_events.py
tests/agent/test_domain_result_validators.py
tests/agent/test_evaluation_runner.py
tests/agent/test_real_acceptance_checks.py
tests/agent/test_specialist_agents.py
tests/agent/test_supervisor_delegation.py
tests/agent/test_workflow_executor.py
tests/agent/test_workflow_resume.py
tests/frontend_safe_render_test.js
tests/test_activity_model_registry.py
tests/test_activity_prediction_contract.py
tests/test_agent_anti_hallucination_fallbacks.py
tests/test_agent_platform_health_check.py
tests/test_deployment_assets.py
tests/test_docking_agent_architecture.py
tests/test_docking_configuration.py
tests/test_docking_history_index.py
tests/test_rag_index_manifest.py
tests/test_reverse_target_health.py
tests/test_target_search.py
tests/test_task_runtime.py
```

本交接提交另修改 `docs/handoff/latest.md`。

### 最终验证结果

实际运行命令与结果：

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests -q -p no:cacheprovider
# 439 passed, 1 skipped, 9 subtests passed, 6 warnings

C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
# passed

# src/web/static/js 下排除 ketcher/backup 后的全部 JS
node --check <47 changed/supported JS files>
# 47/47 passed

node tests/*.js
# 6/6 passed

C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/run_agent_acceptance.py --mode contract
# passed, 34/34, pass_rate=1.0

C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/run_agent_acceptance.py --mode real --case-set golden --repeat 3
# partial；36 次案例执行中 27 passed、9 partial、0 failed、0 skipped

C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts/health_check.py --strict
# 22/22 passed
```

全量 pytest 的唯一 skip 是 Windows 上不适用的 POSIX process-group 回归。6 条 warning 均为现有依赖/迁移提示：3 条 SWIG 类型、2 条 FastAPI `on_event` 弃用、1 条 PyTorch Geometric distributed 弃用。

### Golden 真实科研案例

三轮结果完全一致：每轮 9 passed、3 partial、0 failed、0 skipped。

| Case | 场景 | 状态 | 分数 | 结论 |
|---|---|---:|---:|---|
| GOLD-001 | 阿司匹林性质/类药性 | passed | 100 | RDKit 性质与 Lipinski/QED 通过 |
| GOLD-002 | 咖啡因 ADMET/类药性 | passed | 100 | ADMET method 与规则来源完整 |
| GOLD-003 | 伊马替尼综合评价 | partial | 90 | 性质、ADMET、反向寻靶、靶点证据通过；RG-MPNN 无真实结果 |
| GOLD-004 | EGFR 结构搜索 | passed | 100 | 数据库证据通过，无伪结合能 |
| GOLD-005 | PDE5A 结构搜索 | passed | 100 | 数据库证据通过，无伪结合能 |
| GOLD-006 | 伊马替尼反向寻靶 | passed | 100 | 相似度/数据库证据通过 |
| GOLD-007 | 长链烷烃低可信寻靶 | passed | 100 | 正确保留低可信状态 |
| GOLD-008 | PDE5 靶点驱动设计 | partial | 90 | 10 个有效唯一 SMILES 被下游性质/ADMET 消费；RG-MPNN 无真实结果 |
| GOLD-009 | Hit-to-lead | partial | 90 | baseline、候选生成与候选性质数据流闭环；RG-MPNN 无真实结果 |
| GOLD-010 | 样例 Vina docking | passed | 100 | 3 个 pose、最佳能量 -3.5、pose 文件存在 |
| GOLD-011 | 无效 SMILES | passed | 100 | 结构化失败，无 QED/LogP/pIC50/kcal/mol 伪结果 |
| GOLD-012 | 缺参数 docking | passed | 100 | 明确拒绝，无结合能伪结果 |

稳定性统计：

- 报告总状态：`partial`。
- 严格 passed rate：`27/36 = 75%`；partial rate：`9/36 = 25%`；failed/skipped rate：`0%`。
- 报告内 `pass_rate=1.0` 表示 passed 或可诚实解释的 partial 均完成，不代表 36 次全部严格通过。
- p50 latency：`179.335 ms`；p95 latency：`60268.288 ms`。
- 唯一失败类型：`truth_partial:rg_mpnn_provenance`，共 9 次。
- 12 个案例的 anti-hallucination 均通过，未发现伪造 pIC50、binding energy、文献或实验结论。
- 事件序列、trace、tool results、warnings、provenance 和 artifact 相对路径均被保留。

真实依赖探针：

- 外部主模型：`gemini-3.1-pro`，passed，精确探针匹配，2667 ms。
- 本地分子生成：Ollama `gmm-llama:latest`，passed，3/3 有效且唯一，35875 ms。
- 靶点搜索：passed，返回 1 条可追溯结果，52 ms。
- RG-MPNN：failed，3 个探针分子均无真实预测，`real_model_used=false`，未输出模拟 pIC50。
- AutoDock Vina：passed，3 个 pose，最佳能量 -3.5，pose 文件存在，22768 ms。

结构化报告：`outputs/agent_evaluation/agent_acceptance_report.json`。该文件可由 Python `json` 正常解析；真实运行时 API key 值和 `sk-*` 凭据模式均未出现在报告中。

### 已知限制与后续优先级

1. **P0：恢复 RG-MPNN 模型 registry 与真实推理。** 当前有两个历史 registry info 文件被判定为无效或不在 registry state，真实探针 0/3 成功。这会使综合评价、靶点驱动设计和 hit-to-lead 固定为 partial。应重新注册经校验的模型目录，要求权重、配置、scaler 和 provenance 完整，再跑 GOLD-003/008/009。
2. **P1：收紧 SMILES 提取。** 真实日志显示 `SMILES:`、`RG-MPNN`、`partial/failed`、`Objectives:` 等普通文本被尝试送入 RDKit。当前 Validator 阻止了伪结果，但产生噪声和额外开销。应改为边界明确的结构提取，并为自然语言标签补负例契约。
3. **P1：提高生成候选的科学质量与延迟稳定性。** 最终数量/有效性契约通过，但生成过程中出现无效 SMILES，PDE5 候选中还出现形式上有效但科研价值很低的单原子 `I`；p95 超过 60 秒。应增加最小重原子数、片段/盐过滤、靶点约束和分阶段超时，并验证优化目标确实改善而不只验证“候选已被消费”。
4. **P2：迁移 FastAPI lifespan。** `@app.on_event("startup")` 仍产生弃用 warning，建议独立任务迁移，不与科学行为修改混合。

### 凭据与安全状态

- 外部 API key 仅从当前进程的 `OPENAI_COMPATIBLE_API_KEY` 读取；base URL 和 model 也只来自运行时环境变量。
- key 未写入源码、`.env`、测试、日志、报告或 Git；报告只记录 provider/model/latency/success。
- 真实报告检查：runtime key value 命中 `false`，`sk-*` 长凭据模式命中 `false`，私钥标记命中 `false`。
- 最终提交前已运行 tracked-file 凭据扫描与 `git diff --check`，均通过；只精确暂存本交接文件。

## 2026-07-10 工作树整理与 Agent 升级交接

- 当前分支：`codex/agent-runtime-real-validation`，未直接修改或合并 `main`。
- 原有混杂改动已按领域整理为五个可独立回滚的提交：
  - `bc08d34 feat(science): harden molecular prediction and docking services`
  - `46afb1c feat(agent): add persistent multi-agent scientific runtime`
  - `ba1cdfa fix(web): secure agent operations and frontend rendering`
  - `54fc3b7 chore: ignore local sqlite3 runtime files`
  - `docs: record agent platform standards and rollout plans`（当前文档提交）
- `data/agent_state.sqlite3` 保留在本地但由 `.gitignore` 排除，未提交运行数据库。
- 整理前扫描 155 个候选文件：真实运行时密钥命中 0，通用长密钥模式命中 0，超过 1 MB 的候选文件 0。
- 最近验证：Agent pytest 178 passed；聚焦安全/路由测试 19 passed；健康检查 22/22；Node 静态安全测试通过；contract、real repeat=3 与 replay 验收均通过。
- 真实报告位于本地 `outputs/agent_evaluation/`，该目录受忽略规则保护，不提交运行产物。
- 整理完成后 `git status --short` 为空，工作区和暂存区均无待处理 diff。

## 2026-06-21 Agent 唯一入口与真实验收

### 本轮目标

- 消除 WebSocket 聊天链路仍由 `ReActMolecularAgent` 接管的双主入口。
- 确保所有原子和复合技能统一经过 WorkflowExecutor、Orchestrator、ToolResult 适配和领域 Validator。
- 使用外部 `gemini-3.1-pro` 主模型与本地 Ollama `gmm-llama:latest` 完成真实案例验收。

### 本轮修改

- `src/web/app.py`
- `src/agent/supervisor.py`
- `src/agent/planning/task_planner.py`
- `tests/agent/test_app_supervisor_entrypoint.py`
- `tests/agent/test_supervisor_agent.py`
- `tests/agent/test_task_planner.py`
- `outputs/agent_evaluation/agent_contract_cases_20260621.json`
- `outputs/agent_evaluation/agent_real_cases_20260621.json`
- `outputs/agent_evaluation/agent_validation_summary_20260621.md`

### 最终状态

- ChatHandler 的 `agent_system` 现在是 `SupervisorAgent`。
- 原子技能使用声明式单步计划，不再绕过统一执行与 Validator。
- Chat Supervisor、Orchestrator 与 EventBus 复用 `data/agent_state.sqlite3` 状态存储。
- 外部主模型与本地分子生成模型保持隔离。
- pytest 221/221、Agent 128/128、健康检查 22/22、JS 45/45、Node 3/3 均通过。
- Contract 34/34 通过。
- REAL-001 至 REAL-016：14 Passed、2 预期 Partial、0 Failed、0 Skipped。
- 外部模型、Ollama、RG-MPNN、靶点检索和 Vina 均完成真实执行。
- 凭据未写入仓库或报告，验收结束后环境变量已清除。

### 后续建议

- 动态 LLM Planner 只作为受约束的候选计划生成器引入；必须保留工具白名单、schema 校验、权限预检和可回放测试。
- FastAPI `on_event` 和 PyTorch/PyG 弃用警告可在独立维护任务中处理，不应混入本次架构修复。

## 2026-06-19 Agent 架构真实测试与修复

当前分支：`codex/agent-runtime-real-validation`

本轮依据以下用户文档完成架构修复与真实测试：

- `molecular_chat_system_agent_issues_solutions_detailed.docx`
- `molecular_chat_system_agent_real_test_plan.docx`

### 完成内容

- 新增统一 `WorkflowExecutor`，执行前强制计划与工具权限预检。
- `TaskPlanner` 成为 workflow 步骤唯一来源，workflow skill 仅声明意图、权限和 `is_workflow`。
- 新增 `WorkflowState`，落实 output binding、required/optional、timeout、错误与附件汇总。
- 完整透传 `ToolResult` 的 warnings/evidence/artifacts/quality/error。
- 新增 docking/activity/ADMET/target evidence 领域 Validator。
- ChatHandler 支持执行中实时事件，并默认直出 workflow 权威结果。
- 修复 ADMET 与 hit-to-lead、ADMET 与 comprehensive 的真实中文路由冲突。
- Vina 结果增加 pose 文件路径，验收要求真实 pose 文件存在。
- 新增 REAL-001 至 REAL-016 版本化数据集与合同/真实案例执行器。

### 验证结果

- 合同验收：34/34。
- REAL-001 至 REAL-016：16/16 通过或符合预期 partial。
- Agent 测试：122 passed。
- 全量测试：217 passed。
- 健康检查：22/22。
- Python compileall：通过。
- JavaScript：47/47 语法通过。
- Node 静态测试：3/3。
- 密钥扫描：352 个文本文件，0 命中。

真实依赖：

- Ollama `gmm-llama:latest`：通过，生成结果有效且唯一。
- PDE5A 靶点检索：通过。
- RG-MPNN：通过，使用真实权重。
- AutoDock Vina：通过，3 个 pose，最佳能量 -3.5 kcal/mol，pose 文件存在。
- 外部 `gemini-3.1-pro`：补测通过，精确探针匹配，延迟 2393 ms。密钥只在单次子进程中临时使用，未持久化。

补测外部模型时重新执行了全链验收。外部模型、本地 Ollama、PDE5A 检索、RG-MPNN 和 Vina 均通过；REAL-005 本轮因生成模型随机波动仅得到 9/10 个有效唯一分子，被验收器正确判为失败，总状态为 partial。此前完整真实矩阵曾达到 16/16；当前 JSON 保留最新一次诚实结果，不将数量不足伪装为成功。

### 证据文件

- `outputs/agent_evaluation/agent_contract_cases_20260619.json`
- `outputs/agent_evaluation/agent_real_cases_20260619.json`
- `outputs/agent_evaluation/agent_validation_summary_20260619.md`
- `docs/superpowers/plans/2026-06-19-agent-runtime-real-validation.md`

### 继续工作规则

- 保留当前所有未提交改动，不 reset、clean、checkout 或回退。
- 不将用户 API Key 写入命令、文件、日志、测试或 Git。
- 外部模型补测已完成；后续运行仍应通过临时环境变量注入密钥。
- 当前任务尚未提交、推送或合并；禁止直接合并 `main`。

---

## 上一轮：项目规范文档

日期：2026-06-19
分支：`docs/project-standards`
目标分支：`main`
状态：文档已完成校验并发布到 draft PR #1，尚未合并。

## 1. 本轮任务目标

基于当前仓库真实结构，建立完整、实用、可长期维护的项目规范，且不修改业务代码：

- 新增仓库级 Agent/Codex 工作说明；
- 建立详细工程规范；
- 记录本轮事实来源、现状、待确认问题和后续建议；
- 使用独立分支、独立 commit 和 PR，不直接修改或合并 `main`。

## 2. 本轮新增或修改的文件

本轮只应包含以下三个文档文件：

- `AGENTS.md`
- `docs/PROJECT_STANDARDS.md`
- `docs/handoff/latest.md`

没有修改业务代码、测试代码、配置、数据库或数据资产。

工作区在本轮开始前已存在大量其他未提交改动。它们属于既有工作，不属于本轮文档 commit，禁止使用 `git add -A`。

## 3. Codex 读取的关键文件

### 项目与依赖

- `README.md`
- `main.py`
- `requirements.txt`
- `.gitignore`
- `.gitattributes`
- `.env.example`
- `start_ollama.ps1`

### 配置与部署

- `config/ollama_config.yaml`
- `config/modelscope_config.yaml`
- `config/deployment.yaml`
- `deployment/README.md`
- `deployment/requirements.txt`
- `deployment/medchat.service`
- `deployment/nginx-medchat.conf`
- `deployment/docking_tools.md`

### 后端与 API

- `src/web/app.py`
- `src/web/api_response.py`
- `src/web/chat_handler.py`
- `src/web/routes/main_routes.py`
- `src/web/routes/page_routes.py`
- `src/web/routes/api_routes.py`
- `src/web/routes/agent_workflow_routes.py`
- `src/web/routes/system_routes.py`
- `src/web/routes/websocket_routes.py`
- `src/target_search/routes.py`
- `src/target_search/service.py`
- `src/target_search/database.py`
- `src/task_runtime/database.py`
- `src/task_runtime/manager.py`
- `src/agent/persistence/sqlite_store.py`

### 前端

- `src/web/templates/*.html`
- `src/web/static/js/home/`
- `src/web/static/js/design/`
- `src/web/static/js/docking/`
- `src/web/static/js/reverse_target/`
- `src/web/static/js/target_search/`
- `src/web/static/js/activity_prediction/`
- `src/web/static/css/`

### 数据、脚本与测试

- `data/REGISTRY.md`
- `docs/target_search_local_db.md`
- `scripts/health_check.py`
- `scripts/run_agent_acceptance.py`
- `scripts/target_db/validate_target_db.py`
- `tests/test_deployment_assets.py`
- `tests/test_phase2_phase3_routes.py`
- `tests/agent/`
- `tests/home_agent_task_panel_test.js`
- `tests/reverse_target_broad_recall_test.js`
- `tests/reverse_target_pagination_test.js`

## 4. 发现的项目现状

### 架构

- 后端采用 FastAPI/Uvicorn，`main.py` 是推荐入口，`src/web/app.py` 导出 ASGI `app`。
- 前端采用 Jinja2 + 原生 JavaScript/CSS，没有 npm 构建链。
- 页面与前端资源已按功能拆分，但部分核心文件仍较大。
- API 路由同时存在集中式 `api_routes.py` 和按领域拆分的 `setup_*_routes()`。
- WebSocket `/ws` 承担聊天与 Agent 事件流。

### Agent

- 已存在混合路由、任务规划、Supervisor、专家代理、工具适配、SQLite 状态、长期记忆和评测。
- Agent 运行状态默认写入 `data/agent_state.sqlite3`。
- 后台任务默认写入 `scratch/tasks.sqlite`。
- 分子生成模型与主聊天模型有独立配置边界。

### 数据

- 靶点搜索使用 SQLite 元数据、CSV 种子和本地结构缓存。
- 反向寻靶依赖 ChEMBL 表和 NumPy 指纹文件。
- 活性预测依赖本地 RG-MPNN 权重。
- RAG 使用本地 CSV 和 FAISS。
- 大型运行资产已在 `.gitignore` 和 `data/REGISTRY.md` 中规定不提交。

### 运行与部署

- 开发依赖来自 `requirements.txt`。
- CUDA/Linux 部署依赖来自 `deployment/requirements.txt`。
- Linux 部署使用 systemd + nginx，应用默认端口为 `6001`。
- 本地 Ollama 默认地址为 `127.0.0.1:11434`。
- 对接需要 AutoDock Vina、ADFRsuite 和 Meeko。

### 测试

- 当前有 49 个 Python 测试文件和 3 个直接运行的 Node.js 测试脚本。
- 没有 `pytest.ini`、`pyproject.toml`、`package.json` 或正式 lint 配置。
- 项目提供 `compileall`、健康检查和 Agent 验收作为 pytest 之外的质量门。

### Git 状态

- 本轮开始时处于 `main`，工作区已有大量未提交业务改动和新增测试/Agent 文件。
- 已创建独立分支 `docs/project-standards`。
- 本轮提交必须只暂存三份规范文档。

## 5. 不确定或需要用户确认的问题

1. Python 3.11 是否正式支持；当前文档和部署更偏向 Python 3.10。
2. 通用依赖与部署依赖版本差异如何治理。
3. 是否引入 Ruff/Black、ESLint/Prettier。
4. 是否建立 GitHub Actions、覆盖率阈值和分支保护。
5. API 是否逐步统一为 `api_success/api_error`。
6. FastAPI `@app.on_event("startup")` 迁移 lifespan 的时间点。
7. README 声明 MIT，但未看到根目录 LICENSE，许可证状态需确认。
8. 用户上传文件、聊天、任务和 Agent 状态的保留期限。
9. 生产环境长期使用 SQLite，还是迁移 PostgreSQL/对象存储/Redis。
10. Ketcher、3Dmol.js、ECharts 和 CDN 依赖的升级与离线策略。

## 6. 后续建议

优先级建议：

1. 审查并确认本轮三份规范文档。
2. 单独 PR 建立 CI：pytest、compileall、Node 静态测试、密钥扫描。
3. 单独 PR 确定 Ruff/Black 和 JavaScript formatter 策略。
4. 明确 Python/依赖支持矩阵，减少两套 requirements 漂移。
5. 为 GitHub 设置 `main` 分支保护和至少一名人工审查。
6. 补充根目录 LICENSE 或修正 README 许可证声明。
7. 制定数据保留、备份、恢复和隐私策略。
8. 逐步拆分大型 API/前端文件，但不要与功能 PR 混合。

## 发布注意

- PR 应创建为 draft，base 为 `main`。
- PR 描述应强调：只修改文档、未包含既有业务改动。
- 审查重点：命令是否真实、边界是否清晰、待确认项是否准确。
- 不要由 Codex 直接合并。

发布记录：

- 远端分支：`docs/project-standards`
- Draft PR：`https://github.com/kxyesyes/molecular_chat_system/pull/1`
- PR 状态：Open、Draft、未合并
