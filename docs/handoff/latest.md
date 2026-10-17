# Latest handoff

## PR #209 剩余自动审查关闭与有序合并门禁（2026-10-17）

- 维护者已确认完成修复、独立复审、新 CI 后按 #209 → #210 分别 Squash merge；这项最新授权覆盖下方历史“暂不合并”，不允许绕过失败、保护或审批边界。
- 基线 `1c567b30` 的剩余反馈已补 RED 后最小修复：普通阈值设计保留生成并追加身份绑定的独立性质计算，缺失结果明确未验证；无关禁止对接不阻断精确 PDB，但否定/混合标签歧义仍拒绝；泛知识“的性质”不误路由，中文化合物主体保留。
- SPEC 与 QUALITY 发现的禁止计算/性质保留及混合标签边界均先补失败回归；最终 SPEC PASS，原 QUALITY 审查者确认三项 P2 全关闭、无剩余 finding。最终联合回归 739 passed，独立 QUALITY 157 passed +31 离线探针；compileall/diff 通过。批次重叠，不累计唯一总数。
- 本记录随修复发布，当前新提交 CI 尚待核对，未合并；随后远端结果更新 PR 最新状态。#210 必须收到新 main、保留六文档范围及预热/多 worker 指导、独立复审并通过自己的新 CI，才可合并。
- [完整 RED/GREEN、审查与限制](../superpowers/validation/2026-10-17-open-review-merge-readiness.md)。本轮不重跑真实 Vina，不改变既有 6SAM 结果，不提交运行资产/凭据，也不改动 D: 主工作区。

## PR #209 最终独立审查修复（2026-10-17）

