# MedChat 真实浏览器智能体验收

验收日期：2026-10-15。基线为 `5168da5f`，工作分支为 `codex/browser-agent-real-acceptance`。14 个预先定义的场景已完成浏览器复验：11 个 PASS，3 个 BLOCKED。下文保留首次失败及中间修复证据，最终结论以“最终复验”列和“最终浏览器证据”为准。

## 范围与判定

全部业务请求来自真实浏览器主页聊天输入框。独立场景使用新页面和独立 WebSocket；同一场景的追问保持原连接。运行标准 `native/a1_closed` 路径，不使用实验决策页面、假工具或直接 API 请求代替浏览器验收。检查持久化工具记录仅用于核对参数与返回值。

实际运行环境包含已配置的 DeepSeek 主模型、Ollama 分子模型、RDKit、ADMET-AI 1.4.0、现有活性模型资产及反向寻靶数据。运行时隔离验收会话和任务数据库；保留用户已有服务。凭据仅由既有私有配置加载，不写入报告。

PASS 要求意图、输入、约束、工具结果及最终回答一致；FAIL 表示应用行为错误；BLOCKED 表示实际外部依赖不可用；NOT RUN 表示尚未执行。没有真实数值、仅显示完成或回复了自然语言，都不构成科学任务通过。

## 场景结果

完整输入与事先定义的预期见 [验收计划](../plans/2026-10-15-browser-agent-real-acceptance.md)。

| 场景 | 首次浏览器结果 | 首次关键证据 | 最终复验 |
| --- | --- | --- | --- |
| S01 阿司匹林性质和 ADMET | PASS | 真实属性和 ADMET-AI 输出，来源及预测边界明确 | PASS：RDKit、ADMET-AI 真实数值与来源一致 |
| S02 改为咖啡因并比较，禁止 ADMET | FAIL | 仍调用 ADMET；没有阿司匹林对照 | PASS：仅计算咖啡因属性并复用阿司匹林证据 |
| S03 人源 BuChE 实验结构查询 | FAIL | 误派发分子对接，没有执行靶点搜索 | PASS：原请求返回人源 P06276、实验结构及配体 ID |
| S04 生成 3 个 PDE 候选并检查约束 | FAIL | 将新生成请求误当作旧候选引用，要求重新选择集合 | PASS：3 个真实新结构、6 项约束满足，六工具及附属来源/排序均核验通过 |
| S05 第 2 个候选的两种优化 | NOT RUN | 等待真实生成结果后执行 | PASS：正确继承候选/PDE/数量/MW 约束；2 个新结构，仅 1 个降低 logP，如实区分目标是否达到 |
| S06 三分子 BuChE 活性批次 | FAIL | 明确给出 BuChE，仍报靶点未知或歧义 | PASS：真实模型完成三行，保留分类及 pIC50 |
| S07 一致性筛选与活性排序追问 | FAIL | 即使上轮已有三行真实活性，仍要求提供 SMILES | PASS：复用紧邻证据，按原始 pIC50 排序，不虚构排除 |
| S08 咖啡因反向寻靶 Top 5 | FAIL | 未进入工作流；普通聊天错误称无反向寻靶工具，给出未关联来源的文献数值 | BLOCKED：路由已修复，真实工具在 source 阶段校验失败 |
| S09 首个寻靶结果的结构追问 | FAIL | 无真实上游排名，仍误派发对接；没有合理澄清依赖 | BLOCKED：依赖澄清正确，但没有真实上游排名，结构任务未完成 |
| S10 真实结构及共晶口袋对接 | BLOCKED | 使用实际返回的 1P0I；聊天缺少已准备的受体与可验证口袋参数 | BLOCKED：明确未运行，不猜坐标；未获得真实 Vina 结果 |
| S11 两分子性质、ADMET、活性与排序 | FAIL | 只运行属性、类药性及 ADMET；遗漏活性和排序却显示完成 | PASS：三个请求工具均完成；身份对齐、原始值和来源保留，按通过检查的活性排序 |
| S12 全新会话的模糊对接指代 | FAIL | 没有先询问分子/靶点，派发后仅显示尚未计算 | PASS：先要求明确配体、受体及口袋，未运行对接 |
| S13 无效 SMILES 后更正继续 | FAIL | 无效输入正确拒绝；更正后的继续丢失原计算意图 | PASS：拒绝 C1CC 后以更正结构继续属性计算 |
| S14 只查 PDE 资料，禁止生成及对接 | FAIL | 仍派发对接 | PASS：澄清亚型/UniProt，未生成或对接 |

