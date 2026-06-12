# Homepage Tool Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化首页右侧主内容区顶部的 5 个工具控件，让它们拥有清晰语义、统一排版和可直接使用的最小功能。

**Architecture:** 保留现有顶栏和首页主体结构，只对首页工具控件层做“语义化 DOM + 状态收口 + 最小可用面板”改造。左侧 3 个按钮作为页面控制组，右侧 2 个胶囊按钮作为辅助入口组；文件管理第一版使用 `localStorage` 持久化文件元数据，帮助入口使用轻量面板，不引入新后端依赖。

**Tech Stack:** Jinja2 模板（`index.html` 内联样式）、原生 JavaScript（`src/web/static/js/home/*.js`）、Node.js smoke tests、浏览器 `localStorage`

---

## File Structure

- `src/web/templates/index.html`
  - 将现有 `.btn_list` 和 `.top` 的纯图片控件改成语义化按钮组
  - 增加文件管理抽屉、帮助面板、遮罩层和隐藏文件输入
  - 增加工具层、按钮、面板、tooltip、响应式样式
- `src/web/static/js/home/config.js`
  - 增加 `sidebarCollapsed` 与 `managedFiles` 存储键
- `src/web/static/js/home/state.js`
  - 增加工具控件状态、文件元数据、DOM 引用缓存
- `src/web/static/js/home/main.js`
  - 初始化 5 个工具控件
  - 实现侧栏折叠/展开、新建对话、清空当前会话、文件管理、帮助面板
  - 维护工具按钮状态、tooltip 文案和面板开关
- `tests/ui/homepage_tool_controls_smoke.test.js`
  - 静态 smoke test，验证模板和脚本是否具备语义化结构、关键函数和存储键

### Task 1: 语义化首页工具控件 DOM

**Files:**
- Create: `tests/ui/homepage_tool_controls_smoke.test.js`
- Modify: `src/web/templates/index.html`

- [ ] **Step 1: 写首页工具控件的失败 smoke test**

```js
const fs = require("fs");
const assert = require("assert");

const html = fs.readFileSync("src/web/templates/index.html", "utf8");

[
  'id="toolbarControls"',
  'class="tool-rail"',
  'id="sidebarToggleBtn"',
  'id="newChatBtn"',
  'id="clearChatBtn"',
  'id="fileManagerBtn"',
  'id="helpBtn"',
  'id="fileManagerPanel"',
  'id="helpPanel"',
  'id="toolPanelsOverlay"',
  'id="managedFileInput"',
  'id="managedFileList"',
].forEach((token) => {
  assert(html.includes(token), `Missing homepage toolbar token: ${token}`);
});

console.log("homepage_tool_controls_smoke: structural tokens present");
```

- [ ] **Step 2: 运行测试，确认它先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing homepage toolbar token: id="sidebarToggleBtn"` or another missing token from the current homepage template.

- [ ] **Step 3: 把首页 5 个纯图片控件改成语义化按钮和面板骨架**

将 `src/web/templates/index.html` 里现有这段：

```html
<div class="btn_list">
  <img src="/static/images/l11.png" alt="" />
  <img src="/static/images/l12.png" alt="" />
  <img src="/static/images/l13.png" alt="" />
</div>
<div class="top">
  <img src="/static/images/r2.png" alt="" />
  <img src="/static/images/r3.png" alt="" />
