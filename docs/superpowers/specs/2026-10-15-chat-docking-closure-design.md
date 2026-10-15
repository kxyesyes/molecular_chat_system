# 聊天到真实 Vina 的审批闭环设计补充

日期：2026-10-15。基线：`7ab815de891aa3d3c7dda1f784f021b91babedbd`。
分支：`codex/browser-agent-real-acceptance`；PR #209。
2026-10-16 授权更新：维护者要求完成剩余任务后自行提交、推送、合并；在全部门禁
通过前仍保持 Draft。此更新不替代绑定当前输入与预览的一次性运行时审批。

## 1. 授权与设计关系

维护者已批准方案 A、全量科学校验和显式审批，并要求补齐聊天对接链路及
三个 BLOCKED 浏览器场景。反向寻靶由同日
`2026-10-15-reverse-target-compact-source-design.md` 单独约束；本增量针对 S10。

本文件是 `2026-09-27-docking-consent-integration-design.md` 的有界补充，
不是放宽 C1/C2/C4a 保护的替代方案。原设计关于一次执行、同任务身份、owner、
nonce、Origin、资源所有权、有限反馈、产物授权和旧入口隔离的规定继续适用。
原文件中已有的实现阶段和历史审阅记录不重写为本次完成证据。

明确补充范围：普通 legacy 首页同样使用安全 C 协调器；从已接受的结构检索
证据和明确 SMILES 生成可审阅的输入制品；从**经核对并明确选定的共晶残基**
生成口袋建议，用户确认全部输入后才进入已有 file-backed 执行契约。
不改变整个应用的聊天 profile，不用独立对接页或实验室入口替代首页验收。

维护者已于 2026-10-15 确认本书面设计并批准在当前会话按 TDD 实施。
没有真实 Vina 成功记录，S10 仍为 BLOCKED；运行时仍需用户亲自点击审批卡。

## 2. 事实基线与非目标

本机脱敏可用性检查返回 Vina、受体准备、配体准备及 RDKit 可用；这不是执行
验证。现有 `prepare_docking_consent` / `approve_docking_consent` 等底层 seam
尚未形成首页聊天闭环，C0c/C3/C4b/C5/C6/C8 门禁不能省略。

S03 已返回人源 BCHE / P06276 及 1P0I 等实验结构，1P0I 的记录包含 BUA/NAG。
此信息不证明配体角色或坐标可信。当前 `_auto_box_from_co_crystal` 仅接受一个
非排除 HETATM 残基；不能删除不方便的残基来强行触发自动盒。

不新增批量 docking、共价 docking、盲对接、口袋预测模型、蛋白修复模型、
远端执行器或队列；不猜 protonation、missing residues、绑定能或实验亲和力。
不自动合并、不改主聊天/分子生成模型，不放宽已通过的 A2/B1/B2 边界。

## 3. 组件、入口与身份链

| 单元 | 文件 | 边界 |
| --- | --- | --- |
| C 请求协调 | 新建 `src/web/docking_request.py` | 原请求、已验证引用、补充输入、同一 root/task；不执行科学命令 |
| 输入来源生产者 | 新建 `src/docking/chat_preparation.py`、`src/docking/pocket_evidence.py` | 有界真实结构读取、明确 SMILES 的 SDF、共晶残基证据和网格建议；不调用 Vina/ADFR/Meeko |
| C Web 投影与所有者 | 新建 `src/web/docking_consent.py` | 单一反馈期限、物理 send/close 所有权、历史与完成投影 |
| HTTP 与产物 | 新建 `src/web/routes/docking_consent_routes.py` | 同源同 owner 的 prepare/approve/revoke/cancel/status/download |
| 已有运行时 | `src/task_runtime/runtime.py`、`store.py`、`database.py`、`docking_execution.py` | 补齐 draft refinement/投影持久化和现有 CAS/执行/清理接线；不重写调度器 |
| 入口接线 | `src/web/app.py`、`chat_handler.py`、`decision_runtime.py`、`legacy_websocket_protocol.py`、`ordinary_capabilities.py` | 两个既有 profile 的窄 C 分支；不新增 WebSocket receiver，不改非 C 请求路径 |
| 来源与交接 | `src/web/scientific_references.py`、必要的已有持久化模块、`src/target_search/downloader.py` 的窄 seam | 同会话、真实来源、稳定文件快照；不接受任意用户路径/URL作为权限 |
| 前端 | 新建 `src/web/static/js/home/docking_consent.js`；窄改 `main.js`、消息分派、`index.html` | 原消息下的准备/审批/进度/结果卡；原生 JS，无新框架 |
| 旁路封闭 | `src/task_runtime/routes.py`、`src/web/routes/docking_routes.py`、`docking_report_routes.py`、现有 retention | 识别持久化 C 归属，阻断旧下载/删除/取消等绕行 |

