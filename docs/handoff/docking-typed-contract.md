# Docking 类型契约实施记录

日期：2026-09-24。基线 main `9baab4622cb88a2bbeb4beab616e54e4b478b171`；独立分支 `codex/docking-typed-contract-pr`。用户已确认书面设计 `957f602`。实现、独立双审及最终本地回归已完成；远端CI/发布状态另记，不提前声明合并。

## 边界

生产写集为新 `src/agent/tooling/docking_contract.py` 与工厂的 molecular_docking 选择。保留现有 compat、领域验证、失败清理、取消/超时/并发/关闭；不启用四个辅助工具，不动 Planner、HTTP、数据库、模型或真实 Vina/OpenSandbox/Temporal。

父任务写实际领域/委派集成测试和文档；子任务写专用契约/单测，写集不重叠。原始项目13项混杂状态不动。所有合成结果仅用于契约检查，不是科研验收。

## 实际 TDD 记录

- 书面设计前七路径基线：318 passed、1 skipped、7 warnings、9 subtests passed，15.19秒exit0。唯一skip为Windows不支持POSIX process-group；不是本批新增。
- 首次实际集成RED：`tests/agent/test_docking_contract_integration.py`，**9 failed、15 passed，1.37秒exit1**。直接结构缺query被拒绝、query+结构丢字段、原始文本未进领域拒绝，以及委派缺参被编码为internal_error而非invalid_input。已有wrapped成功/不可用/失败/取消兼容对照正常。
- 增加8个输出边界测试，继续使用旧版已支持的wrapped输入，实际MolecularDocking配合明确合成服务返回缺pose、非有限能量、布尔pose数或空结果：**17 failed、15 passed，1.84秒exit1**。8个新失败不是输入缺陷重复计算；Registry及单独专科调用接受了不完整合成科学结果。共享Session校验原本存在，不能据此声称正式聊天无校验。

## 命令

使用已批准计划中的隔离runner，替换工作树路径，MedChat Python3.10 `-B -m pytest <absolute test paths> -q -p no:cacheprovider --tb=short -rs`。完整提取命令见 `docs/superpowers/plans/2026-09-24-docking-typed-contract.md`。

当前已跑的新测试命令目标：

```text
tests/agent/test_docking_contract_integration.py
```

临时cwd/配置/数据库、清空非白名单环境、关闭real/canary；service仅注入可控替身，未加载真实资产或外部服务。临时合成pose不算真实Vina证明。

## 首轮实现与验证（尚非最终发布门禁）

- 实现者契约RED：113 failed、19 passed；最初两新文件GREEN164 passed，与七文件基线合跑482 passed、1 skipped。补pose/proof回归先得到4 failed、134 passed；修复后488 passed、1 skipped。嵌套Pydantic实例复用再补1项RED，修复后契约文件139 passed。
- 父任务增加实际Supervisor→共享Session→SQLite的3项集成。首次 **2 failed、33 passed，1.54秒**：新断言错误要求失败会话完全没有provenance。核对既有 `EvidenceLedger.register_tool_result()`，它会在科学清理后补追踪身份，不是恢复科学来源。仅校正新增测试：原科学数据/模型来源必须清除，input_digest仍保留，账本scientific_usable=false。重跑 **35 passed，1.66秒exit0**，未改Session生产代码。实现者同期合跑490 passed、2 failed、1 skipped，也是这两条尚未校正的断言，不隐去该次失败。
- 第一轮23路径联合回归：**5051 passed、9 skipped、7 warnings、9 subtests passed，271.12秒exit0**。该进程在后补10项测试之前已完成collection，不代表最终测试集；审查后需要重跑。
- socket连接全阻断的离线 `scripts/run_agent_acceptance.py --mode contract --output scratch/t10-docking-typed-contract.json`：**34/34 passed，exit0**。是工程契约，不是真实科研结果。
- 305个src/scripts Python文件内存编译、diff-check通过；11个`*_test.js`加`activity_family_acceptance_dom.js`共12个Node脚本通过。未改前端，产物不提交。