## 首次证据摘要

- S01：`agent-5a07fd9ed627`。属性工具、类药性和 ADMET 输入均为阿司匹林。RDKit MW 180.16 Da、logP 1.31；ADMET-AI 原始物化端点为 MW 180.159 Dalton、logP 1.3101，与界面四舍五入值一致。ADMET 权重标识 `84060276c0886dd8019262ac029b483b1f00d6600c5ac2f2218b74c478051608`。界面明确声明非实验结果。
- S02：`agent-c63a10c98d6d`。`tool_executions` 确认 `admet_predictor` 接收了包含禁止 ADMET 的当前请求，并实际完成咖啡因预测。不是仅有错误文案。
- S03：`agent-f53b9916d15d`。唯一业务工具是 `molecular_docking`，终态 `not_calculated`。
- S06：`agent-1b627e2f7b2d`。`activity_predictor` 输入含三种明确结构及 BuChE；终态 `invalid_input`。
- S09、S12：分别为 `agent-c3abdc63ecc7`、`agent-95bf9103e9b6`；均进入 `molecular_docking` 后 `not_calculated`。未出现伪造结合能，但缺少可操作的澄清说明。
- S11：两个分子的属性和 ADMET 行保持身份对应；缺陷是未覆盖完整任务。对乙酰氨基酚 ADMET 还返回负的半衰期等原始回归输出，不能当作物理上成立的测量值或直接用于决策。

## 修复与复验

已按浏览器失败划分修复范围，先新增失败回归，再修改代码。下列中间记录用于说明修复过程，不替代最终结果。

1. 意图路由、否定操作、上下文继续与明确多工具请求。
2. 新生成数量与旧候选序号引用的区分，保留确认与隔离边界。
3. 自然语言活性请求中明确靶点和批量结构的解析。

### 已完成的中间浏览器复验