旧 C proposal DTO、模型 transport/journal 已存在时复用，不重新实现。
如需要新的辅助模块，由实施计划逐一给出职责和测试，不在大文件中堆叠所有逻辑。

每条 C 请求固定 owner_session_id、服务端 root trace、origin turn、request ID、
task ID、原请求摘要、profile/model/runtime generation。浏览器 ID 只是 locator。
预备、补充、READY、执行、报告、重连均引用这个 task ID；不能审批时另建任务。

legacy 与 decision 入口都先验证现有帧、会话和 tools 开关。C 协调器接收完整
原请求与所有权已核验的历史；解析提案仍用闭合 `docking_preparation_v1` 和
已选 native/JSON transport，模型 lease 必须实际收敛。关键词仅可筛选是否评估，
不能批准执行或丢弃额外义务。知识问答、否定对接、仅问能否对接、混合/不确定
任务不能变成可执行子集。协议失败不回落到 legacy 自由文本对接。

## 4. 审批前的真实输入准备

### 4.1 已接受的受体结构

只解析当前请求明确指定且实际出现在同 owner 已接受 S03 证据中的结构 ID；
无历史或含糊“上一个”先澄清。核对 target、物种、PDB entry、polymer entity、
链和来源版本；同一结构含多实体时不能只凭文件名认定是目标蛋白。

复用结构下载器的可信来源与大小上限，不接受模型/浏览器提供的任意 URL。
重新核验缓存，使用稳定 descriptor 读取实际字节与 SHA-256，不把路径存在当作
验证。网络失败、结构格式不支持、身份冲突均返回明确等待/不可用。

初版支持经严格验证的 PDB 输入。需要 mmCIF 才能保留链/坐标信息的结构明确
报告不支持，不能有损转换后悄悄继续。原始结构与用于执行的受体衍生物分别
保存 hash 和转换记录：所选模型/蛋白链、保留和移除的 hetero residues、水、
氢处理及任何限制均展示；不修补缺失残基，不默选存在歧义的链/替代构象。

选定共晶参考配体用于网格后，必须从受体执行输入中移除其占位原子并记录，
不能把参考配体留在受体中再对接阿司匹林。若 cofactor/离子是否保留不清楚，
暂停要求明确选择；此选择进入输入证据，不凭模型推断处理。

### 4.2 明确 SMILES 的输入构象

使用全字符串严格 SMILES 校验，保留原子连接、正式电荷和明确立体化学；
不默默截取子串、去盐、改变 tautomer 或枚举 protonation。初版限制单个连通
小分子、最多 512 原子及现有字符串长度上限；不支持时明确说明。

在审批前允许完成**输入制品生成**，不等于 docking：RDKit 加氢，ETKDGv3
固定 seed=20261015，最多一次嵌入；只有完整 MMFF 参数存在时进行最多 200 步
优化并要求收敛。失败不使用理想化坐标或悄悄换方法。
不修改 legacy `_prepare_3d_molecule` 的既有行为。

通过现有可终止子进程所有权机制离线生成 SDF，最多 60 秒；记录 RDKit 版本、
方法、seed、连接/立体校验和制品 hash。此处没有外部模型、ADFR、Meeko 或 Vina。
输出明确标为“由 SMILES 建模的输入构象”，不是实验构象或已对接 pose。
同一 draft 只生成一次并冻结；刷新预览不能重新随机生成不同字节。

