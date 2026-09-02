# 结构化分子渲染与无效 SMILES 失败闭环设计

## 背景与问题

用户输入无效 SMILES `CC(C)((` 时，科学工作流已经正确失败，`property_calculator` 没有返回性质；但聊天处理器随后把同一问题交给外部主模型继续回答。主模型在解释无效结构时给出了 `CC(C)(C)O`、`CC(C)O`、`CC(C)Cl` 等示例，还提到了 `MarvinSketch`。前端又从自然语言中用正则提取“疑似 SMILES”，将这些示例和普通单词标记为“工具生成”，并主动请求性质和结构图片，最终显示了与用户输入无关的分子卡片。

这不是 RDKit 伪造数据：卡片中的部分数值确实由 RDKit 对错误提取出的示例计算得到。但是它破坏了结果语义和 provenance，用户会误以为这些卡片是当前科学任务的工具产物。

## 目标

- 无效 SMILES 在科学工作流入口被可靠识别，并以结构化失败结束。
- 科学 Agent 的失败、拒绝或取消结果直接返回用户，不再交给主模型二次生成。
- 分子卡片只由成功或部分成功的真实工具结果驱动，不扫描助手自然语言。
- 只渲染通过 RDKit 校验、去重并带来源信息的候选分子。
- 保留现有 `CandidateSet@1`、`ToolResult`、RAG 卡片和 Agent 事件协议，避免建立平行契约。
- 前端不再把模型解释文本、软件名或示例 SMILES 标记为“工具生成”。

## 非目标

- 不重写 Router、Supervisor 或整个 WebSocket 协议。
- 不改变本地 Ollama `gmm-llama:latest` 的生成职责。
- 不让外部主模型承担 SMILES 有效性、性质、pIC50 或 docking 能量的科学判定。
- 不在本轮重做分子卡片视觉样式、RAG 卡片或历史消息持久化。
- 不为自然语言中的任意 SMILES 自动创建卡片；纯文本回答仍可显示 SMILES 文本。

## 已选方案

采用端到端结构化渲染契约：

1. Router/输入门禁提取用户显式提供的 SMILES，并用 RDKit 做有效性判定。
2. 无效输入返回 `INVALID_INPUT`/`invalid_smiles` 类型的结构化失败，不启动后续科学工具。
3. ChatHandler 把 Agent 的失败视为科学工作流的权威终态，直接发送失败内容。
4. ChatHandler 仅从受信任的成功 `ToolResult` 中提取 `CandidateSet@1`，发送独立的 `molecule_candidates` 消息。
5. 前端只消费 `molecule_candidates.candidate_set.candidates`，彻底停止从助手文本中猜测分子。

## 端到端数据流

### 无效输入

```text
用户输入
  -> Router/输入门禁提取显式 SMILES
  -> RDKit 判定无效
  -> AgentResult(outcome=rejected/failed, error.reason=invalid_smiles)
  -> ChatHandler 发送 agent_result + complete
  -> molecule_candidates 不发送（或为空）
  -> 前端只显示错误文本，不请求性质/图片接口
```

### 真实分子生成

```text
用户设计请求
  -> Supervisor / Workflow
  -> llm_molecular_generator（本地 Ollama）
  -> AgentResultValidator / RDKit
  -> ToolResult(data=CandidateSet@1, quality.output_contract=CandidateSet@1)
  -> ChatHandler 构造 molecule_candidates 事件
  -> 前端按 candidate_id + canonical_smiles 渲染
```

### 普通聊天或解释

```text
用户问题
  -> 主模型自然语言回答
  -> complete
  -> 不发送 molecule_candidates
  -> 即使文本含 SMILES 示例也不生成卡片
```

## 输入门禁设计

