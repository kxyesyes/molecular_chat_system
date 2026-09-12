# Agent 证据隔离与续接存储基础集成

日期：2026-09-12。分支 `codex/agent-evidence-continuation-store`。
初始基线 `15061b2`；PR #21 获用户授权合并后无冲突快进至 `9876cc4`，本批六个代码/测试文件未被覆盖。
状态：实施、独立规格和质量复审完成；本批提交后创建 draft PR，等待 CI 和具体合并授权。

## 范围

选择性移植历史 `9312bf5`，不整体合并旧树，不切换生产 Agent、不训练/激活模型。

- `src/agent/evidence/ledger.py`：有限数值 JSON 输出摘要；可选 request-input/evidence/operation 绑定进入证据 ID；登记、读取、列表和 claim 嵌套值深拷贝。
- `src/agent/persistence/base.py`：新增独占启动和续接 CAS 协议。
- `src/agent/persistence/sqlite_store.py`：默认保留 UPSERT，显式 exclusive 使用 INSERT；用户/会话绑定、BEGIN IMMEDIATE 原子发布与单次领取；严格类型比较，不把 bool/int/float 相等当同一快照。
- `src/agent/persistence/redaction.py`：凭据材料检测，不把分子斜杠或普通路径当密钥。
- `tests/agent/test_evidence_ledger.py` 与 `test_decision_continuation_store.py`：嵌套修改、身份摘要、并发/跨进程竞争、事务回滚、提交结果不确定与资源边界测试。

## 边界

存储信任本地数据库及调用方传入身份，不是身份认证、科学证据验证或崩溃恢复系统。
校验 checksum、configuration/revision、上游输出消费和“提交结果不确定时不派发工具”属于后续 harness。
本批数据库接口只在确定提交后返回领取成功；错误向上传递，禁止调用方将错误解释为授权。
每份 JSON 上限 512 KiB（包含 claimed_by）、32 层、65,536 节点、4,096-bit 整数；不截断。
满预算发布之后可能因新增 claimed_by 超预算而拒绝领取，调用方需预留空间。
凭据检测覆盖已知形状及带标签秘密，不是通用 DLP。测试只用合成样例和临时数据库。
Ledger 保留既有科学可用性判断；证据 ID 存在不证明某个数值与该证据相符，后续完整性门禁仍必需。

## 已运行验证（审查前证据）

解释器为 MedChat Conda Python，使用 `-B` 和 `-p no:cacheprovider`。

- Ledger TDD：34 failed / 10 passed → 44 passed；ledger/scientific trust/contracts 聚焦 61 passed，`-W error`。
- Store TDD：125 failed（接口缺失）；扩展阶段 1 failed / 136 passed（幂等发布兼容性）→ 137 passed。
- Store/persistence/events/resume/audit/decision contract：240 passed。
- `pytest tests/agent tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q --tb=short`：旧基线 2271 passed、1 skipped、7 warnings，29.23s；不是合入 PR #21 后的最终证据。
- 旧基线 compileall、8 个 Node 测试、contract、`git diff --check` 通过；contract 的无效 SMILES 解析警告是负例预期。

合入 PR #21 后，Agent + decision model + OpenAI-compatible model + 反幻觉/健康联合回归为
2469 passed、1 skipped、7 warnings，28.99s。这是修复以下审查发现之前的证据，不代替最终回归。

## 独立规格审查发现与修复

1. 摘要函数仍容许 tuple、非字符串字典键被 JSON 自动转换；严格 JSON 输入契约需补全，合法 JSON 字节格式保持不变。
2. Cookie、refresh_token 和带连字符的 access-key 标签未被新凭据检测拒绝；合成测试证实可进入临时数据库，需补标签识别及拒绝无写入测试。
3. 续接 JSON 在检查字节上限前完整序列化，大整数列表可产生明显超预算分配。1024/4096 个 4096-bit 整数分别产生约 1.27/5.06 MB 文本，峰值约 2.62/10.42 MB；未写数据库，但资源拒绝过晚。需增量编码并在累积前拒绝超预算。

独立审查聚焦 181 + 11 passed；独立负例 6 failed/2 passed 复现前两项，资源诊断 4 passed 仅证明第三项存在。
修复使用严格递归 JSON 类型/循环检查、新的完整凭据标签识别和增量编码预算，不改旧 `_json_dump` 或全局脱敏行为。
迭代中发现旧脱敏会把普通 token_count、secretory_protein、password_length 改写；仅在新 CAS 路径对已验证无凭据的快照无损写入，其余 metadata 仍按旧规则脱敏。
补测先失败再修复；独立规格复审 363 passed，修正后的独立探针 13 passed，三项 P2 均关闭。
数字数组探针峰值约 565–589 KB；单个转义字符串编码块仍可能约 3 MiB，是受现有输入长度限制的临时分配，不宣称总内存严格小于 512 KiB。

## 规格修复后的父任务回归（基线 9876cc4）

```text
python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short
2640 passed, 1 skipped, 7 warnings in 33.51s
```

7 warnings 为现有 SWIG/FastAPI 弃用提示；唯一跳过为 `test_harness_shadow.py` 中显式 opt-in 的性能测试
（需要 `MEDCHAT_RUN_PERF_TESTS=1`），不是科学工具依赖失败，也未用模拟结果替代。
最终 `python -m compileall -q src scripts`（字节码放系统临时目录）、全部 8 个 Node 测试、
`python -B scripts/run_agent_acceptance.py --mode contract`、`git diff --check` 均通过。
独立质量审查运行 474 项现有聚焦测试通过，但独立探针 3 failed / 5 passed 发现两项 P2：

- 无关 `update_run_metadata` 调用仍会二次脱敏改写已验证快照，导致后续合法 claim 失败；需要保留快照并保证元数据读改写不覆盖并发领取。
- 新 guard 复用的 URL 表达式对 `a.` 重复文本存在平方级扫描；131,072 字符低于预算但超出 5 秒探针时限。应线性识别 URL，不通过截断扫描来漏检末尾凭据。

两项已按 TDD 修复（初轮 11 failed，补充边界 3 failed），增加 34 项正式用例：

- 普通 metadata 更新在 `BEGIN IMMEDIATE` 中读改写，保留已验证 continuation；其他 metadata 仍脱敏。
  明确传入保留字段 `decision_continuation` 现在抛 `ValueError`，要求通过 CAS 写入。这是新增保留字段契约，不影响已有普通字段调用。
- 新 guard 线性扫描 URL 候选，旧 sanitizer 不变；不截断全文。覆盖 520,000 字符、19,000 个 URL 和末尾合成凭据。
  原来超时的 5 秒探针约 0.20 秒完成（合成微基准，非整体系统吞吐指标）。
- worker 聚焦及探针 338 passed，原质量探针 8 passed；独立复审仍待完成。

最终父任务按上述联合命令重新运行：**2674 passed、1 skipped、7 warnings，49.35s**；
compileall、8 Node、contract、diff-check 再次通过。
最终独立质量复审 **516 passed**（含原探针 8 项），另 **21,190 组 URL 差分检查通过**；两项 P2 均关闭，无新的可操作问题。
本批五项审查发现均保留失败与修复证据；CI 状态见本分支 PR，未获该 PR 具体授权不得合并。
PR #20 等待具体合并授权；PR #15 仍需纳入关闭测试修复后重验。
旧 sandbox artifact_failed 偶发失败与历史残差继续保留，不能据此批通过宣布整个项目可部署。
