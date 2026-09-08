# 逐轮模型 Agent：A2 第一批交接

日期：2026-09-08。分支：`codex/agent-decision-loop`；起点 `1b2cbff`。
工作位置：独立 scientific-assets worktree。原项目分支与混杂改动未修改。

## 已交付的实际边界

新增显式实验入口 `ModelDecisionLoop.run`。使用现有 LangGraph 的 decide / tool 节点循环；工具选择来自每轮模型响应，不调用 TaskPlanner、不预编译固定工具序列、不回退旧流程。

工具执行仍使用同一个 WorkflowRunSession，保留适配、领域校验、EvidenceLedger、检查点、事件和失败信息。没有复制一套宽松工具执行器。

目前只开放调用方授权的 property_calculator、drug_likeness_assessment、activity_predictor、target_database_search，以及显式普通聊天模式。工具名称与描述来自 ToolRegistry。allowed_tools、required_tools、request_kind 必须由可信服务端调用方提供，不能直接信任模型或 Web 客户端提交值。

这不是整个 A2 完成，也不是当前网站已经迁移为自主 Agent。

## 已实现的保护

- 模型参数仅接受 `{"input_ref":"user"}`，服务端绑定原始用户 query；不接受新编 SMILES、跨任务引用或额外参数。
- 四工具授权、能力开关、只读/幂等属性与配置变化检查；未知工具和 molecular_docking 不开放。
- 同一 trace 中重复动作复用已校验观察，不重复调用；模型请求仍计入预算。
- 默认最多 16 次模型请求、12 单位工具尝试预算、300 秒循环时限。工具预算按 spec 最大尝试数保守预留，不等于实际重试次数；实际每次分发仅执行一次。
- native call ID 与 observation 成对；JSON 模式显式配置。调用 ID 错误在工具执行前拒绝。
- scientific finish 的证据必须来自本次会话且科学可用；缺调用方 required_tools、demo/fallback、错误来源不能获得 completed。
- 科研回答只显示服务端生成的工具结构化结果块，不显示模型自由科研正文；模型填写伪数值不能覆盖工具值。聊天仍调用主模型，不使用固定欢迎语。
- 各轮记录 decision ID、轮次、provider/model、usage、状态；usage 使用 prompt/completion/total 和 usage_unit=tokens，避免共享凭据脱敏器将计数误当秘密 token。
- 动态进度未知时不发布工具百分比；工具事件附 decision_id/tool_call_id/round。每个新运行只收尾一次。
- 新 trace 使用 SQLite 原子 INSERT；重复 trace 明确拒绝，不覆盖所有者、不重置预算、不自动重放。
- 失败观察也保存完整 ToolResult，包括 warnings、provenance、evidence、artifacts；持久化错误后的单步重入只恢复 journal，不重新执行工具。
- 连续取消等待当前有界 journal 收尾；超时后的底层同步线程可能仍在运行，禁止在其后再次派发 retry。不宣称取消等于杀死计算进程。

## 代码文件

新增：

- `src/agent/harness/decision_loop.py`：逐轮模型控制与预算、终态。
- `src/agent/harness/decision_policy.py`：授权目录、证据 finish、受控结果呈现。
- `src/agent/harness/decision_execution.py`：请求局部单次调用、事件交付与取消收尾。
- `tests/agent/test_decision_loop.py`：契约、真实 RDKit 与故障注入测试。
- `docs/superpowers/plans/2026-09-08-agent-decision-loop.md`、本交接。

修改：

- `src/agent/runtime/run_session.py`：显式 dynamic 模式及追加/收尾；旧模式默认不变。
- `src/agent/persistence/base.py`、`sqlite_store.py`：exclusive start，默认保留旧 upsert 语义。
- `src/agent/tooling/adapters.py`：请求级 allow_retry，默认 True 保持旧入口行为。

关键阅读还包括上一轮 A1 交接/设计、decision contracts/transport、ToolSpec/registry/factory、validator、EvidenceLedger、event bus，以及原工作树 Git 状态。

## 验证

Python：已有 MedChat Conda 环境；未使用系统 Python。

| 命令 | 本轮结果 |
|---|---|
| `python -B -m pytest tests/agent/test_decision_loop.py -q -p no:cacheprovider --tb=short` | 40 passed |
| `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py -q -rs -p no:cacheprovider --tb=short` | 1784 passed、1 skipped、7 warnings，74.16 秒 |
| `python -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/decision-loop-contract.json` | 34/34 passed |
| `python -B -m compileall -q src scripts` | exit 0 |
| `git diff --check` | exit 0 |

唯一 skipped 是未启用的 harness shadow 性能测试；warnings 为既有 SWIG/FastAPI 弃用提示。contract 的无效 SMILES 负例打印 RDKit parse error 是预期拒绝，不是成功分子计算。

真实执行：新循环确实调用本地 RDKit 计算 CCO，并独立复核分子量；无效结构没有成功性质结果。HTTPX MockTransport 模拟外部主模型协议，两种模式均覆盖；不应将其宣称为真实外部 LLM 验收。其他分支测试使用明确测试替身；没有调用生产活性权重、Ollama、Vina 或外部 API，没有读取 key。

独立审查已复现并推动修复三类生命周期问题：重复取消、回调/存储异常后无终态、超时后内部重试。复审进一步覆盖终态 metadata/status 写入、提交后抛错及等待输入状态写入；有界重试只恢复持久化和原 finish journal，不重跑 graph/tool。最终复审 40 项通过，确认本批范围无剩余阻断。未将契约通过当作所有科研能力已通过。

## 下一增量与未覆盖项

1. 实现经过科学字段校验的跨观察 input_ref，以及候选/target 证据的真实下游消费。本批只能引用原始输入，不能声称已完成跨工具数据流迁移。
2. 澄清状态保存与恢复：目前仅 waiting_for_input 标记及终态，不恢复既有 trace；需继续原授权快照和预算，核实未确认执行，不自动重放。
3. 细化任务义务到指标/候选数量/禁止动作；本批 required_tools 仅是可信调用方的工具级验收要求，尚未实现通用自然语言义务提取。
4. A3 接入 Supervisor/ChatHandler/前端，隔离真实主模型与科学工具验收，再讨论切换默认入口。科学 JSON 结果块不是已完成的面向用户排版。

长期存储完全不可用时不能保证落盘成功；当前代码不会因此编造科学成功。此类部署故障必须修复存储后人工核查。跨进程恢复/批准与 Web 端断线体验仍未验收。

未切默认入口、未重启实例、未启用生产模型、未删除旧业务工作流。未 push/创建 PR/合并 main；分支继承历史较多，发布前仍需整理依赖范围。
