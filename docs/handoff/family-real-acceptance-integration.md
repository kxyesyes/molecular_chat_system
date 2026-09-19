# 家族真实权重隔离验收集成交接

## 范围和当前状态（2026-09-19）

用户已选择子代理逐任务实施，采用TDD及独立SPEC→QUALITY审查。
分支：`codex/family-real-acceptance-integration`；实施起点`415b24d`，main基线`57680aa`。
依据[已批准设计](../superpowers/specs/2026-09-15-family-real-acceptance-design.md)及
[七任务实施计划](../superpowers/plans/2026-09-15-family-real-acceptance-integration.md)。

本批只做代码与合成资产工程验收。不读取真实模型目录/权重、原始数据集、.env或API凭据，不调用外部模型，不启用生产模型。
真实权重执行、生产接管、历史报告展示残差和部署验证仍是后续事项。
原始混杂工作树的13项已修改/未跟踪路径与任务开始前一致，没有覆盖或暂存。

## 实施与审查进度

| 任务 | 当前状态 |
|---|---|
| 1. 配置与合成前向fixture | 完成，SPEC/QUALITY均通过，最终135 passed |
| 2. 精确bundle快照与摘要 | 完成，SPEC/QUALITY均通过，306 passed、3 skipped |
| 3. 隔离进程与资源回收 | 完成，SPEC/QUALITY均通过，50 passed、1 skipped；Linux待CI |
| 4. DOM fixture与生产渲染断言 | 完成，SPEC/QUALITY通过，旧18/18、新15/15 |
| 5. 预测/API/逐轮决策/ASGI链路 | 完成，SPEC/QUALITY复审通过，六组255 passed |
| 6. opt-in入口与报告 | 完成，SPEC/QUALITY均通过；父级11组680 passed/5 skipped |
| 7. 全回归、双审、PR | 代码与本地/独立审查完成，draft PR #29；e409125首轮Linux CI全7项成功，后续文档head以PR最新检查为准 |

## 已取得的验证证据

- 实施前现有10个根目录Node测试脚本全部通过，其中家族DOM18项、隔离lab30项；不是浏览器或真实模型测试。
- 实施前Agent/model/fallback联合基线：3288 passed、2 skipped、7 warnings，123.08秒；没有把两个skip计为科研通过。运行命令为计划Task7 Step2的Python联合回归命令，使用指定MedChat解释器并关闭真实验收开关。
- CI实际配置是`.github/workflows/quality.yml`；root pytest组自动收集根目录新增Python测试，static组只运行`*_test.js`。新的DOM验收驱动需要由其Python链路/Node既有测试显式覆盖，不能只创建文件就声称CI覆盖。
- Python科学环境固定为MedChat Conda解释器；不使用缺RDKit/LangGraph的默认环境。
- Task3实施期间父级补跑既有家族API、Agent家族工具、decision_chat及transport四组：196 passed，5.98秒；使用MedChat解释器、`-B -m pytest ... -q -p no:cacheprovider --tb=short`及真实开关0，未运行未来Task5链路。

本记录会随每项真实执行与审查结果更新，未标完成的项不可解释为通过。

## Task1证据

- `8835cee`：默认关闭的纯配置解析、精确bundle配置、合成真实RGNN前向fixture；旧推理断言保留。实施者RED61 failed（待建接口不存在），GREEN130 passed/2 warnings。首次确定性测试对torch.save归档字节做不当相等断言，已改为比较真实张量，不把文件名导致的归档差异误称模型差异。
- 独立SPEC运行130 passed/2 warnings，发现CPU fixture使用torch.manual_seed会同时设置加速器种子，与仅恢复CPU RNG的边界不符。父级核对本机PyTorch该函数源码，确认反馈成立。
- `0fd493b`：改用CPU default_generator.manual_seed(71)。实施者记录RED2 failed/1 passed（成功/异常均捕获4次加速器播种），聚焦GREEN3 passed、Task1回归133 passed/2 warnings；种子对应的真实权重张量和前向值与独立CPU参考完全相同。等待独立复审，不能提前称双审完成。
- 父级contract命令通过；RDKit对固定无效SMILES的解析错误是预期拒绝日志，不是科研计算成功。
- 独立SPEC复审通过（6 passed/58 deselected）；QUALITY独立全聚焦133 passed仍发现默认设备问题：后置`.to(cpu)`不能约束此前的特征/网络构造。其无GPU的meta-device探针复现失败、局部CPU context对照成功。已要求补RED/GREEN，修复前不进入下一任务。
- `e4a8f24`：局部CPU context覆盖完整构造；实施者RED2 failed→GREEN5 passed→全聚焦135 passed/2 warnings。独立SPEC增量复审meta2项+seed71一项通过；独立QUALITY重新运行完整三个文件135 passed/2 warnings并批准，无剩余审查项。本批使用子代理TDD和双审发现并关闭了上述两处隔离缺陷，未改生产代码。

