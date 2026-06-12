# 首页与活性预测脚本模块化整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将首页聊天脚本与活性预测脚本从两个超大单文件整理为与现有项目一致的多文件模块结构，同时尽量保持现有行为不变。

**Architecture:** 继续使用普通 `<script>` 顺序加载，而不是引入 `type="module"` 或打包器。首页脚本拆到 `src/web/static/js/home/`，活性预测脚本拆到 `src/web/static/js/activity_prediction/`，各模块通过全局命名空间对象协作，入口文件只负责初始化和串联。

**Tech Stack:** 原生 JavaScript、Jinja2 模板、ECharts、PowerShell、Node `--check`

---

### Task 1: 建立备份与模块目录骨架

**Files:**
- Create: `src/web/static/js/script.legacy.backup.js`
- Create: `src/web/static/js/activity_prediction_v2.legacy.backup.js`
- Create: `src/web/static/js/home/config.js`
- Create: `src/web/static/js/home/state.js`
- Create: `src/web/static/js/home/theme.js`
- Create: `src/web/static/js/home/formatters.js`
- Create: `src/web/static/js/home/molecule_renderer.js`
- Create: `src/web/static/js/home/chat_renderer.js`
- Create: `src/web/static/js/home/advanced_options.js`
- Create: `src/web/static/js/home/ws_client.js`
- Create: `src/web/static/js/home/main.js`
- Create: `src/web/static/js/activity_prediction/utils.js`
- Create: `src/web/static/js/activity_prediction/model_manager.js`
- Create: `src/web/static/js/activity_prediction/results_renderer.js`
- Create: `src/web/static/js/activity_prediction/charts.js`
- Create: `src/web/static/js/activity_prediction/preflight.js`
- Create: `src/web/static/js/activity_prediction/training_monitor.js`
- Create: `src/web/static/js/activity_prediction/main.js`
- Modify: `src/web/templates/index.html`
- Modify: `src/web/templates/activity_prediction.html`
- Test: `node --check` 对新脚本做语法检查

- [ ] **Step 1: 复制旧文件作为只读备份**

```powershell
Copy-Item src\web\static\js\script.js src\web\static\js\script.legacy.backup.js -Force
Copy-Item src\web\static\js\activity_prediction_v2.js src\web\static\js\activity_prediction_v2.legacy.backup.js -Force
```

Expected: 两个备份文件创建成功，内容与当前运行文件一致。

- [ ] **Step 2: 创建新目录**

```powershell
New-Item -ItemType Directory -Force src\web\static\js\home
New-Item -ItemType Directory -Force src\web\static\js\activity_prediction
```

Expected: `home` 和 `activity_prediction` 目录存在。

- [ ] **Step 3: 写入首页模块空骨架**

```js
// src/web/static/js/home/config.js
"use strict";

window.HomeConfig = {
  storageKeys: {
    theme: "medchatHomeTheme",
    advancedOptions: "medchatAdvancedOptions",
  },
  websocket: {
    reconnectLimit: 5,
    connectTimeoutMs: 10000,
    maxBackoffMs: 30000,
  },
  advancedDefaults: {
    ragResultCount: 6,
    temperature: 0.7,
    moleculeCount: 3,
  },
};
```

```js
// src/web/static/js/home/state.js
"use strict";

window.HomeState = {
  ws: null,
  isConnected: false,
  chatMode: false,
  ragEnabled: false,
  toolsEnabled: true,
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
  },
};
```

```js
// src/web/static/js/home/main.js
"use strict";

window.HomeMain = (function () {
  function init() {}

  return {
    init: init,
  };
})();
```

为其余 `home/` 文件先创建相同风格的命名空间壳：

```js
"use strict";

window.HomeTheme = (function () {
  return {};
})();
```

对应文件名与对象名：
- `theme.js` -> `HomeTheme`
- `formatters.js` -> `HomeFormatters`
- `molecule_renderer.js` -> `HomeMoleculeRenderer`
- `chat_renderer.js` -> `HomeChatRenderer`
- `advanced_options.js` -> `HomeAdvancedOptions`
- `ws_client.js` -> `HomeSocket`

- [ ] **Step 4: 写入活性预测模块空骨架**

```js
// src/web/static/js/activity_prediction/utils.js
"use strict";

window.ActivityUtils = (function () {
  const DEFAULT_ACTIVITY_RANGE = {
    regression: { min: 4, lowUpper: 5, highUpper: 7, max: 9, label: "pIC50" },
    classification: { min: 0, lowUpper: 0.33, highUpper: 0.66, max: 1, label: "Score" },
  };

  return {
    DEFAULT_ACTIVITY_RANGE: DEFAULT_ACTIVITY_RANGE,
  };
})();
```

