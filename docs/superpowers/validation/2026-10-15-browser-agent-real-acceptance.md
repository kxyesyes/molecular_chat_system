# MedChat 真实浏览器智能体验收

验收日期：2026-10-15，后续复验至 2026-10-16。基线为 `5168da5f`，工作分支为 `codex/browser-agent-real-acceptance`。14 个预先定义场景的累计浏览器结果为 13 个 PASS，1 个 BLOCKED（S10）；这不是所有场景在最终集成版本上的完整重跑。下文保留首次失败及中间修复证据，最终结论以“最终复验”列及最新增量记录为准。

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
| S08 咖啡因反向寻靶 Top 5 | FAIL | 未进入工作流；普通聊天错误称无反向寻靶工具，给出未关联来源的文献数值 | PASS（10-16 04:58）：全量 831,120 行来源 READY，真实前五行与完整十行证据、原始数据行一致 |
| S09 首个寻靶结果的结构追问 | FAIL | 无真实上游排名，仍误派发对接；没有合理澄清依赖 | PASS（10-16 05:00）：同会话 NOTUM → 人源 Q6P988 → 十项实验结构；链接/来源显示正确，明确未启动对接 |
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
  $tests = git diff --name-only 5168da5f cc06a050 -- tests | Where-Object { $_ -like '*.py' }
  python -m pytest $tests -q -p no:cacheprovider --tb=short
  ```

- 全仓命令：`python -m pytest tests -q -m "not real_external" --tb=short -o faulthandler_timeout=60`，使用具备 RDKit / PyTorch 的 MedChat Conda 环境。第二轮运行到约 69% 时挂起，已中断；此前进度中未出现失败标记，但没有最终汇总，不能记作全仓通过。
- 初次交付时的挂起证据：60 秒 faulthandler 指向 `tests/task_runtime/test_docking_consent.py::test_c4a_pending_command_retains_real_lease_and_capacity` 的 `asyncio.run` / executor shutdown，以及 `src/task_runtime/docking_execution.py::_CommandLeaseGate._retain` 的条件等待。当时未完成根因隔离，未更改安全门禁或隐藏该阻塞；后续定位与修复见下节。
- 后续覆盖补跑：`python -m pytest tests --ignore=tests/agent --ignore=tests/task_runtime -q -m "not real_external" --tb=short -o faulthandler_timeout=60`。明确排除已执行的 Agent 目录和发生挂起的 task_runtime 目录，结果为 **1 failed、7653 passed、242 skipped、1 deselected、173 subtests passed（1513.24 秒）**；这不是完整全仓通过。
- 初次交付补跑唯一失败为 `tests/test_activity_route_budget.py::test_cancel_terminates_child_and_releases_capacity`：预期 `asyncio.CancelledError`，实际为 HTTP 504 `ACTIVITY_REQUEST_TIMEOUT`。单独执行 `python -m pytest tests/test_activity_route_budget.py -q -p no:cacheprovider --tb=short -o faulthandler_timeout=30` 仍为 **1 failed、10 passed（10.56 秒）**。当时没有改阈值、放宽断言或绕过子进程清理；后续确定性复现与修复见下节。
- 首次全仓运行在长空白输入的生成数量正则上发生高 CPU 停滞，已中断，不能记作通过。用独立子进程 8 秒上限建立失败回归后消除正则回溯；相关 98 项通过，再启动上述完整一轮。
- 聚焦回归包含 760 项活性/生成集成、180 项聊天事件/部分完成、141 项历史兼容/比较边界、104 项约束/优化/序号、263 项靶点输入，以及 ADMET / 运行时相关 771 项，分别通过；这些计数有交集，不相加作为独立总数。
- `python -m compileall -q src scripts`：通过。
- `node --check`：54 个非 ketcher/backup JavaScript 文件通过。
- `node tests/home_comparison_table_test.js`、`node tests/frontend_safe_render_test.js`、`node tests/home_agent_task_panel_test.js`、`node tests/reverse_target_broad_recall_test.js`、`node tests/reverse_target_pagination_test.js`：全部通过。
- `git diff --check`：通过。未更改部署/数据，未把健康检查或 fixture 测试当作真实科学验收替代。
- 各修复分组的需求符合性与独立质量审查均已完成，最终 ADMET / 对齐摘要集成审查未发现重要问题。最后两位审查者的结论为静态审查：需求审查的独立测试因沙箱临时目录权限在 setup 阶段失败，质量审查未运行测试；它们不替代主执行者的回归结果和真实浏览器证据，也不是合并批准。

## PR #209 后续：租约挂起与活性取消修复

维护者在初次交付后明确授权继续定位、修复这两个回归问题并更新同一 PR，暂不合并。运行环境为 Windows / Python 3.10.20；临时目录权限导致的沙箱 setup 错误不计作产品失败，实际回归在获得许可的本机环境执行。未调用真实模型或外部科学服务，未改动既有运行服务。

追加文件仅七个：生产代码 `src/web/process_isolation.py`、`src/web/routes/activity_prediction_routes.py`；测试 `tests/task_runtime/test_docking_consent.py`、`tests/test_activity_route_budget.py`、`tests/test_process_isolation.py`；本报告及 `docs/superpowers/plans/2026-10-15-runtime-regression-followup.md`。未修改其他业务、预算、模型、数据或部署配置。

### 对接：测试夹具漏隔离分析受体命令

原 `normal` 用例单独运行也挂起。有限时诊断在 `CommandAdapter` 的进程创建边界捕获 `spawn_outcome_uncertain`：测试夹具仅允许九参数的 Python 文件写入命令，但生产服务在受体准备前还会调用分析受体的 `reduce`。该调用仍指向宿主机工具，六参数 argv 被测试白名单拒绝，随后运行时按设计保留不确定的物理所有权、租约和容量；最终测试清理等待这个永久保留的线程。因此根因是夹具未覆盖新增调用边界，不是租约保护应提前释放。

- 新增 `test_c4a_runtime_analysis_command_is_controlled`：先检查夹具工具配置，再实际执行受控子进程，验证 scope、资源关闭与 `copy_unavailable`，不产生伪造加氢/科学结论。修复前 **1 failed（2.28 秒）**，在启动子进程前明确发现 `reduce_cmd` 未绑定测试解释器。
- 夹具显式配置 `reduce_cmd`，只把精确分析命令转换为无输出的隔离 Python 子进程；继续保留命令白名单、真实 Job/线程/句柄观察和生产所有权判定。对取消/截止时间用例，等待非故意挂起的分析线程结束，仍只保留受体 spawner 的原有限屏障。
- C4a 回归：`python -m pytest tests/task_runtime/test_docking_consent.py -k c4a -q`，**14 passed、151 deselected（11.13 秒）**，覆盖正常、取消、截止时间、重复取消/关闭及各失败/验证/完成路径。
- 对接生产代码（包括 `_CommandLeaseGate`）没有修改，没有将不确定所有权降级为可释放状态。

### 活性：启动/结果完成与请求取消发生竞态

在支持的 Python 3.10 环境，`asyncio.wait_for(asyncio.shield(task), timeout)` 在内部任务完成与调用方取消同一轮发生时可能返回任务结果，吞掉取消。启动边界丢失取消后，慢子进程继续等待至请求预算，返回 HTTP 504；结果边界也可能错误返回成功。

- 新增三个确定性回归：真实线程工作完成后，在唤醒调用方前安排取消，覆盖共享进程启动、活性启动和活性结果等待；不靠增加 sleep 或超时来改变时序。修复前 **3 failed、25 deselected**，分别显示取消丢失。
- 两个请求路径改为 `asyncio.wait` 保留调用方取消并继续拥有内部任务，完成后显式取 `task.result()`；实际超时、错误传递和既有终止/关闭流程保持不变。
- 测试验证清理期间并发容量仍被占用、清理后才释放、真实子进程停止且慢任务未写入结果。`python -m pytest tests/test_activity_route_budget.py tests/test_process_isolation.py -q`：**28 passed（10.31 秒）**；新增三个竞态回归另重复五轮，每轮 **3 passed**。

### 组合验证与共享调用方检查

- 组合命令：`python -m pytest tests/task_runtime tests/test_docking_command_cancellation.py tests/test_activity_route_budget.py tests/test_process_isolation.py tests/test_task_runtime.py tests/test_task_runtime_binding.py -q -m "not real_external" -p no:cacheprovider --tb=short -o faulthandler_timeout=60`，**1893 passed、31 skipped（487.16 秒）**。涵盖整个 task_runtime，以及真实 OS 命令所有权、取消、清理和共享进程隔离模块。
- 共享启动函数的扩展检查：`python -m pytest tests/test_pharm3d_resource_budget.py tests/test_reverse_target_result_status.py tests/test_admet_routes.py -q -m "not real_external"`。与上述组合回归并行的首轮为 **50 passed、2 failed（20.52 秒）**；两处都在 Pharm3D 的“4 秒内出现子进程启动标记”前置断言失败，尚未执行取消/超时断言。单独选择两个用例仍曾失败，不能删去这一记录。
- 对该波动进行只读 A/B：同一临时诊断入口分别加载 `cc06a050` 的原启动函数与当前函数，两者均为 **2 passed**；无请求预算包装的真实测试目标观测中，进程创建返回为 0.157 秒，目标入口为 2.922 秒，随后已终止且未完成计算。正常 pytest 入口最后复跑整个 Pharm3D 模块为 **12 passed（8.45 秒）**。这些结果指向启动前置条件的时序波动，但不证明所有负载下都稳定；没有提高预算、修改该模块或仅凭重跑宣布问题不存在。
- 两位独立审查者先后完成需求符合性、代码质量静态审查，均未发现重要新增问题。审查未独立复跑测试；质量审查时仍明确保留上述扩展检查失败，不能将静态 PASS 写成全套验证通过。
- 最终单独执行完整离线回归：`python -m pytest tests -q -m "not real_external" -p no:cacheprovider --tb=short -o faulthandler_timeout=60`，使用独立 `--basetemp`。结果为 **23526 passed、270 skipped、1 deselected、13 warnings、173 subtests passed（3750.14 秒 / 1:02:30）**，退出码 0；没有失败或挂起。包含原对接租约、活性取消及上述 Pharm3D 用例，不再以排除故障目录的补跑代替完整结果。跳过/未选中的测试和真实外部服务仍不在本轮通过结论内。
- 13 条警告涉及既有 SWIG、FastAPI lifespan 弃用、PyG 弃用、张量构造性能和 Pandas 拼接行为提示；未作为本次修复范围处理。完整运行通过不抹去之前的启动时序波动记录，也不等于任何负载下均无竞态。
- `python -m compileall -q src scripts` 与 `git diff --check` 通过。

原浏览器结论仍为 **11 PASS / 3 BLOCKED**，这些运行时 fixture 结果不构成真实 Vina 科学验收。PR 仍保持草稿，未合并。

## 局限与实际能力判断

1. 本轮只能证明所列代表场景在当前本机配置可复现，并非所有自然语言表达都可靠。确认绑定、模糊输入和未知靶点采用保守拒绝；不能据此宣传全自动端到端药物发现。
2. 反向寻靶资产与严格加载预算不兼容：表格 107,988,940 字节超过 32 MiB 上限，指纹矩阵又超过默认 512 MiB 快照预算。需要单独的数据发布/加载方案；未通过关闭校验或扩大安全预算制造成功。
3. 对接没有完成真实 Vina 运行，未得到结合能或姿态文件。已知结构编号不会自动补齐受体准备与可信共晶口袋参数，聊天到对接的自动准备仍有能力缺口。
4. 活性模型为家族级、混合物种或混合 PDE 亚型；预测不是人源单一亚型实验事实。一致性通过也不等于模型校准、外推可靠性或实验有效性验证。
5. ADMET 原始回归有负半衰期、负分布容积等非物理输出，报告保留而不篡改。这些端点仍需独立适用域/物理合理性审查；当前风险汇总只是分类端点启发式，不构成安全结论。
6. 长原始结果仍较冗长，宽表需要横向滚动；当前候选卡片并非总能显示优化后的独立属性，权威属性以有来源的验证表为准。
7. 初次交付时的两个回归阻塞已修复，本机完整离线 Python 回归现已通过；共享调用方早期启动时序波动及 270 个跳过、1 个未选中测试仍如实保留。远端 CI、必需审查规则和维护者合并授权并未由本机通过替代；PR 按要求保持草稿，不执行合并。

修复后，基础性质/ADMET、明确靶点查询、有限约束生成、候选优化追问、活性批量预测及证据排序已能完成本次代表任务，并对未改善、缺信息和失败给出诚实边界。适合作为有人监督的研究辅助；反向寻靶与真实对接尚不能宣称闭环可用。

## 2026-10-16 科学闭环后续：分阶段进度，浏览器状态未变

维护者另行批准了完整反向来源及聊天对接接线的书面设计和当前聊天内实施。
本节只记录后续增量，**不改变 11 PASS / 3 BLOCKED**，也不把早先 `7ab815de`
的 23,526 项完整离线通过当成当前新增实现的全仓验证。

- `73b01758` / `6c1f96ae`：R1/R2 私有紧凑来源、全量科学校验和有界精确评分，
  已分别通过独立需求/质量审查。核心三模块 475 项通过；相关组合 930 项通过。
  831,120 行的初步只读校验说明可行性，不是当前应用 READY 或 S08 已通过。
- `fb5499b8`：D1 经验证的受体/口袋/配体输入生产器，100 项新测试通过，
  独立需求与质量审查通过；尚未完成 D2–D6 来源、审批、事件、产物和首页接线。
- `b23fbc71`：R4a 有界名称精确映射，125 项新测试通过；父执行者与既有回退
  测试组合 376 passed / 2 skipped。独立审查通过，但没有接入当前会话的排名
  证据，因此不能声称 S09 已完成。
- R3 预热增量：此前独立质量复审 126 passed / 1 failed，发现
  首次 RDKit 路由安装重置全局 flags。后续显式单线程初始化修复通过冷进程和
  并发回归，父执行者组合 **1139 passed / 7 warnings（243.01 秒）**。
  但新一轮独立需求审查（179 passed）又复现 Uvicorn 多 worker 子进程启动
  顺序、冷导入覆盖预设 Python 日志配置两项缺口。新增回归先 3 failed /
  3 passed，再 6 passed；修复后独立需求复审 187 passed、质量审查
  APPROVED（63 项测试及额外并发探针），父执行者相关组合 95 passed。
  编译、严格健康 23/23 及四个 Node 脚本通过。实际 Windows spawn 路径已测，
  Linux 未运行；直接 Uvicorn 多 worker 不受支持，须使用已测试的 main.py
  多 worker 引导。尚未重启完整来源应用，不能据此宣布 S08/S09 通过。

按维护者选择扩大 BuChE 真实结构检索，113 个同靶点官方条目逐一只读检查：
91 个被现有严格 PDB 子集拒绝，21 个 PDB 地址为 404，唯一通过解析的 2PM8
没有建立所需口袋配体证据。404 不代表 mmCIF 或条目不存在，解析拒绝也不等于
原始科学数据无效。没有改写坐标或用另一结构/伪造口袋强行通过 S10。

维护者已确认预处理方案 A，设计文件：
`docs/superpowers/specs/2026-10-16-receptor-conformer-projection-design.md`
（`21280ee9`）。建议显式单构象投影并保留原占有率/逐原子去向；不补缺失原子、
不忽略连接型参考配体、不改旧严格入口，不保证 1P0I 或其他条目会合格。
实施计划已提交为 `7b519db0`，P1/P2 原始解析与完整清单校验已实施并独立审查，
但显式选择、衍生物及 Web 来源/审批接线仍未完成；真实 Vina 审批仍须满足
当前输入与预览绑定的一次性审批约定，开发授权不生成运行时审批记录。
维护者另于 2026-10-16 授权完成后自行提交、推送和合并，`19399c4b` 已记录
更新；全部验收与审查门禁通过前仍不得合并。

逐步 RED/GREEN、独立审查及剩余任务详见两个 `2026-10-15` 实施计划。
本次未新增真实浏览器通过场景、结合能或姿态制品，PR #209 继续 Draft，未合并。

### 后续代码里程碑（2026-10-16，仍非浏览器通过）

- `cee60582`：R4b 当前来源、会话和持久化 checkpoint 的排名证据绑定，59 项
  聚焦测试通过，独立 SPEC/QUALITY 通过；辅助函数本身不完成 S09。
- `649fc3c6`：P1 原始 PDB 全构象解析，217 项新测试及 58 项旧严格 D1 测试
  通过；独立 SPEC/QUALITY 通过。未改写原坐标或选择真实构象。
- `05dabc60`：pytest 宿主 RDKit 隐私初始化及冷安装子进程测试，29 项通过；
  独立审查通过，生产隐私保护未放宽。
- `ff6132e2`：P2 完整源清单、独立化学定义和资源预算校验；主执行者及两位
  独立审查者均复现 465 项通过。质量审查另通过 1,421 个探针；12,000 原子
  合成输入通过，36,000 原子在序列化前触发共享逻辑预算限制。它不认证实际
  官方来源，不选择构象，也不生成可对接受体；这些仍是后续门禁。

R4c/d 聊天/provider 续接已通过 SPEC 和独立 QUALITY 复审。607 项组合回归通过后，
质量审查又复现 HTTP 清理异常可能进入聊天/日志，新增回归先一失败/一通过，
修复后 helper/provider/chat 108 项、最终组合回归 609 项通过（7 条既有警告）。
质量复审另验证九个清理场景/24 条断言；该失败未被较广通过结果掩盖。当前服务
全数据预热、真实 S08/S09 和最终全仓回归尚待完成。

### 2026-10-16 04:12–04:13 全量来源的实际 S08/S09 增量

以下替代上述“尚未重启/没有新增浏览器证据”的当时状态，不把历史验证结果
改写为新版本的全仓通过。服务基线为 `13e4658d`，只使用本任务独立的 6001
服务；用户的 6003 服务未操作。

- 全量只读预热从 03:54:12.568 到 04:11:50.451，历时 17 分 37.883 秒，
  **831,120 / 831,120** 行校验完成并进入 READY。未扩大逻辑预算或改写来源。
  预热期间抽样观察到 RSS 2,017,538,048 字节，READY 后一次采样为
  554,700,800 字节；这些不是精确峰值，512 MiB 逻辑预算不是 OS RSS 保证。
- S08 在正常首页提交原问题，实际成功保存 10 条完整证据，只展示原顺序前
  5 行。依次为 NOTUM（人）、TRPA1（大鼠）、腺苷 A1 受体（大鼠）、肌型
  糖原磷酸化酶（人）、肝型糖原磷酸化酶（大鼠）；五项 final/Morgan/MACCS
  相似度均为 1.0，系来源中的咖啡因相同结构命中，不是活性预测或实验确认。
  对应来源行索引为 145870、158110、230699、330045、330046。
- 只读核对当前持久化 checkpoint：状态 succeeded、质量验证 true，真实工具
  `reverse_target_predictor`，receipt 为 `verified_hits`；top_k=10、threshold=0.6、
  组合指纹开启。无模拟结果或 demo 回退。原始 trace 为
  `agent-366803c08ba1`，证据摘要为
  `64196f10ffdc39f0ac8056fafd0db0c2a9f8ff3124fbff4bc87cc540166da7b1`。
  来源摘要 `317538cf6e5810a5d0ebb870e4d960280ebfb6a9e1b1f4a7b5ca273ae8e809e4`，
  代际 `0bbd2e0051074feb81eb2001f8513eb8`；receipt 结果摘要
  `0fcd6a90d2fae9cec9e346ffc2e00884bb8735604dc6e86c68944fcd7721b8f2`。
- S09 在同一页面、同一连接继续原问题，真实解析首位 NOTUM / Homo sapiens
  到 UniProt Q6P988（entry version 147），映射响应摘要为
  `sha256:95a0ff16f393906e54dd9a1361d96c988c5756029c490d2f8b33a4bf94329509`。
  返回 4UYU、4UYW、4UYZ、4UZ1、4UZ5、4UZ6、4UZ7、4UZ9、4UZA、4UZL 十项
  实验 X-ray 结构，并明确显示“对接状态：未启动”和剩余输入/审批条件。
- 浏览器同时暴露两项显示问题：UniProt 链接被网址策略置为 `#`，来源标签
  显示为 `RCSB&#95;PDB`。已建立失败回归，修复正在独立审查；修复版本尚未
  重新做完整浏览器验收，因此暂不把 S09 记为最终 PASS，也不刷新总 PASS 数。
  无真实受体选择、Vina 审批、结合能或姿态制品；S10 仍 BLOCKED。