## Task2证据

- `321c858`：精确bundle只读快照、所选四资产/注册表大小与哈希检查、封存证据在副本重新校验、源前后摘要复核、路径/重叠保护。仅修改两份验收support/单测文件。
- 实施者记录累计83项预期RED；四组最终301 passed/3 skipped/2 warnings，126秒。Windows的3项symlink创建权限不足已单列，reparse属性负例通过；Linux真实链接检查尚待CI。独立审查未完成，不将实现者自测当作双审。
- 父级另验证现有命令取消/后代/late-spawn原语7 passed，为Task3复用提供本机基线；未启动docking或模型服务。
- 首次SPEC发现P1：verify_source重新跟随修改后的源registry，在报source_changed之前读取了另一组资产。`35d9d09`改为先核对registry摘要、只解析该次固定字节、匹配快照身份后读原资产、结束再查registry。实施者RED3项→聚焦14 passed→全304 passed/3 skipped。
- 独立SPEC复审7个合成重定向探针均通过（新资产打开次数0），完整304 passed/3 skipped/2 warnings，85.52秒；批准。QUALITY审查尚在进行。
- QUALITY发现复核期间registry可能在四资产读取时改变；`6022c6e`将共享复核统一为registry→原四资产→registry，不声称跨文件原子性。实施者RED2项→聚焦18 passed→全306 passed/3 skipped/2 warnings。独立SPEC增量18 passed；独立QUALITY重跑原复现及四组回归306 passed/3 skipped/2 warnings（89.88秒），批准，无剩余审查项。

## Task3证据

- `1dcc610`：只修改tests内进程support及测试，复用已有CommandAdapter原语，环境白名单、限时及后代进程回收。实施者记录RED28项→最终50 passed/1 skipped（三次连续通过）；Linux实际执行尚待CI，不冒充完成。
- 父级既有`tests/test_docking_command_cancellation.py`完整回归48 passed/1 skipped，5.75秒。未启动对接工具或科学服务。
- Task3双审后父级合并运行support/process/inference/models/predictor五组：356 passed、4 skipped、2 warnings，89.70秒；未读取真实资产。
- 独立SPEC运行50 passed/1 skipped并批准；额外探针确认成功传输信封内的科学failed/partial报告及source_check能够完整保留。Task6必须按内层科学状态汇总，不能仅因ChildResult.status=passed就声称科研通过；顶层failed仅用于传输错误。QUALITY审查中，跨平台实际执行仍待CI。
- 独立QUALITY运行50 passed/1 skipped，另8个有界探针通过，批准；明确只验证Windows，Linux留给CI。没有剩余审查项，未修改生产适配器。

## Task4证据

- `fee957b`：显式导出现有DOM fixture与main guard，新增有界JSON驱动。实施者记录缺导出RED、驱动2/13及13/15后最终15/15；旧18项完整通过。只修改两个测试JS文件。
- 独立SPEC复核旧18/18、新15/15、两个node --check及静默导入/非法参数探针通过；父级另9个现有Node脚本均通过。QUALITY审查中；这些只是生产renderer的离线DOM契约，不等于真实浏览器或科学模型验收。
- 独立QUALITY批准：旧18项、28项内存断言及输入边界探针通过；澄清“禁止spawn”指子代理而非测试进程后，另独立重跑新15/15通过。父级通过Task3受监督进程执行完整Node驱动15/15，exit0、ownership_released/cleanup_complete均true，之后清理自有临时目录。

## Task5证据