</div>
```

替换成下面这段完整结构：

```html
<div id="toolbarControls" class="homepage-toolbar" aria-label="首页工具控制">
  <div class="tool-rail" role="toolbar" aria-label="页面控制">
    <button
      id="sidebarToggleBtn"
      class="tool-icon-btn"
      type="button"
      aria-label="收起导航"
      title="收起导航"
      data-tooltip="收起导航"
    >
      <img src="/static/images/l11.png" alt="" />
    </button>
    <button
      id="newChatBtn"
      class="tool-icon-btn"
      type="button"
      aria-label="新建对话"
      title="新建对话"
      data-tooltip="新建对话"
    >
      <img src="/static/images/l12.png" alt="" />
    </button>
    <button
      id="clearChatBtn"
      class="tool-icon-btn tool-icon-btn--danger"
      type="button"
      aria-label="清空当前会话"
      title="清空当前会话"
      data-tooltip="清空当前会话"
    >
      <img src="/static/images/l13.png" alt="" />
    </button>
  </div>

  <div class="toolbar-pills" role="toolbar" aria-label="辅助入口">
    <button
      id="fileManagerBtn"
      class="tool-pill-btn tool-pill-btn--primary"
      type="button"
      aria-controls="fileManagerPanel"
      aria-expanded="false"
    >
      <img src="/static/images/r2.png" alt="" />
      <span>文件管理</span>
    </button>
    <button
      id="helpBtn"
      class="tool-pill-btn"
      type="button"
      aria-controls="helpPanel"
      aria-expanded="false"
    >
      <img src="/static/images/r3.png" alt="" />
      <span>帮助</span>
    </button>
  </div>
</div>

<div id="toolPanelsOverlay" class="tool-panels-overlay" hidden></div>

<aside
  id="fileManagerPanel"
  class="tool-panel tool-panel--drawer"
  aria-labelledby="fileManagerTitle"
  hidden
>
  <div class="tool-panel__header">
    <div>
      <h3 id="fileManagerTitle">文件管理</h3>
      <p>管理首页已选择的文档文件。</p>
    </div>
    <button
      id="closeFileManagerBtn"
      class="tool-panel__close"
      type="button"
      aria-label="关闭文件管理"
    >
      ×
    </button>
  </div>

  <input id="managedFileInput" type="file" multiple hidden />

  <div id="managedFileDropzone" class="file-manager-dropzone" tabindex="0">
    <strong>添加文件</strong>
    <span>点击选择文件，或将文件拖放到这里。</span>
  </div>

  <div class="file-manager-actions">
    <button id="managedFilePickBtn" class="tool-pill-btn tool-pill-btn--primary" type="button">
      选择文件
    </button>
    <button id="managedFileClearBtn" class="tool-pill-btn" type="button">
      清空列表
    </button>
  </div>

  <ul id="managedFileList" class="file-manager-list"></ul>
</aside>

<section
  id="helpPanel"
  class="tool-panel tool-panel--popover"
  aria-labelledby="helpPanelTitle"
  hidden
>
  <div class="tool-panel__header">
    <h3 id="helpPanelTitle">首页帮助</h3>
    <button
      id="closeHelpBtn"
      class="tool-panel__close"
      type="button"
      aria-label="关闭帮助"
    >
      ×
    </button>
  </div>
  <ul class="help-panel-list">
    <li>在输入框中直接输入问题或任务即可开始对话。</li>
    <li>中间快捷卡片适合快速进入典型分析或生成任务。</li>
    <li>“文件管理”可添加、查看和移除当前首页可用文档文件。</li>
    <li>“RAG 检索”适合结合文档检索，“智能工具”适合启用页面内置工具链。</li>
  </ul>
</section>
```

- [ ] **Step 4: 重新运行 smoke test，确认 DOM 骨架已齐**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS and prints `homepage_tool_controls_smoke: structural tokens present`

- [ ] **Step 5: 创建本地 checkpoint（当前目录不是 git 仓库）**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-tool-controls-task-1 | Out-Null
Copy-Item tests\ui\homepage_tool_controls_smoke.test.js,src\web\templates\index.html scratch\checkpoints\homepage-tool-controls-task-1 -Force
```

### Task 2: 收口工具控件配置与状态

**Files:**
- Modify: `src/web/static/js/home/config.js`
- Modify: `src/web/static/js/home/state.js`
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 扩展 smoke test，让它先对配置和状态落点报错**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 追加下面这段：