```js
// src/web/static/js/activity_prediction/main.js
"use strict";

window.ActivityMain = (function () {
  function init() {}

  return {
    init: init,
  };
})();
```

为其余 `activity_prediction/` 文件先创建同风格命名空间壳：

```js
"use strict";

window.ActivityCharts = (function () {
  return {};
})();
```

对应文件名与对象名：
- `model_manager.js` -> `ActivityModels`
- `results_renderer.js` -> `ActivityResults`
- `charts.js` -> `ActivityCharts`
- `preflight.js` -> `ActivityPreflight`
- `training_monitor.js` -> `ActivityTraining`

- [ ] **Step 5: 先不要改模板功能顺序，只把旧模板脚本引用抄到注释区备用**

在两个模板的现有 `<script src="...">` 区域上方加临时注释：

```html
<!-- JS modularization in progress: replace single-file entry after modules are wired -->
```

Expected: 模板仍然继续引用旧入口脚本，页面运行不受影响。

- [ ] **Step 6: 运行语法检查，确保骨架文件均有效**

```powershell
node --check src\web\static\js\home\config.js
node --check src\web\static\js\home\state.js
node --check src\web\static\js\home\theme.js
node --check src\web\static\js\home\formatters.js
node --check src\web\static\js\home\molecule_renderer.js
node --check src\web\static\js\home\chat_renderer.js
node --check src\web\static\js\home\advanced_options.js
node --check src\web\static\js\home\ws_client.js
node --check src\web\static\js\home\main.js
node --check src\web\static\js\activity_prediction\utils.js
node --check src\web\static\js\activity_prediction\model_manager.js
node --check src\web\static\js\activity_prediction\results_renderer.js
node --check src\web\static\js\activity_prediction\charts.js
node --check src\web\static\js\activity_prediction\preflight.js
node --check src\web\static\js\activity_prediction\training_monitor.js
node --check src\web\static\js\activity_prediction\main.js
```

Expected: 所有命令无输出且退出码为 0。