- S02：`agent-704ec452e2e7` 为复验首轮；第二轮 `agent-5e69860d3a6f` 仅运行 `property_calculator`，显示阿司匹林 MW 180.16 Da / logP 1.310 与咖啡因 MW 194.19 Da / logP -1.029。上轮结果以 trace 和 evidence ID 标明复用，没有重复 ADMET。
- S06：`agent-825434ceed03` 实际调用已部署的 `pde-buche-20261006-r2-buche-family` 分类与回归模型。阿司匹林、咖啡因、对乙酰氨基酚的原始 pIC50 分别为 4.808964729309082、4.993906497955322、4.516423225402832；原始分类概率分别为 0.29711100459098816、0.4056379795074463、0.2878395617008209。三行一致性均为 true，界面保留对应四位小数。模型范围为家族级且混合物种，不能解释为人源单一实验验证。
- S03 补充精确搜索：`agent-0e354351d884` 发送 `P06276`，返回 Homo sapiens BCHE、10 条结构以及 1P0I 等编号。原始 1P0I 记录含 BUA/NAG 配体 ID；初版界面漏展示，已补回归和格式修复，尚待最终 UI 验证。结构中的配体 ID 不等于已确认用于网格定义的结合口袋配体。
- S08：已由普通聊天转为 `reverse_target_predictor`；返回 `stage=source; reason=validation_failed`。本地 TSV 为 107,988,940 字节，而严格来源加载器的表格上限为 32 MiB；Morgan/MACCS 数组还超过默认 512 MiB 私有快照预算。没有缩小/替换数据、关闭校验或提高安全预算以制造通过。
- S04：引用误拦截已消除，但第一次复验暴露后续靶点语法将 MW/logP 约束当成未知靶点。已建立 3 项失败回归，修复后与旧边界合计 529 项通过；真实生成仍待再次复验。
- S03 原请求：`agent-dba36dcbe4bc` 返回人源 P06276、10 条实验结构及配体 ID，明确说明配体的口袋角色未验证。此前与生成任务同时提交的一次请求 `agent-fd4fc4116fd3` 命中工具并发上限，真实返回失败；待生成结束后重试成功，没有放宽限流。
- S04 第三轮：`agent-ecb753570135` 由 `gmm-llama:latest` 生成 3 个有效唯一结构；独立 RDKit MW/logP 分别为 388.54/2.409、317.37/1.706、290.29/2.649，均落在请求阈值内。下游活性仍因角色句解析而失败，整体显示部分完成；继续修复该参数链及显式约束检查展示。
- S05 原追问：`agent-253768c2485e` 正确选取 `cand-002-9181eef4`，基线 MW 317.37 / logP 1.706。实际生成一个新结构，MW 315.4 / logP 3.473；验证器明确 `evaluated_not_improved`，没有宣称优化成功。仍发现数量错误和前轮 PDE 靶点丢失；继续修复，不能算通过。
- S13：`agent-cd8e33b7b2fd` 在同一会话更正后计算阿司匹林 MW 180.16 Da / logP 1.31；未运行 ADMET。S12/S14 则直接澄清而没有科学任务派发。
- S07：复用 `agent-baba786df469` 的已接受 S06 证据，按咖啡因、阿司匹林、对乙酰氨基酚顺序排列；原始 pIC50 分别为 4.993906497955322、4.808964729309082、4.516423225402832。全部候选均通过一致性检查，因此明确说明没有排除候选，没有按追问文字虚构排除原因。
- S04 新轮：`agent-204a1529b73f` 生成 3 个有效唯一结构；独立 MW/logP 为 341.41/2.526、252.30/0.528、273.34/1.749，界面逐项显示六个约束均满足。真实 PDE 家族活性已完成；但附属证据卡片的 ADMET 来源和排序仍未通过核验，不能据此将全部报告判为通过。
- S05 新轮：`agent-91dbaab696d5` 正确继承第二候选 `cand-002-97f3e22a` 与 PDE。两个新候选 MW/logP 为 268.36/0.786、258.32/-0.777；相对基线 logP 0.528，变化分别 +0.258、-1.305。两个 MW 均小于 450，仅第二个达到降低 logP 的目标。该候选分类概率 0.38029658794403076 与 pIC50 5.422169208526611 冲突；界面明确部分完成并保留原值，未把描述符改善等同于活性改善。
- S11 新轮：`agent-b09f441b9978` 恰好执行属性、ADMET 和活性三个工具。按原始 pIC50 将阿司匹林排在对乙酰氨基酚前，属性行身份正确，完整原始 ADMET 仍保留；但现有校验器把三条常规科学边界提示也计为验证失败，导致汇总把 ADMET 标成不可用。已定位，修复必须保留提示且不能绕过真正的科学校验。

## 最终浏览器证据