```js
const config = fs.readFileSync("src/web/static/js/home/config.js", "utf8");
const state = fs.readFileSync("src/web/static/js/home/state.js", "utf8");

[
  'sidebarCollapsed: "medchatSidebarCollapsed"',
  'managedFiles: "medchatManagedFiles"',
].forEach((token) => {
  assert(config.includes(token), `Missing home config token: ${token}`);
});

[
  "sidebarCollapsed: false",
  "managedFiles: []",
  "sidebarToggleBtn: null",
  "newChatBtn: null",
  "clearChatBtn: null",
  "fileManagerBtn: null",
  "helpBtn: null",
  "fileManagerPanel: null",
  "helpPanel: null",
  "toolPanelsOverlay: null",
  "managedFileInput: null",
  "managedFileList: null",
].forEach((token) => {
  assert(state.includes(token), `Missing home state token: ${token}`);
});
```

- [ ] **Step 2: 运行 smoke test，确认配置/状态断言先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing home config token: sidebarCollapsed: "medchatSidebarCollapsed"` or another newly added state/config token.

- [ ] **Step 3: 在配置、状态和初始化阶段增加工具控件所需字段**

把 `src/web/static/js/home/config.js` 改成：

```js
"use strict";

window.HomeConfig = {
  themes: ["rose", "amber", "mint"],
  storageKeys: {
    theme: "medchatHomeTheme",
    advancedOptions: "medchatAdvancedOptions",
    sidebarCollapsed: "medchatSidebarCollapsed",
    managedFiles: "medchatManagedFiles",
  },
  websocket: {
    reconnectLimit: 5,
    connectTimeoutMs: 10000,
    maxBackoffMs: 30000,
  },
  advancedDefaults: {
    ragCount: 5,
    temperature: 0.7,
    moleculeCount: 5,
  },
};
```

把 `src/web/static/js/home/state.js` 改成：

```js
"use strict";

window.HomeState = {
  ws: null,
  isConnected: false,
  chatMode: false,
  ragEnabled: false,
  toolsEnabled: true,
  sidebarCollapsed: false,
  managedFiles: [],
  currentMessages: [],
  reconnectAttempts: 0,
  elements: {
    input: null,
    sendBtn: null,
    ragToggle: null,
    toolsToggle: null,
    themeButtons: null,
    connectionStatus: null,
    chatContainer: null,
    welcomeContent: null,
    modelSelect: null,
    quickActions: null,
    toolbarControls: null,
    sidebarToggleBtn: null,
    newChatBtn: null,
    clearChatBtn: null,
    fileManagerBtn: null,
    helpBtn: null,
    fileManagerPanel: null,
    helpPanel: null,
    toolPanelsOverlay: null,
    closeFileManagerBtn: null,
    closeHelpBtn: null,
    managedFileInput: null,
    managedFileDropzone: null,
    managedFilePickBtn: null,
    managedFileClearBtn: null,
    managedFileList: null,
  },
};
```

在 `src/web/static/js/home/main.js` 的 `init()` 里补 DOM 缓存与初始读取：

```js
elements.toolbarControls = document.getElementById("toolbarControls");
elements.sidebarToggleBtn = document.getElementById("sidebarToggleBtn");
elements.newChatBtn = document.getElementById("newChatBtn");
elements.clearChatBtn = document.getElementById("clearChatBtn");
elements.fileManagerBtn = document.getElementById("fileManagerBtn");
elements.helpBtn = document.getElementById("helpBtn");
elements.fileManagerPanel = document.getElementById("fileManagerPanel");
elements.helpPanel = document.getElementById("helpPanel");
elements.toolPanelsOverlay = document.getElementById("toolPanelsOverlay");
elements.closeFileManagerBtn = document.getElementById("closeFileManagerBtn");
elements.closeHelpBtn = document.getElementById("closeHelpBtn");
elements.managedFileInput = document.getElementById("managedFileInput");
elements.managedFileDropzone = document.getElementById("managedFileDropzone");
elements.managedFilePickBtn = document.getElementById("managedFilePickBtn");
elements.managedFileClearBtn = document.getElementById("managedFileClearBtn");
elements.managedFileList = document.getElementById("managedFileList");

HomeState.sidebarCollapsed =
  localStorage.getItem(HomeConfig.storageKeys.sidebarCollapsed) === "true";