23路径为前批 `docs/handoff/activity-typed-contract.md` 中21路径，额外加 `tests/test_docking_agent_architecture.py`、`tests/test_docking_configuration.py`。九项skip为前批八项平台/未配置runtime/未启用性能条件，加Windows不支持POSIX进程组一项；七项警告为SWIG/FastAPI弃用。未新增skip或放宽既有断言。

## 后续门禁

实现已进入独立SPEC审查，随后QUALITY；还需最终联合回归、离线contract、编译、敏感材料检查和当前head CI。本批未提交生产改动、未推送或创建PR。任务书T09、Planner及其他T11事项不属于本批完成声明。

### 首次独立SPEC：未批准

父任务九路径聚焦 **492 passed、1 skipped、7 warnings、9 subtests passed，29.03秒**；SPEC独立同范围 **492 passed、1 skipped，25.02秒**。但审查另加对抗探针发现两项漏检，不能因为已有回归全绿就进入发布：

1. P1：failed/partial的直接`data.binding_energy`、其他已知科学载体仍能保留非有限能量或无效pose；原实现仅识别四个data键。不是对任意扩展元数据进行推断，也不代表共享Session原有清理失效。
2. P2：`elapsed_ms`未纳入严格输出视图，合法canonical成功结果中的字符串耗时原样通过。

SPEC探索性探针与契约组合 **27 failed、146 passed，15.77秒**，其中包含重复载体变体和一条不计入阻断项的过严scrub形态断言。最小3缺陷测试+既有scrub对照+集成为 **3 failed、36 passed，8.77秒**；父任务独立最小复现 **3 failed、1 passed，7.17秒exit1**。父任务通过`receiving-code-review`的核验原则检查实际probe与生产路径后交回原实现者，要求永久RED→GREEN，仅限原两生产文件和契约单测写集。

这次审查源码SHA256：docking_contract.py=`e5d0741907d1ec20f275136e6094236caba5c5497d0c6dc6ae7b93fff7748c96`，factory.py=`8352e74f2b0149e3234e6becd457ea1c36dc6a6a807f7154466160fbdf7c2872`。审查拒绝历史与scratch探针保留；scratch不提交。修复后需SPEC复审，未提前发起QUALITY。

### SPEC问题修复，等待复审

实现者新增永久用例及独立探针RED **74 failed、155 passed**。首轮GREEN **1 failed、263 passed**：测试将非法耗时注入compat结果后，既有Adapter耗时记账正确覆盖了它，未真正到达输出校验；只将测试注入点改到`_normalize`之后，保留拒绝/不得回显断言。最终两新文件+七基线+38个独立探针 **582 passed、1 skipped、7 warnings、9 subtests passed**。父任务独立重跑两新文件+探针 **264 passed，8.90秒exit0**。

生产修复仍限原两文件：明确检查已知科学位置和docking_pose，不深入不透明扩展；复用科学证据检查与scrub；严格非负整数/None耗时并避免反射非法值。契约191项、集成35项；没有改科学算法、Session、服务或原有测试断言。新docking_contract.py SHA256=`4e2eab3549352c092a0b32528c6f3ae37033285332a63fc9219d3297d9a4ca69`，factory未变。SPEC复审正在进行，尚不宣称通过。

### 第二次SPEC：保留语义回归，仍未批准

独立复审确认前两问题修复，重跑 **582 passed、1 skipped、7 warnings、9 subtests passed，25.23秒**；但另补16项保留语义探针得到 **4 failed、12 passed，1.35秒**。合法failed观察带quality/evidence能量引用时，compat把完整data放入`error.details.raw_result`，新校验只用空的顶层data作为证明，导致误拒绝并清除合法诊断/来源。legacy和canonical、quality和evidence四组合复现；partial对照通过。

按原批准的“保留合法失败观察”范围交回实现者，要求永久RED后修复，校验可使用经过验证的有界原始快照证明，但不得恢复已经清除的输出。继续保留不透明扩展和循环/深度边界，不改共享compat或Session。未发布，未启动QUALITY审查。