- S01 / S02：`agent-02617c19cb91` / `agent-6a11ea2b9795`。阿司匹林 MW 180.16 Da、logP 1.310；咖啡因 MW 194.19 Da、logP -1.029。S02 的持久化执行记录只有 `property_calculator`，没有 ADMET；历史数据有 trace / evidence ID 且没有重算。截图 `s02-final-comparison.jpg` 保留在本地验收输出目录。
- S03：`agent-f58c71e64efa`。真实查询返回 Homo sapiens BCHE / P06276、10 条实验结构，1P0I 的配体 ID 为 BUA / NAG。明确标注配体口袋角色未验证，不把数据库记录当作已经准备好的受体或网格。
- S04：`agent-8cdde6f9623e`。三个新候选依次为 `cand-001-f49ffa32`、`cand-002-d26378ee`、`cand-003-05241ebb`；独立 RDKit MW / logP 分别为 268.27 / 2.304、259.31 / 2.472、299.33 / 2.232。六项请求约束均满足，属性证据为 `evidence-f3785e1cf9242e5f8aab`。真实 target search、generation、properties、ADMET、activity、ranking 六步骤完成；报告接受 `admet_predictor@2` 的 `evidence-6a8b773378315bd96828`。Top-3 为候选 2、3、1；展示实际权重性质 0.5 / ADMET 0.25 / 活性 0.25，不等同于抑制剂或实验结论。
- S05：`agent-c215b11c56a4`。基线确为 S04 的第二候选，MW 259.31 / logP 2.472；靶点继续为 PDE。两个新候选 MW / logP 为 313.39 / 2.982、287.32 / 1.604，均满足 MW < 450；logP 差值分别 +0.510、-0.868。第一个明确为 `evaluated_not_improved`，第二个为 `supported`。这里 PASS 表示引用、执行与真实改善判定正确，不表示两个候选均改善，更不表示活性获得实验验证。
- S06 / S07：`agent-c6a903d3b935` / `evidence-3d8d8fd0cd1528492f05`。三种分子的原始值与前述真实模型记录一致；S07 不重新运行工具，按咖啡因、阿司匹林、对乙酰氨基酚排序。全部通过一致性检查，明确没有候选被排除。所有 pIC50 均低于该模型 5.0 阈值，因此不能将“排名第一”解释为已经证实有活性。
- S08 / S09：`agent-c8c368532744` 在 source 阶段 `validation_failed`；没有返回候选。追问明确要求已确认靶点 / UniProt，不猜测第一名、不派发对接。两个科学任务均 BLOCKED，而非 PASS。
- S10：在 S03 原会话选择实际返回的 1P0I。聊天明确缺少受体准备与 center/size，未运行对接。仅有 PDB 编号与配体 ID 尚不能完成真实 Vina 闭环；未进一步绕过审批或猜测口袋。
- S11：`agent-6e1e4b5afa22`。恰好完成性质、ADMET、BuChE 活性三工具；性质证据 `evidence-56432099d1d30f191b32`、ADMET `evidence-3816f42fb4fa19eab8f5`、活性 `evidence-b7962f2c3f9fc2b691b8`。阿司匹林 / 对乙酰氨基酚 pIC50 为 4.808964729309082 / 4.516423225402832，分类概率为 0.29711100459098816 / 0.2878395617008209；按前者降序，不引入隐藏跨端点权重。两者均未被排除，完整原始 ADMET 保留。
- S12 / S14：新会话分别要求明确配体、受体和口袋，以及具体 PDE 亚型 / UniProt；未派发生成或对接。S13 拒绝无效 C1CC 后，同一会话 `agent-5b7eef969aca` 按更正的阿司匹林继续性质计算，MW 180.16 / logP 1.31，未运行 ADMET。

复验覆盖最终源码版本：S01–S03、S06–S10、S12–S14 在最后一次靶点并列语法修复加载后完成；S04/S05/S11 已在包含其全部相关修复的服务版本完成。没有为追求理想分子数值反复挑选结果。

## 修复文件与回归范围