- [ ] **Step 7: 做本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\js-modularization-task-1
Copy-Item src\web\static\js\script.legacy.backup.js scratch\checkpoints\js-modularization-task-1\ -Force
Copy-Item src\web\static\js\activity_prediction_v2.legacy.backup.js scratch\checkpoints\js-modularization-task-1\ -Force
```

Expected: checkpoint 目录存在且包含两个备份文件。

### Task 2: 拆分首页共享基础模块

**Files:**
- Modify: `src/web/static/js/home/config.js`
- Modify: `src/web/static/js/home/state.js`
- Modify: `src/web/static/js/home/theme.js`
- Modify: `src/web/static/js/home/advanced_options.js`
- Modify: `src/web/static/js/script.js`
- Test: `node --check src/web/static/js/home/*.js`

- [ ] **Step 1: 将首页常量迁移到 `home/config.js`**

从 `script.js` 提取这些配置并原样放入 `HomeConfig`：
- `maxReconnectAttempts`
- 主题允许值 `["rose", "amber", "mint"]`
- 高级选项默认值

目标代码形态：

```js
"use strict";

window.HomeConfig = {
  themes: ["rose", "amber", "mint"],
  storageKeys: {
    theme: "medchatHomeTheme",
    advancedOptions: "medchatAdvancedOptions",
  },
  websocket: {
    reconnectLimit: 5,
    connectTimeoutMs: 10000,
    maxBackoffMs: 30000,
  },
  advancedDefaults: {
    ragCount: 6,
    temperature: 0.7,
    moleculeCount: 3,
  },
};
```

- [ ] **Step 2: 将共享运行时状态迁移到 `home/state.js`**

把 `script.js` 开头这些变量迁移到 `HomeState`：
- `ws`
- `isConnected`
- `chatMode`
- `ragEnabled`
- `toolsEnabled`
- `currentMessages`
- `reconnectAttempts`
- DOM 缓存对象 `elements`

目标代码形态：

```js
"use strict";

window.HomeState = {
  ws: null,
  isConnected: false,
  chatMode: false,
  ragEnabled: false,
  toolsEnabled: true,
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
  },
};
```

- [ ] **Step 3: 将 `applyHomeTheme` 和 `initThemeSwitcher` 迁移到 `home/theme.js`**

迁移后改为：

```js
"use strict";

window.HomeTheme = (function () {
  function apply(theme, persist) {
    const nextTheme = HomeConfig.themes.includes(theme) ? theme : "rose";
    document.body.setAttribute("data-theme", nextTheme);

    if (HomeState.elements.themeButtons && HomeState.elements.themeButtons.length) {
      HomeState.elements.themeButtons.forEach(function (button) {
        button.classList.toggle("active", button.dataset.theme === nextTheme);
      });
    }

    if (persist !== false) {
      localStorage.setItem(HomeConfig.storageKeys.theme, nextTheme);
    }
  }

  function init() {
    const savedTheme = localStorage.getItem(HomeConfig.storageKeys.theme) || "rose";
    apply(savedTheme, false);

    if (!HomeState.elements.themeButtons || !HomeState.elements.themeButtons.length) {
      return;
    }

    HomeState.elements.themeButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        apply(button.dataset.theme);
      });
    });
  }

  return {
    apply: apply,
    init: init,
  };
})();
```

- [ ] **Step 4: 将高级选项逻辑迁移到 `home/advanced_options.js`**

从 `script.js` 移动以下函数：
- `initAdvancedOptions`
- `saveAdvancedOptions`
- `loadAdvancedOptions`
- `getAdvancedConfig`

并对外导出：

```js
"use strict";

window.HomeAdvancedOptions = (function () {
  function init() {}
  function save() {}
  function load() {}
  function getConfig() {}

  return {
    init: init,
    save: save,
    load: load,
    getConfig: getConfig,
  };
})();
```

要求：函数内部读取默认值时改用 `HomeConfig.advancedDefaults`，不要再写死重复常量。

- [ ] **Step 5: 让 `script.js` 进入“仅供迁移”的中间态**

在 `script.js` 中删除已迁出的定义，改为通过新模块使用：

```js
// 原：
// let ws = null;
// let isConnected = false;
// function applyHomeTheme(...) {}
// function initThemeSwitcher() {}

// 迁移后在调用处使用：
HomeTheme.init();
HomeAdvancedOptions.init();
```

Expected: `script.js` 仍然能运行，但这些基础职责已不再由它定义。

- [ ] **Step 6: 运行语法检查**

```powershell
node --check src\web\static\js\home\config.js
node --check src\web\static\js\home\state.js
node --check src\web\static\js\home\theme.js
node --check src\web\static\js\home\advanced_options.js
node --check src\web\static\js\script.js
```

Expected: 全部通过。

- [ ] **Step 7: 做本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\js-modularization-task-2
Copy-Item src\web\static\js\home\*.js scratch\checkpoints\js-modularization-task-2\ -Force
Copy-Item src\web\static\js\script.js scratch\checkpoints\js-modularization-task-2\ -Force
```

### Task 3: 拆分首页渲染与连接主流程，并切换模板到新入口

**Files:**
- Modify: `src/web/static/js/home/formatters.js`
- Modify: `src/web/static/js/home/molecule_renderer.js`
- Modify: `src/web/static/js/home/chat_renderer.js`
- Modify: `src/web/static/js/home/ws_client.js`
- Modify: `src/web/static/js/home/main.js`
- Modify: `src/web/templates/index.html`
- Modify: `src/web/static/js/script.js`
- Test: `node --check` + 首页基础 DOM 关键字检查

- [ ] **Step 1: 提取首页内容格式化函数到 `home/formatters.js`**

从 `script.js` 移动这些函数：
- `formatSynthesisRoute`
- `formatReactionPrediction`
- `formatLiteratureResults`
- `formatADMETResults`
- `formatDrugLikenessResults`
- `formatMolecularProperties`
- `formatReActResults`
- `formatContent`

导出结构：

```js
"use strict";

window.HomeFormatters = (function () {
  function formatSynthesisRoute(content) {}
  function formatReactionPrediction(content) {}
  function formatLiteratureResults(content) {}
  function formatADMETResults(content) {}
  function formatDrugLikenessResults(content) {}
  function formatMolecularProperties(content) {}
  function formatReActResults(content) {}

  function formatContent(content) {
    // 保留原有分支逻辑，只把内部 helper 调整为同模块调用
    return content;
  }

  return {
    formatContent: formatContent,
  };
})();
```

- [ ] **Step 2: 提取分子相关渲染与属性逻辑到 `home/molecule_renderer.js`**

从 `script.js` 移动这些函数：
- `displayRAGInfo`
- `renderRAGMoleculeStructure`
- `copyToClipboard`
- `analyzeMolecule`
- `fetchMoleculeProperties`
- `fetchMoleculePropertiesForToolMolecule`
- `displayToolMoleculeProperties`
- `displayMoleculeProperties`
- `getPropertyColor`
- `getADMETIcon`
- `getADMETColor`
- `detectAndRenderMolecules`

导出结构：

```js
"use strict";

window.HomeMoleculeRenderer = (function () {
  function displayRAGInfo(molecules, infoMessage) {}
  function renderStructure(smiles, canvas, container, scale, index) {}
  function detectAndRenderMolecules(messageElement, content) {}
  function fetchProperties(smiles, container) {}
  function analyze(smiles) {}

  return {
    displayRAGInfo: displayRAGInfo,
    renderStructure: renderStructure,
    detectAndRenderMolecules: detectAndRenderMolecules,
    fetchProperties: fetchProperties,
    analyze: analyze,
  };
})();
```

- [ ] **Step 3: 提取聊天 UI 渲染到 `home/chat_renderer.js`**

从 `script.js` 移动这些函数：
- `createChatContainer`
- `showToast`
- `addSystemMessage`
- `enterChatMode`
- `getCurrentTime`
- `addUserMessage`
- `addAssistantMessage`
- `appendToLastMessage`
- `completeLastMessage`
- `showStatusMessage`
- `showToolStatus`
- `clearToolStatus`
- `showErrorMessage`
- `showTypingIndicator`
- `removeTypingIndicator`
- `showNotification`
- `scrollToBottom`
- `addEnhancedStyles`

导出结构：

```js
"use strict";

window.HomeChatRenderer = (function () {
  function createContainer() {}
  function enterChatMode() {}
  function addUserMessage(content) {}
  function addAssistantMessage(content) {}
  function appendToLastMessage(content) {}
  function completeLastMessage() {}
  function showTypingIndicator() {}
  function removeTypingIndicator() {}
  function showStatusMessage(message) {}
  function showToolStatus(message) {}
  function clearToolStatus() {}
  function showErrorMessage(message) {}
  function showNotification(message, type) {}
  function updateConnectionStatus(status) {}
  function scrollToBottom() {}
  function addEnhancedStyles() {}

  return {
    createContainer: createContainer,
    enterChatMode: enterChatMode,
    addUserMessage: addUserMessage,
    addAssistantMessage: addAssistantMessage,
    appendToLastMessage: appendToLastMessage,
    completeLastMessage: completeLastMessage,
    showTypingIndicator: showTypingIndicator,
    removeTypingIndicator: removeTypingIndicator,
    showStatusMessage: showStatusMessage,
    showToolStatus: showToolStatus,
    clearToolStatus: clearToolStatus,
    showErrorMessage: showErrorMessage,
    showNotification: showNotification,
    updateConnectionStatus: updateConnectionStatus,
    scrollToBottom: scrollToBottom,
    addEnhancedStyles: addEnhancedStyles,
  };
})();
```

注意：`updateConnectionStatus` 也一起迁出，保证连接状态 UI 归渲染层统一管理。

- [ ] **Step 4: 提取 WebSocket 管理到 `home/ws_client.js`**

从 `script.js` 移动这些函数：
- `connectWebSocket`
- `sendTestMessage`
- `handleWebSocketMessage`
- `handleModelChange`
- `getModelDisplayName`
- `sendMessage`
- `diagnoseWebSocket`

导出结构：

```js
"use strict";

window.HomeSocket = (function () {
  function connect() {}
  function sendTestMessage() {}
  function sendMessage() {}
  function handleMessage(rawData) {}
  function handleModelChange(event) {}
  function diagnose() {}

  return {
    connect: connect,
    sendMessage: sendMessage,
    handleModelChange: handleModelChange,
    diagnose: diagnose,
  };
})();
```

要求：
- 所有 `ws` 访问改成 `HomeState.ws`
- 所有连接状态改成 `HomeState.isConnected`
- 需要渲染消息时调用 `HomeChatRenderer`
- 需要格式化内容时调用 `HomeFormatters`
- 需要渲染分子时调用 `HomeMoleculeRenderer`

- [ ] **Step 5: 在 `home/main.js` 中实现首页初始化入口**

从 `script.js` 迁移这些函数或逻辑：
- `init`
- `bindEvents`
- `toggleRAG`
- `toggleTools`
- `updateToggleStates`
- `handleQuickAction`

目标结构：

```js
"use strict";

window.HomeMain = (function () {
  function cacheElements() {
    HomeState.elements.input = document.querySelector(".fill input");
    HomeState.elements.sendBtn = document.querySelector(".fill .btn");
    HomeState.elements.ragToggle = document.querySelector(".tool .chose .item:nth-child(1) .icon");
    HomeState.elements.toolsToggle = document.querySelector(".tool .chose .item:nth-child(2) .icon");
    HomeState.elements.themeButtons = document.querySelectorAll(".theme-chip");
    HomeState.elements.connectionStatus = document.getElementById("connectionStatus");
    HomeState.elements.modelSelect = document.getElementById("modelSelect");
    HomeState.elements.quickActions = document.querySelectorAll(".icon_list .item");
  }

  function init() {
    cacheElements();
    HomeChatRenderer.createContainer();
    HomeTheme.init();
    HomeChatRenderer.updateConnectionStatus("connecting");
    HomeSocket.connect();
    bindEvents();
    updateToggleStates();
    HomeChatRenderer.addEnhancedStyles();
    HomeAdvancedOptions.init();
  }

  return {
    init: init,
  };
})();
```

- [ ] **Step 6: 切换 `index.html` 到新脚本顺序**

将尾部：

```html
<script src="/static/js/script.js?v=20241213-v5"></script>
```

替换为：

```html
<script src="/static/js/home/config.js"></script>
<script src="/static/js/home/state.js"></script>
<script src="/static/js/home/theme.js"></script>
<script src="/static/js/home/formatters.js"></script>
<script src="/static/js/home/molecule_renderer.js"></script>
<script src="/static/js/home/chat_renderer.js"></script>
<script src="/static/js/home/advanced_options.js"></script>
<script src="/static/js/home/ws_client.js"></script>
<script src="/static/js/home/main.js"></script>
<script>
  document.addEventListener("DOMContentLoaded", function () {
    HomeMain.init();
  });
</script>
```

Expected: 首页不再依赖 `script.js` 作为运行入口。

- [ ] **Step 7: 将 `script.js` 收缩为迁移说明文件**

把 `script.js` 改成最小说明文件，避免被误用：

```js
"use strict";

// Legacy entry retained only for backup reference.
// Runtime entry moved to src/web/static/js/home/*.js
```

- [ ] **Step 8: 运行语法检查并做关键内容 smoke 校验**

```powershell
node --check src\web\static\js\home\config.js
node --check src\web\static\js\home\state.js
node --check src\web\static\js\home\theme.js
node --check src\web\static\js\home\formatters.js
node --check src\web\static\js\home\molecule_renderer.js
node --check src\web\static\js\home\chat_renderer.js
node --check src\web\static\js\home\advanced_options.js
node --check src\web\static\js\home\ws_client.js
node --check src\web\static\js\home\main.js
rg -n "分析工具|快速分析|专业的AI药物设计平台|高级选项" src\web\templates\index.html
```

Expected:
- 所有 `node --check` 通过
- `rg` 能命中首页关键文案

- [ ] **Step 9: 做本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\js-modularization-task-3
Copy-Item src\web\static\js\home\*.js scratch\checkpoints\js-modularization-task-3\ -Force
Copy-Item src\web\templates\index.html scratch\checkpoints\js-modularization-task-3\ -Force
```

### Task 4: 拆分活性预测共享基础模块

**Files:**
- Modify: `src/web/static/js/activity_prediction/utils.js`
- Modify: `src/web/static/js/activity_prediction/model_manager.js`
- Modify: `src/web/static/js/activity_prediction/charts.js`
- Modify: `src/web/static/js/activity_prediction_v2.js`
- Test: `node --check`

- [ ] **Step 1: 将基础 helper 迁移到 `activity_prediction/utils.js`**

从 `activity_prediction_v2.js` 移动这些函数：
- `animateNumber`
- `setText`
- `formatScore`
- `getTagClass`
- `finalizeHistogramBins`
- `buildHistogramBins`

并把常量与 helper 收口为：

```js
"use strict";

window.ActivityUtils = (function () {
  const DEFAULT_ACTIVITY_RANGE = {
    regression: { min: 4, lowUpper: 5, highUpper: 7, max: 9, label: "pIC50" },
    classification: { min: 0, lowUpper: 0.33, highUpper: 0.66, max: 1, label: "Score" },
  };

  function animateNumber(el, endValue, duration, isInt) {}
  function setText(id, text) {}
  function formatScore(score) {}
  function getTagClass(label, score, success) {}
  function finalizeHistogramBins(scores, boundaries) {}
  function buildHistogramBins(scores, taskType) {}

  return {
    DEFAULT_ACTIVITY_RANGE: DEFAULT_ACTIVITY_RANGE,
    animateNumber: animateNumber,
    setText: setText,
    formatScore: formatScore,
    getTagClass: getTagClass,
    finalizeHistogramBins: finalizeHistogramBins,
    buildHistogramBins: buildHistogramBins,
  };
})();
```

- [ ] **Step 2: 将模型与任务类型逻辑迁移到 `activity_prediction/model_manager.js`**

从 `activity_prediction_v2.js` 移动这些内容：
- `_allModels`
- `getSelectedModelMeta`
- `getCurrentTaskType`
- `getRangeConfig`
- `updateTaskTypeStat`
- `loadModels`
- `confirmDeleteModel`
- `updatePopoverContent`
- `infoBtn / popover` 相关逻辑初始化

导出结构：

```js
"use strict";

window.ActivityModels = (function () {
  let allModels = [];

  function getAll() {
    return allModels;
  }

  function setAll(models) {
    allModels = models || [];
  }

  function getSelectedMeta() {}
  function getTaskType() {}
  function getRangeConfig() {}
  async function loadModels() {}
  async function confirmDeleteModel() {}
  function updateTaskTypeStat() {}
  function updatePopoverContent() {}
  function initPopover() {}

  return {
    getAll: getAll,
    setAll: setAll,
    getSelectedMeta: getSelectedMeta,
    getTaskType: getTaskType,
    getRangeConfig: getRangeConfig,
    loadModels: loadModels,
    confirmDeleteModel: confirmDeleteModel,
    updateTaskTypeStat: updateTaskTypeStat,
    updatePopoverContent: updatePopoverContent,
    initPopover: initPopover,
  };
})();
```

- [ ] **Step 3: 将图表逻辑迁移到 `activity_prediction/charts.js`**

从 `activity_prediction_v2.js` 移动这些内容：
- `_renderHistogramLegacy`
- `_renderSingleGaugeLegacy`
- `renderHistogram`
- `_metricsChart`
- `_metricEpochs`
- `_metricHistory`
- `_metricDescriptor`
- `getMetricDescriptor`
- `buildMetricsChartOption`
- `getMetricDescriptorV2`
- `buildMetricsChartOptionV2`
- `initMetricsChart`
- `updateMetricsChart`

导出结构：

```js
"use strict";

window.ActivityCharts = (function () {
  let metricsChart = null;
  let metricEpochs = [];
  let metricHistory = {
    trainLoss: [],
    valLoss: [],
    primary: [],
  };

  function renderHistogram(dataList) {}
  function initMetricsChart() {}
  function updateMetricsChart(epoch, metrics, bestEpoch, bestMetrics) {}
  function resetMetricsChartState() {}

  return {
    renderHistogram: renderHistogram,
    initMetricsChart: initMetricsChart,
    updateMetricsChart: updateMetricsChart,
    resetMetricsChartState: resetMetricsChartState,
  };
})();
```

- [ ] **Step 4: 将 `activity_prediction_v2.js` 进入中间迁移态**

删除已迁出的定义，改用新模块调用：

```js
// 原：
// const DEFAULT_ACTIVITY_RANGE = ...
// function getSelectedModelMeta() {}
// function renderHistogram() {}

// 迁移后：
const range = ActivityModels.getRangeConfig();
ActivityCharts.renderHistogram(successfulResults);
```

- [ ] **Step 5: 运行语法检查**

```powershell
node --check src\web\static\js\activity_prediction\utils.js
node --check src\web\static\js\activity_prediction\model_manager.js
node --check src\web\static\js\activity_prediction\charts.js
node --check src\web\static\js\activity_prediction_v2.js
```

- [ ] **Step 6: 做本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\js-modularization-task-4
Copy-Item src\web\static\js\activity_prediction\*.js scratch\checkpoints\js-modularization-task-4\ -Force
Copy-Item src\web\static\js\activity_prediction_v2.js scratch\checkpoints\js-modularization-task-4\ -Force
```

### Task 5: 拆分活性预测结果与训练主流程，并切换模板到新入口

**Files:**
- Modify: `src/web/static/js/activity_prediction/results_renderer.js`
- Modify: `src/web/static/js/activity_prediction/preflight.js`
- Modify: `src/web/static/js/activity_prediction/training_monitor.js`
- Modify: `src/web/static/js/activity_prediction/main.js`
- Modify: `src/web/templates/activity_prediction.html`
- Modify: `src/web/static/js/activity_prediction_v2.js`
- Test: `node --check` + 模板关键字检查

- [ ] **Step 1: 迁移结果渲染到 `results_renderer.js`**

从 `activity_prediction_v2.js` 移动这些函数：
- `resetResultViews`
- `renderSingleSummary`
- `handlePredict` 中结果表格与摘要生成部分，拆成独立渲染 helper

导出结构：

```js
"use strict";

window.ActivityResults = (function () {
  function resetViews() {}
  function renderSingleSummary(item) {}
  function renderResultsTable(dataList) {}
  function renderPredictionResults(data, options) {}

  return {
    resetViews: resetViews,
    renderSingleSummary: renderSingleSummary,
    renderResultsTable: renderResultsTable,
    renderPredictionResults: renderPredictionResults,
  };
})();
```

- [ ] **Step 2: 迁移 preflight 与 launch summary 到 `preflight.js`**

从 `activity_prediction_v2.js` 移动这些内容：
- `_preflightData`
- `setTrainHint`
- `openPreflight`
- `closePreflight`
- `confirmPreflight`
- `showLaunchModal`
- `closeLaunchModal`
- `confirmLaunch`

导出结构：

```js
"use strict";

window.ActivityPreflight = (function () {
  let preflightData = null;

  function setData(data) {
    preflightData = data;
  }

  function getData() {
    return preflightData;
  }

  function setTrainHint(msg, type) {}
  function open() {}
  function close() {}
  function confirm() {}
  function showLaunchModal() {}
  function closeLaunchModal() {}
  function confirmLaunch() {}

  return {
    setData: setData,
    getData: getData,
    setTrainHint: setTrainHint,
    open: open,
    close: close,
    confirm: confirm,
    showLaunchModal: showLaunchModal,
    closeLaunchModal: closeLaunchModal,
    confirmLaunch: confirmLaunch,
  };
})();
```

- [ ] **Step 3: 迁移训练监控到 `training_monitor.js`**

从 `activity_prediction_v2.js` 移动这些内容：
- `openDrawer`
- `minimizeDrawer`
- `closeDrawer`
- `toggleDrawerFull`
- `buildMetricSummary`
- `buildMetricSummaryV2`
- `renderTrainingSummary`
- `renderTrainingSummaryV2`
- `updateDock`
- `updateDrawerStats`
- 训练轮询状态更新逻辑

导出结构：

```js
"use strict";

window.ActivityTraining = (function () {
  function openDrawer() {}
  function minimizeDrawer() {}
  function closeDrawer() {}
  function toggleDrawerFull() {}
  function updateDrawerStats(epoch, total, metric, eta, state) {}
  function renderTrainingSummary(status, elapsedSeconds) {}
  function poll(jobId) {}

  return {
    openDrawer: openDrawer,
    minimizeDrawer: minimizeDrawer,
    closeDrawer: closeDrawer,
    toggleDrawerFull: toggleDrawerFull,
    updateDrawerStats: updateDrawerStats,
    renderTrainingSummary: renderTrainingSummary,
    poll: poll,
  };
})();
```

- [ ] **Step 4: 在 `main.js` 中收口页面入口**

从 `activity_prediction_v2.js` 迁移这些逻辑：
- `fillExample`
- `switchTab`
- `setStatus`
- `bindFileDisplay`
- `handlePredict`
- `startTraining`
- 页面初始化绑定

目标结构：

```js
"use strict";

window.ActivityMain = (function () {
  function fillExample() {}
  function switchTab(mode, tabEl) {}
  function setStatus(text) {}
  function bindFileDisplay(inputId, infoId, prefix) {}
  async function handlePredict(url, formData, btnId) {}
  async function startTraining() {}

  function init() {
    bindFileDisplay("batchFile", "fileInfo", "批量文件");
    ActivityModels.loadModels();
    ActivityModels.initPopover();
    ActivityCharts.initMetricsChart();
  }

  return {
    init: init,
    fillExample: fillExample,
    switchTab: switchTab,
    startTraining: startTraining,
  };
})();
```

要求：保留 HTML 中已有的全局入口时，通过 `window.fillExample = ActivityMain.fillExample` 这类桥接兼容。

- [ ] **Step 5: 切换 `activity_prediction.html` 到新脚本顺序**

将尾部：

```html
<script src="/static/js/activity_prediction_v2.js"></script>
```

替换为：

```html
<script src="/static/js/activity_prediction/utils.js"></script>
<script src="/static/js/activity_prediction/model_manager.js"></script>
<script src="/static/js/activity_prediction/results_renderer.js"></script>
<script src="/static/js/activity_prediction/charts.js"></script>
<script src="/static/js/activity_prediction/preflight.js"></script>
<script src="/static/js/activity_prediction/training_monitor.js"></script>
<script src="/static/js/activity_prediction/main.js"></script>
<script>
  document.addEventListener("DOMContentLoaded", function () {
    ActivityMain.init();
  });
</script>
```

- [ ] **Step 6: 将 `activity_prediction_v2.js` 收缩为迁移说明文件**

把该文件改成：

```js
"use strict";

// Legacy entry retained only for backup reference.
// Runtime entry moved to src/web/static/js/activity_prediction/*.js
```

- [ ] **Step 7: 运行语法检查与模板关键字检查**

```powershell
node --check src\web\static\js\activity_prediction\utils.js
node --check src\web\static\js\activity_prediction\model_manager.js
node --check src\web\static\js\activity_prediction\results_renderer.js
node --check src\web\static\js\activity_prediction\charts.js
node --check src\web\static\js\activity_prediction\preflight.js
node --check src\web\static\js\activity_prediction\training_monitor.js
node --check src\web\static\js\activity_prediction\main.js
rg -n "单分子预测|批量预测|模型训练|高级选项|训练日志" src\web\templates\activity_prediction.html
```

Expected:
- 所有 `node --check` 通过
- `rg` 能命中活性预测页关键文案

- [ ] **Step 8: 做本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\js-modularization-task-5
Copy-Item src\web\static\js\activity_prediction\*.js scratch\checkpoints\js-modularization-task-5\ -Force
Copy-Item src\web\templates\activity_prediction.html scratch\checkpoints\js-modularization-task-5\ -Force
```

### Task 6: 全局校验与收尾

**Files:**
- Modify: `src/web/templates/index.html`
- Modify: `src/web/templates/activity_prediction.html`
- Modify: `src/web/static/js/home/*.js`
- Modify: `src/web/static/js/activity_prediction/*.js`
- Test: 全量语法检查与关键引用检查

- [ ] **Step 1: 检查模板引用顺序与旧入口是否已移除**

运行：

```powershell
rg -n "/static/js/script.js|/static/js/activity_prediction_v2.js|/static/js/home/|/static/js/activity_prediction/" src\web\templates
```

Expected:
- `index.html` 不再引用 `/static/js/script.js`
- `activity_prediction.html` 不再引用 `/static/js/activity_prediction_v2.js`
- 两个模板都命中新模块目录脚本引用

- [ ] **Step 2: 运行首页脚本全量语法检查**

```powershell
Get-ChildItem src\web\static\js\home\*.js | ForEach-Object { node --check $_.FullName }
```

Expected: 所有文件通过检查。

- [ ] **Step 3: 运行活性预测脚本全量语法检查**

```powershell
Get-ChildItem src\web\static\js\activity_prediction\*.js | ForEach-Object { node --check $_.FullName }
```

Expected: 所有文件通过检查。

- [ ] **Step 4: 验证备份文件仍然存在**

```powershell
Test-Path src\web\static\js\script.legacy.backup.js
Test-Path src\web\static\js\activity_prediction_v2.legacy.backup.js
```

Expected:

```text
True
True
```

- [ ] **Step 5: 记录整理完成后的文件布局**

运行：

```powershell
Get-ChildItem src\web\static\js\home | Select-Object Name | Sort-Object Name
Get-ChildItem src\web\static\js\activity_prediction | Select-Object Name | Sort-Object Name
```

Expected: 模块文件齐全，命名符合设计。

- [ ] **Step 6: 做最终本地 checkpoint**

```powershell
New-Item -ItemType Directory -Force scratch\checkpoints\js-modularization-final
Copy-Item src\web\static\js\home\*.js scratch\checkpoints\js-modularization-final\ -Force
Copy-Item src\web\static\js\activity_prediction\*.js scratch\checkpoints\js-modularization-final\ -Force
Copy-Item src\web\templates\index.html scratch\checkpoints\js-modularization-final\ -Force
Copy-Item src\web\templates\activity_prediction.html scratch\checkpoints\js-modularization-final\ -Force
```

- [ ] **Step 7: 产出整理总结**

在最终汇报中明确写出：
- 首页脚本的新目录结构
- 活性预测脚本的新目录结构
- 保留了哪些 legacy 备份
- 哪些行为保持不变
- 哪些点还建议第二轮继续优化