- 该时点前启动的第二轮23路径联合：**5061 passed、9 skipped、7 warnings、9 subtests passed，545.46秒exit0**。它仍不包含上述修复后的新增测试，不能代替最终快照回归。
- 保留语义永久RED：**5 failed、17 passed**，包含独立审查四组合和一个合法嵌套链；修复后首次组合 **604 passed、1 skipped、7 warnings、9 subtests passed，16.70秒exit0**。范围为契约213项、实际集成35项、318基线和38独立探针。
- 原始快照链改为有界叶节点优先验证，仅已通过的子观察data可作为父观察校验上下文，不投影、恢复或改写返回观察。仍是专用契约内的最小改动。当前docking_contract.py SHA256=`f2c69ded44bf3eb881e4f8d99ec7c87f534194a6d0e85f1d920c85b96b6bf23d`，等待再次SPEC复审。

### SPEC最终批准

同一代码hash独立复审 **APPROVED**。实际九路径+原probe **604 passed、1 skipped、7 warnings、9 subtests passed，22.27秒exit0**；另42项内存探针全部通过，2.78秒exit0。验证合法failed/partial保留，内层无效科学数据、身份错误、外层缺输入证明、外层非法能量及OpenSandbox hash损坏均拒绝；不透明扩展不能贡献证明。没有未解决规格问题。

随后才派发独立QUALITY；联合回归仍运行中，尚不提前声明质量审查/CI通过。

### 最终本地联合回归

同一SPEC批准hash下重跑完整23路径：**5135 passed、9 skipped、7 warnings、9 subtests passed，362.19秒exit0**。没有修改既有回归文件；新增契约213项和实际集成35项。九项skip仍为前述平台/环境条件，不计作成功；不是全仓真实科研验收。

再次socket禁连运行离线contract：**34/34 passed、exit0**，报告SHA256=`2c219da19bfb947925505932a25a4e1850065800e794244d0ef50efcd14a162e`。305文件内存编译、diff-check、七文件凭据模式检查（0命中）再次通过；Node12文件已通过且无前端改动。远端main只读核对仍为9baab462，未改写main。

### 首次QUALITY：未批准

独立QUALITY九路径 **566 passed、1 skipped、7 warnings、9 subtests passed**（不含scratch probes）；但新增14项定向探针与两新文件合跑 **7 failed、255 passed**。父任务独立运行14项探针 **7 failed、7 passed，1.29秒exit1**，确认三处问题：

- P1：OpenSandbox后端标记只存在于failed原始快照data时，外层额外docking_pose未沿用沙盒hash要求；错误hash被保留。
- P2：文件检查抛PermissionError/OSError时，前置校验可能透出路径异常文本，后置校验可能抛出execute；仅应在校验内部转安全拒绝，不改变调用者raw_guard异常语义。
- P2：有效failed原始输出带非空结构化error.details时，现有compat会丢掉data且不放raw_result；后置校验误拒绝其中有效quality/evidence声明。需要本次调用内已验证证明，不改全局compat、不恢复输出、不共享跨请求状态。

已交回原实现者按原写集TDD。测试中只有合成路径/pose，不是真实OpenSandbox；artifact-only探索性断言未列入阻断项。最低Pydantic2.5.0仍待CI验证。之前SPEC批准与5135联合通过不覆盖这些新增反例，发布继续暂停。

### QUALITY修复后的门禁

永久回归与独立定向探针先得到 **15 failed、263 passed**，首次GREEN278 passed。最终组合 **636 passed、1 skipped、7 warnings、9 subtests passed，18.22秒exit0**，包含两新文件、七基线、SPEC38探针与QUALITY14定向探针。新增并发隔离、伪造证明和调用者异常语义检查。

实现保留共享compat算法：只在本次Adapter调用内部用私有完成对象传递已验证证明，继承的规范化仍只执行一次，返回普通ToolResult且无私有证明字段。不把无法验证的外来canonical失败变成功，不在实例/线程全局保存证明；超时结果不带该对象。沙盒后端从已验证快照延续到所有artifact校验，校验内OSError按原scrub拒绝，调用者guard异常不被新catch吞掉。

当前docking_contract.py SHA256=`2d00c109bd85ea089adedbd7c0038b25f66a0432ddf7ae156647925f349146ee`。因新增内部完成交接方式，先再次独立SPEC复核其是否符合薄适配范围，再交回QUALITY；不是沿用旧hash批准。联合回归重新运行中。