| 修复范围 | 生产文件 | 回归重点 |
| --- | --- | --- |
| 否定意图、多工具覆盖、继续与澄清 | `src/agent/planning/request_parsing.py`、`task_planner.py`、`src/agent/routing/hybrid.py`、`src/web/chat_handler.py` | 禁止 ADMET/对接；“分别”不被当成否定；更正继续；缺输入不派发；历史字典兼容 |
| 历史比较与活性证据排序 | `src/web/property_comparison.py`、`src/agent/evidence_followup.py` | 会话隔离、成功证据限定、身份一致、缺失行如实部分完成、不虚构排除、不覆盖活性比较 |
| 生成数量与候选指代 | `src/agent/contracts/generation_request.py`、`src/web/scientific_references.py`、`src/agent/persistence/scientific_references.py`、`src/agent/tools/llm_molecular_generator.py` | 新生成与旧序号区分；前轮靶点携带；两个新结构；排除原种子；未知/否定引用保持拒绝；长空白输入性能 |
| 靶点和活性参数 | `src/agent/contracts/target_request.py`、`src/agent/tools/activity_input.py`、`target_database_tool.py` | 中文化合物名、角色句、序号与真实靶点区分；严格 UniProt/并列/未知标识；配体 ID 仅按来源显示 |
| 真实约束与优化判定 | `src/agent/property_constraints.py`、`src/web/property_constraints.py`、`src/agent/tools/lead_optimization_verifier.py` | MW/logP 的明确数值/单位；来源 trace/候选绑定；失败属性行不可作为基线；改善与硬约束分别检查 |
| ADMET 证据链 | `src/admet/predictor.py`、`src/agent/validators/result_validator.py`、`src/agent/runtime/run_session.py` | 只豁免三个确切科学提示且保留提示；拒绝真正告警/错误/伪造摘要；对齐前核验、对齐后记录摘要；版本一致 |
| 比较表与科学措辞 | `src/web/static/js/home/formatters.js`、`src/web/templates/index.html`、`src/agent/tools/drug_likeness_assessment.py` | 完整表格、逐单元格转义、安全链接、畸形表不丢数据；启发式评分不是成药概率 |

新增测试文件及两个既有测试调整随同本次提交；没有修改模型权重、数据、阈值、外部预算或部署配置。采用“先失败回归、再最小修复、再真实浏览器复验”，并对关键边界分别做需求符合性与独立代码质量审查。

## 自动化验证

- 最终集中回归：本次新增/修改的 **19 个 Python 测试文件，1221 passed（51.96 秒）**。实际运行显式列出这 19 个文件，并使用独立临时目录、`-p no:cacheprovider --tb=short`。提交后可用以下等价命令选取相同文件：

  ```powershell
  $tests = git diff --name-only 5168da5f HEAD -- tests | Where-Object { $_ -like '*.py' }
  python -m pytest $tests -q -p no:cacheprovider --tb=short
  ```