- 在原 `codex/browser-agent-real-acceptance` 分支修复 1 P1 / 4 P2，基线 `ff1d3c06`；先 RED 后最小实现。工具链在每次配体命令前重验，archive/CCD 已知形式电荷冲突拒绝，名称/accession 逐项绑定，新增描述符阈值不再静默遗漏，SMILES/小数/显式路径点号不破坏否定。
- 独立需求/质量复审期间发现多目标/未知名称、中文路径和复合阈值遗漏，均补失败回归并交原审查者关闭；最终 SPEC 和 QUALITY 五项全部 PASS，无未关闭的重要 finding。
- 父级最终集成/精确检索 685 passed，审批/当前执行上下文 196 passed，执行证据/复用 116 passed、2 skipped；compileall/diff 通过。审查者独立回归与探针、长临时根前置失败和修正过程保留在验收记录，不累计重叠批次为总数。
- 本记录随修复提交；新 head CI 必须单独核对，最终结果写入 PR #209 最新状态段，不沿用 ff1d3c06 的 11/11，也不创建自引用文档提交链。
- 验证命令、RED/GREEN、重叠批次、平台跳过及科学限制见 [验收记录](../superpowers/validation/2026-10-15-browser-agent-real-acceptance.md#2026-10-17-最终独立审查修复基线-ff1d3c06)。不重跑真实服务/Vina，不修改既有 6SAM 科学结果或下载文件，不提交运行资产/日志/凭据。
- PR 在本次修复前已 Open / Ready for review；下文旧 Draft 表述为历史状态。维护者明确要求暂不合并，不启用 auto-merge，不把 Agent 复审当作人工审批。

## PR #209 文档提交 CI 已全通过（2026-10-17）

- 真实6SAM闭环验收文档提交`39728e8afd4c2a7cb953e0ea21dfbeb9d483f08b`的
  [quality run39014083190](https://github.com/kxyesyes/molecular_chat_system/actions/runs/39014083190)
  已completed/success，11/11 job均success：static-quality、九个Python分片、
  offline-quality汇总。运行于07:53:07 UTC完成；没有主动重跑已通过job。
- 只读复核PR #209仍Open/Draft，base=main、mergeable=true、merged=false。
  没有合并、关闭、转Ready或变更分支保护；mergeable并不代表必需审查已满足。
- 本次仅补验收/交接文档，不改变已核验的科学制品或代码。若此记录提交触发新
  head CI，须单独回收结果；上述通过严格绑定39728e8a，不冒充后续提交的证据。

## S10 6SAM 真实闭环完成（2026-10-17，文件级构象核验）

- 方案 A 由维护者确认；浏览器选择和当前 READY 点击由维护者明确委托 Agent，
  本次 Agent 通过正常页面完成，没有构造 nonce、回放授权或终端代跑 Vina。
- 最终服务15:12:52完成831120行预热；普通聊天再次精确返回人源BCHE/P06276
  的6SAM。旧准备41d8892a在参数确认前超过180秒被拒，未执行，未扩大时限。
- 新任务`00137413-9b9b-4e01-8386-4bd9f394966c`修订2，在原准备时限内完成
  选择/参数/READY。链A，L3H A603定义25重原子口袋；13糖基保留、86水与5
  unproven组分移除；HIS214=B、HIS372=A、SER466=A，8限制均明确审阅。
- 15:21 Agent点击当次READY；真实Vina1.1.2退出0，attempt1，9构象，最佳
  −6.2 kcal/mol；demo=false/fallback=false。页面SUCCEEDED与原始文件一致。
- 页面下载`pose.pdbqt`13833字节，与原输出、发布制品完全一致，SHA256
  `69af21e29773e6d7664a6dd093f70b5b5d2e28078faa288a6bbb7b78311731af`。
- 独立文件核查通过：每pose14原子/13重原子(C9O4)，坐标有限且在口袋内；
  4361受体重原子身份/坐标完整保留，13糖基保留；cleanup=settled、执行占用0、
  view=SUCCEEDED，1 publication/0 suppression。独立只读复核未发现差异。
- 限制：reduce没有产生可用的显式氢分析受体；未验证完整质子化状态，未做相互
  作用分析、重对接RMSD或实验验证，Vina seed未指定。首页只有下载卡，未做
  3D查看器渲染验收，构象核验为真实文件/化学键长/坐标级，不冒称可视化验收。
- `793f17b9` CI run39011586874 success；此前`e97f49d6` CI亦success。
  本次新增修复及测试见下文，保留原Draft PR #209，未合并。截图、下载、运行库、
  原始结构、验证脚本均留在本机忽略目录或下载目录，没有提交。

## S10 当前浏览器来源已纳入 6SAM；会话路径修复（2026-10-17）

- `e97f49d6` 已推送，当前 head CI run39009545946 success。最终精确检索代码
  服务于14:40:15完成831120行预热；14:43普通首页实际返回人源P06276、仅6SAM。
- 同会话S10来源准备成功，任务显示6SAM和正确原始摘要、修订2.2、参考1条；但
  查看结构失败。浏览器取消请求证实401 / Browser session required。
- 根因是生产 `AgentEntrySessionMiddleware` 未包含 `/api/agent/docking/`，
  不是科学事实不合格。仅补现有会话校验前缀；共享HTTP夹具使用生产entry类，
  覆盖所有既有来源/所有权/Origin/审批/nonce边界，不放宽验证。
- 新entry用例7项先因私有路径夹具位置错误失败（不算缺陷RED），改用系统临时
  目录后7项明确因owner=None失败；修复后213 passed/2 skipped（160.99s），
  来源/控制/HTTP envelope与prepare deadline16 passed（203.04s）。编译/diff通过。
- 独立根因规格核对及质量审查批准，质量审查另12项离线检查通过。14:51:11
  服务加载该修复并重新完整预热；尚无READY、审批、Vina或下载结果。

## S10 精确 PDB 检索（2026-10-17，6SAM 真实执行待完成）

- 维护者已确认方案 A，并授权 Agent 完成浏览器科学选择及当前 READY 一次性确认，
  无需重复询问；没有绕过或制造运行时审批。
- 新增可选精确 PDB 约束，贯穿原始请求、planner、工具、权威 resolver 和 RCSB。
  精确缓存独立绑定 accession/物种/PDB，不回退宽泛结构或 AlphaFold。
- 输入歧义、所有命中实体的物种/身份及实验方法均严格校验；命名靶点和 accession
  同现时，还需与权威 gene_symbol 一致。独立规格及质量审查已批准。
- 最终相关回归 **664 passed, 1 skipped, 1 deselected / 95.54s**，compileall、
  diff check 通过；本增量无 JS 修改，没有重复执行无关 Node 或全仓套件。
- 6SAM 官方来源/受体只读检查成功：参考 L3H A603、蛋白链 A、8 条限制；
  保留蛋白/糖基/参考的重原子完整性诊断无问题。这不等于 READY 或 Vina 成功。
- 最终服务于 14:19:01 开始完整来源预热；普通聊天精确检索、科学选择、READY、
  实际 Vina/构象/下载核对仍待完成。不得用诊断结果替代真实页面验收。

## S10 官方结构来源兼容修复（2026-10-17，真实闭环待验收）

- 继续 `codex/browser-agent-real-acceptance` / Draft PR #209。修复独立编号的
  PDB REVDAT/mmCIF 修订历史、独立更新的 CCD 日期、零占有率水注释、明确缺失
  残基上的序列差异，以及无需重复 modification_feature 的显式糖基化连接。
- 原子完整清单、坐标/占有率/B 因子/电荷、CCD 原子/键、准确身份和离去原子
  仍严格核对；不修补坐标、不猜配体角色、不扩大执行或确认权限。
- 来源/投影/口袋/准备/任务门禁聚焦回归 **748 passed**；`compileall -q src scripts`
  和 `git diff --check` 通过。独立只读审查无可操作发现，另有 16 项聚焦验证。
- 受体派生/准备生命周期/Web 协调器/输入及执行上下文消费者回归 **684 passed**
  （348.26s）。兼容修复已提交并推送 `fac3e351`，不代表当前 head CI 或 S10 通过。
- 官方 1P0I/P06276 来源专用诊断现已成功（24.187s，生命周期 settled），但
  明确 `input_ready=False` / `execution_authorized=False`，不是 READY 或 Vina 证据。
- 后续真实受体检查亦成功（24.453s，两 owner 均 settled）：1 条蛋白选择、
  **0 个合格参考配体**、11 条限制。不得将 unproven 配体升级为口袋依据。
- strict health 缺省路径为 21/23（工作树未配置的反向来源/RAG），指定现有
  S10 资产路径后 23/23；没有复制或提交这些运行资产。
- 本地 6001 于 13:20:11 完成全部 831120 行预热；当前普通首页 S03 实际返回人源
  P06276。真实 S10 来源准备已越过原来的误拒绝，进入结构选择阶段。
- 另修正前端将合法 `reference_residue=0` 误报为“确认已失效”的缺陷：现在明确说明
  无合格参考配体，保持等待输入，禁用所有结构准备提交，不触发请求或执行。
  Node 49/49、父控制器集成、模板静态契约 7 项及语法检查通过；独立只读审查批准。
- 13:26 实际页面已显示上述缺参说明、原始结构摘要、`input_ready=false` 和
  `execution_authorized=false`。1P0I 仍不具备合格口袋参考；READY/Vina/构象/下载
  均未发生。后续需另选并纳入当前权威检索的合格结构，不能绕过来源选择门禁。
- 详细依据和 RED/GREEN 见
  `docs/superpowers/validation/2026-10-15-browser-agent-real-acceptance.md`。

## PR #209 普通聊天对接 D6 集成（2026-10-17）

- 分支 `codex/browser-agent-real-acceptance`；继续同一 Draft PR，不直接修改 main。
- 共享聊天 controller、两模式既有 receiver、可信默认关闭装配、内联来源/参数/
  一次性确认/结果卡已通过独立规格与质量审查。未增加 receiver 或模型权限恢复。
- 当前聚焦 controller/receiver/服务端 DOM 85 passed；app 72 passed；旧 transport/
  lifecycle 283 passed；首页 71/71，全部 44 个 Node 文件与 Python 编译通过。
- 完整离线 pytest 正在运行，尚不是最终通过证据；最后 acceptance 实际发送回执
  补丁由当前 85 项重跑覆盖，须与先启动的完整运行分别记录。strict health 23/23。
- C 默认仍关闭；真实服务、当前 S03/S10、用户亲自点击 READY、真实 pose/能量/
  下载摘要核验、最终集成审查及当前 head CI 为剩余 D7 门禁。没有新 Vina 结果，
  开发/提交/合并授权不替代运行时人工确认。
- 完整命令、RED/GREEN、审查与平台限制见
  `docs/superpowers/validation/2026-10-15-browser-agent-real-acceptance.md`。

## PR #183 恢复逻辑兼容审查（2026-10-15）

- 以当前 `main` 的 `web_request`、owner-scoped continuation CAS 和独立进程恢复契约为准；
  不引入旧分支的 detached-turn 内存重放，也不从恢复投影重建 `semantic_v1` 权限。
  semantic 模式仍在缺少安全持久化 intent journal 时拒绝跨连接恢复；运行中任务断线的取消、
  物理 worker 清理和结果投影失败语义不变。
- 前端收到当前 turn 的合法 waiting result 后立即缓存有限 trace/nonce 指针，即使后续 `complete`
  丢失仍可在重新连接或刷新后显式请求续接。当前 turn 未结束时控件仍锁定；不自动执行，
  不重放旧帧，服务端持久化状态、归属校验和一次性 CAS 仍是唯一授权来源。尚未收到合法 waiting
  result 或服务端尚未完成持久化的中断不承诺恢复；服务端拒绝时清除缓存。
- 修复重建 waiting 后下一个新聊天丢失先前普通对话历史的问题：只在正常终帧交付时恢复已验证
  历史前缀，不把未显示回答、工具结果写成普通聊天，也不覆盖新 socket 已有的更新历史。
- 保留生产路由 `register_*_routes` 与旧 `setup_*_routes` 兼容包装器分离；活动训练的行数 413、
  并发容量 429 限制和当前失败契约保留。
- 新回归覆盖丢失终帧、旧 receiver 尚未清理时续接、恢复后历史、放弃后换运行时/独立进程拒绝复活。
  测试使用离线协议夹具、真实 SQLite/ASGI/RDKit；不代表真实外部模型、HTTPS/WSS 或生产部署验收。

## 活动 worker 清理回归修复（2026-10-11，待发布）

发现并复现一个本地可重复的资源清理缺陷：活动预测超时终止子进程后，子进程管道可能先关闭，后台 waiter 已经以 `ProcessExecutionError` 完成；旧清理逻辑只等待尚未完成的 waiter，因而会留下 `Task exception was never retrieved`。修复 `_stop_activity_process()` 统一观察 waiter，并保留一秒清理上限、取消和进程关闭行为；新增事件循环异常处理器与垃圾回收回归测试，确认已完成失败被消费且不再泄漏。

- 聚焦 activity/Pharm3D/process-isolation：`37 passed`。
- 领域矩阵：靶点/数据库 `54 passed`；Docking `160 passed, 1 skipped`；ADMET、反向寻靶、活动、Pharm3D、task runtime `2222 passed, 25 skipped, 2 warnings`。
- 严格健康检查 `23/23`；Agent contract 通过；Node 三项脚本、全部非 vendor JavaScript 语法检查、`compileall` 和 `git diff --check` 通过。
- 独立只读审查：P0/P1/P2 均无发现。以上是本机/离线证据，不替代目标 Linux 服务器上的真实模型、Temporal/PostgreSQL、多 worker/重启、HTTPS/WSS、Vina 及参考 pose RMSD 验收；PDE/BuChE 根 registry 仍未激活生产 bundle。

## 继续生产验收（2026-10-11）

本轮在正确的 `MedChat` Conda 环境重新执行了真实模块验收；没有把缺失依赖、外部服务失败或未激活模型转换为成功。

- `conda run --no-capture-output -n MedChat python scripts/health_check.py --strict`：`23/23` 通过。基础 Anaconda Python 3.13 不含 RDKit，不能作为 MedChat 验收环境；这不是 MedChat 环境的结论。
- `scripts/run_agent_acceptance.py --mode real --repeat 3 --case-set all-real`：总体 `partial`，结构化报告为 `outputs/agent_evaluation/continuation-real-agent-20261011.json`。本轮本地 Ollama 分子生成、靶点搜索和真实 Vina 对接检查通过；Vina 产生了 3 个 pose。外部主模型配置/上游不可用，PDE/BuChE 根注册表没有 active family bundle，因此活性预测明确失败并返回 `family_model_bundle_unavailable_or_invalid`，没有返回 pIC50 或概率数值。
- `ADMET_AI_PYTHON` 指向独立 `admet-ai==1.4.0` worker 后，`scripts/verify_admet_ai_real.py --repeat 3`：退出码 0；3/3 次成功，2/2 行每次均返回 49 个端点，方法 `admet_ai`，模型版本 `1.4.0`，使用同一权重 SHA-256 标识。该结果是本地真实 ADMET 权重证据，不代表实验或临床验证。
- `scripts/run_temporal_docking_acceptance.py --mode contract --repeat 3`：通过，`run_count=3`；这是生命周期 contract，不是真实 Temporal/Vina 科学执行。
- `scripts/validate_temporal_deployment.py`：`partial`，Python 静态检查通过；Docker Compose、promtool 和 systemd 因当前 Windows 主机不可用而 `skipped`。目标 Linux 主机验收仍未完成。
- `scripts/run_decision_chat_acceptance.py --mode native`：`failed`，`isolated_acceptance_unavailable`；外部主模型仍不能作为可用生产端点。未记录或输出密钥。

当前仍阻断完整生产发布的证据：根注册表激活且通过部署门禁的 PDE/BuChE bundle、真实主模型端点、Temporal/PostgreSQL/systemd/cgroup、HTTPS/WSS 反向代理、多 worker/重启恢复、真实 ProLIF/MDAnalysis 相互作用几何及参考 pose 重对接 RMSD。以上缺口保持 `partial`/`unavailable`，不以本机 contract 或模拟值替代。

## 生产部署预检补充（2026-10-11）

- `python scripts/validate_opensandbox_deployment.py --static`：通过，部署资产静态检查完成。
- `python scripts/validate_opensandbox_deployment.py --runtime`：失败，`runsc_unavailable`；当前 Windows 主机没有目标 Linux OpenSandbox runtime，不能把静态通过解释成运行时通过。
- `python scripts/validate_temporal_worker_production.py --check-config`：失败，`backend_not_temporal_canary`；当前环境没有目标 Temporal canary 后端。
- `python scripts/run_temporal_production_preflight.py --output scratch/temporal-production-preflight-20261011.json`：失败，报告中的 `all_required_gates=false`；contract、real science 为 `failed`，backup/alert/deployment/infrastructure 为 `skipped`。这进一步确认目标 Linux/Temporal/PostgreSQL/代理环境仍需现场验收。

## RG-MPNN 质量搜索（2026-10-11）

本机真实 PDE/BuChE r7 搜索已完成。四个选中的 family/task 均在固定 scaffold test split 上完成两 seed 评估，当前稳定性阈值和常数基线门槛通过；但与当前 r2 候选逐指标比较仍未全部满足，因此 `deployment_gate.eligible=false`，所有 bundle 均保持 `activated=false`。r7 只能用于离线误差分析、排序实验和后续训练，不能复制到根注册表或作为生产 pIC50 结果。完整指标、报告路径和限制见[RG-MPNN r7 质量搜索交接](activity-quality-search-r7.md)；此前 r6 记录仍保留在[RG-MPNN 质量搜索交接](activity-quality-search.md)。该结果不代表生产部署、临床验证或实验验证完成。

## 当前权威状态（2026-10-10）

本节优先于下方历史批次阅读；下方内容保留用于追溯，不代表当前仍待执行。

- 2026-10-10 的 PR #182 已 squash 合并到 `main`，合并提交为 `9e668a41`：活动模型训练上传新增
  `MEDCHAT_ACTIVITY_TRAINING_MAX_ROWS` 行数上限（默认 100,000），在创建临时文件和提交后台训练前
  fail-closed 返回 413；修复了 CSV 解析器可能低估训练记录数的资源风险。相关 API 回归、活动/对接
  边界测试与 CI 9/9 均通过。已有 `data/molecular_faiss_index.index.manifest.json` 未跟踪且未被修改。
- 合并后的本机复核：默认 `scripts/health_check.py --strict` 为 23/23；同时设置
  `MEDCHAT_REQUIRE_ACTIVE_ACTIVITY_MODELS=1` 与 `MEDCHAT_REQUIRE_DURABLE_TASKS=1` 为 21/23，剩余两项
  明确是 Temporal 后端未配置和根注册表未选择 PDE/BuChE family bundle，不是代码测试失败。
- 本轮核验快照中，PR #180 合并前的 `origin/main` 为 `5983b71`；PR #180 已通过
  9/9 质量检查并 squash 合并为 `fceb203`。PR #179、PR #178 的交接记录更新和 PR #177
  的真实 PDE/BuChE 活性验收证据门禁均已通过 PR 进入 `main`，
  本次验收改动均通过 PR 进入 `main`，没有直接修改 `main`。
- Web 路由和 Agent 工具均复用 `src/molecular_design/chemistry.py`；对接适配器现在保留
  `succeeded`、`unavailable`、`timeout`、`cancelled`、`invalid_input`、`not_calculated` 等统一状态，
  不再把非成功终态降级成普通 `failed`；失败适配器只保留安全状态诊断，Agent Validator 会清除
  失败结果中的科学数据、证据和 provenance。
- PR #170 最终 CI 为 `9/9`；root 分区 `3405 passed, 105 skipped, 9 warnings`，Agent 分区
  `12685 passed, 2 skipped`。Node 合同测试、JavaScript 语法检查、`compileall`、凭据扫描和
  跨层依赖扫描均通过。
- 路由兼容层已完成审计：正式注册路径使用窄依赖，不再把整个 `api_routes` 作为生产依赖；
  目标路由的 `_support` 仅保留给旧直接调用者，且通过统一 `route_compat.lazy_dependency` 延迟解析。
- 2026-10-09 已将活性预测、活性模型、分子工具和对接报告的生产注册函数与旧兼容包装器分离；
  `api_routes.setup_api_routes()` 不再调用这四个 `setup_*_routes` 兼容入口，旧直接调用及其动态替换测试仍保留。
- 使用本机真实运行资产的严格健康检查已达到 `23/23`。模型权重、靶点数据库、缓存与 RAG 索引属于
  本地资产，不得提交或用模拟文件替代。
- 本分支 `codex/complete-remaining-architecture` 已补齐 Web 决策等待的服务端持久化续接：
  `a1_closed` 等待态写入有限请求快照，断线后同一 Agent session 可通过原子 continuation CAS
  继续；`semantic_v1` 在缺少安全 intent journal 时保持 fail-closed。新增 owner-scoped
  `GET /api/agent/runs/{trace_id}` 与 `/events` 只读审计接口，前端只把 sessionStorage 当作
  恢复指针，服务端状态仍是唯一权威来源。
- 本轮提交 `0d77f70` 收紧恢复控制边界：非法 `trace_id`/`continuation_id` 在访问持久化层前即拒绝，
  状态存储不可用时统一 fail-closed 为 `continuation_unavailable`，不泄露存储异常或错误重放任务。
- 本分支新增真实独立 Python 进程恢复证据：两个独立 worker 进程可用同一持久化 A1 waiting
  continuation 续接，已缓存工具结果不重复执行，跨进程竞争时只有一个进程完成 owner-scoped CAS；
  运行时模型替换仍产生新的进程内 epoch，防止 live replacement 接收旧请求。用户配置边界新增
  私有 `.generation` sidecar：只把随机 opaque token 写入 continuation，配置文件摘要只留在受保护
  配置边界内用于变更检测；凭据轮换会使旧 continuation fail-closed。配置与 generation 在同一
  跨进程锁内读取，避免启动时混合快照。该证据使用真实 SQLite、ASGI/WebSocket 路由和 RDKit，
  但使用协议夹具，不代表目标服务器 HTTPS/WSS 或真实模型验收。
- 后续提交 `33b1d96` 和 `108ee57` 已补齐本轮剩余代码缺口：单/批量对接输入互斥、Vina
  旧产物清除、药效团空结果 fail-closed、训练并发上限、WSS CSP、RG-MPNN 日志解析安全性，
  以及 docking 姿态几何/盒子证据门禁。真实 Vina 样例以 3 个不同 seed 完成，均生成 10 个
  pose、存在 pose 文件且几何门禁通过；能量范围为 `0.1 kcal/mol`，这只是本次运行的稳定性观察，
  不是普适科学阈值。
- 本轮真实活性验收已切换到正式 `src.activity.prediction_service`，并同时检查 PDE/BuChE
  family bundle；没有读取、写入或提交 API key。
- PR #177 的审查修复已包含在 `60634b0`：部分成功行和失败行不会再从报告中丢失；真实模型
  provenance 必须完整且与请求 family、bundle、端点、模型卡和权重摘要绑定；PDE/BuChE
  不能用另一 family 的“看起来有效”证据冒充通过。

### 当前真正未完成

1. 目标部署环境中的端到端验收：本机已用真实 PDE/BuChE 权重、靶点数据、RAG 索引和 Vina
   完成 Agent acceptance，但 Web 入口、反向代理和生产配置下的输入、结果、来源和失败状态一致性
   仍需在目标服务器验证；CI 只证明离线工程契约。
2. 运行中的任务刷新恢复和断线续接：等待用户输入的 `a1_closed` continuation 已支持服务端
   持久化和同 session 续接，并已有独立进程/SQLite/CAS 回归；仍需在目标部署环境验证真正的
   多 worker、服务重启、反向代理和 HTTPS/WSS，以及为 `semantic_v1` 建立经过审计的 intent
   journal 后再开放跨 socket 续接。
3. 部署环境验收：HTTPS/WSS、真实模型客户端、资源目录、任务取消与清理需要在目标服务器验证。
4. 对接科学增强已完成基础门禁：Vina 输出坐标、pose 质心、重原子数量、盒子来源、预处理状态和
   产物证据会进入结构化结果；相互作用几何和重对接 RMSD 仍需提供目标服务器的拓扑分析器、
   参考 pose 与真实 redocking 方案，不能用本机 3 次重复替代。
5. 历史批次正文仍保留追溯信息；它们的“待合并”描述不代表当前开放 PR。后续只需维护本节，
   不再重复创建同一批架构任务。

### 最近验收记录（2026-10-10）

- Web 决策运行时、生命周期、恢复和普通 Web 生命周期联合回归的历史证据仍保留；本轮
  `tests/agent` 全量：`12931 passed, 3 skipped, 7 warnings`；新增真实活性验收聚焦测试：
  `49 passed`；`compileall` 和 `git diff --check` 通过。
- `scripts/run_agent_acceptance.py --mode contract`：`passed`；真实 `--mode real --repeat 3`：`partial`，
  `run_count=3`、`case_count=36`。本机 Ollama `gmm-llama:latest`、靶点搜索、样例 Vina 对接通过；
  PDE 与 BuChE 均确认使用非 demo、非 fallback 的真实 RG-MPNN bundle。PDE 三个固定探针中有一条
  分类/回归一致性冲突，保持 `partial`；BuChE 三条全部 `passed`。外部主模型仍为配置失败，未包装
  成科研成功。
- 默认 `health_check.py --strict`：`23/23`；同时启用
  `MEDCHAT_REQUIRE_ACTIVE_ACTIVITY_MODELS=1` 和 `MEDCHAT_REQUIRE_DURABLE_TASKS=1` 后为 `21/23`，
  失败项明确是生产根注册表未激活 PDE/BuChE，以及本机未配置可提供重启恢复的 Temporal 后端。
- Temporal contract `repeat=3` 通过；Temporal real/生产 preflight 仍需目标服务器的 Temporal、
  HTTPS/WSS、资源目录和清理环境，不能用本机结果替代。
- 本轮未读取、写入或提交任何 API key；`data/molecular_faiss_index.index.manifest.json` 是用户已有的
  未跟踪文件，保持原样未触碰。

## 第2项发布完成／第3项开始（2026-09-25）

[PR #63](https://github.com/kxyesyes/molecular_chat_system/pull/63) 已按用户默认授权 squash 合并为
`aa86377`。合并前复核 head `d4860d7`、main目标、CI7/7、无未解决审查线程或更改请求；
合并树与审查树一致。下方本地交接的“待发布”是历史状态，不表示仍未合并；未部署或启用模型。
第3项在独立 `codex/domain-api-separation` 工作树实施：30个接口行为等价拆分，
旧九文件基线169 passed、1 skipped、11 subtests passed；本地实现已完成，双审通过。
联合5965 passed、3 skipped、7 warnings、11 subtests；本机/CI/deployment框架组合分别68 passed。
保留两份旧实现固定schema基线，未知版本明确失败，不隐藏框架差异；生产算法/资源生命周期未变。
发布及CI/合并仍待执行，不能当作第3项已交付或全8项完成。临时profile依赖目录清理被策略拒绝，
未绕过；具体路径、RED/GREEN及遗留ADMET问题见[领域API交接](domain-api-separation.md)及[第1–8项台账](remaining-through-step8.md)。

## ChatHandler 纯展示/提示拆分（2026-09-25，本地完成）

第1项 PR #62 已 squash 合并为 `6af7292`，合并树与审查 head 相同；未部署。
第2项独立分支 `codex/chat-presentation-extraction` 提取两个无状态模块，旧 helper 保留薄兼容委托。
科学正文、失败/partial、安全摘要、覆写钩子、提示预算与资源生命周期保持；不修改模型或生产入口。
独立 SPEC、QUALITY 均通过；联合回归 **5728 passed、2 skipped、7 warnings**，
最终边界补跑 **99 passed**，五个 Node 回归、JS语法及314路径内存编译通过。
跳过为 Windows 目录符号链接不可用和未启用性能测试，不能视为真实模型验收。
本地实现完成，发布/CI/合并待执行；原始混杂工作树未动。
完整 RED/GREEN、命令、范围和限制见[本批交接](chat-presentation-extraction.md)。
用户已授权按推荐方案完成第1–8项及通过门禁后的合并；[完成台账](remaining-through-step8.md)仍明确第3–8项未完成，部署不在范围内。

## main 集成验收与终态展示修复（2026-09-25，本地）

独立分支 `codex/main-integration-acceptance`，基线为已合并PR #61后的main `c2aee30`。
下方旧批次的“尚未合并”仅表示当时状态：PR #60/#61现均已合并，不等于部署。
T00–T11逐项对账及本轮失败记录见[集成验收交接](main-integration-acceptance.md)。

离线真实首页验收覆盖无效输入、普通问答、RDKit性质/类药性、候选挂载与引用、恢复和partial。
模型/生成器为显式离线夹具，不作为真实模型质量验收。发现并经用户批准TDD修复任务面板终态文字：
五种终态明确区分；规划/工具完成不再冒充整项完成。后端状态、来源、科学数值与清理机制未改。
新增六个HTTP→WS场景及一个子进程超时保护回归，共七项；外层截止时间覆盖测试收尾，独立双审通过。

联合回归 **5748 passed、7 skipped、7 warnings**；前端修改后补跑 **14 passed**，
五个Node脚本与JS语法检查通过。真实页面确认“部分完成”标签、成功数值和失败步骤完整保留。
前端独立SPEC/QUALITY复审均APPROVED。仅本地交付，未推送、创建PR、合并、部署或启用外部模型。
原始混杂工作树未动；测试服务/标签页已关闭，两处夹具临时目录因删除策略拒绝仍保留，路径见交接。
下一批可单独评估ChatHandler纯展示职责拆分，不能自动扩大到生产入口或整体重构。

## 类药性解释与评分输入修复（2026-09-25，本地）

分支 `codex/drug-likeness-evidence-fix`，基于main `4ff483d`。用户批准B及书面设计后按TDD完成：
综合评分改为消费真实违反数量和规则布尔值；去除未经支持的口服生物利用度结论，
单分子/批量报告和推理明确RDKit、启发式规则与证据边界。公式、权重、阈值、接口、失败状态未变。
新评分可能与旧记录不同；不重写历史run/checkpoint，也未启用模型。

独立SPEC/QUALITY均APPROVED；聚焦313 passed，冻结2路径联合回归
**5741 passed、7 skipped、7 warnings，405.22s**；编译/diff通过。
首次RED、评分前后对照、准确命令和限制见[本批交接](drug-likeness-evidence.md)。
仅本地提交，尚未推送/PR/合并/部署；原始工作树未动。下一步为发布审查与CI，不直接改main。

## T09 发布：Draft PR #60 与CI清单修正（2026-09-24）

已发布T09分支至 [PR #60](https://github.com/kxyesyes/molecular_chat_system/pull/60)，未合并/部署。
首轮CI五项成功，但root的首页精确脚本清单漏列本批新增引用模块；已实际复现后仅补清单，
未改业务代码或弱化门禁。聚焦回归由1 failed/6 passed转为7 passed；首轮CI失败证据保留在
[验收交接](scientific-reference-acceptance.md)。最新head/CI以PR为准，不把原始本地结果替代CI。

## T09：故障恢复与浏览器验收第三增量（2026-09-24）

独立分支 `codex/scientific-reference-continuity` 在 `212e454` 上补故障、取消、锁、
重放与来源失效测试。实际离线浏览器发现并最小修复：恢复候选被挂在隐藏聊天区；
同时更新静态脚本版本，避免缓存阻止修复生效。新增测试夹具的无期限接收问题也经TDD修复。

刷新、空闲进程重启、选中第二个、清除及新标签页隔离已实际验证。候选为显式合成夹具，
性质使用真实RDKit；未使用外部模型，不称已完成真实科研或运行中任意硬崩溃验收。
独立SPEC/QUALITY通过，冻结7路径哈希不变，联合回归 **5609 passed、7 skipped、7 warnings**；
五个Node、三个JS语法、编译与diff通过。失败记录、文件和限制见
[第三增量交接](scientific-reference-acceptance.md)。

仅本地提交，未推送/合并/部署；原始13项混杂改动不动。临时服务/标签页已停止，
一个合成测试临时目录因删除策略拒绝仍保留，详情见交接。下一步应审查发布本分支；
另批处理类药性工具将Lipinski规则过度解读为口服生物利用度的问题，不混入本批。
T09本地三增量已完成，CI/发布与整个任务书其余职责拆分仍未完成。

## T09：科研引用 Web/工具接线第二增量（2026-09-24）

独立分支 `codex/scientific-reference-continuity` 已实现权威展示投影、真实挂载后确认、
标签页指针恢复及选中 canonical SMILES 到实际工具输入；保留归属、24小时期限、
partial/warnings/provenance 和原科学校验。来源在规划期间失效时阻止分发；
多义/范围指代不默认取第一个，多个集合确认互不干扰。

独立 SPEC/QUALITY 及增量复审通过；冻结20路径前后哈希一致，最终联合回归
**5591 passed、7 skipped、7 warnings**；五个Node脚本、四个JS语法和编译检查通过。
失败尝试、跳过原因、23路径职责与命令见[本批交接](scientific-reference-web-integration.md)。
仅本地提交，未推送/合并/部署/启用模型，原始混杂工作树未动。
第三增量故障/取消/重启/重放与真实浏览器恢复验收仍待，不称整个T09完成。

## T09：科研引用契约与存储第一增量（2026-09-24）

已确认设计后按TDD实施，独立分支 `codex/scientific-reference-continuity`。
固定24小时、展示顺序ACK、会话归属、来源版本、科学Validator与原子SQLite边界已实现；
不新增表、不接入首页、不启用模型。失败尝试、准确测试和后续未完成范围见
[本批交接](scientific-reference-storage.md)。完整T09仍需Web/工具输入接线和全链路验收。

## 英文候选筛选短语（2026-09-24，本地独立分支）

`codex/target-selection-phrase-pr` 修复句尾 `and/逗号 select top N` 被误当未知靶点的问题。
只改共享解析边界，保留未知/多靶点/否定/选择性澄清和科学证据门禁。
TDD失败记录、实际测试与发布依赖见[本批交接](target-selection-phrase.md)。
PR #53 后续获明确授权，已合并为 `5f56053`；本分支已对齐并更新冲突的旧特征测试。
尚未推送、发布或部署；不能把本地通过当作已上线。最新集成结果见本批交接。

## T10-A：RAG 工具类型契约（2026-09-24）

本批分支 `codex/rag-typed-contract-pr`，基于 main `3974f96`，只迁移注册表中的
`rag_search` / `rag_database_search`。输入明确为文本；成功、partial、失败的记录和来源结构
均验证，畸形原始失败数据在规范化丢弃前被拒绝。保留来源、扩展字段、warnings、artifacts、
旧别名、默认 k=3、超时/并发和错误语义，不用 schema 投影覆盖科学数据。

实际复现并封住了非文本输入、畸形记录/metadata 和构造型 Pydantic 对象绕过；
两个旧测试仅补合规合成检索数据，保留原行为断言。
独立 SPEC / QUALITY 通过，新增 RAG 契约 **308 passed**，联合 Agent/Web/RAG
**4539 passed、8 skipped**，离线 contract **34/34**。跳过不是成功，
具体原因、全部失败尝试、文件/命令与来源见
[本批实施记录](../superpowers/plans/2026-09-24-rag-typed-contract.md)。

尚未合并或部署；PR/CI 以发布记录和远端状态为准。未调用真实外部模型、未读取生产索引，
未修改原始混杂工作树。仅完成 RAG 子批，不代表整个 T10 或任务书完成。

## 主模型默认值与本机持久化（2026-09-20）

本批 `codex/persistent-user-llm-config` 将首页默认配置改为 DeepSeek 官方接口与空 Key，
用户保存后使用仓库外、同一系统账号共享的私有配置文件。旧隐式 Key 不迁移，
分子生成 Ollama 配置不变。范围、验证及生效状态见
[本批交接](persistent-user-llm-config.md)。不直接提交或合并 main。
实现 `f7bfae7`、Draft PR #32；3365 passed/5 skipped，独立双审通过。
本机 loopback 服务已切换到该提交试运行，DeepSeek 空 Key 配置与首页 200 已核验；PR 未合并。

## 部署前隔离验收：partial，未部署（2026-09-20）

PR #30 已授权 squash 合并为 `cd041120c85e49a7de01a789430635f98a660bdc`，
合并树与审查 head `6824137` 一致；下方历史“未合并”记录保留当时状态。
本批 `codex/deployment-preflight` 只更新文档，原始混杂工作树保持原样。

Windows 聚焦回归 417 passed/84 skipped；contract 34/34、Node 30/30 与 22/22、
compileall、OpenSandbox 静态检查通过。健康检查 19/23，新工作树运行资产尚未配置齐全。
Temporal 静态计算 partial，CLI 因输出父目录 DACL 不可信而拒绝发布报告，未修改权限或安全门。
未调用外部主模型、未启用权重、未切换生产入口、未重启或部署服务。
管理面访问控制/HTTPS、目标服务器资料、原生 Linux 与真实负载验收仍待确认/完成。
详见[部署前验收与命令](deployment-preflight.md)。

## 家族预测冲突复核：实现与隔离真实复测完成（2026-09-19）

PR #29 已合并为 `86639344c472549fb9552a9eaae1682c72b86139`；下方旧记录保留当时状态。
本批分支 `codex/family-prediction-review`，核心提交 `358f542`，验收适配 `dfda3dd`。
尚未合并或部署；原始混杂工作树保持原样。

分类与回归冲突现在区分计算 `execution_status=passed` 和结果 `status=partial`，
原值/来源/警告保留，前端显示需复核，不允许伪装科研成功。
独立 SPEC/QUALITY 均通过，Agent 3269 passed/2 skipped；修订后的验收四模块
681 passed/4 skipped；10 个 Node 脚本及额外 17/17 DOM 通过。
固定真实 PDE/BuChE 权重隔离验收 42/42 工程检查通过，BuChE 七入口科学状态均 partial。
与上次 100 个预测字段（含空值）完全相同，源哈希/生产选择未变，临时资源清理完成。
未调用外部主模型、未重训或激活生产模型；不能据此声称预测性能达标。

详见[本批交接及真实报告位置](family-prediction-review.md)。[Draft PR #30](https://github.com/kxyesyes/molecular_chat_system/pull/30) 已创建，未合并；
下一步在指定 PR 审查/CI 通过并获得明确授权后再合并，不自动接管生产入口。

## 家族真实权重验收：代码完成，draft PR待合并（2026-09-19）

当前任务分支为 `codex/family-real-acceptance-integration`，独立工作树
`family-real-acceptance-integration`，基于 main `57680aa`（PR #28 已授权合并）。
用户确认方案A：本批先集成验收代码，不读取真实权重、不调用外部模型、不启用生产模型。

已批准的[设计](../superpowers/specs/2026-09-15-family-real-acceptance-design.md)与
[逐步实施计划](../superpowers/plans/2026-09-15-family-real-acceptance-integration.md)已落盘。
用户已选择子代理逐任务TDD实施。Task1–6及Task7全批独立SPEC、QUALITY均通过；家族相关11组680 passed、5 skipped，既有Agent/model/fallback联合回归3288 passed、2 skipped。[Draft PR #29](https://github.com/kxyesyes/molecular_chat_system/pull/29)已创建，e409125的Linux CI run35443974454全7项通过（Python合计9628 passed/86 skipped）；后续文档head以PR最新检查为准，尚未合并。完整证据见[本批交接](family-real-acceptance-integration.md)，不将离线工程通过当作真实权重验收。
原始混杂工作树保持原样；真实权重验收、独立报告展示残差和部署验收仍未完成。

## 隔离验收页面与 CLI（2026-09-15）

PR #26 逐轮决策 harness 已合并为 `4cef43b`；PR #27 服务端聊天桥接已获具体授权
squash 合并为 `40fcd2a`，合并树与已审查 head 一致，CI run34945834822 全7项通过。
下方旧记录保留其当时状态，不代表当前 main。

本批在独立分支 `codex/agent-isolated-lab-integration` 集成 loopback 验收页面和 CLI，
当前尚未合并。范围、运行方法和证据见
[隔离验收入口交接](isolated-decision-lab-integration.md)。
不接管生产首页，不调用真实外部模型、不读取或启用训练权重。
真实权重 opt-in 全链路、原始报告展示等历史残差、目标服务器验证仍未完成；
以 [状态台账](historical-integration-status.md) 为准。

## 历史代码集成持续任务（2026-09-12）

PR #14–#25 中已实施批次（含 #15、#20、#23、#25）均已获具体授权并合并，当前 main 为 `59e8cde`；历史功能集成尚未全部完成。
总体计划见 [历史集成计划](../superpowers/plans/2026-09-12-historical-integration-completion.md)，
当前证据/续接存储子批次见 [agent-evidence-continuation-store.md](agent-evidence-continuation-store.md)，
完整残差见 [historical-integration-status.md](historical-integration-status.md)。
PR #18 修复 Linux run-id 问题后 CI 7/7 通过并合并；详情见
[family-training-run-integration.md](family-training-run-integration.md)。前端 PR #15 在同步 #20/#23 修复后，独立复审、2695 passed/1 skipped、9Node和CI7/7通过，已合并。
决策协议 PR #19 最新 head 双审、本地回归及 CI 7/7 通过，已获具体授权合并。
PR #20 独立修复关闭测试，双审及 CI 7/7 通过，已授权合并为4e6a2c0；PR #21 经双审和 CI 7/7 通过后获授权合并。
证据隔离/续接存储 PR #22 在长文本矩阵完整分组后通过双审、联合2686 passed/1 skipped和最新CI7/7，已授权合并；首次CI失败保留。
动态会话 PR #24 已通过独立双审、联合2744 passed/1 skipped及CI7/7并获授权合并，见 [动态会话记录](agent-dynamic-session-integration.md)。
当前 [逐轮决策harness](agent-decision-harness-integration.md) 已修复规格/质量复审发现的证据与状态边界，最终独立SPEC/QUALITY通过；父联合3165 passed/1 skipped、9Node/compileall/contract通过。准备独立PR，等待最新CI与具体PR授权，未接管生产入口。
PR #23 独立修复沙盒late-create测试同步，最新联合638 passed/2 skipped及CI7/7通过，已授权合并为3988def；不声称修复旧manifest偶发问题。
ADMET完整输入修复PR #25（d9716c2）通过双审、2723 passed/1 skipped、CI run34698556963全7项；用户明确授权squash为59e8cde，整树一致，不与harness混合提交。
动态执行与调用方续接的新实现尚未进入main，隔离入口仍待下一批；修正输入保留PARTIAL与终结兜底已经实现，但不得提前宣称最终验收通过。
用户优先代码集成；不启动生产模型或部署服务。以下旧交接仅作历史依据。

## 家族双模型隔离推理（2026-09-10）

PR #12 已合并为 main `132a600`。本批记录见
[activity-family-predictor-integration.md](activity-family-predictor-integration.md)，
仅集成 Python 预测器，不接线线上入口、不启用生产模型。以下旧交接保留。

## 家族双模型成组管理（2026-09-09）

PR #11 已合并为 main `c85775a`。下一批独立集成见
[activity-family-bundle-integration.md](activity-family-bundle-integration.md)，
仅含成组注册与选择，不训练或切换线上模型。以下旧交接保留。

## 家族数据层分批集成（2026-09-09）

最新任务见 [activity-family-data-integration.md](activity-family-data-integration.md)。
PR #1 已作为旧版文档关闭；本批从已合并 PR #10 的 main 拆出家族数据层，
不启用模型，不改变线上入口。下方既有 OpenSandbox 记录保留供追溯。

# OpenSandbox docking broker handoff

## OpenSandbox runtime remediation plan finalized (2026-08-31)

Branch: `codex/opensandbox-stability-hardening`

Final plan commit: `2e0b9d5bac0a8d62e6e59f73ff6606dfff05f1a3`

The separate runtime-remediation implementation plan is now complete at
`docs/superpowers/plans/2026-08-30-opensandbox-real-stability-remediation.md`.
Independent specification and implementation-quality reviewers both returned
`APPROVED` with no remaining Critical or Important finding.

The plan closes the formal-gate contracts for root-only SQLite authority,
immutable claims and sealed evidence, actor orphan recovery, OpenSandbox/Docker
resource observation, an authority volume that survives QEMU OS-disk rewind,
static fail-closed systemd interlocks, phase-isolated output mounts, and
transactional service-start capabilities. The final start boundary uses a
root-owned guard wrapper that revalidates journal and capability state under the
lifecycle lock and directly executes a fixed, hash-attested command after
dropping to the configured service identity. The first-mutation lock remains
exclusive from capability revocation through staging, `STAGED`, `ACTIVATING`,
and durable publication of the current authorization.

This planning stage did not implement those future runtime changes and did not
run QEMU, Docker, OpenSandbox, Vina, acceptance, or a new 30-job soak. The
preserved `79860ec` QEMU report and its failures remain unchanged. The next stage
is to execute the reviewed remediation plan test-first; it must not be reported
as already implemented or promoted.

## OpenSandbox soak evaluator correction (2026-08-30)

Branch: `codex/opensandbox-stability-hardening`

Tested commit: `2d4797ec54288b5b5e28a191621f7e2892a37010`

This sub-project corrected the stability-soak evaluator without changing the
meaning of real runtime failures. A measured submission is now correlated to
the single Broker lifecycle found inside its diagnostics window by
`(trace_id, job_id)`; the caller idempotency key remains an independent worker
request key. Event order, attempt numbering, cleanup state, and terminal state
are validated strictly. A valid recovered provisioning or cleanup retry keeps
`events_complete=true`, but adds `broker_retry_observed`, so the stability gate
still fails instead of silently treating recovery as a clean run.

Validated tool provenance comes from the Broker `validation_completed` event.
Vina must agree with `ToolResult` provenance and Meeko is mandatory. Invalid or
missing clock samples fail closed; an initial clock failure is classified as
`process_failed`. Failed-run latency is bounded before report aggregation. The
report writer remains sanitized, atomic, and non-clobbering.

### Corrected offline evidence

The preserved-shape offline regression contains 30 runs:

```text
18 success
5 provider_error / server_500
5 tool_timeout / command_timeout
2 invalid_output / none
pass_rate=0.6
event_completeness_rate=1.0
overall_status=failed
```

This is an offline evaluator result, not a new real soak. It proves that the
evaluator no longer turns complete Broker lifecycles into false negatives while
retaining all 12 real failure outcomes.

The prior real QEMU evidence remains immutable:

```text
runtime_commit=79860ec175f0d3235dfd0597f248d7d52788fc76
report=/opt/medchat/molecular_chat_system/outputs/agent_evaluation/opensandbox_stability_soak-79860ec.json
sha256=c66e07c3e9ad4669a8ab703fa57e887d16b8d71b938ba8a0755725692ae3c67f
```

This sub-project did not rerun QEMU, Docker, OpenSandbox, or Vina and did not
overwrite that report.

### Verification and review

```text
focused sandbox-broker tests: 688 passed, 14 skipped
full tests/sandbox_broker: 2008 passed, 75 skipped
compileall: passed
static OpenSandbox deployment validation: passed
independent review: APPROVED
secret scan: clean
placeholder scan: clean
git diff --check: clean
git status --short: clean
```

Before the final green verification, one focused run reported `686 passed,
14 skipped, 2 failed` in a capacity-timing case and a worker-cancellation case.
The two isolated tests then passed 5/5 and 10/10, and the complete focused suite
passed on rerun. Earlier, the full service test file also reported `169 passed,
1 failed`; that timing-sensitive case passed in isolation. These are
non-stably-reproduced test-timing signals, not evidence that a deterministic
runtime defect was fixed or that runtime stability is proven.

The `OpenSandbox soak evaluator correction` section added in this sub-project
contains no API key, credential, raw environment value, user-specific absolute
path, or other sensitive runtime value. Older historical handoff sections are
outside this statement's scope.

Date: 2026-08-27

Branch: `codex/opensandbox-docking-broker`

Workspace: isolated task worktree

## Objective and current state

This stage added a docking-only OpenSandbox Broker between the Temporal worker
and AutoDock Vina. The Broker is reachable only through a protected Unix domain
socket, runs one fixed digest-pinned docking image under gVisor, denies outbound
network access, enforces CPU/memory/PID and timeout limits, validates all input,
scientific output, artifacts, hashes and provenance, and always performs bounded
sandbox cleanup. The Web process receives no OpenSandbox credential.

The implementation and the Ubuntu 24.04 QEMU pre-production deployment have a
verified functional and security baseline. This is not a production promotion:
later smoke runs exposed intermittent OpenSandbox execution latency/failures,
so rollout remains gated by both that stability issue and the existing Temporal
canary, production-host observation and backup gates documented below.

## Final stability fixes

- Acceptance now uses the production default Vina `exhaustiveness=8`. The
  previous value 32 was a stress workload that intermittently exceeded the
  fixed 267-second tool deadline on a 2-CPU gVisor sandbox and produced only
  1/3 successful measured runs. Stress testing must be a separate explicit
  profile, not the baseline availability gate.
- A non-zero sandbox command no longer automatically loses the wrapper's
  structured failure. The Broker reads only the fixed `result.json` path and
  accepts only an exact allowlisted schema plus valid phase/error-code pairs.
  Verified `tool_timeout` becomes `execution_timeout`; missing, malformed or
  impossible output remains fail-closed as `command_failed`. A verified Vina or
  Meeko failure retains the existing `command_failed` persistence/API value plus
  a safe `sandbox_tool_failed` warning marker; the new worker maps that marker to
  a provider/tool failure while an older worker remains fail-closed and can still
  read the record.
- OpenSandbox creation readiness failures are reconciled by metadata and retried
  once only after cleanup is confirmed. Acceptance observers synchronize with
  sandbox readiness, and cancellation waits long enough for bounded cleanup.
- Both failure-result reads and successful output download/validation now have
  hard deadlines and cooperative cancellation. A cancellation-resistant SDK
  coroutine is tracked after a short grace period so external sandbox cleanup
  can proceed without leaving the Broker job indefinitely non-terminal. Remote
  download is side-effect-free; local validation and artifact publication occur
  only after the bounded download completes, preventing writes after terminal.
- The destructive PID-exhaustion probe now runs after the setsid and tool-version
  probes. This prevents a deliberately exhausted sandbox from destabilizing
  subsequent security observations in the same acceptance run.

## Real QEMU evidence

The final command ran through the worker-side UDS client, Broker, OpenSandbox,
gVisor and real Vina; it did not call Vina directly and did not use demo or
fallback results.

```text
status=passed
repeat=3
pass_rate=1.0
p50_latency_ms=24412
p95_latency_ms=26142
security_failures=0
Vina=1.2.5
Meeko=0.7.1
secure_runtime=gvisor
pose_count=7,9,9
best_energy=-3.348,-3.360,-3.313
cleanup_status=succeeded,succeeded,succeeded
```

Each run produced a verified repository-relative pose artifact and SHA-256.
Warnings accurately record receptor normalization, duplicate-atom conformer
selection, and ligand hydrogen/3D preparation. The report is a local ignored
artifact at `outputs/agent_evaluation/opensandbox_docking_acceptance.json` and
is not committed.

## Post-deployment bounded smoke

After deploying the final timeout/cancellation and failure-semantics changes, a
single non-retried `repeat=1` smoke was run. Its measured scientific run passed
through gVisor and real Vina with 9 poses, best energy `-3.388`, verified
artifact/provenance, 90.924-second latency and successful cleanup. The later
idempotency probe exceeded the fixed 267-second execution deadline, was recorded
as `execution_timeout`, and also cleaned up successfully. The overall smoke is
therefore honestly `failed` with `idempotency_execution_failed`, despite a
scientific run pass rate of 1.0. No retry was used to turn this result green.

This is consistent with other late pre-production observations of intermittent
OpenSandbox command/server-proxy 500/502 responses and variable execution time.
The Broker now terminates these paths with bounded, typed failures and cleanup,
but the OpenSandbox daemon/proxy stability issue remains an explicit production
blocker rather than an application-level success.

After the final review fixes were deployed, one focused real Vina task (without
re-running the full destructive/idempotency probe suite) succeeded with 10
poses, best energy `-3.391`, a present pose artifact and successful cleanup.
There were no running `opensandbox.io/id` containers afterward. This confirms
the refactored success publication path; it does not erase the failed bounded
smoke or the remaining infrastructure stability blocker above.

## Verification

```powershell
$PY -m pytest tests\sandbox_broker -q -p no:cacheprovider
# 1247 passed, 73 skipped

$PY -m compileall -q src scripts deployment\opensandbox\run_docking.py
# passed
```

The deployment validator passed both static and runtime checks after the final
source synchronization and service restart. The formal repeat-3 acceptance
exercised same-key reuse, cooperative cancellation, worker timeout cleanup,
read-only filesystem checks, PID limits, host-data isolation, fixed tool
versions, digest pinning and denied outbound networking with no security
failure. The later bounded smoke result and its idempotency timeout are recorded
separately above and must not be conflated with that earlier passing evidence.

## Operational state and boundaries

- QEMU pre-production runtime: `/opt/medchat/molecular_chat_system`.
- Release source: `/usr/local/src/medchat-release`.
- Active services: OpenSandbox, Sandbox Broker, Temporal worker and firewall.
- Active image digest:
  `sha256:5370d55a6e667bb78c8bf5d24c3c8d797879416032c5f56b8855133e5a820509`.
- OpenSandbox and Broker secrets remain only in root-managed runtime environment
  files; no key, token or raw environment value is present in Git or reports.
- The original mixed workspace was not modified. All edits and commands used
  this isolated worktree; no reset, clean or broad staging command was used.

## Previous handoff: Temporal production canary readiness

Date: 2026-08-25

Branch: `codex/temporal-production-canary-readiness`

Task 11 starting HEAD: `73b17026609df6e14705df728791ba0a93baca6f`

Workspace: isolated task worktree

## Objective and current state

This stage hardened the docking-only Temporal canary for a native Linux +
systemd production candidate. It added evidence-gated rollout, real observation
projections, loopback monitoring, an independent single-concurrency worker,
immutable generation staging/activation, descriptor-confined PostgreSQL backup
and isolated restore verification, and a canonical production preflight.

The implementation is complete for local contract/static verification, but it is
not yet production-ready. This Windows session did not run native Linux/systemd,
Docker Compose, promtool, Temporal, PostgreSQL, or real Vina evidence. Deployment
validation therefore correctly reported `partial`; no skipped check is reported
as passed.

The repository defaults remain `MEDCHAT_TASK_BACKEND=local` and
`MEDCHAT_TEMPORAL_CANARY_PERCENT=0`. Production supports only
`0 -> 5 -> 10 -> 25`, without a force or level-skipping path.

## Changed categories and key paths

- Runtime configuration, rollout, observation, persistence, and metrics:
  `src/task_runtime/config.py`, `rollout.py`, `observation.py`,
  `prometheus_metrics.py`, `deployment_contract.py`, `secure_io.py`,
  `trusted_files.py`, and `trusted_process.py`.
- Temporal docking execution and worker safety:
  `src/task_runtime/temporal/`, `production_worker.py`,
  `scripts/run_temporal_docking_worker.py`, and
  `scripts/validate_temporal_worker_production.py`.
- Operator rollout and evidence commands:
  `scripts/manage_temporal_canary.py`, `observe_temporal_canary.py`,
  `run_temporal_docking_acceptance.py`,
  `run_temporal_production_preflight.py`, and
  `validate_temporal_deployment.py`.
- Compose, metrics, alerts, and dashboards:
  `deployment/temporal/docker-compose.yml`, its mounted bootstrap/role scripts,
  Prometheus configuration/rules/tests, Grafana provisioning/dashboard, and
  `scripts/configure_temporal_metrics_relay.py`.
- Immutable worker deployment:
  `deployment/install-temporal-worker.sh`,
  `activate-temporal-worker-generation.sh`,
  `libexec/install-temporal-worker-bundle.py`, both systemd units, trusted
  environment/directory helpers, and `temporal-worker.env.example`.
- PostgreSQL safety:
  `scripts/backup_temporal_postgres.py` and
  `scripts/restore_temporal_postgres.py` use held descriptors, atomic reports,
  trusted version-specific tools, server-major checks, and fixed verification
  database `temporal_verify`.
- Verification:
  focused tests under `tests/task_runtime/`, Temporal operator/deployment/relay
  tests, stdlib Linux trust tests, opt-in systemd/cgroup tests, and the isolated
  live-install runner source.
- Operational documentation:
  `docs/runbooks/temporal_docking_canary.md`, this handoff, deployment guidance,
  and the production-readiness specifications/plans.

The Task 11 documentation commit changes only:

- `docs/runbooks/temporal_docking_canary.md`
- `docs/PROJECT_STANDARDS.md`
- `docs/handoff/latest.md`

## Key commits

- `b56eb66` production Temporal configuration validation.
- `5d0d3f6`, `73f2c18`, `91e201b` rollout gates, observation projection, and
  atomic canary management.
- `60c3bbf`, `cb618c6`, `bbec8d6` worker metrics, release-blocker monitoring,
  and the loopback/private metrics relay.
- `b82f396` pinned Temporal/PostgreSQL/Prometheus/Grafana Compose stack.
- `82a8fc3`, `4749530`, `26ee610` immutable generation staging,
  transactional activation, and verified rollback.
- `3ecd59b`, `ad3f950`, `8ea28cb`, `a05571f` atomic backup/restore and subsequent
  descriptor/tool trust hardening.
- `39ef218`, `097f1d6`, `56ede66` production preflight and full deployment asset
  validation.
- `544890a`, `bcfbb97`, `976eb64`, `73b1702` final alert, real-evidence,
  artifact, timeout, and cancellation hardening.

## Verification run in this session

`PY` below is the requested MedChat Conda Python interpreter. Pytest cache was
disabled in every test command.

```powershell
$PY -m pytest tests\task_runtime\test_temporal_config.py tests\task_runtime\test_temporal_rollout.py tests\task_runtime\test_temporal_observation.py tests\task_runtime\test_temporal_prometheus.py tests\test_temporal_operator_scripts.py tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
# 921 passed, 92 skipped in 39.27s

$PY -m pytest tests\task_runtime tests\test_temporal_docking_routes.py -q -p no:cacheprovider
# 1515 passed, 21 skipped in 86.11s

$PY -m pytest tests\agent -q -p no:cacheprovider
# 647 passed, 1 skipped, 5 existing deprecation warnings in 12.23s

$PY -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
# 17 passed, 5 existing deprecation warnings in 11.40s

$PY scripts\run_agent_acceptance.py --mode contract --output scratch\task11-agent-contract.json
# passed, 34/34 contract cases

$PY scripts\run_temporal_docking_acceptance.py --mode contract --repeat 3 --output scratch\task11-trusted-reports\temporal-contract-repeat3.json
# passed, run_count=3, scientific_execution=false
# sha256=34c96e1e4850e7c507b9257bf8e7ec9d529f4f097d33c6408b35731e5b2a27f6

$PY scripts\validate_temporal_deployment.py --output scratch\task11-trusted-reports\temporal-deployment-validation.json
# partial (expected nonzero partial exit)
# python_static=passed
# docker_compose=skipped code=docker_unavailable
# promtool=skipped code=promtool_unavailable
# systemd_analyze=skipped code=linux_systemd_required
# sha256=c511237adf67e6bf697140b4e02bf8e684f6d4a7b50d8933cd839da9d9f876ae

$PY -m compileall -q src scripts deployment\libexec
# passed
```

The Temporal contract and deployment report canonical SHA-256 values were both
recomputed from their JSON payloads and matched the recorded values.

The first Temporal contract attempt targeted the inherited `scratch` directory
and failed closed with `report_write_failed`. Inspection showed that its Windows
DACL did not satisfy the hardened writer's trusted-parent contract. A dedicated
ignored report directory with inheritance removed and write access restricted to
the current user passed `capture_trusted_path_boundary`; the exact contract then
passed without a code change.

Agent/contract reports are local ignored artifacts and are not committed:

- `scratch/task11-agent-contract.json`
- `scratch/task11-trusted-reports/temporal-contract-repeat3.json`
- `scratch/task11-trusted-reports/temporal-deployment-validation.json`

## Evidence not run

- No real Vina acceptance was run, so this handoff records no binding energy,
  pose count, latency, or other real-science value. Nothing was fabricated from
  contract output.
- No Temporal server or production worker was started. Contract repeat 3 used
  controlled lifecycle evidence only.
- PostgreSQL backup and isolated restore were not run against a real server.
- Docker Compose config/health and promtool were unavailable.
- Native Linux/WSL was not invoked. `systemd-analyze`, systemd activation and
  migration, cgroup mixed-kill/runtime-mask checks, stdlib root trust tests, and
  the isolated live-root runner were not run.
- Production preflight was not run because real Temporal, Vina, Prometheus,
  alerts, and verified PostgreSQL restore evidence were unavailable. Running it
  here could only produce non-promotable partial/failed evidence.

## Required production-host actions

1. On a disposable native Linux production candidate, verify root-owned `go-w`
   release/Conda/deployment sources and run the trust-boundary stdlib suites.
2. Run the isolated live-install runner in its private mount namespace and stage
   the immutable worker generation without activating it. If a flat predecessor
   exists, exercise the allowlisted migration only during the later activation
   window and confirm legacy tmpfiles remains quarantined.
3. Run `systemd-analyze verify` for the Web and worker units, real activation/
   rollback/migration signal tests, mixed lifecycle tests, and the runtime-mask +
   held old `cgroup.kill` descriptor integration test on cgroup v2.
4. Provision runtime-only Compose secrets and both environment files through the
   trusted host process; set an approved positive backup retention policy.
5. As root with an empty inherited environment, render a metrics-relay
   generation from the root-only env files. With its generation override,
   stop/force-recreate only the Prometheus container without starting it so
   Compose creates the dedicated labeled bridge. Confirm Prometheus is not
   running, then activate the relay and require its pinned nginx config
   test/reload to pass. With the base Compose file and published live override,
   start/wait for healthy Temporal and its PostgreSQL/schema dependencies, then
   synchronously run `temporal-namespace` with `--rm --no-deps --no-TTY` and
   require exit code 0. Only then run
   `up -d --wait --wait-timeout 180 prometheus`; its
   remaining dependency closure supplies role-sync and PostgreSQL exporter. Do
   not use `sudo -E`, `--preserve-env`, or loosen env/secret modes.
6. Activate the staged worker generation, then require the Prometheus query
   `up{job="medchat-temporal-worker"}` to return exactly one target with value
   `1`. Only after that gate, start `temporal-ui` and `grafana` with the same base
   and live override. Run promtool config/rules/tests, dashboard, health, queue,
   and alert checks.
7. Use a trusted version-specific PostgreSQL bin directory to create a real
   atomic backup and verify restore into `temporal_verify`; confirm the passed
   `latest-verified.json` marker.
8. At canary 0, run production preflight. It must include deployment, contract
   repeat 3, real Temporal/Vina repeat 3 with verified artifacts/provenance,
   infrastructure, release-blocker alerts, and backup/restore evidence.
9. Promote only `0 -> 5 -> 10 -> 25`. At each nonzero level collect 20 real
   terminal tasks, or observe 24 hours with at least 3, before the next level.

Any missing host action keeps readiness `partial`. Rollback first routes only new
traffic to 0; it does not delete or locally replay Temporal-accepted work.

## Workspace integrity

All commands and edits in this task used the isolated task worktree. No command
was run against, and no file was changed in, the original mixed workspace.
Runtime reports remain ignored and outside the documentation commit.

## OpenSandbox stability hardening pre-soak snapshot (2026-08-30)

- Branch: `codex/opensandbox-stability-hardening`.
- Locally tested source commit: `b4c912d8d5c0562fbbaaa6e47e0e58f95f297ab6`.
- QEMU soak at this checkpoint: `not run`. The Windows host exposes a stopped WSL2 Ubuntu
  distribution, but no QEMU CLI or prepared Ubuntu 24.04 QEMU acceptance host.
  WSL2 is not an allowed substitute for this production gate.
- Runtime report at this checkpoint: not created. No failed report was replaced and no passing
  runtime status is inferred from unit tests.

### Local verification

All commands used `C:\Users\xkx52\.conda\envs\MedChat\python.exe` with the
pytest cache disabled:

```powershell
python -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
# 436 passed, 2 skipped

python -m pytest tests/sandbox_broker -q -p no:cacheprovider
# 1878 passed, 75 skipped

python -m pytest tests -q -p no:cacheprovider
# 4873 passed, 230 skipped, 9 subtests passed, 6 dependency/deprecation warnings

python -m compileall -q src scripts
# passed with no output

python scripts/validate_opensandbox_deployment.py --static
# opensandbox_deployment_validation=passed
```

The first full-repository run encountered one transient Temporal test-server
download failure and exposed a real stale Temporal worker-unit validator
contract. The download succeeded on focused retry; the validator was aligned
with the hardened Broker dependency and UDS access, after which the complete
repository passed.

Independent review of `c9eed10..0f68b3c` found no Critical issue and three
Important issues: unknown remote sandbox identity could lose cleanup
uncertainty, one raised measured run could prevent later submissions, and a
later run could replace an existing failed report. Commit `58a0cb4` added
regressions and closed all three. Reviewer re-verification found no remaining
Critical or Important issue.

### Historical pre-soak QEMU evidence state

Because the real 30-job suite had not yet run at this checkpoint, the following values were unavailable
and must remain `N/A`: pass rate, cleanup rate, event completeness, p50/p95
latency, phase p95 latency, failure-class distribution, pose-count range,
binding-energy range, Vina/Meeko runtime versions, image digest, and
artifact/hash observations. No docking energy or pose count is reported from
contract tests.

Likewise, real runtime evidence is unavailable for the idempotency sandbox
count, queue saturation count, cancellation/timeout/security probes, and final
running labelled container count. Local tests verify the contracts and failure
preservation only; they do not satisfy the production gate.

### Pre-soak production gate

On the reviewed Ubuntu 24.04 QEMU generation, operators must verify source
identity and service state, then run exactly one gated invocation of
`scripts/run_opensandbox_stability_soak.py --repeat 30`. Promotion remains
blocked until its report is `passed`, all gates are true, and an independent
Docker label query reports zero running labelled containers. A `partial`,
`failed`, `skipped`, Windows, or WSL result is non-promotable. Operators must
preserve the first failed report and must not replace it with a passing rerun.

## OpenSandbox real 30-job QEMU soak (2026-08-30)

- Branch: `codex/opensandbox-stability-hardening`.
- Active QEMU release commit: `79860ec175f0d3235dfd0597f248d7d52788fc76`.
- Ubuntu 24.04, 4 vCPU, approximately 12 GiB RAM, Docker 29.1.3 and gVisor
  `runsc release-20260817.0` were observed. The Broker, OpenSandbox, Temporal
  worker and firewall services remained active after the run.
- Linux deployment exposed two acceptance blockers before the real suite:
  five reviewed assets had Windows-CRLF hashes even though Linux Git checkouts
  use LF, and the documented `medchat-temporal` soak user did not exist and
  could not perform host-firewall validation. Commits `e3f4bde` and `79860ec`
  fix the line-ending contract and document the operator/root execution model.
- Post-run independent review found that an already-existing Windows checkout
  would not automatically rewrite otherwise unchanged assets after the new
  `.gitattributes` rules. Commit `f39bb7d` changes each of the five reviewed
  blobs with a common LF identity marker and updates their pins, forcing safe
  rematerialization on upgrade. This post-run commit passed local static/tests
  but was not deployed to, and does not change the identity of, the completed
  `79860ec` QEMU evidence.
- The first `e3f4bde` invocation failed before submitting a job with
  `deployment_validation_failed`. Its 0600 report remains preserved in the
  prior runtime generation; it was not overwritten or reclassified.
- The reviewed `79860ec` generation passed static/runtime deployment validation
  and executed one fixed 30-job suite. The root-owned 0600 runtime report is
  `outputs/agent_evaluation/opensandbox_stability_soak-79860ec.json` with
  SHA-256 `c66e07c3e9ad4669a8ab703fa57e887d16b8d71b938ba8a0755725692ae3c67f`.

The formal report is honestly `failed`:

```text
repeat=30
report_pass_rate=0.0
scientific_pose_artifacts=18/30
cleanup_rate=0.6
event_completeness_rate=0.0
p50_latency_ms=22781
p95_latency_ms=86924.55
queue_accepted=7
queue_saturated=1
running_labelled_containers=0
security_probe=true
cancellation_probe=false
timeout_probe=false
```

Eighteen measured submissions produced real gVisor/Vina artifacts with valid
SHA-256 values, 8-9 poses and best energies between `-3.286` and `-3.452`.
Twelve submissions did not produce accepted scientific output: five reported
`provider_error`, five `tool_timeout`, and two `invalid_output`. These are real
stability failures and block promotion. The queue gate also failed because only
seven requests succeeded instead of nine, and the cancellation/timeout probes
did not satisfy their contracts. The independent post-run Docker label query
reported zero running OpenSandbox containers.

The report runner also has two confirmed false-negative defects that do not
erase the twelve real failures: it correlates Broker telemetry with the caller
idempotency key instead of the Broker-generated trace ID, so all lifecycle
sequences become `events_incomplete`; and measured observers disable intrusive
version probes without sourcing the Meeko version from the Broker validation
event, so valid pose runs receive `tool_version_invalid` and
`tool_version_mismatch`. Fix these contracts with recorded-report unit tests
before any new reviewed QEMU generation. Do not rerun the `79860ec` report.

Local verification after the deployment fixes:

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
# 157 passed, 40 skipped

python -m pytest tests/sandbox_broker -q -p no:cacheprovider
# 1883 passed, 75 skipped

python -m compileall -q src scripts
# passed
```

No API key state or value is required for this stage, and no credential was
recorded in source, tests, logs, or this handoff.

## Local docking interaction geometry acceptance (2026-10-11)

- Branch: `fix/production-acceptance-hardening`.
- Implementation commits: `79e72d40` and `5fbb2ddc`; the implementation plan is
  `docs/superpowers/plans/2026-10-11-docking-interaction-geometry-local-acceptance.md`.
- The adapter now consumes ProLIF metadata through the documented
  `Fingerprint.generate(..., metadata=True)` boundary. It preserves only
  validated tool-derived distances, angles, residue-local atom indices and
  parent atom indices. Count data, PDBQT-only inputs, malformed metadata and
  unsupported analyzer APIs remain explicitly unavailable or geometry-free.
- ProLIF and MDAnalysis are not installed in the local `MedChat` environment;
  the real ProLIF smoke test is therefore skipped. No interaction distance,
  angle, affinity or docking claim is inferred from that skip.

### Reproducible local checks

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_docking_interaction_analysis.py tests/test_docking_reproducibility.py -q -p no:cacheprovider
# 106 passed, 1 skipped in 15.63s

conda run --no-capture-output -n MedChat python -m compileall -q src scripts
# passed with no output

conda run --no-capture-output -n MedChat python scripts/health_check.py --strict
# Summary: 23/23 checks passed

node tests/home_agent_task_panel_test.js
# Homepage agent task panel static and lifecycle checks passed
node tests/reverse_target_broad_recall_test.js
# reverse target broad-recall defaults checks passed
node tests/reverse_target_pagination_test.js
# reverse target pagination checks passed

# node --check over 54 non-ketcher, non-backup JavaScript files
# passed

conda run --no-capture-output -n MedChat python scripts/run_agent_acceptance.py --mode contract
# statuses: ["passed"]
```

The combined `tests/agent` long run was interrupted near completion after
unrelated failures appeared while concurrent user activity-training files were
being edited in the same worktree. It is not used as acceptance evidence for
this docking change; the focused docking/reproducibility suites and contract
acceptance above completed successfully. A new full `python -m pytest tests -q`
result is not claimed for this slice.

The local-only boundary remains: target-server HTTPS/WSS and reverse-proxy
behavior, production model assets, Temporal/systemd restart and multi-worker
recovery, real ProLIF/MDAnalysis execution, and real reference-pose redocking
RMSD still require their respective deployment assets or host. Missing
evidence remains `unavailable`/`partial`; it is not converted to a scientific
pass.

### Review follow-up

Independent review identified and the follow-up commit `48d97f5d` closed these
compatibility gaps:

- ProLIF `PiCation`/`WaterBridge` are mapped; unknown interaction names are
  surfaced and make the runtime result `unsupported_interaction_type` instead
  of being silently dropped.
- Multiple metadata occurrences preserve the legacy one-record aggregate
  `count`; validated per-occurrence geometry is retained under
  `geometry_occurrences`.
- The interaction route's early pose-export and missing-input responses now
  include `geometry_status: "unavailable"`.
- A valid geometry alias paired with an invalid alias remains usable but emits
  `invalid_geometry_metadata`.

Regression rerun:

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_docking_interaction_analysis.py tests/test_docking_reproducibility.py tests/test_api_route_boundary.py -q -p no:cacheprovider
# 219 passed, 1 skipped in 225.46s
```

## 本机验收补充（2026-10-11）

本轮只执行本机验收，不宣称目标服务器部署完成。当前分支为
`fix/production-acceptance-hardening`；用户已有的活动模型训练/搜索改动及其运行产物未被暂存或修改。

已完成的可复现检查：

```text
docking interaction/RMSD/API 边界：219 passed, 1 skipped
目标搜索、对接配置/manifest/取消、RG-MPNN 日志转换：181 passed, 7 skipped
health_check.py --strict：23/23 checks passed
run_agent_acceptance.py --mode contract：passed
compileall：passed
Node 合同测试：3/3 passed
非 ketcher/backup JavaScript node --check：passed（54 files）
```

本机外部兼容模型探针确实到达上游，但上游返回 HTTP 403（配额不足），因此不把它计为真实模型通过，
也没有改变网络白名单或绕过 DNS/传输安全边界。ProLIF/MDAnalysis、Temporal 后端、目标服务器
HTTPS/WSS、反向代理/多 worker 重启、真实参考 pose 重对接，以及根注册表激活 PDE/BuChE 仍按
`unavailable`/`partial` 记录；没有用模拟分数或模拟几何替代这些证据。全仓 `pytest tests -q`
尚未作为本轮证据声明，因为用户启动的模型搜索进程仍在运行并持续占用训练资源。

后续提交 `f8a9f1a2` 仅稳定 Windows docking 进程树回归夹具：增加真实子进程就绪标记，
再触发受控超时，保留进程树终止、输出、PID 和 marker 不存活断言；未改变生产进程管理代码。
该配置模块复核为 `10 passed, 1 skipped`，第二组目标搜索/对接/日志转换复核为
`181 passed, 7 skipped`。

用户的活动模型搜索进程自然结束后，全仓复核命令
`C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests -q -p no:cacheprovider --tb=short`
完成为 `22106 passed, 272 skipped, 10 warnings, 173 subtests passed`（3276.30 秒）。