try {
  HomeState.managedFiles = JSON.parse(
    localStorage.getItem(HomeConfig.storageKeys.managedFiles) || "[]"
  );
} catch (error) {
  console.warn("Failed to restore managed files:", error);
  HomeState.managedFiles = [];
}
```

- [ ] **Step 4: 重新运行 smoke test 和语法检查**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS and keeps printing `homepage_tool_controls_smoke: structural tokens present`

Run: `node --check src/web/static/js/home/config.js`  
Expected: exits with code `0`

Run: `node --check src/web/static/js/home/state.js`  
Expected: exits with code `0`

- [ ] **Step 5: 创建本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-tool-controls-task-2 | Out-Null
Copy-Item src\web\static\js\home\config.js,src\web\static\js\home\state.js,src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-tool-controls-task-2 -Force
```

### Task 3: 实现左侧 3 个页面控制按钮

**Files:**
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 扩展 smoke test，让它先对左侧控制函数和事件绑定报错**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 追加下面这段：

```js
const main = fs.readFileSync("src/web/static/js/home/main.js", "utf8");

[
  "function setSidebarCollapsed(collapsed)",
  "function toggleSidebar()",
  "function resetToHomeState()",
  "function clearCurrentConversation()",
  "function updateToolbarControlStates()",
  'elements.sidebarToggleBtn.addEventListener("click", toggleSidebar)',
  'elements.newChatBtn.addEventListener("click", resetToHomeState)',
  'elements.clearChatBtn.addEventListener("click", clearCurrentConversation)',
].forEach((token) => {
  assert(main.includes(token), `Missing left toolbar behavior token: ${token}`);
});
```

- [ ] **Step 2: 运行 smoke test，确认左侧行为断言先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing left toolbar behavior token: function setSidebarCollapsed(collapsed)` or another newly added token.

- [ ] **Step 3: 在 `home/main.js` 里实现左侧按钮功能**

把下面这组函数加入 `src/web/static/js/home/main.js`，并在 `bindEvents()` 内绑定按钮事件：

```js
function bindToolbarEvents() {
  if (elements.sidebarToggleBtn) {
    elements.sidebarToggleBtn.addEventListener("click", toggleSidebar);
  }

  if (elements.newChatBtn) {
    elements.newChatBtn.addEventListener("click", resetToHomeState);
  }

  if (elements.clearChatBtn) {
    elements.clearChatBtn.addEventListener("click", clearCurrentConversation);
  }
}

function toggleSidebar() {
  setSidebarCollapsed(!HomeState.sidebarCollapsed);
}

function setSidebarCollapsed(collapsed) {
  const leftPanel = document.querySelector(".index_con .left");
  const root = document.querySelector(".index_con");
  const tooltip = collapsed ? "展开导航" : "收起导航";

  HomeState.sidebarCollapsed = collapsed;
  localStorage.setItem(
    HomeConfig.storageKeys.sidebarCollapsed,
    String(collapsed)
  );

  if (leftPanel) {
    leftPanel.classList.toggle("is-collapsed", collapsed);
  }

  if (root) {
    root.classList.toggle("sidebar-collapsed", collapsed);
  }

  if (elements.sidebarToggleBtn) {
    elements.sidebarToggleBtn.setAttribute("aria-label", tooltip);
    elements.sidebarToggleBtn.setAttribute("title", tooltip);
    elements.sidebarToggleBtn.setAttribute("data-tooltip", tooltip);
  }
}

function resetToHomeState() {
  const rightPanel = document.querySelector(".index_con .right");
  const mass = document.querySelector(".mass");
  const iconList = document.querySelector(".icon_list");

  currentMessages = [];
  HomeState.currentMessages = [];
  chatMode = false;
  HomeState.chatMode = false;

  if (elements.input) {
    elements.input.value = "";
  }

  if (elements.chatContainer) {
    elements.chatContainer.innerHTML = "";
    elements.chatContainer.style.display = "none";
  }

  if (mass) {
    mass.style.display = "";
  }

  if (iconList) {
    iconList.style.display = "";
  }

  if (rightPanel) {
    rightPanel.classList.remove("chat-mode");
  }

  closeToolPanels();
  updateToolbarControlStates();
}