本地截图（运行产物不提交）：`s08-current-full-source-context-20261016.png`、
`s09-structures-before-label-fix-context-20261016.png`，均在任务验收输出目录。

S09 显示修复的离线证据：首轮网址回归 3 failed → 31 passed，来源标签回归
1 failed → 聊天/provider 联合 49 passed。独立 SPEC 又发现空 `?`/`#` 与
路径归一化的非规范 UniProt URL 被接受；追加四个失败用例后，改为仅匹配原始
规范条目 URL，35 项通过，SPEC 复审通过（48 项独立 URL 检查、60 项基线比较）。
独立 QUALITY APPROVED，582 个 URL 用例/1,253 条断言和 7 类来源输入/28 条
断言通过。没有通用实体解码、泛子域授权或科学数据改写。编译、所有自有 JS
语法检查、既有首页/反向分页/召回脚本通过；这仍不替代修复后的实际页面重跑。

### 2026-10-16 04:58–05:00 S08/S09 修正后实际通过

本任务 6001 服务在 04:32:20.526 开始新一轮完整来源预热，04:51:17.149
确认 **831,120 / 831,120 READY**，耗时 18 分 56.623 秒。后端启动基线为
`69e507f2`；后续 `d4e0575b` 是未接入执行入口的 D2 引用基础。本节与首页
脚本版本修复一起提交。用户 6003 服务未停止或重启，没有扩大科学预算。
一次只读进程采样 RSS 为 554,647,552 字节，不宣称这是峰值。