上游准备总预算 180 秒，读取下载和子进程时间均计入；取消/超时后保留实际 I/O
和子进程 owner 直到收敛。未生成全部有效制品前，不调用 C1 的 30 秒 staging
入口，不把上游计算塞进 C1 的 prepare-only 契约。

### 4.3 共晶残基与网格建议

从已验证原始结构列出真实非水 HETATM 候选，识别 model、chain、residue name、
sequence、insertion code、altloc、occupancy 及重原子坐标。坐标必须有限，
拒绝损坏记录、混合 MODEL、未消解的替代位置或缺失关键原子，不能宽松跳行。

结合真实来源注释/结构论文或 deposited ligand 信息核对候选角色。糖基化、
溶剂、缓冲物及金属不自动当作口袋配体；仅有三字母 ligand ID 不足以证明角色。
记录支持来源和局限。多个可用候选或角色不明确时展示选择并等待，不猜第一项。
用户选择不能豁免结构身份和坐标完整性检查；没有可验证配体时保持 BLOCKED。

经核对且明确选定的单个残基，按其**重原子**轴对齐边界计算建议：
`center_i=(min_i+max_i)/2`，`size_i=max(10, max_i-min_i+8)`，单位 Å。
这是每侧 4 Å margin 的可审阅规则，不是实验测得的完整口袋，也不是预测模型。
超出运行时允许尺寸则拒绝，不截断。显示原子数、残基身份、原始结构 hash、
算法版本、margin、center 和 size；明确小参考配体可能不能覆盖完整结合位点。

算法建议、exhaustiveness=8、num_modes=9 只作为可见待确认值，不是用户已输入
或已批准。卡片要求用户确认该口袋及全部参数；确认尚未固定的建议推进 draft
revision，此时还没有审批 nonce。任何已固定输入的变更必须撤销旧请求及其
已有 nonce，再另起明确新请求，不能在 READY 卡片上就地改值后继续审批。
模型不能写入任意中心；从服务端固定证据描述符产生的数字必须
逐项验证，不能仅凭 proposal 的 source_spans 通过。

## 5. 冻结输入、显式审批与一次执行

沿用原 C 的 AWAITING_INPUT → PREPARING → READY → CLAIMED → 终态契约。
draft 只保留元数据摘要/标识，原文本按现有请求内存边界保管。
缺失输入使用明确 pending slot，不冒充已有制品。

新增 draft refinement CAS：同 owner、当前 revision/generation、尚未 CLAIMED，
只能填入尚缺字段；固定输入发生变化则撤销旧请求并要求新明确请求，不修改已
密封制品。补充输入沿用原等待期限和已耗费模型预算，不能续期或重置调用次数。

最终 receptor_ref/ligand_ref 为服务端内容寻址描述符 ID，其 digest 覆盖输入
制品、原始来源、转换/构象记录和口袋证据。沿用现有 `source_refs` 字符串字段，
不放宽 `DockingConsent@1` 的闭合 schema。描述符只能由持有该 owner/root 的
生产者发布，并在预备、审批、执行及读取时核验摘要与来源。

C1 固定并验证两份文件和 `center/size/exhaustiveness/num_modes`；其输入 hash、
config hash、request digest 及 consent binding digest 保持现有定义。
预览还展示来源证据摘要，与封存 source_ref 对应；未经验证的显示文本不能
提供执行权限。Vina 随机 seed 如执行器输出可用则如实记录，不宣称逐比特复现。

只有用户在**当前卡片**点击明确的“确认输入并运行真实 Vina”才能提交
`{approval_nonce, binding_digest, confirm:true}`。普通聊天“可以/开始”、模型
提案、此开发任务的总体授权以及测试 Agent 均不能代替该次应用审批。
真实浏览器验收到 READY 时请用户亲自点击；Agent 不代点、不直接 POST 批准。