- 全仓命令：`python -m pytest tests -q -m "not real_external" --tb=short -o faulthandler_timeout=60`，使用具备 RDKit / PyTorch 的 MedChat Conda 环境。第二轮运行到约 69% 时挂起，已中断；此前进度中未出现失败标记，但没有最终汇总，不能记作全仓通过。
- 挂起证据：60 秒 faulthandler 指向 `tests/task_runtime/test_docking_consent.py::test_c4a_pending_command_retains_real_lease_and_capacity` 的 `asyncio.run` / executor shutdown，以及 `src/task_runtime/docking_execution.py::_CommandLeaseGate._retain` 的条件等待。这些源文件和测试本次均未改动；触发保留状态的根因尚未完成隔离，不能仅凭未改动就断言与本次集成无关。未更改安全门禁或隐藏该阻塞。
- 后续覆盖补跑：`python -m pytest tests --ignore=tests/agent --ignore=tests/task_runtime -q -m "not real_external" --tb=short -o faulthandler_timeout=60`。明确排除已执行的 Agent 目录和发生挂起的 task_runtime 目录，结果为 **1 failed、7653 passed、242 skipped、1 deselected、173 subtests passed（1513.24 秒）**；这不是完整全仓通过。
- 补跑唯一失败为 `tests/test_activity_route_budget.py::test_cancel_terminates_child_and_releases_capacity`：预期 `asyncio.CancelledError`，实际为 HTTP 504 `ACTIVITY_REQUEST_TIMEOUT`。单独执行 `python -m pytest tests/test_activity_route_budget.py -q -p no:cacheprovider --tb=short -o faulthandler_timeout=30` 仍为 **1 failed、10 passed（10.56 秒）**。相关测试、`src/web/routes/activity_prediction_routes.py` 和 `src/web/process_isolation.py` 本次未改动；稳定复现的取消/超时边界问题仍待根因隔离，不能以“原有文件”或一次重跑掩盖。没有改阈值、放宽断言或绕过子进程清理。
- 首次全仓运行在长空白输入的生成数量正则上发生高 CPU 停滞，已中断，不能记作通过。用独立子进程 8 秒上限建立失败回归后消除正则回溯；相关 98 项通过，再启动上述完整一轮。
- 聚焦回归包含 760 项活性/生成集成、180 项聊天事件/部分完成、141 项历史兼容/比较边界、104 项约束/优化/序号、263 项靶点输入，以及 ADMET / 运行时相关 771 项，分别通过；这些计数有交集，不相加作为独立总数。
- `python -m compileall -q src scripts`：通过。
- `node --check`：54 个非 ketcher/backup JavaScript 文件通过。
- `node tests/home_comparison_table_test.js`、`node tests/frontend_safe_render_test.js`、`node tests/home_agent_task_panel_test.js`、`node tests/reverse_target_broad_recall_test.js`、`node tests/reverse_target_pagination_test.js`：全部通过。
- `git diff --check`：通过。未更改部署/数据，未把健康检查或 fixture 测试当作真实科学验收替代。
- 各修复分组的需求符合性与独立质量审查均已完成，最终 ADMET / 对齐摘要集成审查未发现重要问题。最后两位审查者的结论为静态审查：需求审查的独立测试因沙箱临时目录权限在 setup 阶段失败，质量审查未运行测试；它们不替代主执行者的回归结果和真实浏览器证据，也不是合并批准。

## 局限与实际能力判断

1. 本轮只能证明所列代表场景在当前本机配置可复现，并非所有自然语言表达都可靠。确认绑定、模糊输入和未知靶点采用保守拒绝；不能据此宣传全自动端到端药物发现。
2. 反向寻靶资产与严格加载预算不兼容：表格 107,988,940 字节超过 32 MiB 上限，指纹矩阵又超过默认 512 MiB 快照预算。需要单独的数据发布/加载方案；未通过关闭校验或扩大安全预算制造成功。
3. 对接没有完成真实 Vina 运行，未得到结合能或姿态文件。已知结构编号不会自动补齐受体准备与可信共晶口袋参数，聊天到对接的自动准备仍有能力缺口。
4. 活性模型为家族级、混合物种或混合 PDE 亚型；预测不是人源单一亚型实验事实。一致性通过也不等于模型校准、外推可靠性或实验有效性验证。
5. ADMET 原始回归有负半衰期、负分布容积等非物理输出，报告保留而不篡改。这些端点仍需独立适用域/物理合理性审查；当前风险汇总只是分类端点启发式，不构成安全结论。
6. 长原始结果仍较冗长，宽表需要横向滚动；当前候选卡片并非总能显示优化后的独立属性，权威属性以有来源的验证表为准。
7. 完整回归门禁未通过：对接租约测试挂起，以及活性请求取消测试稳定返回超时而非取消。两者没有在本次浏览器场景中被证实修复，应保留为后续独立定位范围；本次交付只适合草稿审查，不满足合并门禁。

修复后，基础性质/ADMET、明确靶点查询、有限约束生成、候选优化追问、活性批量预测及证据排序已能完成本次代表任务，并对未改善、缺信息和失败给出诚实边界。适合作为有人监督的研究辅助；反向寻靶与真实对接尚不能宣称闭环可用。