- 保留现有词法模式用于识别“可能包含结构输入”的意图，但不能把正则命中等同于有效 SMILES。
- 对科学计算请求中显式标注或提取出的 SMILES 使用 RDKit `MolFromSmiles` 校验。
- 校验失败时返回稳定错误原因 `invalid_smiles`，错误内容包含用户可理解的修正提示，但不得包含 QED、LogP、pIC50、binding energy 等结果字段。
- 无效 SMILES 不进入 Planner/工具执行；事件终态应明确为 rejected/failed，不得出现成功的 `tool_completed`。
- 普通概念问答中的化学文本不强制触发该门禁；门禁只约束已被判定为科学计算且要求消费分子输入的请求。

## Agent 失败短路

ChatHandler 按 `AgentResult` 的结构化状态处理结果：

- `success=true` 或 `partial=true`：保留现有工作流直出策略；仅从成功/部分成功的工具结果提取可信候选。
- `outcome=rejected|failed|cancelled`：发送 `agent_result` 和最终 `complete`，内容优先使用结构化错误消息、工具错误消息或 Agent 最终答案。
- 上述终态不得再次调用外部主模型润色或补答，避免模型把失败改写成看似成功的科学输出。
- 普通聊天、没有启动科学工作流的请求继续调用外部主模型，不受此短路影响。
- 失败响应必须保留 `trace_id`、`active_skill`、`status`、`warnings` 和可安全展示的错误原因。

## WebSocket 分子候选事件

新增向后兼容的消息类型：

```json
{
  "type": "molecule_candidates",
  "trace_id": "trace-id",
  "source": {
    "tool_name": "llm_molecular_generator",
    "model_name": "gmm-llama:latest",
    "status": "succeeded"
  },
  "candidate_set": {
    "version": "1",
    "requested_count": 2,
    "valid_count": 2,
    "unique_count": 2,
    "invalid_count": 0,
    "duplicate_count": 0,
    "candidates": [],
    "rejected": [],
    "status": "succeeded"
  },
  "warnings": []
}
```

约束如下：

- `candidate_set` 必须能够通过 `CandidateSet.from_dict()` 的严格恢复校验。
- 来源工具必须 `success=true`，且观察状态为 `succeeded` 或 `partial`。
- `quality.output_contract` 必须为 `CandidateSet@1`；不能仅凭 `data` 中存在 `smiles` 字段就发送。
- `source` 只包含可公开 provenance，不包含 API key、完整环境变量或敏感输入。
- `canonical_smiles` 是渲染和图片请求的唯一结构字段；不渲染 `rejected` 项。
- 工具返回部分成功时允许显示有效候选，同时显示 `invalid_count`、`duplicate_count` 和 warnings。
- 旧前端会忽略未知消息，因此协议向后兼容；新前端不再依赖从 `complete.content` 提取结构。

## 前端渲染规则

- 删除 `completeLastMessage()` 对 `detectAndRenderMolecules()` 的无条件调用。
- 移除或停用从 Markdown、反引号、`SMILES:` 标签和普通单词中扫描候选的路径。
- 新增 `molecule_candidates` 消息处理器；先验证基础 schema，再渲染候选。
- 前端使用 DOM API 和共享安全 helper 填充 candidate id、SMILES、来源和 warnings，不拼接不受信任的 `innerHTML`。
- 卡片来源标签显示真实工具或模型，例如“gmm-llama:latest · RDKit 已验证”，不再硬编码“工具生成”。
- 图片请求只针对后端已验证的 `canonical_smiles`。
- 性质只显示结构化工具结果中已经存在的字段；不得因为文本中出现一个候选字符串就在浏览器端自动触发性质计算。需要候选性质时，应由工作流显式调用 `property_calculator` 并按 candidate id 对齐。
- `rag_info` 继续走现有结构化 RAG 卡片，不经过分子生成候选事件。

## 数据流与 provenance

- `candidate_id` 是生成候选与后续性质、ADMET、活性结果对齐的主键。
- 下游结果只有通过现有 candidate alignment 校验后才能附加到对应卡片。
- 前端不根据数组位置猜测结果归属。
- ToolResult 的 `warnings`、`quality`、`provenance` 和 `artifacts` 在后端保留；UI 事件只传展示所需的脱敏子集。
- 外部主模型可以解释工作流结果，但不能创建 `molecule_candidates` 事件。

## 错误处理