- `74d84f0`实现者自测Task5 32 passed，六组联合231 passed/2 warnings（56.74秒）；科学结果来自合成RGNN真实前向，不是外部模型。
- 独立SPEC复跑231 passed/2 warnings（56.40秒）仍发现3项P2验收漏检：复用链路只含正向输入；WebSocket首个complete后停止读取遗漏后续重复终结/分子帧；无效输入测试没有核对公开失败内容。独立探针分别使额外终结/分子帧、伪completed+pIC50混入而仍通过。父级核对代码后要求补RED并修复；尚未批准Task5，不将这些探针等同已确认生产缺陷。
- `82f2efe`补可复用拒绝用例与真实混合批次/DOM、读取到正常WebSocket关闭并核对完整帧、对比公开拒绝结果。实施者记录12项问题RED及额外边界RED，六组GREEN249 passed/2 warnings（87.33秒）；SPEC复审中。
- 独立SPEC复审249 passed/2 warnings（87.27秒），原额外尾帧和伪completed+pIC50探针现在均正确失败，三项发现关闭并批准。QUALITY审查中；Task6逐入口计时与公开报告仍待实施。
- QUALITY独立六组249 passed/2 warnings（87.29秒）后，用实际ASGI探针复现事件trace改写、task_completed重复、拒绝事件全部删除仍passed（3个预期拒绝断言失败）。已核对现有检查只覆盖部分事件名/顺序，要求共享事件一致性断言及RED/GREEN修复；该发现是验收漏检，不声明生产已发生串线。
- `a5409df`新增共享内部/公开事件一致性检查：实施者原3个ASGI探针RED，Task5 GREEN56 passed，六组255 passed/2 warnings；SPEC增量与QUALITY复审待完成。
- 独立SPEC增量批准：聚焦事件7 passed、原探针3 passed、Task5全56 passed、六组255 passed/2 warnings（114.24秒）。独立QUALITY原探针3 passed（15.87秒）、六组255 passed/2 warnings（100.86秒），批准，无剩余审查项。只验证合成工程链路，不代表真实训练权重或外部模型验收。

## Task6实施期间的既有模块回归

- `cae31875`实现唯一opt-in入口、逐家族受监督worker和脱敏报告；实施者11组656 passed、5 skipped、2 warnings。独立SPEC聚焦323 passed、5 skipped，仍用仓库外合成探针复现3项缺口：必需科学字段一致损坏仍passed、classification成功/regression失败被投影为无可用阶段、快照阶段source_changed被覆写为not_completed。已要求先补RED再修复，不能以既有绿灯代替这些失败证据；默认真实入口单独1 skipped，不代表真实验收通过。
- `37efdbaa`修复上述三项：原SPEC探针4 failed/2 passed→6 passed，本地25项RED→11组680 passed、5 skipped、2 warnings；独立复审中，尚不宣称Task6双审完成。未修改生产代码或独立探针。
- 独立SPEC复审批准：原探针6 passed、Task6四组347 passed/5 skipped/2 warnings、默认真实入口1 skipped、diff检查通过；三个finding全部关闭。QUALITY由新的独立审查者进行，未提前进入下一Task。
- 父级独立重跑11组（support/process/chain/real/inference/API/models/predictor、Agent family tool/decision chat/transport）：680 passed、5 skipped、2 warnings，270.29秒；真实开关0。该数量包含离线真实RGNN前向，不包含真实训练权重测试通过。
- 独立QUALITY批准Task6；另发现Task5继承断言扫描整个JSON中的`9.9`会误中事件时间戳，四组346 passed/1 failed/5 skipped（203.53秒），不能抹去这次失败。原SPEC探针6 passed，新增实际双worker探针3 passed，定点时间戳复现2 passed。双worker探针覆盖分别破坏第一/第二家族，验证另一家族实际执行、partial、源未变和自有目录清理。Task7将按TDD修正误报并保留伪造科学值拒绝断言。

## Task7执行边界

- 五个本地skip分别是默认关闭真实入口1项、Windows symlink权限3项、POSIX专属1项；Linux真实执行留待本批CI，不把skip统计为科研成功。
- CI静态发现`*_test.js`不会自动执行新DOM驱动的15个默认自测；本批将补显式命令及契约测试，并为Python root组明确Node依赖和关闭真实验收开关。保留原五组Python、旧10个Node脚本和最终质量门。
- 全批SPEC/QUALITY、draft PR和最新Linux CI尚未完成；没有合并授权。历史报告展示残差、真实训练权重、外部主模型、真实浏览器和部署仍未执行。
- `44e8f4ba`最小修正三文件：确定性时间戳RED与科学字段断言；CI显式DOM自测、root Node20、全局真实开关0。RED5 failed/4 passed→聚焦18 passed/1 skipped；CI契约7 passed、默认真实入口1 skipped；11组680 passed/5 skipped/2 warnings（262.72秒）。旧10个Node、新DOM15项、两份语法及diff检查通过。实施者compile+清理组合再次被策略整体拒绝、没有执行；父级此前短缓存compile已成功，生产源码未变。全批独立审查以44e8f4ba为代码head。
- 父级在Task7提交后单独重跑短缓存`compileall -q src scripts`通过，缓存留在系统temp，未执行删除。全批独立SPEC批准44e8f4ba：chain/process/默认真实入口/CI契约152 passed、2 skipped（183.50秒），support风险选择83 passed/122 deselected，实际双家族及分别损坏家族的6个worker探针3 passed（46.12秒）；Node15+18项、语法与diff通过。全批QUALITY仍待完成，Linux CI尚未运行。
- 全批独立QUALITY批准44e8f4ba：support/process/默认入口/CI契约261 passed/5 skipped；链路风险选择26 passed/67 deselected；独立两家族各19类损坏、数值容差、实际ASGI篡改探针4 passed；Node15/18项、语法及diff通过。各组相同2项既有警告。无剩余重要审查意见；仅代码发布认可，不是合并授权。原始混杂工作树13项保持不变，凭据模式检查仅输出匹配文件名、未发现匹配；没有资产、运行报告或src进入本批修改。

