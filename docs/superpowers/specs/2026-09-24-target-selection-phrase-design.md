# 英文候选筛选短语的最小解析修复

日期：2026-09-24。用户已确认最小方案；本书面设计待审阅。
分支：`codex/target-selection-phrase-pr`。
初始基线：`75d6a3abc6f6d79e96bf38a019ce2980576c100e`。

## 1. 已复现问题

`src/agent/contracts/target_request.py` 的 `_FOLLOWING` 会把靶点后
`and select` 中的 select 作为后续标识。`select` 不在 `_ACTION_WORDS`，
因此 `Design 2 molecules for PDE5A and select top 3` 得到：

```text
targets=('PDE5A',), unknown=('select',), needs_clarification=True
```

将 select 改为 screen 时不澄清。大小写 SELECT、逗号连接也复现。
当前 Router 和 Planner 使用同一解析函数，因此错误会阻止靶点设计流程。

临时内存探针还证明：直接将 select 加入全局动作词集合，会让
`for PDE5A and select XYZ999` 不再澄清，错误丢失未知选择。
不能采用这种看似简单的全局白名单方案。

## 2. 选定方案与非目标

在共享解析边界增加一个有界的候选筛选短语判别，不改 Router/Planner 的职责或分数。
只在 `_FOLLOWING` 正要消费 select 为靶点标识时，将明确的候选筛选动作识别为动作边界。
不改变 `_ACTION_WORDS`，不删除或改写原 query，不引入 LLM 解析或新依赖。

备选方案：全局动作词放行已有反例，排除；重写自然语言语法范围过大，也排除。
本批不承诺识别任意英文筛选措辞，不修复跨轮引用或其他旧 Agent 接口。

## 3. 有界语法与安全约束

首批只接受靶点后的 `and` 或逗号连接、大小写不敏感的下列完整句尾短语：

```text
select top N
select top N candidates
select top N molecules
```

- 单词间允许空格或 tab，不跨换行拼接动作。
- N 是无前导零的 ASCII 正整数，范围 1–100（与现有 Top N 上限一致）。
- 句尾可有空白和一个英文/中文句末标点（`.`、`!`、`?`、`。`、`！`、`？`）。
- 必须校验从 select 起直到输入结束的完整后缀，不能仅匹配前缀。使用有长度上界的数字规则，禁止嵌套无界回溯。
- `select XYZ999`、`select EGFR`、`select top 3XYZ999`、`select top 3 and XYZ999`、`select top 3 candidates for XYZ999` 不符合上述短语，保留澄清。
- `select top 3.5`、负数、指数、全角数字、0、101及超长数字不由新识别器放行；不修改原 Top N 解析函数。
- `or select`、跨行连接和任意其他英文后缀不扩展支持；保持原行为。
- 已发现的其他未知标识不能因识别筛选动作而消失；多靶点、否定、切换、选择性检查仍对完整原文执行。
- 不用此短语证明靶点结构或活性模型可用，也不豁免执行器的 target_evidence、SMILES、数量或来源验证。

## 4. 文件与兼容边界

生产只修改 `src/agent/contracts/target_request.py`，新增小型有界判别 helper 和一个既有后续标识循环的判断。
测试在 `tests/agent/test_target_identity_alignment.py` 扩展语句矩阵，必要时新增同主题独立测试文件，避免将职责塞入无关测试。
不修改能力注册、工具 Adapter、workflow executor、数据表或 API。

PR #53 已有一个“保留原 select top 澄清行为”的特征测试。
本修复应在 PR #53 获具体授权合并后对齐 main，再将该测试明确改为本次新行为，并保留其余全字段迁移断言。
本分支先只提交设计，不提前改变 PR #53 的已审快照，也不让两个互相矛盾的断言同时发布。
若 #53 暂缓，本修复的发布顺序需要明确调整，不自动合并任何 PR。

## 5. TDD 与验收

1. 在原实现增加正向短语用例，记录真实 RED：Router 不应要求靶点澄清、应选择 target_driven_design，Planner 应保留 requested_count=2、Top N=3、PDE5A 及完整六步。
2. 覆盖 and/逗号、大小写、可选 candidates/molecules、句末标点、数字上下界。
3. 对第3节全部负向、其他未知标识、多靶点、否定/选择性建立澄清断言；既有 screen/report/do 行为保持不变。
4. 复用现有真实 WorkflowExecutor.prepare 安全门：合法短语仍需真实靶点证据；不执行工具的拒绝场景不能冒充已完成计算。
5. 保留 `test_target_list_scan_has_linear_operation_bound`，补较长数字/后缀验证，不改既有时间门禁。
6. 最小修复达到 GREEN 后跑靶点身份、Router→Planner、全 Agent、相关联合与断网 contract；独立规格/质量审查通过后创建独立 draft PR。

所有测试离线、隔离配置与数据库，不读取凭据，不调用外部模型或部署；合成工具输出不作为科学验收。

## 6. 已有证据

现状临时探针：9 passed（0.11s）；加入全局白名单反例后10 passed（0.12s）。
这些断言刻画旧行为与危险备选，不表示问题已修复。探针仅在 ignored scratch，不提交。

本分支原代码的迁移前回归使用既有隔离 RAG runner（仅替换工作树），实际参数：

```text
python -B -m pytest tests/agent/test_target_identity_alignment.py tests/agent/test_task_planner.py tests/agent/test_routing_prompt_matrix.py -q -p no:cacheprovider --tb=short -rs
```

结果：**379 passed，3.82s，exit 0**。业务代码尚未修改。