审批 nonce 只在内存，禁止 URL、local/session storage、历史、日志或报告持久化。
默认 15 分钟有效，失去 nonce 不能从 status 恢复；撤销/过期后需新明确准备。
双击、重放、跨 owner、旧 revision/generation、输入/策略变化、过期均不得执行。
运行固定 local backend；未知提交结果不重试、不退回另一执行器。

## 6. HTTP、反馈、取消和产物访问

继续使用原 C 设计的 `/api/agent/docking/preparations`、approve/revoke、
task status/cancel/artifacts 路径以及 `docking_refine` / `docking_watch` 帧。
HTTP 准备只能解析既有 chat draft；不能从独立表单建立无关科学任务。
应用配置 `MEDCHAT_CHAT_DOCKING_ENABLED` 默认关闭；全部门禁通过后，在受控
本机验收启动参数中显式开启，不修改用户其他服务或应用默认 profile。

所有 mutation 要求唯一有效同 Origin 和 session；身份核验在文件 I/O、状态
refresh 或 cancel 之前。严格拒绝重复/未知 multipart 字段、畸形 JSON、超量
body、任意路径/URL、client owner/backend/timeout 字段。缺身份存储时 fail closed。

`DockingView@1` 卡片绑定原始消息、root/task/revision/send generation/sequence。
模型阶段实际收敛后 segment_complete 才解锁输入；等待审批不是科学完成。
既有状态机的 failure/cancel/timeout/unknown 没有分数或 pose 成功链接。

C4b 保持原设计的单一 5 秒控制反馈期限和实际 send/close 所有权：不调用会无限 drain
的 A2 帮助函数来伪造有限反馈；超时后仍保留物理操作，晚到 send 不能发布成功、
创建下载 grant 或写成功历史。close 只能在该 socket 的 in-flight send 收敛后
执行一次，不追加第二个反馈预算；A2 的 drain-before-return 语义不改。
前端也锁定本地 cancel/timeout/disconnect 终态并拒绝晚到成功。

取消操作立即封存非成功视图，并驱动同一个运行时取消 owner；物理进程、reader、
spawn、Job 或 SQLite I/O 尚未收敛时显示 cleanup pending，不释放 lease、容量或
可替换资源。沿用 C4a 的不确定 spawn 保留机制，不为通过测试伪造 settled。

成功必须来自现有完成校验器，重新核验输入/配置/工具身份、真实 PDBQT pose、
有限非 bool 能量、pose_count 和 artifact bytes/hash。报告准确原值、kcal/mol、
计算来源和局限；不使用模型重新评分或改写数字，不称为实验 affinity。

产物放入 C 独立私有根，不暴露于 legacy history/static 枚举。授权绑定
owner + task + completion digest + artifact ID + runtime generation，最长 24 小时，
无滑动续期。每次下载复核 grant、成功视图、containment、输入/输出摘要，使用
经验证的实际字节/稳定 descriptor，不能先验路径再无检查 FileResponse。
reader 取消/断开也必须释放且仅释放自己的实际所有权。

通用 `/api/tasks` 和 legacy result/pose_sdf/report/history-delete 必须按持久化
C 身份封闭旁路：外部 owner 不可读/refresh/cancel，授权 C 取消进入 C 协调器；
不可用的 ownership store 不能把 C 当 legacy。retention 不删除仍有审批、writer、
执行 lease、artifact reader 或未解决清理的制品；去重墓碑保留，不能删除后重放。

## 7. 分阶段门禁与测试

先固定可独立验证的输入 producer，再补 C-entry/HTTP/反馈/产物/前端，最后
同时启用。任何阶段未过，不以底层 C2 已通过为理由对外开放执行入口。

新增或扩展：`tests/test_chat_docking_preparation.py`、
`tests/test_docking_pocket_evidence.py`、`tests/agent/test_web_docking_consent.py`、
`tests/test_docking_consent_artifacts.py`、`tests/home_docking_consent_test.js`。
先记录 RED 的断言与阶段；低层 fake 仅隔离外部调用，不能替换被测 CAS/完成验证。