function clearCurrentConversation() {
  const hasMessages =
    HomeState.currentMessages.length > 0 ||
    (elements.chatContainer && elements.chatContainer.children.length > 0);

  if (!hasMessages) {
    return;
  }

  if (!window.confirm("确认清空当前会话？")) {
    return;
  }

  currentMessages = [];
  HomeState.currentMessages = [];

  if (elements.chatContainer) {
    elements.chatContainer.innerHTML = "";
  }

  updateToolbarControlStates();
}

function updateToolbarControlStates() {
  const hasMessages =
    HomeState.currentMessages.length > 0 ||
    (elements.chatContainer && elements.chatContainer.children.length > 0);

  if (elements.clearChatBtn) {
    elements.clearChatBtn.disabled = !hasMessages;
    elements.clearChatBtn.classList.toggle("is-disabled", !hasMessages);
  }
}
```

然后在 `init()` 的结尾补上：

```js
bindToolbarEvents();
setSidebarCollapsed(HomeState.sidebarCollapsed);
updateToolbarControlStates();
```

- [ ] **Step 4: 重新运行 smoke test 和主脚本语法检查**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

Run: `node --check src/web/static/js/home/main.js`  
Expected: exits with code `0`

- [ ] **Step 5: 创建本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-tool-controls-task-3 | Out-Null
Copy-Item src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-tool-controls-task-3 -Force
```

### Task 4: 实现右侧文件管理与帮助面板

**Files:**
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 扩展 smoke test，让它先对右侧面板行为报错**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 追加下面这段：

```js
const mainSource = fs.readFileSync("src/web/static/js/home/main.js", "utf8");

[
  "function openFileManagerPanel()",
  "function toggleHelpPanel()",
  "function closeToolPanels()",
  "function renderManagedFiles()",
  "function persistManagedFiles()",
  "function handleManagedFilesSelected(event)",
  'elements.fileManagerBtn.addEventListener("click", openFileManagerPanel)',
  'elements.helpBtn.addEventListener("click", toggleHelpPanel)',
].forEach((token) => {
  assert(
    mainSource.includes(token),
    `Missing right toolbar behavior token: ${token}`
  );
});
```

- [ ] **Step 2: 运行 smoke test，确认右侧面板行为先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing right toolbar behavior token: function openFileManagerPanel()` or another newly added token.

- [ ] **Step 3: 在 `home/main.js` 里实现右侧按钮和面板逻辑**

把下面这组函数加入 `src/web/static/js/home/main.js`：

```js
function bindToolPanelEvents() {
  if (elements.fileManagerBtn) {
    elements.fileManagerBtn.addEventListener("click", openFileManagerPanel);
  }

  if (elements.helpBtn) {
    elements.helpBtn.addEventListener("click", toggleHelpPanel);
  }

  if (elements.closeFileManagerBtn) {
    elements.closeFileManagerBtn.addEventListener("click", closeToolPanels);
  }

  if (elements.closeHelpBtn) {
    elements.closeHelpBtn.addEventListener("click", closeToolPanels);
  }

  if (elements.toolPanelsOverlay) {
    elements.toolPanelsOverlay.addEventListener("click", closeToolPanels);
  }

  if (elements.managedFilePickBtn) {
    elements.managedFilePickBtn.addEventListener("click", function () {
      elements.managedFileInput.click();
    });
  }

  if (elements.managedFileInput) {
    elements.managedFileInput.addEventListener("change", handleManagedFilesSelected);
  }

  if (elements.managedFileClearBtn) {
    elements.managedFileClearBtn.addEventListener("click", clearManagedFiles);
  }
}

function openFileManagerPanel() {
  if (elements.helpPanel) {
    elements.helpPanel.hidden = true;
    elements.helpBtn.setAttribute("aria-expanded", "false");
  }

  if (elements.toolPanelsOverlay) {
    elements.toolPanelsOverlay.hidden = false;
  }

  if (elements.fileManagerPanel) {
    elements.fileManagerPanel.hidden = false;
    elements.fileManagerPanel.classList.add("is-open");
  }

  if (elements.fileManagerBtn) {
    elements.fileManagerBtn.setAttribute("aria-expanded", "true");
  }

  renderManagedFiles();
}

