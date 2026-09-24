# T09 第二增量：展示确认、恢复与科研输入接线

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. 按任务逐项执行 TDD；先规格审查，再质量审查。

**Goal:** 让首页真实显示并确认的候选可以在同一会话恢复，且下一轮选择实际进入现有科学工具输入。

**Architecture:** 复用第一增量的 ScientificPresentation / SQLite 三个专用接口；独立 Web 服务负责权威展示映射，独立路由负责 ACK/恢复。浏览器只持有不透明指针；Supervisor/Planner 复用现有执行器及科学校验，不将客户端 SMILES 当事实。

**Tech Stack:** Python、FastAPI、临时 SQLite、pytest、原生 JavaScript、Node 模块行为测试。

基线 `632af8b`；工作树 `scientific-reference-continuity`，分支 `codex/scientific-reference-continuity`。依据已确认的同日设计，不推送、合并、部署或调用真实模型。原始混杂工作树不动。

实施记录：实现与独立 SPEC/QUALITY 复审已完成；最终冻结整合5591 passed、7 skipped、7 warnings，20路径前后SHA-256一致。下列未勾选的早期 RED 步骤表示实现代理中断后未交付历史命令证据，并非测试缺失；不补写未经观察的失败。协调者实际复现的后续 RED/GREEN 全部见交接文档。

## Task 1：可信展示投影与受保护路由

文件：新增 `src/web/scientific_references.py`、`src/web/routes/scientific_reference_routes.py`、`tests/agent/test_scientific_reference_web.py`；必要时最小扩展已有存储接口，但不能改 schema 或弱化第一增量校验。

- [ ] RED：临时 SQLite seed 真实 CandidateSet/checkpoint；调用实际服务。未确认不能恢复；错误会话、修改顺序、来源变更统一拒绝。路由必须只取 scope 身份并在工作线程读写存储。
- [x] GREEN：`ScientificReferenceService` 从最终观察及 checkpoint 身份匹配，确定性投影与首页相同的去重/整事件容量限制。发布前必须核对展示候选与持久化来源完全匹配；无法确定映射时只保留旧展示，不发可用引用。
- [x] 接口：POST `/api/agent/workflows/references/confirm` 接收 `{trace_id,presentation_id,revision,ordered_keys}`；POST `/api/agent/workflows/references/restore` 接收 `{trace_id,presentation_id,revision}`。无客户端 owner/scientific data，严格未知字段拒绝。失败返回不透露跨会话存在性的通用错误。
- [x] 事件向后兼容：可选 `reference` 含指针及与显示候选对应的 `ordered_keys`；不改旧 CandidateSet 科学内容；partial/warnings/provenance 保留。恢复返回同一权威视图的可渲染候选及元信息，不读取其他会话最近运行。
- [x] 针对来源撤销、重复确认、24h 边界、存储异常补回归并记录实际命令。

示例门禁：`assert restore(pointer, other_session) is None`；`assert confirm(pointer, reversed(order)) is False`；成功恢复仍满足原 `expires_at`，不续期。

## Task 2：首页与 ChatHandler 薄接线

文件：`src/web/chat_handler.py`、`src/web/app.py`、`src/web/static/js/home/molecule_candidates.js`、`src/web/static/js/home/main.js`、必要的独立 `scientific_references.js` 和模板；测试 `tests/home_scientific_references_test.js` 及现有候选显示回归。

- [ ] RED：无 reference 旧事件照常显示；严格拒绝未知/损坏 reference；去重后实际顺序才可 ACK；渲染失败/ACK失败不得保存可用指针。
- [x] GREEN：注入应用已持有的 store/service；ChatHandler 发布引用失败不吞掉科学结果，不声称可续接。保留旧调用签名兼容。
- [x] 独立前端控制器在 DOM 成功挂载后确认；仅在确认成功后写 tab sessionStorage 的 trace/presentation/revision。不可保存 SMILES、密钥或 owner。
- [x] 刷新/重连通过受保护 restore 恢复，校验指针和服务端数据；异步旧 ACK/restore 不覆盖较新请求/清除操作。多个集合不自动选择最后一个，显式卡片选择或澄清。
- [x] 清除当前选择不删除科研历史；存储禁用、响应异常有清晰降级，普通聊天不被阻塞。
- [x] Node 测试跑真实控制器和实际模块逻辑，不只搜索代码字符串；新增 JS 全部 node --check。

## Task 3：选择到实际工具输入

文件：独立引用解析模块（职责保持在 Web 服务或 Agent contracts），`src/agent/supervisor.py`、`src/agent/planning/task_planner.py`、相关 ChatHandler 输入线；`tests/agent/test_scientific_reference_execution.py`。

- [ ] RED：真实 Supervisor/Planner/Session + 捕获输入的离线工具，验证选中 canonical SMILES 实际到达工具；不能只断言 metadata。
- [x] 下一轮 chat 可带待验证 pointer 和 selection（compound identity 或有界 ordinal）；服务端重新检查归属/确认/期限/来源。无明确选择时仅解析有界明确指代，多义/越界澄清。
- [x] 新 SMILES 优先，普通聊天及关闭工具不触发科研；更换靶点只继承结构，不继承旧 pIC50/结合能；不能用引用绕过 missing receptor/box 拒绝。
- [x] Supervisor 接收服务端验证的结构化引用，通过 AgentContext/Planner 明确绑定相关科学步骤，保留现有输入绑定和生成数量语义。
- [x] 幂等身份覆盖会话、view revision、候选和请求；不同选择/靶点/内容不得命中旧结果。复用已有存储 claim 机制，不新增执行循环。
- [x] 记录至少两轮聊天的真实模块测试：生成替身返回合成候选→持久化→显示事件→ACK→引用→工具捕获。离线替身不冒充科研预测。

## Task 4：审查与交接

- [x] 规格独立审查通过后做质量审查；发现缺口先补失败测试再修。
- [x] 使用第一增量既有临时环境 runner 跑新测试、全部 tests/agent 和受影响 Web 路由测试；相关 Node 回归和 compileall。
- [x] 准确记录 RED/GREEN、失败及 skipped 原因；冻结源码后跑最终联合回归。
- [x] 更新 `docs/handoff/scientific-reference-web-integration.md` 和 latest；精确暂存，仅本地提交（提交是否成功以git记录为准）。

第三增量仍负责系统性的故障/取消/重启/重放矩阵与最终整合验收；本批必要的端到端闭环不得推迟到第三增量，也不得把本批等同于 T09 全部完成。