- `CandidateSet.from_dict()` 失败：记录无敏感数据的服务端警告，不发送候选事件；科学结果状态保持原样。
- 候选工具失败或候选集状态为 failed/rejected/cancelled：不渲染任何卡片。
- 图片端点失败：单张卡片可显示“结构图片不可用”，但不得回退到文本扫描或替代分子。
- 属性步骤失败：保留候选结构和该步骤 warnings，不显示占位模拟值。
- WebSocket 客户端收到 malformed `molecule_candidates`：拒绝渲染并记录前端诊断日志。

## 测试策略

遵循 TDD，先建立失败测试，再修改生产代码。

### 后端单元与集成测试

- 精确提示 `请分析这个 SMILES 的成药性：CC(C)((。` 被判定为无效输入。
- 无效输入不执行性质、ADMET、活性、生成或 docking 工具。
- Agent 结构化失败不会调用主模型 `generate`/`stream_generate`。
- 失败链路发送一个最终 `complete`，并保留结构化状态和错误原因。
- 只有带 `CandidateSet@1` 契约的成功或部分成功工具结果会产生 `molecule_candidates`。
- failed/rejected candidate set、伪造 contract、重复或无效 SMILES 不产生候选事件。
- 事件中的 source 来自真实 ToolResult provenance，且不包含密钥字段。

### 前端静态与行为测试

- `completeLastMessage()` 不再调用自然语言 SMILES 检测器。
- `MarvinSketch`、`CC(C)X` 以及解释文本中的示例不会创建卡片，也不会请求性质接口。
- malformed 或 failed `molecule_candidates` 不渲染。
- 有效的 `CandidateSet@1` 只渲染唯一、已验证的 `canonical_smiles`。
- 卡片显示真实来源，不出现无条件“工具生成”标签。
- 无效 SMILES 完整交互中，性质和图片端点调用次数均为零。

### 回归验证

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_chat_handler_agent_events.py tests\agent\test_routing_prompt_matrix.py tests\agent\test_prompt_acceptance.py -q -p no:cacheprovider
node tests/frontend_safe_render_test.js
node tests/home_agent_task_panel_test.js
node --check src/web/static/js/home/main.js
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

## 兼容性与迁移

- `complete`、`agent_result`、`agent_event` 和 `rag_info` 字段保持兼容。
- `molecule_candidates` 是新增消息；未知消息类型对旧客户端无破坏。
- 新前端上线后不再扫描历史自然语言，因此旧历史消息不会自动补画分子卡片，这是有意的安全收敛。
- 若静态文件受浏览器缓存影响，按项目现有静态资源版本方式更新 query version，并在验收时执行强制刷新。

## 验收标准

- 输入 `CC(C)((` 后只显示结构化无效 SMILES 错误，不出现分子卡片、QED、LogP、pIC50 或结合能。
- 服务端日志中该请求没有对 `/api/molecule/properties` 或 `/api/utils/smiles_to_image` 的后续请求。
- 主模型不参与该科学失败链路。
- 真实分子生成任务仍可显示 RDKit 验证后的有效唯一候选，且卡片来源可追溯。
- 部分生成任务只显示有效候选，并明确报告淘汰数量和 warnings。
- 普通聊天或概念解释即使包含 SMILES 示例，也不会产生分子卡片。

## 风险与控制

- **风险：** 移除文本扫描后，过去依赖自然语言自动画图的回答不再出现卡片。
  **控制：** 这是可信渲染的必要变化；需要画图的工作流必须显式产生结构化工具结果。
- **风险：** 部分旧工具尚未输出 `CandidateSet@1`。
  **控制：** 不为旧字典做宽松猜测；逐个工具通过已有 adapter/validator 升级，失败时不渲染。
- **风险：** 前端候选与下游性质错位。
  **控制：** 只按 `candidate_id` 对齐，拒绝按数组位置合并。
- **风险：** Router 校验与工具校验重复。
  **控制：** Router 门禁负责快速拒绝用户输入，工具 validator 继续作为不可绕过的最终科学安全门。