function toggleHelpPanel() {
  const nextOpen = elements.helpPanel && elements.helpPanel.hidden;

  closeToolPanels();

  if (!nextOpen) {
    return;
  }

  if (elements.toolPanelsOverlay) {
    elements.toolPanelsOverlay.hidden = false;
  }

  if (elements.helpPanel) {
    elements.helpPanel.hidden = false;
    elements.helpPanel.classList.add("is-open");
  }

  if (elements.helpBtn) {
    elements.helpBtn.setAttribute("aria-expanded", "true");
  }
}

function closeToolPanels() {
  if (elements.toolPanelsOverlay) {
    elements.toolPanelsOverlay.hidden = true;
  }

  if (elements.fileManagerPanel) {
    elements.fileManagerPanel.hidden = true;
    elements.fileManagerPanel.classList.remove("is-open");
  }

  if (elements.helpPanel) {
    elements.helpPanel.hidden = true;
    elements.helpPanel.classList.remove("is-open");
  }

  if (elements.fileManagerBtn) {
    elements.fileManagerBtn.setAttribute("aria-expanded", "false");
  }

  if (elements.helpBtn) {
    elements.helpBtn.setAttribute("aria-expanded", "false");
  }
}

function handleManagedFilesSelected(event) {
  const incomingFiles = Array.from(event.target.files || []).map(function (file) {
    return {
      id: [file.name, file.size, file.lastModified].join("-"),
      name: file.name,
      size: file.size,
      lastModified: file.lastModified,
    };
  });

  if (!incomingFiles.length) {
    return;
  }

  const existingIds = new Set(HomeState.managedFiles.map((file) => file.id));
  incomingFiles.forEach(function (file) {
    if (!existingIds.has(file.id)) {
      HomeState.managedFiles.push(file);
    }
  });

  persistManagedFiles();
  renderManagedFiles();
  event.target.value = "";
}

function clearManagedFiles() {
  HomeState.managedFiles = [];
  persistManagedFiles();
  renderManagedFiles();
}

function persistManagedFiles() {
  localStorage.setItem(
    HomeConfig.storageKeys.managedFiles,
    JSON.stringify(HomeState.managedFiles)
  );
}

function renderManagedFiles() {
  if (!elements.managedFileList) {
    return;
  }

  if (!HomeState.managedFiles.length) {
    elements.managedFileList.innerHTML =
      '<li class="file-manager-list__empty">暂无文件，请先添加文档。</li>';
    return;
  }

  elements.managedFileList.innerHTML = HomeState.managedFiles.map(function (file) {
    return `
      <li class="file-manager-list__item">
        <div class="file-manager-list__meta">
          <strong>${file.name}</strong>
          <span>${Math.max(1, Math.round(file.size / 1024))} KB</span>
        </div>
        <button
          class="file-manager-list__remove"
          type="button"
          data-file-id="${file.id}"
          aria-label="移除 ${file.name}"
        >
          移除
        </button>
      </li>
    `;
  }).join("");

  elements.managedFileList
    .querySelectorAll(".file-manager-list__remove")
    .forEach(function (button) {
      button.addEventListener("click", function () {
        const fileId = button.getAttribute("data-file-id");
        HomeState.managedFiles = HomeState.managedFiles.filter(function (file) {
          return file.id !== fileId;
        });
        persistManagedFiles();
        renderManagedFiles();
      });
    });
}
```

然后在 `init()` 结尾补上：

```js
bindToolPanelEvents();
renderManagedFiles();
```

- [ ] **Step 4: 重新运行 smoke test 和主脚本语法检查**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

Run: `node --check src/web/static/js/home/main.js`  
Expected: exits with code `0`

- [ ] **Step 5: 创建本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-tool-controls-task-4 | Out-Null
Copy-Item src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-tool-controls-task-4 -Force
```

### Task 5: 完成工具层样式、tooltip 和响应式排版

**Files:**
- Modify: `src/web/templates/index.html`
- Test: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 扩展 smoke test，让它先对关键样式类报错**

在 `tests/ui/homepage_tool_controls_smoke.test.js` 追加下面这段：