- 输入：精确 PDB/target/species/chain、错误历史、外部 URL、缓存替换、截断坐标、
  NAG/溶剂误认、多个 MODEL/altloc、参考配体未移除、缺共晶与超尺寸全部拒绝。
- 配体：全字符串/电荷/立体一致，MMFF 缺参、嵌入失败、不收敛和超时不产生
  可执行制品；seed/hash 可追踪；审批前零 ADFR/Meeko/Vina 调用。
- 请求：两种首页 profile 真实路由到同一 C 协调器；非 C/否定/仅询问、关闭
  tools 或 C、混合义务、model protocol 失败保持正确行为，不改变 A2/B1/B2。
- 审批：真实 SQLite CAS、两个连接竞态、nonce/owner/Origin/版本/expiry 边界，
  失去响应、claim/dispatch 不确定及文本“yes”均无自动第二次执行。
- 反馈：真实有所有权的延迟 send/close，取消抵抗、锁等待到期、晚到成功、
  断线、重连、shutdown 均保持单一终态及实际清理所有权。
- 安全：通用任务/旧下载/报表/转换/删除旁路、跨数据库 ID 碰撞、文件篡改、
  symlink/reparse/hardlink、下载时替换和 reader 断开覆盖；无凭据泄漏。
- DOM：消费实际 route 的闭合事件 schema；同消息同任务卡、参数展示、显式
  click、双击锁、nonce 不持久化、XSS 转义、晚到结果抑制与授权下载。

回归包含现有 task_runtime、command cancellation、staging/completion、会话、
Web 生命周期、legacy protocol、科学 references 及 Node 首页测试。
运行 Python 全仓离线回归、compileall、修改 JS 的语法检查、相关 Node 测试及
健康检查。跳过/失败与平台限制单列，不用历史 23,526 passed 证明新代码正确。

## 8. 真实浏览器验收与完成定义

从原标准首页运行 S03，选择实际返回的结构，在**同一会话**提交原 S10；
不从终端 Vina 或直接 API 生成成功后贴到聊天。使用实际结构字节和可信配体
证据；没有可用共晶时保留 BLOCKED，并明确下一项需要的科学输入。

依次核验：原请求 → 真实输入准备/必要澄清 → 当前参数/来源预览 → 用户显式
审批 → 同任务真实 Vina → 经过验证的结果与 pose 下载。记录程序版本、输入/
参数/输出 hash、trace/task、实际能量/单位及局限；独立解析输出复核界面。
执行结果即使很差也按原值报告，不为理想能量重复挑结果。

通过真实 UI 另测未批准、撤销、过期/重复审批保护；危险并发和文件篡改以隔离
回归测试验证，不污染用户真实制品。用户点击前以及缺少真实结果时不能标 PASS。
保持用户其他本机服务、会话和未提交资产不变。

实施结果追加到 `docs/superpowers/validation/2026-10-15-browser-agent-real-acceptance.md`，
保留首次失败和历史 BLOCKED。PR 描述写清文件、命令、结果、剩余门禁、commit，
全部门禁通过前保持 Draft；之后按 2026-10-16 的更新授权合并。
S08/S09/S10 各自有独立证据，不能以一个通过代替另外两个。

## 9. 设计审阅记录

已完成与现有 C 设计、file-backed DTO、draft store、源文件快照、真实首页 profile
和执行所有权的对照。补充的审批前输入建模不调用外部 docking/preparation 工具；
最终 C1/C2 仍只接收被冻结的文件和明确参数，没有把总体开发授权当执行 consent。
书面自检已核对范围、契约冲突、失败状态、所有权、预算、测试与验收对应关系；
这是设计自检，不是已实现代码的独立审查或测试通过记录。

书面设计已经维护者确认，进入实施计划。若无法在上述 scope 内完成旧入口隔离、
物理 owner 收敛或共晶角色核对，停止该项发布并报告具体缺口，不扩大权限或造结果。