- 该代码23路径联合 **5153 passed、9 skipped、7 warnings、9 subtests passed，302.01秒exit0**；离线contract34/34通过，报告SHA256=`ef99663f66d61dcfcff0dafb4dc095c586ffa71d3cc40c4c6518c8335bed36f1`。
- 新独立SPEC确认内部完成对象符合薄适配范围、未改变生命周期；已有聚焦318 passed，7.72秒。但新增探针 **2 failed、13 passed，1.69秒**：外层data为`{}`或诊断字典时，仍能遮盖内层已验证OpenSandbox身份并绕过额外artifact的hash要求（data=None与正确hash对照通过）。该探针首轮4 failed/11 passed中的两个测试自身错误已更正，不计作生产缺陷。
- 已回交实现者：沙盒安全要求不能被外层无科学意义的数据降低，继续保持不恢复数据、不扫描不透明扩展。上述绿灯不代替尚未修复的反例，仍不发布。

- 增补永久RED **9 failed、22 passed**；修复后两新文件、七基线与15项复审probe **615 passed、1 skipped、7 warnings、9 subtests passed，16.66秒exit0**。已验证快照的OpenSandbox约束独立累积，不能被外层/中间诊断、空字典或显式local标记降低，实际输出不被重写。
- 再次独立SPEC **APPROVED**：两新文件+38原probe+15复审probe **335 passed，4.92秒**；另15项独立测试 **15 passed，0.99秒**，覆盖外层完整local科学数据、产物证明、runtime/image/cleanup/input、状态保留及请求隔离。当前hash=`f26615028e41ad4fac9170c2eda3b6ac54d7012b958554cbaa4190a25cd8e458`，再交回QUALITY复审；当前尚未推送或合并。

### 当前源码最终门禁与未解释失败

- QUALITY复审 **APPROVED**，原14项探针与两新文件296 passed；增加内部交接、后置投毒、并发/超时隔离等55项控制后 **351 passed，8.57秒exit0**。公共返回仍为普通ToolResult，不泄漏私有证明；继承规范化只调用一次。未发现可复现的剩余阻断项。
- 必须保留一次中间失败：`test_snapshot_sandbox_requirement_is_monotonic[False-True-None]` 在两文件272项中得到 **1 failed、271 passed，4.44秒exit1**。`result == expected` 不等，差异包含message/error/warnings/evidence/provenance；pytest截断了146行，未记录具体error.code，也没有保存原始stderr。后来41项定向和351项组合通过，**不证明这次失败原因已定位或已修复**。当时并行运行其他回归，但无证据证明调度或文件变化是原因。该待查项必须随PR披露，不删除断言、不新增skip或重试来隐藏。
- 父任务当前hash重新执行23路径联合：**5169 passed、9 skipped、7 warnings、9 subtests passed，309.13秒exit0**。其中新契约247项、实际集成35项；九项skip条件未变。不是全仓全绿或真实科研验收声明。
- socket禁连离线contract再次 **34/34 passed，exit0**，报告SHA256=`423619cef06f009bab1b6763090f710e1cef88ae3f898581c38b187cedeb8144`。305个src/scripts文件内存编译通过；12个Node脚本已通过且未改前端。
- 两个生产文件SHA256：`docking_contract.py=f26615028e41ad4fac9170c2eda3b6ac54d7012b958554cbaa4190a25cd8e458`；`factory.py=8352e74f2b0149e3234e6becd457ea1c36dc6a6a807f7154466160fbdf7c2872`。独立双审对应这组hash。本地Pydantic2.12.5，最低2.5.0仍需当前head CI验证。

本批七路径为两生产文件、两新测试、设计、计划与本文。原始项目未修改；没有真实工具、模型、私有数据、数据库迁移或部署。任务书剩余T09、T10-B Planner和其他T11事项继续单独处理。

补充有界诊断：仅在ignored scratch中复用原测试，通过`_bounded_read`期间一次重命名测试自己tmp_path内的合成兄弟文件，记录实际父目录版本变化。控制组保持合法观察，变动组安全返回INVALID_OUTPUT并清除科学数据/来源，**2 passed，2.60秒exit0**。这验证继承的`read_file_snapshot`目录版本校验能产生类似拒绝，**不能证明历史偶发失败就是这个原因**。未改生产代码、既有安全策略或永久测试；不把它记录为已修复缺陷。