```js
const htmlSource = fs.readFileSync("src/web/templates/index.html", "utf8");

[
  ".homepage-toolbar",
  ".tool-rail",
  ".tool-icon-btn",
  ".toolbar-pills",
  ".tool-pill-btn",
  ".tool-panels-overlay",
  ".tool-panel--drawer",
  ".tool-panel--popover",
  ".file-manager-list__empty",
  "@media (max-width: 1400px)",
].forEach((token) => {
  assert(
    htmlSource.includes(token),
    `Missing homepage toolbar style token: ${token}`
  );
});
```

- [ ] **Step 2: 运行 smoke test，确认样式断言先失败**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: FAIL with `Missing homepage toolbar style token: .homepage-toolbar` or another newly added selector.

- [ ] **Step 3: 在 `index.html` 的样式区加入完整工具层样式**

把下面这段样式加入 `src/web/templates/index.html` 的首页样式区，并删除旧的 `.index_con .right .btn_list` / `.index_con .right .top` 图片样式：

```css
.index_con .right {
  position: relative;
}

.homepage-toolbar {
  position: absolute;
  top: 24px;
  left: 24px;
  right: 28px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  pointer-events: none;
  z-index: 4;
}

.tool-rail,
.toolbar-pills,
.tool-panel,
.tool-panels-overlay {
  pointer-events: auto;
}

.tool-rail {
  width: 56px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tool-icon-btn,
.tool-pill-btn,
.tool-panel__close,
.file-manager-list__remove {
  border: 1px solid rgba(170, 184, 204, 0.42);
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 14px 34px rgba(24, 39, 75, 0.08);
  transition: transform 0.18s ease, box-shadow 0.18s ease,
    background-color 0.18s ease, border-color 0.18s ease, opacity 0.18s ease;
}

.tool-icon-btn {
  position: relative;
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.tool-icon-btn img {
  width: 22px;
  height: 22px;
}

.tool-icon-btn::after {
  content: attr(data-tooltip);
  position: absolute;
  left: calc(100% + 10px);
  top: 50%;
  transform: translateY(-50%) translateX(-4px);
  opacity: 0;
  pointer-events: none;
  white-space: nowrap;
  padding: 7px 10px;
  border-radius: 10px;
  background: rgba(22, 30, 49, 0.92);
  color: #fff;
  font-size: 12px;
  line-height: 1;
}

.tool-icon-btn:hover::after,
.tool-icon-btn:focus-visible::after {
  opacity: 1;
  transform: translateY(-50%) translateX(0);
}

.tool-icon-btn:hover,
.tool-icon-btn:focus-visible,
.tool-pill-btn:hover,
.tool-pill-btn:focus-visible {
  transform: translateY(-1px);
  box-shadow: 0 18px 40px rgba(24, 39, 75, 0.12);
}

.tool-icon-btn--danger:hover,
.tool-icon-btn--danger:focus-visible {
  border-color: rgba(222, 95, 109, 0.36);
  background: rgba(255, 244, 246, 0.94);
}

.tool-icon-btn.is-disabled,
.tool-icon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
  box-shadow: 0 10px 24px rgba(24, 39, 75, 0.05);
}

.toolbar-pills {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tool-pill-btn {
  min-height: 44px;
  padding: 0 18px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #5d6f8d;
  font-weight: 600;
}

.tool-pill-btn--primary {
  border-color: rgba(116, 98, 255, 0.18);
}

.tool-panels-overlay {
  position: absolute;
  inset: 0;
  background: rgba(20, 27, 45, 0.08);
  backdrop-filter: blur(2px);
  z-index: 5;
}

.tool-panel {
  position: absolute;
  z-index: 6;
  border: 1px solid rgba(181, 194, 214, 0.42);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 22px 56px rgba(22, 33, 62, 0.14);
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.tool-panel.is-open {
  opacity: 1;
  transform: translateY(0);
}

.tool-panel--drawer {
  top: 88px;
  right: 28px;
  width: 360px;
  border-radius: 24px;
  padding: 20px;
}

.tool-panel--popover {
  top: 88px;
  right: 28px;
  width: 320px;
  border-radius: 22px;
  padding: 18px 20px;
}

.tool-panel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}

.tool-panel__header h3 {
  margin: 0;
  color: #23324f;
  font-size: 18px;
}

.tool-panel__header p {
  margin: 6px 0 0;
  color: #74829a;
  font-size: 13px;
  line-height: 1.5;
}

.tool-panel__close {
  width: 36px;
  height: 36px;
  border-radius: 12px;
}

.file-manager-dropzone {
  border: 1px dashed rgba(118, 130, 168, 0.48);
  border-radius: 18px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #61708c;
  background: rgba(245, 248, 253, 0.92);
}

.file-manager-actions {
  display: flex;
  gap: 10px;
  margin: 14px 0 16px;
}

.file-manager-list,
.help-panel-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.file-manager-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.file-manager-list__item {
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(245, 248, 253, 0.92);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.file-manager-list__meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.file-manager-list__meta strong,
.file-manager-list__meta span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-manager-list__meta strong {
  color: #22314e;
}

.file-manager-list__meta span {
  color: #74829a;
  font-size: 12px;
}

.file-manager-list__remove {
  padding: 8px 12px;
  border-radius: 999px;
  color: #5d6f8d;
}

.file-manager-list__empty {
  padding: 16px;
  border-radius: 16px;
  text-align: center;
  color: #74829a;
  background: rgba(245, 248, 253, 0.92);
}

.help-panel-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  color: #5f6f8c;
}

.help-panel-list li {
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(246, 248, 252, 0.94);
  line-height: 1.6;
}

@media (max-width: 1400px) {
  .homepage-toolbar {
    left: 18px;
    right: 20px;
  }

  .tool-rail {
    width: 48px;
  }

  .tool-icon-btn {
    width: 42px;
    height: 42px;
  }

  .tool-pill-btn {
    min-height: 42px;
    padding: 0 15px;
  }

  .tool-panel--drawer {
    width: 320px;
  }
}
```