04:53–04:55 首次复跑暴露部署缺口：来源标签已修正，但 UniProt 链接仍为
`#`。当时实际 DOM 引用旧的 `safe_render.js?v=20261007-url-policy-v2`；
服务端同地址已经返回新规则，静态响应未指定 Cache-Control，旧资源 URL
仍可复用缓存。新增首页版本/顺序测试为 **35 passed / 1 failed**，只更新
首页脚本版本至 `20261016-uniprot-entry-v1` 后 **36 passed**；独立 SPEC 和
fresh QUALITY 各自复跑 36 项、语法与 diff 检查通过。未再次放宽 URL 策略。

新的普通首页会话实际 DOM 确认新资源版本后，依次执行原始 S08 和 S09：

- S08 trace `agent-9e84460c7f7f`，页面证据摘要
  `2d4f22e35b853357931c8db5b309a46ebe3e9f713bb0b9475b1268bb30740907`。
  真实 checkpoint 为 succeeded/validated，10 条完整记录，`verified_hits`，
  无 demo/fallback。前五名称、物种、SMILES、ChEMBL 分子 ID 经只读逐行核对，
  与原 TSV 第 145870、158110、230699、330045、330046 行完全一致；顺序及
  1.0 相似度未改写。此分数不是靶点验证、活性或结合能。
