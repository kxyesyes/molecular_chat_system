# Workflow Completion Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让首页在 Agent 工作流只发送带 `content` 的 `complete` 终态帧时展示最终答案并停止转圈。

**Architecture:** 保持 ChatHandler 的 WebSocket 终态协议不变，在首页终态处理函数中消费权威完整内容。终态函数负责创建或校准助手消息、清除输入中动画、幂等处理重复帧，并沿用既有安全格式化与历史记录流程。

**Tech Stack:** 原生 JavaScript、WebSocket、Node.js 静态回归测试、pytest

---

## File Structure

- `tests/home_agent_task_panel_test.js`：约束首页 Agent 终态帧必须传递内容并清理输入中状态。
- `src/web/static/js/home/main.js`：实现 `complete.content` 的终态渲染、流式结果校准和幂等完成。
- `tests/agent/test_chat_handler_agent_events.py`：复用既有后端回归测试，确认 `complete.content` 协议不变。

### Task 1: 建立终态消息契约回归测试

**Files:**
- Modify: `tests/home_agent_task_panel_test.js`
- Test: `tests/home_agent_task_panel_test.js`

- [ ] **Step 1: Write the failing test**

在既有静态检查中加入以下契约：

```javascript
const completionSnippets = [
  "completeLastMessage(message.content);",
  "function completeLastMessage(content)",
  "removeTypingIndicator();",
];

const missingCompletionSnippets = completionSnippets.filter(
  (snippet) => !source.includes(snippet)
);

if (missingCompletionSnippets.length) {
  console.error(
    `Missing workflow completion rendering snippets: ${missingCompletionSnippets.join(", ")}`
  );
  process.exit(1);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/home_agent_task_panel_test.js`

Expected: FAIL，至少报告缺少 `completeLastMessage(message.content);`。

- [ ] **Step 3: Commit the failing regression test**

```powershell
git add -- tests/home_agent_task_panel_test.js
git commit -m "test(web): cover direct workflow completion rendering"
```

### Task 2: 消费并渲染 complete.content

**Files:**
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/home_agent_task_panel_test.js`
- Test: `tests/frontend_safe_render_test.js`

- [ ] **Step 1: Pass terminal content into the completion handler**

将 WebSocket 分支改为：

```javascript
case "complete":
  completeLastMessage(message.content);
  clearToolStatus();
  break;
```

- [ ] **Step 2: Implement terminal reconciliation**

将终态函数签名改为 `completeLastMessage(content)`，并实现以下次序：

```javascript
removeTypingIndicator();

const hasFinalContent = typeof content === "string" && content.length > 0;
let lastMessage = document.querySelector(
  ".assistant-wrapper:last-child .message-box"
);

if (lastMessage?.classList.contains("complete")) {
  if (!hasFinalContent || lastMessage.getAttribute("data-content") === content) {
    return;
  }
  lastMessage = null;
}

if (!lastMessage && hasFinalContent) {
  appendToLastMessage(content);
  lastMessage = document.querySelector(
    ".assistant-wrapper:last-child .message-box"
  );
}
```

若流式消息已存在且终态内容不同，则通过 `HomeFormatters.formatContent(content)` 替换其受控内容，再沿用原有完成、历史写入、工具栏和分子渲染逻辑。无内容且无消息时安全返回。

- [ ] **Step 3: Run focused frontend verification**

```powershell
node tests/home_agent_task_panel_test.js
node tests/frontend_safe_render_test.js
node --check src/web/static/js/home/main.js
```

Expected: 三条命令均以退出码 0 完成。

- [ ] **Step 4: Run backend protocol regression**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests/agent/test_chat_handler_agent_events.py -q -p no:cacheprovider
```

Expected: 所有测试通过，后端仍发送带完整 `content` 的 `complete` 帧。

- [ ] **Step 5: Commit the implementation**

```powershell
git add -- src/web/static/js/home/main.js
git commit -m "fix(web): render direct workflow completion content"
```

### Task 3: 实际页面验证与范围检查

**Files:**
- Verify only: `src/web/static/js/home/main.js`

- [ ] **Step 1: Reload the homepage and submit the reproduction prompt**

输入：`随机生成一个分子`

Expected: 工作流完成后出现助手结果；输入中动画消失；Agent 面板不再阻塞最终消息。

- [ ] **Step 2: Inspect the final working tree**

Run: `git status --short`

Expected: 本任务文件均已提交；既有 `data/molecular_faiss_index.index.manifest.json` 仍未跟踪且未被修改或暂存。