## 发布记录

- [Draft PR #29](https://github.com/kxyesyes/molecular_chat_system/pull/29)，目标main，尚未合并；分支`codex/family-real-acceptance-integration`。代码审查终点44e8f4ba，后续仅交接文档更新。
- head `e409125`的[Linux CI run 35443974454](https://github.com/kxyesyes/molecular_chat_system/actions/runs/35443974454)全7项成功：五组Python、static-quality、offline-quality。日志确认root使用Node20.20.2；新DOM自测属于static显式命令，真实权重开关始终0。
- Python日志汇总：agent 3198 passed/1 skipped；sandbox-api 153 passed；sandbox-core 1928 passed/4 skipped；task-runtime 1550 passed/3 skipped；root 2799 passed/78 skipped（476.53秒）。合计9628 passed/86 skipped；这些skip不计为科学通过，未逐项重新归因既有全部skip。已有大量依赖弃用警告仍保留，没有因本批而屏蔽。
- 本地与Linux都仅做合成资产工程验收。真实权重、外部主模型、浏览器、生产部署及原始reporting残差依然未完成。
- 首次push遇TLS握手错误，保持证书校验后重试成功；首次创建PR连接失败，先确认没有已创建的PR再重试，最终只有#29。未执行合并。当前无未解决GitHub审查线程；文档更新后的最新head CI应查看PR检查，不能把本条旧head结果冒充新head结果。

## 后续真实权重执行（本轮未执行）

需另行取得明确授权，并指定可信本地注册表目录及两个精确bundle ID；不自动扫描、选择最新bundle或读取原始训练数据。不需要外部主模型API，因为本入口使用明确的scripted决策替身，只验收已训练权重的工程调用链。

| 运行时环境变量 | 用途 |
|---|---|
| `MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE` | 仅字面值`1`开启；本轮与CI始终为`0` |
| `MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR` | 授权的可信本地绝对模型目录，不接受网络路径 |
| `MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID` | 精确PDE分类/回归bundle ID |
| `MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID` | 精确BuChE分类/回归bundle ID，须与PDE ID不同 |

授权并注入上述配置后才运行：`python -B -m pytest tests/test_activity_family_real_acceptance.py -q -p no:cacheprovider --tb=short`。
该入口顺序执行两个家族；每个受监督子进程最长120秒，另有有界清理。报告写入新的`outputs/agent_evaluation/family_acceptance_<uuid>.json`，不覆盖旧报告、不提交Git。配置错误直接failed；传输成功不等于科研成功；部分阶段和失败仍保留。
权重完整性检查不是恶意权重的安全沙盒。报告通过只说明这组输入上的调用一致性、数据和模型溯源，不证明泛化性能、药效或实验结论；不自动激活生产模型。

- 父级重跑Agent/model/fallback联合回归：3288 passed、2 skipped、7 warnings，108.52秒；这些src/既有测试在本批未修改，Task6新增测试仍待单独验收。
- 10个既有Node脚本、新DOM15项、两份JS语法检查、contract均通过。无效SMILES的RDKit解析日志为预期拒绝，不是伪造性质。
- 首次带清理的组合命令被工具策略拒绝，未执行。拆分非删除命令后，长临时缓存前缀导致3个compileall文件创建失败；实测失败路径长度265，短前缀同类路径221。仅缩短PYTHONPYCACHEPREFIX后`compileall -q src scripts`通过，未改源码；临时编译缓存保留在系统temp，不声称已清理，未进入Git。计划命令同步使用短前缀。