- 当前来源 generation 为 `37180e8c42a54d67bdd26868cb767e4e`，来源摘要
  `53373dba389b2b0d3da57df3eb999ac251c23d793a61f6b383ac45d3544d6ae1`，
  receipt 结果摘要仍为
  `0fcd6a90d2fae9cec9e346ffc2e00884bb8735604dc6e86c68944fcd7721b8f2`。
- S09 保留同一 trace/证据摘要；NOTUM 精确映射人源 Q6P988，UniProt entry
  version 147 和映射摘要与前次一致。十项 RCSB 实验结构及分辨率与前次一致。
  实际链接为官方 `/uniprotkb/Q6P988/entry`，来源列为 `RCSB PDB`；页面明确
  显示“对接状态：未启动”及所缺输入/审批，不把结构条目当作对接结果。

本地页面截图：`s08-final-ranked-20261016.png`、
`s08-final-ranked-context-20261016.png`、`s09-final-structures-20261016.png`、
`s09-final-structures-context-20261016.png`。截图不提交到 Git。
本次将 S08/S09 单独记为 PASS；S10 仍 BLOCKED，D2–D7/P4–P7 和最终完整
离线回归、集成审查/远端门禁仍待完成，PR 保持 Draft。

### 2026-10-16 远端启动测试兼容性修正

`3f321077` 的 GitHub Actions `quality` 运行 `38885210749` 中，root 分区
得到 **3 failed / 5576 passed / 106 skipped / 12 warnings，276.66 秒**。
三项失败均在 `tests/test_rdkit_startup.py` 导入 Uvicorn 内部 `Process` 时
发生；尚未进入真实 spawn 或应用 bootstrap。CI 按仓库固定依赖使用 0.24.0，
而先前本机验证环境是 0.43.0。