- [ ] **Step 4: 重新运行 smoke test**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

- [ ] **Step 5: 创建本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-tool-controls-task-5 | Out-Null
Copy-Item src\web\templates\index.html,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-tool-controls-task-5 -Force
```

### Task 6: 全量校验与交付前核对

**Files:**
- Verify: `src/web/templates/index.html`
- Verify: `src/web/static/js/home/config.js`
- Verify: `src/web/static/js/home/state.js`
- Verify: `src/web/static/js/home/main.js`
- Verify: `tests/ui/homepage_tool_controls_smoke.test.js`

- [ ] **Step 1: 运行 smoke test 和语法检查**

Run: `node tests/ui/homepage_tool_controls_smoke.test.js`  
Expected: PASS

Run: `node --check src/web/static/js/home/config.js`  
Expected: exits with code `0`

Run: `node --check src/web/static/js/home/state.js`  
Expected: exits with code `0`

Run: `node --check src/web/static/js/home/main.js`  
Expected: exits with code `0`

- [ ] **Step 2: 手工验证首页 5 个控件**

在浏览器里验证下面这些行为：

```text
1. 左侧第一个按钮可以收起/展开左侧导航，并在刷新后记住状态。
2. 左侧第二个按钮点击后会清空输入框、隐藏聊天区、恢复首页欢迎态。
3. 左侧第三个按钮在没有聊天内容时置灰；有内容时点击会二次确认并清空当前会话。
4. 右侧“文件管理”可以打开抽屉，添加文件后能看到文件列表，移除或清空后列表会同步更新。
5. 右侧“帮助”可以打开轻量帮助面板，再次点击或点击遮罩可关闭。
6. 左右两组控件在同一视觉高度上，主标题、快捷卡片和输入框仍是首页主视觉中心。
```

- [ ] **Step 3: 保存最终本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\homepage-tool-controls-final | Out-Null
Copy-Item src\web\templates\index.html,src\web\static\js\home\config.js,src\web\static\js\home\state.js,src\web\static\js\home\main.js,tests\ui\homepage_tool_controls_smoke.test.js scratch\checkpoints\homepage-tool-controls-final -Force
```
