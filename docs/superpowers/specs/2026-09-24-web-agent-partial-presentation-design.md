# Web Agent 部分完成结果展示：最小设计

日期：2026-09-24。分支：`codex/web-agent-partial-presentation`。
基线：`ee007a1`（已合并 PR #54、#55）。用户已同意优先处理本问题；本文待书面审阅，尚未修改业务代码。

## 1. 已验证问题与边界

真实模块诊断经过 Supervisor → WorkflowRunSession → ChatHandler，仅领域工具、模型和
WebSocket 传输使用合成对象，不调用外部服务。候选排序失败时，执行层保留 failed 观察、
error 和 tool_failed 事件，聚合为 partial。Supervisor 的旧兼容 success 为 true；Web
仅按该布尔值显示“✅ 智能代理完成”，默认直出 complete 只有 type/content，正文又只含
成功步骤。失败事件并未丢失，但最终答复不能正确表达任务未全部完成。

诊断也确认新 ReAct 适配返回 success=false、status=partial；Web 必须兼容这两种布尔
投影，不能把有效的部分结果丢进全失败通道。该展示问题在 ReAct 改动前已存在。

## 2. 方案比较与选择

- 只改“完成”文案：不能解决 success=false 的 partial 被当作全失败，也不能保留最终错误状态。
- **采用 Web 边界的最小部分完成投影**：保留科学正文与现有事件，附加脱敏的失败步骤和状态。
- 改写 Agent 聚合语义或交给 LLM 重述：影响所有消费者，且可能改写客观结果，不在本批范围。

不改 Supervisor 兼容 success、Session 聚合、工具校验、排序算法、科学阈值或生产入口。
不做 T09、领域路由拆分或前端整体改版；本批不依赖 ReAct PR 合并。

## 3. 状态判定与直出行为

1. 仍先验证返回值是 Mapping 且 success 是严格 bool；畸形返回保持既有安全失败。
2. 显式 failed/rejected/cancelled 优先于 partial 标记，不能因 success=true 或 partial=true
   升级为成功。未知显式终态采用安全失败，不透传任意状态到 UI。
3. status=partial 进入部分完成通道，与 success 为 true/false 无关；无 status 的旧信封
   仅在 partial 严格为 true 时走该通道。completed 与 partial=true 的矛盾投影保守显示
   partial，不显示完整成功。没有规范状态且 partial 缺失/false 时保留现有成功/失败兼容行为。
4. 部分完成信封本身不授予额外科学可信度；候选卡片仍仅来自通过现有 CandidateSet 校验的观察。
5. partial 总是直出工具结果，不再调用主模型润色；包括配置 summarize_workflow_results=true
   和缺 workflow_plan 的兼容信封。正常全成功的总结开关及预算逻辑保持不变。
6. 正文前置“部分完成，并非全部步骤成功”，保留已完成科学正文，追加失败/部分/跳过步骤摘要。
   没有正文时仅展示未完成说明，不编造结果。没有可识别失败步骤时说明部分结果原因未提供，
   不猜测工具名称。明确禁止文案声称未执行步骤已经完成。
7. agent_result 不使用完整成功勾选文案；complete 恰好一次，正文与会话历史一致。
   不触发 legacy RAG 补救，不用 LLM 道歉替代工具结果；取消、断连及模型租约清理保持原机制。

## 4. 结构化字段与安全

partial 的 agent_result 和 complete 均附加 status=partial、partial=true、trace_id、warnings、
error（没有则 null）及 failed_steps。保持原 type/content/message 字段，旧前端仍可直接显示正文，
无需新增前端渲染器。全成功/全失败既有信封无需无关扩充。

failed_steps 为有界列表，每项仅包含 step_id、tool_name、status、message、error_code；来源优先
tool_result_sequence，缺少时兼容 tool_results，不同时合并两者造成重复。按步骤而不是工具名
区分同工具多次调用。明确的 skipped_steps 仅从既有 metadata 读取，不自行构造执行事件。
错误只投影安全 code/message，不原样透传 details、异常对象、文件路径或完整输入。

复用 ChatHandler 现有凭据识别/脱敏与警告长度上限。新状态文案、步骤名、错误及 trace_id
必须脱敏、有长度/数量界限；超过界限明确摘要被截断，不能偷偷表示错误已全部列出。
科学正文不进行数值替换或 LLM 改写；若检出凭据，则用安全提示替代该不安全正文，仍保留
partial 状态与可安全展示的元信息。不新增原始工具字典到网页或日志，不减少现有来源字段。
不在本批把全部 provenance/artifacts 再复制进 complete；现有结构化观察与事件通道继续保留。

## 5. TDD 验收

- 实际 ChatHandler + Supervisor/Session，合成候选排序失败：先复现错误完整成功文案；修复后
  partial 可见、成功正文保留、失败步骤可见、主模型调用为零，真实 tool_failed 事件保留。
- 两种 partial success 布尔投影；有/无 workflow_plan、总结开关两值；无正文及原因缺失。
- 重复工具不同 step_id、错误与 warnings、显式跳过步骤、敏感字段及超限内容的安全投影。
- 显式终态与布尔矛盾、未知 status、非布尔 success 不得走成功通道。
- 全成功、全失败、无效 SMILES、不生成伪候选、普通聊天、RAG、提示预算、断连/取消回归。
- 最终 complete 恰好一次；历史与展示一致，不改数值，不伪造工具事件，不额外调用模型。

测试优先扩展 `tests/agent/test_chat_handler_agent_events.py`，必要时把新增专项测试独立成文件，
不重排大量既有测试。先运行聚焦测试，再运行 Agent 与请求生命周期联合回归、contract、源码
编译、现有 Node 首页完成行为测试。只使用隔离环境/合成工具；不声称真实模型或浏览器验收。

## 6. 交付与非目标

预计业务修改只在 `src/web/chat_handler.py`，若局部纯投影 helper 明显过大再在计划阶段说明
最小独立模块，不能顺势拆整个 ChatHandler。本批提交独立测试、设计/计划、交接文档。
先审阅本文，再写实施计划并进入 TDD；推送、PR、合并仍按各自授权与审查门禁执行。
已合并 PR #54/#55 与未合并 ReAct 批次不算本批代码修改。