[Uvicorn 0.24.0 官方 supervisor 源码](https://github.com/encode/uvicorn/blob/0.24.0/uvicorn/supervisors/multiprocess.py)
直接使用 `get_subprocess`，没有较新版本的 `Process`/心跳线程。此次只修改
启动测试夹具，使其走安装版本的实际 supervisor spawn 路径：新版仍核对真实
心跳和多线程启动拒绝；旧版核对实际单线程子进程及应用 bootstrap。没有跳过
失败测试、放宽隐私初始化保护或修改生产源码、依赖固定版本、部署支持策略。

在临时目录下载已固定的 0.24.0 wheel，仅为测试子进程设置 `PYTHONPATH`，
没有替换本机安装。修复前本机同样复现 **3 failed / 2 passed，1.40 秒**。
修复后三模块命令：

```text
python -B -m pytest tests/test_rdkit_startup.py tests/test_rdkit_logging.py tests/test_pytest_rdkit_bootstrap.py -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short
```

初次固定 0.24.0 组合 **34 passed，16.03 秒**；本机 0.43.0 同组合 **34 passed，
14.76 秒**。SPEC 发现 legacy 路径还应在 app prefix 执行前明确断言 main 的
初始化已完成，避免 app 自身初始化掩盖 launcher 回归。补强后同组合分别为
**34 passed，16.10 秒**（0.24.0）和 **34 passed，15.48 秒**（0.43.0）。
独立 SPEC 复审通过，分别复跑启动五项为 **5 passed，4.83 秒**和 **5 passed，
4.15 秒**；无跳过，冻结哈希和 diff 检查通过。独立 QUALITY APPROVED，
复跑三模块为 **34 passed，16.62 秒**（0.24.0）和 **34 passed，15.85 秒**
（0.43.0），无跳过，测试哈希未变；没有更改生产启动或隐私行为。
这些均为 Windows 验证，修复后的 Linux CI 尚待确认。
这不是新的浏览器或科学运行证据，S10 及最终全仓/远端门禁未被关闭。

### 2026-10-16 固定 Web 依赖下的测试夹具兼容性

进一步按固定 Web 依赖验证时，三个 HTTP-envelope 鉴权测试在读取
`Middleware.kwargs` 时失败。固定 Starlette 0.27.0 使用 `options`，本机
Starlette 1.0.0 才有这些测试依赖的内部字段。修复只让共享 `app_for` 夹具把
实际传给中间件的 SQLite session store 放到 `app.state.agent_session_store`
（与生产 assembly 同名），测试从该状态访问原对象；请求路径、真实鉴权调用、
线程屏障、截止时间和后续无副作用断言均未改动。

固定包仅安装到隔离临时目录，以测试进程 `PYTHONPATH` 引入：FastAPI 0.104.1、
Starlette 0.27.0、httpx 0.25.2、anyio 3.7.1、Pydantic 2.5.0/core 2.14.1。
没有升级、降级本机环境或修改仓库依赖。RED 为 **3 failed / 4 deselected，
16.56 秒**。随后执行：

```text
python -B -m pytest tests/agent/test_web_docking_http_envelope.py tests/agent/test_web_docking_http.py -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short
```

固定 Web 组合 **17 passed / 2 warnings，89.93 秒**；本机新版 Web 组合
**17 passed，86.89 秒**。两项固定栈警告为既有 multipart 导入弃用和 Pydantic
`model_` 命名空间提示。独立 SPEC PASS，复跑三个受影响用例为 **3 passed /
4 deselected / 2 warnings，25.79 秒**，两个文件哈希未变。独立 QUALITY
APPROVED，三个受影响认证用例及两个禁用/无中间件负向用例在固定栈为
**5 passed / 12 deselected / 2 warnings，34.67 秒**，新版栈为 **5 passed /
12 deselected，34.28 秒**；哈希和 diff 检查通过。修复后的 Linux CI 尚待
完成；以上本机结果均在 Windows 上运行。

### 2026-10-16 URL-safe 会话 owner 首字符兼容性

`830825ca` 的 `quality` 运行 `38887968647` 中，root 分区为 **1 failed /
5578 passed / 106 skipped / 12 warnings，372.22 秒**。先前三个 Uvicorn
启动失败已消失；本次失败为旧报告接口的真实预约竞态测试，期待 404 却得到
500。`AgentSessionStore.issue()` 使用 `token_urlsafe(24)`，因此 owner ID
可合法地以 `-` 或 `_` 开头；聊天对接的 draft/coordinator 校验却要求首字符
为字母或数字。这不只是随机测试数据问题，也会拒绝相同形状的真实会话。

先用仅作用于离线测试进程的合成会话 ID 稳定复现 **3 failed / 3 passed，
3.50 秒**。新增真实 SessionStore、SQLite draft、coordinator/model transport
正例及 owner/path 负例，并将既有 HTTP 竞态参数化为 `A`、`-`、`_` 首字符，
得到 **10 failed / 38 passed，6.39 秒**。随后只为 owner 放行 URL-safe 首字符；
会话生成器、现有会话库、身份精确比较、nonce/审批和密钥过滤均未改变，
其他 task/root/preparation/origin/generation 标签和路径限制不放宽。

首次组合回归发现旧 coordinator 负例将合法 `-owner` 当作坏输入：**1 failed /
418 passed / 1 skipped，60.19 秒**。该负例改为真正含路径分隔符的 `-owner/`；
合法首字符由新正例覆盖，没有跳过失败或放宽断言。最终命令：

```text
python -B -m pytest tests/agent/test_docking_session_identifiers.py tests/test_docking_consent_legacy_routes.py tests/task_runtime/test_docking_consent_draft.py tests/agent/test_web_docking_coordinator.py -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short
```

固定 Web 依赖组合 **419 passed / 1 skipped / 2 warnings，58.58 秒**；跳过为
Windows 目录 symlink 权限不可用，两项警告仍为既有 Pydantic/multipart 提示。
本机新版 Web 栈聚焦新 owner、HTTP 竞态和无 I/O 拒绝负例 **64 passed /
268 deselected，7.05 秒**。五个源码/测试文件编译和 diff 检查通过。
独立 SPEC 初次验证 **107 passed，13.97 秒**，另以真实关闭探针发现
`close_chat_docking_draft` 仍使用旧首字符规则：**2 failed / 1 passed**。
父任务在正式回归补上持久化 `REVOKED` 和无 `cleanup_pending` 的断言，
再次确认 **2 failed / 1 passed，2.59 秒**，再将关闭调用点改为 owner-only
校验；关闭 SQL、版本 CAS、身份精确匹配和状态条件均未变化。

最终在上述命令追加 `tests/task_runtime/test_docking_draft_close.py`，固定
Web 栈为 **542 passed / 1 skipped / 2 warnings，58.66 秒**；六个源码/测试
文件编译及 diff 检查通过。SPEC 复审 PASS：新模块、全部 draft-close 测试及
独立三个前缀关闭探针 **155 passed / 1 warning，14.68 秒**，冻结哈希一致。
独立 QUALITY APPROVED：本机聚焦 **62 passed**，独立探针 **22 passed**；
最终固定栈回归及探针 **297 passed / 2 warnings**。六文件编译通过，冻结
哈希未变；确认旧会话兼容、跨 owner 隔离、非 owner/凭据边界和关闭持久化。
未以本机结果宣称远端 CI 或 D4–D7 完成。

### 2026-10-16 Agent CI 对接分组

同一远端运行 `38887968647` 的 Agent 分组收集了 14,630 项，日志持续推进至
97%，最终在 1,200 秒命令截止点以 124 退出。最后一项刚开始约两秒，不能把
它诊断为挂起或删除它绕过门禁。此前完成的 `web_docking` 等对接用例已占用
至少约 247 秒，剩余对接集成及新增候选模块还需时间。

保持既有 Agent 1,200 秒、Web 生命周期 600 秒、job 30 分钟及离线 collection
总 180 秒/单子进程 60 秒限制。新增 `agent-docking` 分组（1,200 秒），选择
顶层 `tests/agent/test_*docking*.py`；核心分组只排除同一 pattern 实际展开的
文件，不使用会误伤嵌套目录的 broad ignore-glob。最终 `offline-quality`
仍要求全部 Python 分组与静态检查成功，不降低其他测试门禁。

实际 inline 收集校验扩为原全集与三个互斥分组；新合成收集覆盖普通未来文件、
顶层 docking 文件、嵌套 docking 名称/目录，以及重叠、遗漏、空匹配、重复、
错误、超时和最后清理超过原截止点。初次 RED **9 failed / 6 passed**，其中
两项测试准备阶段因新行缺失触发 KeyError；改为明确缺失断言后 **11 failed，
0.61 秒**。实现后两模块为 **38 passed / 2 skipped / 7 warnings，22.59 秒**；
跳过为 Windows 不支持 Linux GNU timeout watchdog 与 native sendmsg。

```text
python -B -m pytest tests/test_quality_workflow_contract.py tests/test_deployment_assets.py -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short
```

在本地当前完整仓库执行 CI 文件里的原始 inline verifier，实际隔离收集得到：
`AGENT_COLLECTION_OK baseline=14973 agent=13299 web=225 docking=1449`。
这是用例集合证明，不是 14,973 项执行通过。测试文件编译和 diff 检查通过。
独立 SPEC PASS：聚焦契约和部署门禁 **31 passed / 2 skipped，19.46 秒**；
独立 Bash 实际参数展开探针得到核心 12、Web 6、对接 4，保留嵌套 docking
文件，并确认缺失分组会失败关闭。原有 jobs、网络隔离和总截止时间未变；
三个冻结文件哈希、编译和 diff 检查通过。独立 QUALITY APPROVED：同一聚焦
选择 **31 passed / 2 skipped，18.28 秒**；独立 Bash 展开当前仓库的 23 个
顶层对接文件，核心分组恰好排除同一 23 个，与 Python collector 一致，Web
三个文件不变。Bash 语法、内存编译和 scoped diff 检查通过，哈希一致。
未重复完整 collection/suite，拆分后的远端执行时长仍待实测。

严格健康检查默认独立工作树得到 **21/23**：未携带反向数据目录和 RAG 索引。
只给本次检查进程的 `REVERSE_TARGET_DATA_DIR`、`RAG_INDEX_PATH` 指定主工作区
中已存在的对应资产后，**23/23**；均使用不存在的 `--env-file` 路径。
没有复制数据、修改配置或启动真实 Vina，这也不证明新科学入口已经可用。

### 2026-10-16 D4 私有结果候选验证

新增 `src/web/docking_publication.py`，仅从原始 live coordinator/input context/
C2 execution 捕获不可变私有结果；`docking_input_context.py` 留存原始身份。
校验成功清理回执、当前源证据、完整 consent/token/binding、实际 staged 输入、
completion 摘要和实际 pose 字节/能量/数量。TaskResult 分数不作为科学依据。
使用原始整个操作截止时间，不续期，也不误用 raw 或未消费 consent 的截止点。
该函数没有发布、下载 grant、HTTP 接入或真实科学执行权限。

初始缺失 callable RED 和后续边界 RED 后，候选模块 **84 passed**。独立 SPEC
发现浅拷贝 root/context 后重绑当前双向指针仍被接受，三个独立探针失败。
补充构造时原始 root/context 身份校验，以及合法 URL-safe owner 首字符的
两个查找边界测试：**5 failed / 84 deselected，17.17 秒**，修复后聚焦
**14 passed，21.34 秒**。完整命令：

```text
python -B -m pytest tests/agent/test_docking_publication_candidate.py tests/task_runtime/test_chat_docking_input_context.py tests/task_runtime/test_chat_docking_execution_context.py -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short
```

最终 **152 passed，634.18 秒**。父侧 context、completion readback 和 private
output 回归 **149 passed / 4 skipped，152.58 秒**；四项跳过为 Windows
symlink 权限不可用，不计为通过。三个修改文件 compileall 通过。
独立 SPEC 复审 PASS：原三个探针 **3 passed，23.60 秒**，新增边界与正例
**6 passed，22.69 秒**。独立 QUALITY APPROVED：聚焦 **8 passed，40.19 秒**，
另两个独立探针 **2 passed，10.13 秒**，确认匹配 hash 仍不能掩盖非有限 pose，
read 中 completion 变化被拒绝，连续四次取消仍保留实际 reader/gate。
三文件冻结哈希未变；独立审查未重复全部 152 项。

所有分数和 pose 均为明确的合成测试夹具，不是 S10 或真实 Vina 验收。
持久化 publication CAS、live terminal latch、grants、retention、UI 和浏览器
整链仍未完成；当前切片通过不能替代这些门禁。

### 2026-10-16 当前已推送版本远端 CI（7a8428ab）

GitHub Actions `quality` **38892610439 / #617** 已完成且结论为 success；
八个 Python 分组、static-quality 和最终 offline-quality 全部 success。
日志中的实际结果如下，不包含其后未提交的 publication receipt 实现：

| 分组 | passed | skipped | warnings | pytest 用时 |
|---|---:|---:|---:|---:|
| root | 5594 | 106 | 12 | 384.68 s |
| root-activity | 1959 | 6 | 6076 | 655.61 s |
| task-runtime | 2314 | 12 | 1 | 399.19 s |
| sandbox-core | 1977 | 4 | 1 | 50.27 s |
| sandbox-api | 153 | 0 | 0 | 2.67 s |
| agent | 13297 | 2 | 26836 | 1017.66 s |
| agent-web-lifecycle | 225 | 0 | 7311 | 222.74 s |
| agent-docking | 1449 | 0 | 9 | 634.97 s |

合计 **26968 passed / 130 skipped / 40246 warnings**，跳过和警告按原日志
保留，不称为零警告或全部用例执行。远端 collector 实际输出与本地一致：
`AGENT_COLLECTION_OK baseline=14973 agent=13299 web=225 docking=1449`。
核心与对接分组分别在原 1200 秒限制内完成，没有放宽截止时间。
static-quality 的 Python 编译、Node 契约和 tracked non-document 凭据扫描通过。
此前 `3e1923cb` 运行因后续推送而被工作流取消，不能记作完整通过。
此时 PR 仍为 Draft、未合并、GitHub reviews 为空；独立子代理审查不等于
GitHub 必需审批。真实 S10、最终集成版本和后续改动仍须另行验收。
同日只读查询 `main` 返回 `protected=false`、status-check enforcement 为 off，
仓库 rulesets 列表为空；详细 branch-protection 端点因 integration 权限返回
403，未尝试扩大权限。以上只记录可见配置，不豁免本项目约定的科学、审查和
CI 门禁，也未修改任何保护规则。

### 2026-10-16 D4 私有 publication receipt/latch

修改 `database.py`、`store.py`、`docking_coordinator.py`、
`docking_publication.py`，新增 `test_docking_publication_receipt.py`。
沿用现有 TaskStore 增量创建候选/抑制回执表，完整 consent 快照 CAS 不改变
科学 completion 或执行终态。确认写入回执并重新核验实际来源、文件、身份和
原始截止点后，live coordinator 才能确认一次内部终态。迟到提交、提交后抛错、
取消和截止点竞态不能靠数据库回读晋升为成功；未增加公开下载或 UI 接线。

初始缺失 callable RED **30 failed，3.52 秒**。父级集成检查发现提前尝试发布
会安装 latch，可能令未结束 C2 绕过正常关闭路径；实际 READY、阻塞 raw 和
阻塞 claim reader 三例先 **3 failed / 55 deselected，15.07 秒**，然后将
实际物理收尾验证移到状态安装前。三新例加两控制为 **5 passed / 53 deselected，
25.58 秒**；未单独测量只有三例的 GREEN 用时。

```text
python -B -m pytest tests/agent/test_docking_publication_receipt.py -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short
```

最终 **58 passed，285.98 秒**。候选、C2 context、coordinator、execution、
整个 close 模块及两项迁移测试定向组合 **30 passed，94.83 秒**。固定 Web
依赖组合的整个 `test_web_docking_control_http.py` 与
`test_whole_http_approval_loss_does_not_cancel_accepted_c2` 为 **4 passed /
2 warnings，28.15 秒**；警告为已有 multipart 弃用和 Pydantic 命名空间提示。
父级另跑整个 `test_task_store.py`、`test_docking_consent_draft.py` 和
`test_docking_draft_close.py`：**516 passed / 1 skipped，17.27 秒**，跳过为
Windows symlink 权限不可用。五文件编译/diff 和冻结哈希检查通过。
独立 SPEC PASS：**45 项通过**（31 receipt/race、10 生命周期测试和 4 个独立
探针），确认真实提交后不精确 ACK 仍为 UNKNOWN、迟到重复调用不续期、阻塞
suppression SQL 在反复取消/close 后仍由原 owner 保留。原始 420 秒截止点和
提前 publication 的正常 C2 清理均通过；五文件哈希、编译和 diff 未变。
fresh QUALITY 尚待完成；上述夹具全部是合成执行，不是 S10。
随后 QUALITY 独立复现 P2：发布任务登记后、首次执行前同轮 close 会取消任务，
导致其 finally 未执行，实际无 I/O 残留却永久显示 cleanup_pending。该探针
连续两次失败；caller-cancel 控制和另 13 项定向测试通过。正在补正式回归和
仅针对确实从未启动任务的清理确认；当前未批准、未提交此切片。
正式回归 **1 failed / 1 passed / 58 deselected，11.43 秒**。最小修复在首次
await 前登记启动事实，完成回调只对原始、从未启动、已取消且无 I/O 的任务
重新核实 C1/C2 收尾后确认 cleanup；已经启动的工作继续由原 finalizer 负责。
聚焦 **2 passed，11.78 秒**；原独立 QUALITY 探针 **2 passed，19.04 秒**；
迟到提交、反复取消、suppression close 与已确认成功控制 **16 passed /
44 deselected，80.01 秒**。只改 publication 源码和测试，编译通过；完整
58 项没有再次全跑。独立复审尚待完成。
原 SPEC 定向复审 PASS：两个从未启动案例与一个实际 started-reader 控制
**3 passed，15.81 秒**。原 QUALITY 复审 APPROVED：原始独立探针
**2 passed，18.23 秒**，七项已启动 reader/commit/suppression/成功状态控制
**7 passed，36.32 秒**。五文件修复后冻结哈希一致，编译和 diff 检查通过。
P2 已关闭，本切片审查通过；没有以定向复审冒称新全仓或真实科学验收。

### 2026-10-16 receipt/latch 已推送版本远端 CI（b5aca248）

GitHub Actions `quality` **38900024699 / #618** 的八个 Python 分组、
static-quality 与最终 offline-quality 全部 success。实际 job 日志结果：

| 分组 | passed | skipped | warnings | pytest 用时 |
|---|---:|---:|---:|---:|
| root | 5594 | 106 | 12 | 382.81 s |
| root-activity | 1959 | 6 | 6076 | 656.94 s |
| task-runtime | 2314 | 12 | 1 | 497.92 s |
| sandbox-core | 1977 | 4 | 1 | 37.63 s |
| sandbox-api | 153 | 0 | 0 | 3.80 s |
| agent | 13297 | 2 | 26836 | 995.31 s |
| agent-web-lifecycle | 225 | 0 | 7311 | 146.32 s |
| agent-docking | 1509 | 0 | 9 | 932.65 s |

合计 **27028 passed / 130 skipped / 40246 warnings**，包含最终 receipt
模块新增的 60 项；全部保留原截止点。跳过和警告不是通过用例，也没有称为
零警告。静态 Python 编译、Node 契约和 tracked non-document 凭据扫描通过。
这是 b5aca248 的远端离线证据，不覆盖随后进行中的 grant/read 代码，不代表
S10、真实 Vina、公开成功卡片或完整 D1–D7 已通过。PR 仍保持 Draft，未合并。

### 2026-10-16 D4 grant/read 冻结候选（独立审查中）

四文件：`src/web/docking_publication.py`、新 `src/web/docking_artifacts.py`、
`src/web/routes/docking_consent_routes.py`、新
`tests/agent/test_docking_publication_artifacts.py`。只在原 live publication
成功 latch 捕获固定双时钟，创建同 generation、最长 24 小时的私有 pose grant；
初次 publication 仍受原始 420 秒限制。GET 不创建/续期 grant，不恢复数据库
成功；实际来源、manifest、completion 与返回 bytes 每次核验。读取租约保留至
实际 ASGI 发送和释放完成；慢发送不持有模型 reader，释放异常保留 unresolved。

工作者 TDD 首轮 **3 failed**，另有 **14 个边界失败**先复现再修复，涉及克隆
身份、原始截止点、取消/排队、租约竞争、畸形 query 与失效观察。最终结果：

| 测试 | passed | skipped | 用时 |
|---|---:|---:|---:|
| `test_docking_publication_artifacts.py` | 63 | 1 | 361.70 s |
| `test_docking_publication_receipt.py` | 60 | 0 | 363.15 s |
| `test_docking_publication_candidate.py` | 89 | 0 | 441.80 s |
| 既有 HTTP 与 readback 六文件 | 181 | 12 | 209.26 s |

六文件为 `test_web_docking_http.py`、`test_web_docking_approval_http.py`、
`test_web_docking_control_http.py`、`test_web_docking_http_envelope.py`、
`test_completion_artifact_readback.py` 与 `test_staging_readback.py`。
合计 **393 passed / 13 skipped**；跳过是 Windows symlink/reparse 权限限制，
hardlink 负例通过。既有 Pydantic/multipart 警告保留。全部使用统一
`python -B -m pytest <上述文件> -q -p no:cacheprovider --basetemp <独立临时目录> --tb=short`。
四文件 compileall、diff/空白和冻结 hash 检查通过。

父级另外运行既有 `tests/task_runtime/test_docking_consent_generic_routes.py`、
`tests/test_docking_consent_legacy_routes.py`、`tests/test_task_retention.py`：
**285 passed / 1 skipped / 2 warnings，62.19 秒**。这证明现有隔离回归未坏，
不证明尚未补测试的通用 retention 删除 consent 缺口已解决。所有本段科学结果
均为合成 C2 夹具；未运行真实 Vina、浏览器、服务激活或自动清理。独立 SPEC
与 QUALITY 尚待完成，冻结候选尚未提交。
独立 SPEC 随后 PASS：19 项聚焦通过、1 项 symlink 跳过，加 4 个实际
C2/SQLite/ASGI 独立案例。一个外部测试最初误期望 401，按既有中间件的新
session/404 行为修正后单例通过；生产代码未因此修改。
fresh QUALITY 的 12 项聚焦和发送失败控制通过，但连续两次复现 P2：真实
SQLite 写事务未改任何数据，仅锁竞争超时，就被 blanket exception handler
误记为 sticky evidence invalidation；回滚后原有效 GET 永久 404。两次 HTTP
反馈约 5.016/5.000 秒，实际 lease/model reader 已释放，故并非所有权泄漏。
正在补真实数据库锁回归与最小异常分类修复，当前尚未批准或提交。
正式 RED：真实 Task/Agent DB × GET/重复观察 **4 failed / 4 passed，
67.67 秒**；错误分类及即时锁异常 **18 failed，24.23 秒**。最小修复仅将
SQLite BUSY/LOCKED 视为本次请求不可用（Python 3.10 使用精确标准错误文本，
有原生错误码时校验 primary code）；其他数据库/证据异常继续 sticky。没有
新增重试、预算或池，没有改变首次 publication 的 UNKNOWN/取消规则。
GREEN **26 passed，92.07 秒**，已有有界控制 **31 passed，178.40 秒**，原
外部 QUALITY 两个探针 **2 passed，29.55 秒**。编译/diff 检查通过，没有
重跑全部 393 项。原 SPEC 定向复审 **23 passed，80.23 秒**，其中独立探针
确认首次 publication 提交后 BUSY 仍 UNKNOWN、无 grant/重试/恢复。四文件
修复后 hash 未变；原 QUALITY 复审待完成。
原 QUALITY 复审 APPROVED：未修改的两个独立探针 **2 passed，27.44 秒**，
实际锁超时 **5.000 秒**后 `access_disabled=False`，回滚后 GET 200；另有
**20 passed，34.27 秒**覆盖分类、非 busy 负例、首次 publication 截止点和
release failure。四文件编译、修复后冻结 hash 均通过，P2 已关闭。此批准仅
覆盖 grant/read，不代表 D5 清理隔离、公开科学结果 UI 或 S10 已完成。

上述 grant/read 已提交为 `5087ce97`（尚未推送）；父级完整 artifact 模块
复验 **89 passed / 1 skipped / 2 warnings，464.99 秒**。跳过仍为 Windows
symlink 权限限制。该次完整结果早于下述浏览器 GET 修复，不混作修复后证据。

### 2026-10-16 浏览器 GET 兼容性与 CI 分区维护（进行中）

实际隔离浏览器按钮触发的同源 GET 显示 `Origin` 缺失、
`Sec-Fetch-Site: same-origin`、`Sec-Fetch-Mode: cors`；与
[Fetch 的 Origin 规则](https://fetch.spec.whatwg.org/#origin-header)一致。
探针不读取 Cookie、不启用 MedChat、不调用科学工具。用该请求头形态进入
真实 session/C2/ASGI 夹具，原路由错误返回 403，外部回归 **1 failed**。
正式 `test_docking_publication_browser_get.py` RED **5 failed / 1 passed /
2 warnings，36.28 秒**，另发现有效 Origin 与跨站 Fetch Metadata 冲突时仍
返回 200。最小修复仅限 artifact GET：缺 Origin 时要求唯一精确的
`same-origin` Fetch Metadata；有 Origin 时沿用既有严格检查；任何冲突或重复
Metadata 均拒绝。所有 mutation 仍必须带唯一有效 Origin，session/grant 和
物理读取租约不变。GREEN 与独立 SPEC/QUALITY 尚待完成。

CI 将顶层 `test_docking_publication*.py` 从 docking 分至独立分区，保留
1200 秒执行预算和 180/60 秒收集预算。显式文件参数会绕过 pytest `--ignore`，
因此同批精确路径同时传 `--deselect`，不是放宽集合检查。工作者 RED
**11 failed / 25 passed / 2 skipped，22.08 秒**，补充 RED **2 failed /
36 deselected，3.51 秒**；GREEN **36 passed / 2 skipped，40.28 秒**。
实际全仓收集验证 **57.80 秒通过**：baseline 15123，core 13299，web 225，
docking 1360，publication 239，四者不交且并集等于 baseline；这些是收集 ID，
不是运行通过数量，也早于新 browser GET 用例。此前一次收集触及 60 秒上限，
未提高预算。独立 SPEC **36 passed / 2 skipped，40.76 秒**，确认分区符合
要求；跳过 Linux watchdog 和 Windows 不可用 `sendmsg`。QUALITY 及远端
新分区结果仍待确认，PR 保持 Draft。

浏览器修复 GREEN：新 6 项与三个既有 HTTP 模块合计 **31 passed /
2 warnings，159.34 秒**；artifact 正常/foreign/no-grant 控制 **6 passed /
84 deselected，38.57 秒**；既有错误 HTTP shape 控制 **6 passed /
84 deselected，38.22 秒**。两文件编译、diff 与不输出内容的凭据扫描通过。
独立 SPEC **6 passed，38.67 秒**，另有 182 个内存请求头组合通过，未改代码；
fresh QUALITY 仍在进行。上述均是合成科学结果夹具，不是 S10 真实通过。
CI fresh QUALITY APPROVED：独立合成 collector 与实际 Bash 分区 **5.48 秒
通过**，另有 pytest 前缀匹配探针通过；两文件 hash 不变。`--deselect` 分离
执行，不阻止被取消选择模块的 import，未发现新有害副作用；远端 CI 尚未运行。
浏览器 GET fresh QUALITY 也已 APPROVED：独立 **6 passed，36.18 秒**及
12 个补充请求头探针通过；AST 比对确认 mutation 与其余授权/读取处理未改。
两文件编译/diff 与冻结 hash 一致。两项维护补丁审查已完成，待提交推送；
不包含仍在实施的 retention，也不意味着 D6/D7 已完成。

### 2026-10-16 当前远端分区结果及部署契约遗漏修正

`5087ce97` 与 `a9ef7b34` 已推送到原分支。quality **38910961874 / #619**
的 publication **245 passed，622.63 秒**，原 docking **1360 passed /
245 deselected，553.56 秒**，集合验证步骤通过；不再将这两个分组合并执行。
root 分组实际 **1 failed / 5599 passed / 106 skipped / 12 warnings，
404.72 秒**：`test_deployment_assets.py` 的旧断言仍要求 8 个
`command_timeout`，新增分区后实际为 9。这是遗漏同步的第二份契约，不是
超时或科学结果失败。其余已完成分组通过，Agent core/最终 gate 当时尚未完成；
因此不能声称本次 CI 通过。

本机单例复现 **1 failed / 7 deselected，14.27 秒**。仅在该测试补上
publication target/exclude 必须存在的断言，并将精确分组数改为 9；没有
修改 CI 工作流、预算、测试选择或删除原有断言。两个契约模块联合 GREEN
**44 passed / 2 platform skips / 10 existing warnings，43.31 秒**。
SPEC 与 QUALITY 定向复审均通过，编译、diff 和凭据扫描通过。
此修正尚待新提交的远端运行；不把较早的 failure 改写为 success。
