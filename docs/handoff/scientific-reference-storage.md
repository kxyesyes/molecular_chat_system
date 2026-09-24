# T09 科研引用：第一增量交接

本批仅实现不可变引用契约与既有 SQLite 的专用发布/确认/读取边界。分支
`codex/scientific-reference-continuity`，基线 `9f065b3`，设计提交 `68e6db8`。
用户已确认书面设计；没有改主分支、原始混杂工作树、生产数据库或模型设置。

## 行为与范围

- 创建引用前从同会话、同trace、当前step的已持久化 CandidateSet checkpoint 读取候选；调用方只能传 checkpoint/candidate 标识，不能传SMILES代替来源。
- 保留候选、来源工具/provenance/evidence/artifacts/warnings与partial状态。复用既有科学Validator重新验证结构；demo/fallback、来源不一致、坏SMILES和格式损坏不得发布。
- 服务端生成presentation UUID、revision和固定24小时期限；精确展示顺序ACK后才可恢复。确认、读取、重复发布均不续期。
- 真实SQLite事务与多store测试验证幂等发布/ACK、错误归属、来源变更/撤销、坏版本和恰好过期拒绝；写入/提交失败透传，包括提交后异常，不能回报成功。专用连接显式关闭。
- 沿用run metadata，不新增表/列。至多32候选/视图、8视图/run，namespace按实际存储JSON不超过512KiB。
- 恢复来源有额外读取界限：至多256个checkpoint、其output/error/metadata总量4MiB；run metadata 2MiB、query 512KiB。超限仅拒绝该引用功能，保留科学历史，不截断后宣称完整。
- 普通metadata/start/claim不能注入保留字段；不允许legacy upsert替换有引用的来源run。重新执行或改变状态撤销引用，continuation原CAS语义保留。无引用时legacy null metadata仍兼容。

## TDD与审查证据

所有测试为离线工程契约，使用临时SQLite和合成候选；不是外部模型生成或预测性能验收。

1. 契约RED：79 failed，缺少ScientificPresentation；GREEN 79 passed，扩展后90 passed。契约实现子代理联合相关契约/存储524 passed。
2. 存储RED：28 failed，缺少三个接口或普通metadata允许保留字段；GREEN 28 passed。
3. 扩展联合：1 failed、485 passed，复现时钟回拨后重复发布未来视图；追加损坏root/实际存储字节预算用例后6 failed、44 passed。修复后六模块686 passed（64.38s）。
4. 兼容补测：legacy null metadata的start/claim出现2 failed、4 passed；最小Mapping检查修复。扩展存储56 passed（18.23s）。
5. 独立SPEC审查发现 warnings字符串/对象会被Validator变成字符/键名列表：实际RED 2 failed、2 passed；增加规范化前list[str]检查，warning+null联合6 passed（1.42s）。
6. 契约独立SPEC、QUALITY均通过；SPEC审查者独立运行90 passed（3.72s），QUALITY为静态复审。存储SPEC复审已关闭warnings问题；最终联合质量复审和最新整合回归结果在本文件后续记录。
7. 308个src/scripts Python文件内存编译、两个既有首页Node契约和git diff --check通过。没有执行健康检查/真实验收，因为本批不改部署或运行资产、不启用真实服务。
8. 最终质量审查独立运行150项并复现总metadata超过读取2MiB界限导致“发布成功但立即ACK失败”。新增有/无既有确认视图两个用例RED 2 failed；写入前校验最终序列化metadata后GREEN 2 passed（1.45s），旧视图和run内容保留。未扩大存储上限。
9. 首次Agent+五个Web模块整合探索运行5515 passed、7 skipped、7 warnings（299.96s），exit0。该运行启动于最后warning/null兼容修复之前，不把它冒充冻结最终源码验证。随后受影响五模块591 passed（58.54s）。最终冻结源码整合运行另记。
10. 第一增量最终质量复审APPROVED，审查者独立临时SQLite运行152 passed（19.20s），总metadata预算问题已关闭；不等于全T09完成。`compileall` 在临时bytecode目录编译src/scripts成功并清理产物；首页候选脚本语法检查及两个Node契约再次通过。
11. 最终冻结整合运行：**5527 passed、7 skipped、7 warnings，285.36s，exit0**。运行前后4个生产文件及2个测试文件SHA256逐一相同。跳过分别为目录符号链接不可用1、显式关闭性能测试1、POSIX权限语义2、原任务归属测试要求独立store/已配置runtime而跳过3；均非成功。7条警告为SWIG和FastAPI on_event弃用提示，未修改全局warning策略。

第一增量离线实现与验证完成；没有PR/CI/上线声明。新增两个测试模块共152项，所有审查代理和测试进程已收齐并关闭。任务路径凭据候选扫描为0；原始工作树仍为f377443的13项既有混杂，无覆盖。

## 文件职责

- `src/agent/contracts/scientific_references.py`：不可变展示快照、严格回读、内容摘要与预遍历资源边界。
- `src/agent/persistence/scientific_references.py`：来源校验与发布/确认/读取事务；没有新执行循环。
- `src/agent/persistence/base.py`：声明三个专用存储协议。
- `src/agent/persistence/sqlite_store.py`：薄委托、保留字段保护、来源重执行/撤销时失效。
- `tests/agent/test_scientific_reference_contracts.py`、`test_scientific_reference_store.py`：新增离线契约/真实SQLite回归。
- 同日scientific-reference设计、实施计划、本交接和`docs/handoff/latest.md`：审批、范围与验证记录。没有业务入口/前端修改。

## 可复现命令

使用 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` 中的 `$runner`，只将repo替换为当前worktree；解释器为MedChat Conda Python，运行时清空非必要环境变量、临时cwd、隔离所有配置/数据库路径、禁用real/canary开关。不给测试传生产凭据，不读取.env。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_scientific_reference_store.py tests/agent/test_scientific_reference_contracts.py tests/agent/test_candidate_contracts.py tests/agent/test_agent_persistence.py tests/agent/test_decision_continuation_store.py tests/agent/test_run_session_ownership.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py
node tests/home_structured_molecule_render_test.js
node tests/home_agent_task_panel_test.js
git diff --check
```

## 尚未完成

T09第二增量仍需Web端实际显示清单投影/ACK、受认证路由、标签页sessionStorage恢复、Supervisor/Planner实际工具输入绑定。第三增量仍需两轮聊天、刷新/断线/重启、取消、歧义、改靶点和工具禁用全链路测试。

当前发布接口供受信服务端调用，不是开放给浏览器的checkpoint搜索API；正确展示清单和可信target仍需第二增量建立。不能声称用户已能在首页使用“第三个分子”。不部署、不启用模型；PR/CI/合并状态以明确发布记录为准。
