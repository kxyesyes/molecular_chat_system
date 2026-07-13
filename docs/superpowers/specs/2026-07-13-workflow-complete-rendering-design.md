# Workflow 完成消息渲染修复设计

## 问题

Agent 工作流可以正常完成并通过 WebSocket 发送 `complete` 帧，但首页仍持续显示输入中动画，且不展示最终结果。后端终态帧已经包含完整答案：

```json
{"type": "complete", "content": "<最终工作流结果>"}
```

当前前端只调用无参数的 `completeLastMessage()`，忽略 `content`。直接工作流不会预先发送 `stream` 帧，因此页面没有可供“完成”的助手消息，输入中动画也不会被移除。

## 修复范围

- 保持后端 WebSocket 协议不变，`complete.content` 继续作为权威最终答案。
- 首页收到 `complete` 时将 `message.content` 传给终态处理函数。
- 没有流式消息气泡时，根据最终内容创建助手消息。
- 已有流式消息气泡时，以最终内容校准显示，避免重复追加。
- 无论是否存在消息气泡，终态都清除输入中动画和工具状态。
- 同一终态内容只写入一次会话历史，重复终态帧保持幂等。

## 安全与兼容性

- 最终内容继续经过 `HomeFormatters.formatContent()` 的既有受控渲染路径。
- 不改变 `agent_event`、`agent_result`、`stream` 或 `complete` 的后端字段。
- 仍兼容传统的 `stream` 多帧加空内容 `complete` 流程。
- 不触碰分子生成、Agent 编排和科学工具执行逻辑。

## 验证

- Node 静态回归测试覆盖 `complete.content` 传递、输入中动画清理和终态内容渲染。
- 运行首页 Agent 面板测试、前端安全渲染测试和 JavaScript 语法检查。
- 运行 ChatHandler Agent WebSocket 聚焦测试，确认后端终态协议未回归。
- 实际检查“随机生成一个分子”完成后出现结果且转圈结束。
