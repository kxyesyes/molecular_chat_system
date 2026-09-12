# 历史代码集成状态台账

日期：2026-09-12。核对 main `18f9dc3`（PR #14、#16、#17、#18 已获具体授权合并）；总状态 **partial**。
用户要求先完成代码集成，目标服务器验收 deferred。局部 PR 通过不代表全部集成。
实施顺序见 [总体计划](../superpowers/plans/2026-09-12-historical-integration-completion.md)。

## 按功能而非祖先提交计数

| 功能组 | 候选来源/主要文件 | 状态与下一步 |
|---|---|---|
| 平台基础、LangGraph、Temporal、OpenSandbox、靶点搜索、候选展示 | 平台快照 `a63e385` | 已集成或被替代；与 main 历史 `6daa70f` 整树相同，squash 造成祖先关系不同不等于丢代码。 |
| 性质报告与完整分子输入 | `f06492f`、`76dae50`，property/drug-likeness/molecular_input | 已集成，main 输入边界更强。 |
| 数据准备、端点注册、prepared training、家族数据/bundle/pinned predictor | `5432968`，main PR #9–#13 | 已集成且加强，不重新覆盖底层实现。 |
| 共享预测服务/API | prediction_service.py、api_routes.py | PR #14 已合并为 `370898a`，独立审查及 Linux CI 7/7 通过；本地沙盒一次失败仍保留待查。 |
| Agent 家族活性接线 | `9312bf5`，tools/activity_input.py、activity_predictor_tool.py、workflow、domain_validators | PR #16 已合并为 `becb6ab`；独立审查及 CI 7/7 通过，最新本地 Agent 1933 passed/1 skipped。输入边界、双模型证据、partial 已进入 main。 |
| 活性前端 | `5432968`，activity_prediction/main.js、results_renderer.js、template | PR #15 当前 `33eed11`；Agent/API 1998 passed/1 skipped、9 Node、compile/contract 通过。最新 CI run 34687945728 的 task-runtime 为 1548 passed/1 failed/3 skipped，关闭测试取消状态断言失败后线程未释放导致超时124；正在独立分支排查，暂停合并。原 UI 审查/浏览器证据保留。 |
| 家族训练编排 | `5432968`，scripts/train_family_activity_models.py、test_family_training_run.py | PR #18 `e37e54b` 已获具体授权 squash 为 `18f9dc3`，合并文件树一致。独立双审通过；Linux 设备名问题 6 failed → 19 passed，完整 runner 102 passed，最新 CI run 34687875759 全7项通过。未运行真实训练或激活模型。 |
| 旧 Agent 审计与恢复修复 | `9312bf5`，domain/orchestrators/supervisor/run_session/chat_handler/validators | PR #17 已合并为 `c642bae`；独立双审及 CI 7/7 通过，Agent/反幻觉/健康 1975 passed/1 skipped。旧 run 版本显示和深拷贝固定回归为非阻断待改项。 |
| 决策基础协议 | `9312bf5`，contracts/decision.py、task_requirements.py | PR #19 `b099b59` 双审通过，契约120 passed，Agent/反幻觉/健康2095 passed/1 skipped，CI run34688294504全7项通过。合入 main18f9dc3 的文档冲突按事实整合；更新 head 仍需新CI及具体授权。解析通过不等于执行授权或科研证据成立。 |
| 决策执行/证据/续接 | `9312bf5`，harness/decision_*、transport/privacy、ledger、persistence、runtime | 仍待分批移植；包含敏感输入拒绝、target 续接及证据完整性。revision 3 必须明确拒绝旧等待快照。 |
| 隔离验收入口 | `9312bf5`，web/decision_chat.py、decision_lab.py、静态 lab、run_decision 脚本 | 决策基础集成后移植，不自动接管生产首页。 |
| 真实权重全链路测试 | `9312bf5`，tests/test_activity_family_real_acceptance.py | 待随 API/Agent/UI 集成；保持显式 opt-in，不把合成前向当真实模型验收。 |
| 沙盒产物持久化稳定性 | main 既有 tests/sandbox_broker/test_service.py | 本轮扩大回归发生 1 次 artifact_failed 后无 manifest；单项及模块 172 项重跑通过，触发因素待查。保留失败证据，不放宽 fail-closed。 |

## 明确不回退的保护

- 活性旧 blocker 已在 main 关闭：target/endpoint 冲突、CSV 文本精度/ID、NUL 截断、未激活 prepared 模型混入 legacy。
- main 的 CSV 行宽、prepared 身份再验证、模型卡字段/指标一致性、特征拆分防泄漏、RDKit 兼容和完整 SMILES 解析必须保留。
- 新 Agent 的敏感澄清、续接 target、登记后 evidence/artifacts 修改三个 blocker 在源 `9312bf5` 已修复，但对应新功能尚未进入 main。不得退回较旧 `eb93c43`。
- 不整体合并滞后分支：差异中的文件删除可能只是源缺少 main 新增保护，并非应该删除。

## 原始混杂树与未完成细查

原始工作树保持 `f377443` 及既有未提交状态，未覆盖、清理或暂存。

| 残差 | 处置边界 |
|---|---|
| reporting/target_design.py、supervisor 接线和测试 | 已只读检查：旧 presenter 消费原始分子 list，未核对工具成功态就关联属性，并固定声称没有 docking；不能直接套到 CandidateSet。中文汇总与属性复用是否被新链路覆盖仍待验证，不能恢复固定科研断言。 |
| 原 chat/home/index 候选卡片协议 | 核心功能已被 CandidateSet 事件/生命周期替代，不恢复旧 complete.molecules 协议。 |
| 原 base_tool 整行解析 | 性质/类药性 execute 已走更强整结构 parser；旧 patch 只是整行快捷分支。只读探针 `CCO\nOCC\nCCN` 的 inherited extractor 仍只返回 CCO；ADMET、reverse-target、RXN 等仍有调用，应另建实际输入传递回归。PropertyCalculator 继承方法的探针不能等同其 execute 有回归。 |
| 更早工作树逐块语义与最终全链路 | 尚未全部细查；本台账不是全仓逐行审计结论。 |
| CSV、模型、索引、运行报告和历史性能文档 | 本地资产或历史证据，不读取/提交，不作为当前 main 科研性能或部署依据。 |

## 后续顺序与部署边界

1. API 批次已合并，继续完成 Agent 活性接线和 UI 的独立 PR，验证跨入口数据链路。
2. 适配家族训练编排，不激活生产模型。
3. 集成旧 Agent 恢复修复，再分层移植决策协议、证据、续接及隔离入口。
4. 核查原始专用报告等独有残差，最终整树回归并更新每项处置证据。
5. 服务器系统、硬件、HTTPS、部署方式由用户后续提供；当前无管理员令牌的管理接口契约不是公网安全证明，必须另行确认防护。历史 QEMU/Temporal/OpenSandbox 验收不替代目标宿主测试。

此台账依据独立只读残差审查与本批实际测试；未列为完成的项继续保留，不以删除旧分支代替集成。
