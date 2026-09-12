# 模型决策协议与任务约束集成

日期：2026-09-12。分支 `codex/agent-decision-contracts-integration`，起始 main `c642bae`。
总体计划 Task 5 的基础协议子批次，来源 `9312bf5`；初版 `eb8a862` 及快照预算修复，
独立规格与质量审查均通过，未合并。

## 范围与依赖

- `src/agent/contracts/decision.py`：tool / clarify / finish 三类结构化模型提案，固定版本、
  严格 JSON/Pydantic 校验，有界体积/深度，拒绝重复键、非有限数和损坏 Unicode。
- `src/agent/contracts/task_requirements.py`：服务端持有的任务验收约束，禁止工具、
  分子数量/预期 SMILES/指标集合；调用者输入和构造实例均重新验证并隔离。
- 对应两份 `tests/agent/` 契约测试；不修改现有科学工具、入口、状态库或依赖配置。

模型提案通过 schema 不等于获得工具权限；evidence ID 通过格式检查不等于证据真实。
TaskRequirements 的 SMILES 字段也不是化学结构校验。后续执行边界必须继续验证授权、
真实工具输出、结果身份和持久化证据，本批不允许以模型自评判定科研成功。

## 已读取与后续迁移边界

- main 的 AGENTS、PROJECT_STANDARDS、总体历史集成计划和 Git 状态。
- 历史两份契约与测试；当前主分支尚无对应模块。
- 为核对消费者，另只读历史 `decision_transport.py`、`decision_privacy.py`、
  `harness/decision_loop.py`、`harness/decision_policy.py` 相关入口。
- 传输与隐私模块实际位于 `src/agent/`，不是 `harness/`；前者依赖本批契约，
  另需最小 `openai_compatible_model.py` 接点。它们将在后续批次单独移植和验证。
- 动态 session、evidence ledger 防篡改、等待快照 revision 3、敏感澄清与 target 续接、
  隔离 Web 入口尚待后续集成；不能因本协议测试通过而宣布新 Agent 已接管生产。

## 验收状态

先加入历史测试复现缺失能力，再移植兼容实现并补输入/诊断边界测试；
之后依次进行独立规格与质量审查、全 Agent 回归、编译、Node、contract 和 PR CI。
不读取真实密钥、CSV、生产模型或调用外部服务。

实施者运行 `python -B -m pytest -q -p no:cacheprovider` 的两份契约测试：
缺模块 **2 collection errors → 58 passed**；构造实例 bytes 强制转换、序列化警告回显、
循环对象错误等新增回归 **5 failed / 113 passed → 118 passed**；加 `-W error` 再跑 118 项通过。
固定错误边界使用 Python-mode 序列化保留类型，禁止序列化阶段悄悄把 bytes 转成文本。

父任务用 MedChat Conda Python `-B -m pytest tests/agent tests/test_agent_anti_hallucination_fallbacks.py
tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short`：
**2093 passed、1 skipped、7 warnings，28.21 秒**。
独立规格审查：118 项现有测试 + 204 项额外合成测试，**322 passed（-W error）**；
另两个精确字节边界探针通过，公开接口与源兼容，无规格阻断。
初轮质量审查 118 项现有 + 167 项合成测试，**285 passed（-W error）**，无移植回归阻断；
发现源版本也存在默认字段导致快照超预算的边界问题，已补最小修复，待增量复审。
源码 compileall（临时 pycache）、全部 8 个 Node 测试及 contract 也通过。

### 默认字段扩展预算

输入 JSON 在 32768 字节时可能先通过，默认字段展开后的快照超过限制，导致相同约束
保存后无法再读取。补充“首次拒绝超大规范快照”和“规范快照恰为 32768 字节可回读”
测试，**1 failed / 1 passed → 两份契约 120 passed（-W error）**。
现在同时限制原输入与经过验证、补齐默认字段后的快照；正常接口/schema 不变，
仅将后续必然失败的超预算输入提前拒绝。未增大持久化或模型传输预算。

预算修复独立规格复审：57 项现有 + 66 项边界探针通过；质量复审：120 项现有 +
100 项合成探针，**220 passed（-W error）**，无阻断。
父任务重新执行 Agent + 反幻觉/平台健康，**2095 passed、1 skipped、7 warnings，24.76 秒**。
所有通过仅证明协议/工程边界，不证明动态循环、真实模型执行或服务器部署完成。

原始混杂工作树保持不变。PR #15 等未获得具体授权的 PR 不自动合并。
