# 家族活性预测冲突复核契约

状态：设计待用户审阅；尚未实施。

## 目标与已确认边界

用户批准继续处理分类与回归结论冲突的状态表达问题。保留两模型原始概率、
分类标签、pIC50、warnings 和 provenance；明确区分计算执行成功与预测结论一致。
不重训、不改变阈值、不裁剪数值、不启用生产模型、不重启服务。

基线为 main 合并 PR #29 的提交 `86639344c472549fb9552a9eaae1682c72b86139`。
本任务使用独立分支 `codex/family-prediction-review`，不修改原始混杂工作树。

此前隔离验收已复现 BuChE 分类与回归在标签阈值两侧冲突。
这证明输出有分歧，不能证明其中哪个预测正确；本任务不做模型性能改善声明。

## 当前链路与缺陷

- `src/activity/family_predictor.py`：分类成功行全部回归；两阶段成功统一返回
  `success=true, status=passed`，冲突仅用一致性布尔值和 warning 表示。
- `src/activity/prediction_service.py`：按行 success/status 汇总，不区分预测冲突。
- `src/agent/validators/domain_validators.py`：ActivityResultValidator 当前只允许
  回归值为空的 partial；直接更改预测器状态会导致真实冲突数值被拒绝。
- `src/agent/tools/activity_predictor_tool.py`：按汇总状态构造 ToolResult 与文本。
- `src/web/static/js/activity_prediction/results_renderer.js`：展示冲突注释，
  但整体状态仍可能显示完成。
- `tests/family_acceptance_chain_support.py`：有效输入检查固定要求 passed，
  需要区分工程验收通过与被测预测结果需要复核。

## 方案选择

1. **推荐：独立执行状态 + 保守结果状态**。新增家族行 execution_status，
   冲突用现有 partial 表达，保持原数值。兼容现有 Agent 状态枚举，
   但需要同步修改 Validator、汇总、展示和验收检查。
2. 仅追加“需复核”文案：改动少，但消费 success 的下游仍会误当无条件成功，舍弃。
3. 冲突一律执行失败并清空数值：会丢失真实观察，也误报工具故障，舍弃。

## 行级契约

只对家族双模型路径新增 execution_status，不改 legacy 单模型响应。
沿用 classification_regression_consistent，不再新增重复的 review 布尔字段。

| 情况 | execution_status | status / success | 一致性字段 | 返回数值 |
|---|---|---|---|---|
| 两阶段成功且一致 | passed | passed / true | true | 原始两项 |
| 两阶段成功但冲突 | passed | partial / false | false | 原始两项 |
| 分类成功、回归失败 | partial | partial / false | null | 保留分类，pIC50=null |
| 输入、模型组或分类失败 | failed | failed / false | null | 不补齐失败阶段数值 |

冲突 warning 使用“分类与回归预测不一致，需复核；已保留两项原始结果。”。
冲突不是执行异常，errors 不制造虚假阶段故障。
一致性为 true 仅表示阈值判断一致，不得展示为“实验验证”或“准确可靠”。

概率阈值固定为 0.5，pIC50 标签阈值固定为 5，等于阈值归入有活性。
分类阴性仍执行回归。不得从类别构造回归值或通过改阈值消除冲突。

## 汇总、校验与下游

### 服务与 API

保留现有 HTTP 状态、results 字段、输入顺序、重复行和数值。
有冲突或阶段缺失时，汇总最多 partial / success=false；全部失败仍为 failed。
不增加新的顶层状态枚举，不把 HTTP 200 当预测通过。

旧家族响应显式存在冲突或其两项数值实际矛盾时，汇总不能仍视为完整成功。
不得为缺少计算或溯源的旧行凭空添加 execution_status=passed。
汇总不得通过就地修改 results 改写原始观察。

### Validator 与 Agent

只为具备真实双模型溯源、合法有限数值、合法阈值和类别、无阶段错误的冲突行，
允许 `partial + execution_status=passed + success=false` 同时保留 pIC50。
Validator 重新计算两阈值判断的一致性，拒绝伪造的一致性标记、矛盾状态或错误对象。
不能仅凭一个 needs-review 文案或布尔标记放宽科学校验。
原有缺权重、demo/fallback、缺溯源、NaN/Inf、无效输入拒绝规则不减弱。

工具观察映射为 ObservationStatus.PARTIAL，保留 evidence、quality、warnings、
原始行和模型身份；明确文字为“计算已完成，分类与回归不一致，需复核”。
动态决策及 ChatHandler 不得把 partial 抹平成无条件科学完成。
可展示真实部分观察，但不能因此自动重试相同工具或把原值清空。

### 前端

复用活动预测页的 DOM API 安全渲染，不新增框架。
行状态和整体提示明确出现“需复核”，并解释这是预测分歧而非工具未运行。
保留概率、类别、pIC50 及来源查看入口；单条与混合批次均覆盖。
聊天页面通过工具结构化状态和受控文本保留相同提示；只有测试证明必要时才修改其代码。

## TDD 实施与验收顺序

1. 先补预测器的两种冲突方向、两阈值边界、正常一致、阶段失败测试，确认预期失败。
2. 最小修改行状态，再补并修复共享汇总、API 和 Validator 的契约测试。
3. 补工具适配、动态决策/ChatHandler 透传与 Node DOM 行为测试，确认冲突
   不被伪装为通过，也不导致已算数值丢失；legacy、空批次、混合批次保持原边界。
4. 更新隔离验收检查和报告白名单，保留 execution_status、结果 status 与一致性字段。
   工程测试可以因“正确识别冲突”通过，但报告必须明确预测为 partial。
5. 运行受影响的 Python、Agent 和 Node 回归、修改文件的语法检查。
6. 使用之前授权的 PDE/BuChE 固定模型组重新进行隔离真实权重验收。
   源资产只读、临时副本推理；清理临时资源；不读取原始训练数据或调用外部主模型。
   对照此前预测值，不要求改变模型输出，只验证新的状态及提示。

主要现有测试入口：

- `tests/test_activity_family_predictor.py`
- `tests/test_activity_family_api.py`
- `tests/agent/test_family_activity_tool.py`
- `tests/activity_family_results_test.js`
- `tests/test_activity_family_acceptance_chain.py`
- `tests/test_activity_family_acceptance_support.py`
- `tests/test_activity_family_real_acceptance.py`

同步更新 `docs/activity_family_inference.md`、`docs/activity_family_api.md`
及本任务交接记录；不在设计阶段声称上述测试已执行。

## 发布边界

一个独立分支与一个 draft PR。精确暂存，不提交真实权重、注册库、运行报告、
数据集、密钥或本机部署配置。通过审查和 CI 后另行请求指定 PR 的合并授权。
本修复不等同于模型重新训练、性能达标、外部主模型端到端验收或生产发布。

## 设计自检

- 有界目标、状态含义、冲突处理和测试范围已明确。
- 未把模型分歧当成确定的模型错误；未用展示修复冒充性能提升。
- 不新增顶层状态枚举；Validator 的 partial 放宽仅限可验证的双模型冲突。
- 该文档待用户审阅后，进入实施计划及测试先行编码。
