// enhanced_script.js - 修复版本的聊天交互代码
// Version: 2024-12-02-7 - RAG分子显示完整属性

(function () {
  "use strict";

  console.log(
    "🚀 Enhanced Script 已加载 - Version: 2024-12-02-7 (RAG完整属性)"
  );

  // 全局变量
  let ws = null;
  let isConnected = false;
  let chatMode = false;
  let ragEnabled = false; // 默认禁用RAG
  let toolsEnabled = true;
  let currentMessages = [];
  let reconnectAttempts = 0;
  let maxReconnectAttempts = 5;

  // DOM元素缓存
  const elements = {
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
    closeSidebarBtn: null,
    openSidebarBtn: null,
    sidebarMask: null,
    sidebarPanel: null,
    settingsBtn: null,
    llmSettingsOverlay: null,
    closeLlmSettings: null,
    llmProvider: null,
    llmStream: null,
    llmBaseUrl: null,
    llmModelName: null,
    llmApiKey: null,
    llmApiKeyHint: null,
    llmSettingsStatus: null,
    testLlmConfig: null,
    saveLlmConfig: null,
    agentTaskPanel: null,
  };

  // 初始化函数
  function init() {
    console.log("=== 初始化分子聊天系统 ===");

    // 获取DOM元素
    elements.input = document.querySelector(".fill input");
    elements.sendBtn = document.querySelector(".fill .btn");
    elements.ragToggle = document.querySelector(
      ".tool .chose .item:nth-child(1) .icon"
    );
    elements.toolsToggle = document.querySelector(
      ".tool .chose .item:nth-child(2) .icon"
    );
    elements.themeButtons = document.querySelectorAll(".theme-option");
    elements.connectionStatus = document.getElementById("connectionStatus");
    elements.modelSelect = document.getElementById("modelSelect");
    elements.quickActions = document.querySelectorAll(".icon_list .item");
    elements.closeSidebarBtn = document.getElementById("closeSidebarBtn");
    elements.openSidebarBtn = document.getElementById("openSidebarBtn");
    elements.sidebarMask = document.getElementById("sidebarMask");
    elements.sidebarPanel = document.getElementById("sidebarPanel");
    elements.settingsBtn = document.getElementById("settingsBtn");
    elements.llmSettingsOverlay = document.getElementById("llmSettingsOverlay");
    elements.closeLlmSettings = document.getElementById("closeLlmSettings");
    elements.llmProvider = document.getElementById("llmProvider");
    elements.llmStream = document.getElementById("llmStream");
    elements.llmBaseUrl = document.getElementById("llmBaseUrl");
    elements.llmModelName = document.getElementById("llmModelName");
    elements.llmApiKey = document.getElementById("llmApiKey");
    elements.llmApiKeyHint = document.getElementById("llmApiKeyHint");
    elements.llmSettingsStatus = document.getElementById("llmSettingsStatus");
    elements.testLlmConfig = document.getElementById("testLlmConfig");
    elements.saveLlmConfig = document.getElementById("saveLlmConfig");

    // 获取主要容器
    const rightPanel = document.querySelector(".index_con .right");
    elements.welcomeContent = rightPanel.querySelector(".mass, .icon_list");
    HomeState.elements = elements;

    // 创建聊天容器（初始隐藏）
    createChatContainer();
    HomeTheme.init();

    // 设置初始连接状态
    HomeChatRenderer.updateConnectionStatus("connecting");

    // 建立WebSocket连接
    connectWebSocket();

    // 绑定事件
    bindEvents();

    // 初始化UI状态
    updateToggleStates();

    // 添加增强样式
    addEnhancedStyles();

    // 初始化高级选项面板
    HomeAdvancedOptions.init();

    console.log("初始化完成");
  }

  // 创建聊天容器（ChatGPT 风格：消息区 + 底部输入区）
  function createChatContainer() {
    const rightPanel = document.querySelector(".index_con .right");

    // 1. 创建消息滚动区
    const chatMessages = document.createElement("div");
    chatMessages.className = "chat-messages";
    chatMessages.id = "chatMessages";

    // 2. 把 .fill 和 .tool 包进 composer 容器
    const fillEl = rightPanel.querySelector(".fill");
    const toolEl = rightPanel.querySelector(".tool");

    if (fillEl && toolEl) {
      const composer = document.createElement("div");
      composer.className = "chat-composer";
      // 移动现有元素到 composer 内
      composer.appendChild(fillEl);
      composer.appendChild(toolEl);
      // composer 放在 rightPanel 最后（dom 顺序：chat-messages → composer）
      rightPanel.appendChild(composer);
    }

    // 3. 消息容器插入到 rightPanel 最前面
    const firstChild = rightPanel.firstChild;
    if (firstChild) {
      rightPanel.insertBefore(chatMessages, firstChild);
    } else {
      rightPanel.appendChild(chatMessages);
    }

    elements.chatContainer = chatMessages;
  }

  // WebSocket连接管理 - 修复版
  function connectWebSocket() {
    const wsUrl = `ws://${window.location.host}/ws`;

    console.log(`=== 尝试连接WebSocket ===`);
    console.log(`URL: ${wsUrl}`);
    console.log(`重连尝试次数: ${reconnectAttempts}/${maxReconnectAttempts}`);

    // 如果已经存在连接，先关闭
    if (ws && ws.readyState !== WebSocket.CLOSED) {
      console.log("关闭现有WebSocket连接");
      ws.close();
    }

    try {
      ws = new WebSocket(wsUrl);

      // 连接超时处理
      const connectionTimeout = setTimeout(() => {
        if (ws.readyState === WebSocket.CONNECTING) {
          console.log("WebSocket连接超时");
          ws.close();
        }
      }, 10000); // 10秒超时

      ws.onopen = () => {
        clearTimeout(connectionTimeout);
        console.log("✅ WebSocket连接成功");
        console.log(`WebSocket readyState: ${ws.readyState}`);

        isConnected = true;
        reconnectAttempts = 0;
        // 先显示连接中，等待服务器确认模型就绪
        HomeChatRenderer.updateConnectionStatus("connecting");

        // 发送连接确认消息
        sendTestMessage();
      };

      ws.onmessage = (event) => {
        console.log("📨 收到WebSocket消息:", {
          data: event.data,
          timestamp: new Date().toISOString(),
          dataLength: event.data ? event.data.length : 0,
        });

        try {
          handleWebSocketMessage(event.data);
        } catch (error) {
          console.error("❌ 处理WebSocket消息时出错:", {
            error: error.message,
            stack: error.stack,
            rawData: event.data,
          });
          showErrorMessage(`消息处理错误: ${error.message}`);
        }
      };

      ws.onerror = (error) => {
        clearTimeout(connectionTimeout);
        console.error("❌ WebSocket错误:", {
          error: error,
          readyState: ws ? ws.readyState : "null",
          url: ws ? ws.url : "null",
        });
        HomeChatRenderer.updateConnectionStatus("error");
        HomeChatRenderer.showNotification("WebSocket连接出错", "error");
      };

      ws.onclose = (event) => {
        clearTimeout(connectionTimeout);
        console.log("🔌 WebSocket连接关闭:", {
          code: event.code,
          reason: event.reason || "无原因说明",
          wasClean: event.wasClean,
          timestamp: new Date().toISOString(),
        });

        isConnected = false;
        HomeChatRenderer.updateConnectionStatus("disconnected");

        // 根据关闭原因决定是否重连
        if (!event.wasClean && reconnectAttempts < maxReconnectAttempts) {
          reconnectAttempts++;
          const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000); // 指数退避，最大30秒

          console.log(
            `🔄 ${
              delay / 1000
            }秒后尝试重连 (${reconnectAttempts}/${maxReconnectAttempts})`
          );
          HomeChatRenderer.showNotification(
            `连接断开，${delay / 1000}秒后重连`,
            "warning"
          );

          setTimeout(() => {
            if (!isConnected && (!ws || ws.readyState === WebSocket.CLOSED)) {
              connectWebSocket();
            }
          }, delay);
        } else if (reconnectAttempts >= maxReconnectAttempts) {
          console.log("❌ 达到最大重连次数，停止重连");
          HomeChatRenderer.showNotification("连接失败，请刷新页面重试", "error");
        }
      };
    } catch (error) {
      console.error("❌ 创建WebSocket连接失败:", {
        error: error.message,
        stack: error.stack,
        url: wsUrl,
      });
      HomeChatRenderer.updateConnectionStatus("error");
      HomeChatRenderer.showNotification(
        `连接创建失败: ${error.message}`,
        "error"
      );
    }
  }

  // 发送测试消息确认连接
  function sendTestMessage() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        const testPayload = {
          type: "ping",
          timestamp: Date.now(),
        };
        ws.send(JSON.stringify(testPayload));
        console.log("📤 发送连接测试消息:", testPayload);
      } catch (error) {
        console.error("❌ 发送测试消息失败:", error);
      }
    }
  }

  // 增强的消息处理函数
  function handleWebSocketMessage(data) {
    // 数据有效性检查
    if (!data || typeof data !== "string") {
      console.warn("⚠️ 收到无效消息数据:", data);
      return;
    }

    if (data.trim() === "") {
      console.warn("⚠️ 收到空消息");
      return;
    }

    try {
      const message = JSON.parse(data);
      console.log("📋 解析后的消息:", message);

      // 消息格式验证
      if (!message || typeof message !== "object") {
        console.warn("⚠️ 消息格式无效:", message);
        return;
      }

      if (!message.type) {
        console.warn("⚠️ 消息缺少type字段:", message);
        return;
      }

      // 处理不同类型的消息
      switch (message.type) {
        case "connection_ready":
          console.log("✅ 收到连接就绪消息:", message);
          HomeChatRenderer.updateConnectionStatus("connected");
          HomeChatRenderer.showNotification("模型已就绪", "success");
          break;

        case "pong":
          console.log("🏓 收到pong响应");
          break;

        case "status":
          if (message.message) {
            showStatusMessage(message.message);
          } else {
            console.warn("⚠️ status消息缺少message字段");
          }
          break;

        case "agent_status":
        case "tool_status":
          if (message.message) {
            showToolStatus(message.message);

            // 如果是完成状态消息，3秒后清除
            if (
              message.message.includes("完成") ||
              message.message.includes("失败")
            ) {
              setTimeout(() => {
                clearToolStatus();
              }, 3000);
            }
          } else {
            console.warn("⚠️ tool_status消息缺少message字段");
          }
          break;

        case "agent_event":
          if (message.event) {
            handleAgentEvent(message.event);
          } else {
            console.warn("⚠️ agent_event消息缺少event字段");
          }
          break;

        case "stream":
          if (message.content !== undefined) {
            appendToLastMessage(message.content);
          } else {
            console.warn("⚠️ stream消息缺少content字段");
          }
          break;

        case "complete":
          completeLastMessage();
          clearToolStatus(); // 清除工具状态显示
          break;

        case "agent_result":
          if (message.message) {
            showToolStatus(message.message);
            // 显示结果消息2秒后清除
            setTimeout(() => {
              clearToolStatus();
            }, 2000);
          }
          break;

        case "message":
          if (message.message) {
            addAssistantMessage(message.message);
          } else {
            console.warn("⚠️ message消息缺少message字段");
          }
          break;

        case "rag_info":
          if (message.molecules) {
            displayRAGInfo(message.molecules, message.message);
          } else {
            console.warn("⚠️ rag_info消息缺少molecules字段");
          }
          break;

        case "error":
          const errorMsg = message.message || message.error || "未知错误";
          console.error("❌ 服务器返回错误:", {
            message: errorMsg,
            details: message.details,
            code: message.code,
          });
          showErrorMessage(errorMsg);
          break;

        default:
          console.log("❓ 未知消息类型:", message.type, message);
      }
    } catch (error) {
      console.error("❌ 解析WebSocket消息时出错:", {
        error: error.message,
        stack: error.stack,
        rawData: data,
        dataType: typeof data,
        dataLength: data.length,
        dataPreview: data.substring(0, 200) + (data.length > 200 ? "..." : ""),
      });

      showErrorMessage(`消息解析错误: ${error.message}`);
    }
  }

  // 绑定事件处理器
  function bindEvents() {
    console.log("🔗 绑定事件处理器");

    // 发送按钮点击
    if (elements.sendBtn) {
      elements.sendBtn.addEventListener("click", sendMessage);
      console.log("✅ 绑定发送按钮事件");
    } else {
      console.warn("⚠️ 未找到发送按钮");
    }

    // 输入框回车发送
    if (elements.input) {
      elements.input.addEventListener("keypress", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendMessage();
        }
      });
      console.log("✅ 绑定输入框事件");
    } else {
      console.warn("⚠️ 未找到输入框");
    }

    // RAG开关
    if (elements.ragToggle) {
      elements.ragToggle.parentElement.addEventListener("click", toggleRAG);
      console.log("✅ 绑定RAG开关事件");
    }

    // 工具开关
    if (elements.toolsToggle) {
      elements.toolsToggle.parentElement.addEventListener("click", toggleTools);
      console.log("✅ 绑定工具开关事件");
    }

    // 快速操作按钮
    if (elements.quickActions && elements.quickActions.length > 0) {
      elements.quickActions.forEach((item, index) => {
        item.addEventListener("click", handleQuickAction);
        console.log(`✅ 绑定快速操作按钮 ${index}`);
      });
    }

    // 模型切换
      if (elements.modelSelect) {
        elements.modelSelect.addEventListener("change", handleModelChange);
        console.log("✅ 绑定模型切换事件");
      }

      if (elements.settingsBtn) {
        elements.settingsBtn.addEventListener("click", openLlmSettings);
      }
      if (elements.closeLlmSettings) {
        elements.closeLlmSettings.addEventListener("click", closeLlmSettings);
      }
      if (elements.llmSettingsOverlay) {
        elements.llmSettingsOverlay.addEventListener("click", (event) => {
          if (event.target === elements.llmSettingsOverlay) {
            closeLlmSettings();
          }
        });
      }
      if (elements.testLlmConfig) {
        elements.testLlmConfig.addEventListener("click", testLlmConfig);
      }
      if (elements.saveLlmConfig) {
        elements.saveLlmConfig.addEventListener("click", saveLlmConfig);
      }

      // 侧边栏控制
      if (elements.closeSidebarBtn) {
        elements.closeSidebarBtn.addEventListener("click", () =>
        setSidebarVisible(false)
      );
    }
    if (elements.openSidebarBtn) {
      elements.openSidebarBtn.addEventListener("click", () =>
        setSidebarVisible(true)
      );
    }
    if (elements.sidebarMask) {
      elements.sidebarMask.addEventListener("click", () =>
        setSidebarVisible(false)
      );
    }
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          setSidebarVisible(false);
          closeLlmSettings();
        }
      });
    }

    async function openLlmSettings() {
      if (!elements.llmSettingsOverlay) return;
      elements.llmSettingsOverlay.classList.add("is-open");
      elements.llmSettingsOverlay.setAttribute("aria-hidden", "false");
      setLlmSettingsStatus("正在读取当前模型配置...");
      try {
        const response = await fetch("/api/llm/config");
        const result = await response.json();
        if (!result.success) {
          throw new Error(result.message || "读取配置失败");
        }
        fillLlmSettingsForm(result.config || {});
        setLlmSettingsStatus("配置已读取。API Key 不会回传明文。");
      } catch (error) {
        console.error("读取 LLM 配置失败:", error);
        setLlmSettingsStatus(`读取配置失败：${error.message}`, true);
      }
    }

    function closeLlmSettings() {
      if (!elements.llmSettingsOverlay) return;
      elements.llmSettingsOverlay.classList.remove("is-open");
      elements.llmSettingsOverlay.setAttribute("aria-hidden", "true");
    }

    function fillLlmSettingsForm(config) {
      if (elements.llmProvider) elements.llmProvider.value = config.provider || "ollama";
      if (elements.llmStream) elements.llmStream.value = String(config.stream !== false);
      if (elements.llmBaseUrl) elements.llmBaseUrl.value = config.base_url || "";
      if (elements.llmModelName) elements.llmModelName.value = config.model_name || "";
      if (elements.llmApiKey) elements.llmApiKey.value = "";
      if (elements.llmApiKeyHint) {
        elements.llmApiKeyHint.textContent = config.api_key_configured
          ? `已配置：${config.api_key_hint || "******"}。留空保存会沿用原 Key。`
          : "未配置 API Key。本地 Ollama 可留空。";
      }
    }

    function collectLlmSettingsForm() {
      return {
        provider: elements.llmProvider ? elements.llmProvider.value : "ollama",
        base_url: elements.llmBaseUrl ? elements.llmBaseUrl.value : "",
        model_name: elements.llmModelName ? elements.llmModelName.value : "",
        api_key: elements.llmApiKey ? elements.llmApiKey.value : "",
        stream: elements.llmStream ? elements.llmStream.value === "true" : true,
      };
    }

    function setLlmSettingsStatus(message, isError = false) {
      if (!elements.llmSettingsStatus) return;
      elements.llmSettingsStatus.textContent = message || "";
      elements.llmSettingsStatus.style.color = isError ? "#b91c1c" : "#64748b";
    }

    async function testLlmConfig() {
      const payload = collectLlmSettingsForm();
      setLlmSettingsStatus("正在测试连接...");
      try {
        const response = await fetch("/api/llm/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        setLlmSettingsStatus(
          result.message || (result.success ? "连接测试成功" : "连接测试失败"),
          !result.success
        );
      } catch (error) {
        console.error("测试 LLM 连接失败:", error);
        setLlmSettingsStatus(`连接测试失败：${error.message}`, true);
      }
    }

    async function saveLlmConfig() {
      const payload = collectLlmSettingsForm();
      setLlmSettingsStatus("正在保存并启用配置...");
      try {
        const response = await fetch("/api/llm/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!result.success) {
          throw new Error(result.message || "保存失败");
        }
        fillLlmSettingsForm(result.config || {});
        setLlmSettingsStatus(result.message || "模型接入配置已保存并启用。");
        if (elements.connectionStatus) {
          elements.connectionStatus.textContent = "已连接";
          elements.connectionStatus.style.backgroundColor = "#d1fae5";
          elements.connectionStatus.style.color = "#065f46";
        }
        HomeChatRenderer.showToast("模型接入配置已保存", "success");
      } catch (error) {
        console.error("保存 LLM 配置失败:", error);
        setLlmSettingsStatus(`保存失败：${error.message}`, true);
        HomeChatRenderer.showToast("模型接入配置保存失败", "error");
      }
    }

    // 处理模型切换
    function bindToolbarControls() {}

  function toggleSidebar() {
    const isClosing = elements.sidebarPanel.classList.contains("is-closing");
    setSidebarVisible(isClosing);
  }

  let isSidebarAnimating = false;
  function setSidebarVisible(visible) {
    if (isSidebarAnimating) return;

    const sidebar = elements.sidebarPanel;
    const mask = elements.sidebarMask;
    const openBtn = elements.openSidebarBtn;

    if (!sidebar || !mask) return;

    if (visible) {
      // 打开侧边栏
      isSidebarAnimating = true;
      
      // 只有在小屏幕下才激活遮罩层显影
      if (window.innerWidth <= 1024) {
        mask.classList.add("is-active");
      }

      // 强制重绘以触发动画
      sidebar.offsetHeight;

      sidebar.classList.remove("is-closing");
      sidebar.classList.add("is-open");
      if (openBtn) openBtn.classList.remove("is-visible");

      setTimeout(() => {
        isSidebarAnimating = false;
      }, 300);
    } else {
      // 关闭侧边栏
      if (sidebar.classList.contains("is-closing")) return;

      isSidebarAnimating = true;
      sidebar.classList.remove("is-open");
      sidebar.classList.add("is-closing");
      mask.classList.remove("is-active");

      setTimeout(() => {
        if (openBtn) openBtn.classList.add("is-visible");
        isSidebarAnimating = false;
      }, 280);
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

    document.body.classList.remove("chat-active");

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

  function updateToolbarControlStates() {}

  function bindToolPanels() {}

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

  function handleManagedFilesSelected(event) {
    const incomingFiles = Array.from(event.target.files || []).map(function (
      file
    ) {
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

    const existingIds = new Set(
      HomeState.managedFiles.map(function (file) {
        return file.id;
      })
    );

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

    elements.managedFileList.innerHTML = HomeState.managedFiles
      .map(function (file) {
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
      })
      .join("");

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

  async function handleModelChange(e) {
    const selectedModel = e.target.value;
    console.log(`🔄 切换模型: ${selectedModel}`);

    // 更新连接状态
    if (elements.connectionStatus) {
      elements.connectionStatus.textContent = "切换中...";
      elements.connectionStatus.style.backgroundColor = "#fef3c7";
      elements.connectionStatus.style.color = "#92400e";
    }

    try {
      const response = await fetch("/api/switch_model", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ model: selectedModel }),
      });

      const result = await response.json();

      if (result.success) {
        console.log("✅ 模型切换成功:", result.message);

        // 更新状态显示
        if (elements.connectionStatus) {
          elements.connectionStatus.textContent = "已连接";
          elements.connectionStatus.style.backgroundColor = "#d1fae5";
          elements.connectionStatus.style.color = "#065f46";
        }

        // 显示Toast通知
        HomeChatRenderer.showToast(
          `已切换到 ${getModelDisplayName(selectedModel)}`,
          "success"
        );

        // 如果在聊天模式，也添加系统消息
        if (chatMode) {
          HomeChatRenderer.addSystemMessage(
            `已切换到 ${getModelDisplayName(selectedModel)}`
          );
        }
      } else {
        console.error("❌ 模型切换失败:", result.message);

        if (elements.connectionStatus) {
          elements.connectionStatus.textContent = "切换失败";
          elements.connectionStatus.style.backgroundColor = "#fee2e2";
          elements.connectionStatus.style.color = "#991b1b";
        }

        // 显示错误Toast
        HomeChatRenderer.showToast(
          `模型切换失败: ${result.message}`,
          "error"
        );

        // 如果在聊天模式，也添加系统消息
        if (chatMode) {
          HomeChatRenderer.addSystemMessage(
            `模型切换失败: ${result.message}`,
            true
          );
        }
      }
    } catch (error) {
      console.error("❌ 模型切换请求失败:", error);

      if (elements.connectionStatus) {
        elements.connectionStatus.textContent = "切换失败";
        elements.connectionStatus.style.backgroundColor = "#fee2e2";
        elements.connectionStatus.style.color = "#991b1b";
      }

      // 显示错误Toast
      HomeChatRenderer.showToast("模型切换失败，请检查网络连接", "error");

      // 如果在聊天模式，也添加系统消息
      if (chatMode) {
        HomeChatRenderer.addSystemMessage("模型切换失败，请检查网络连接", true);
      }
    }
  }

  // 获取模型显示名称
  function getModelDisplayName(modelKey) {
    const modelNames = {
      glm4: "GLM-4.6 (魔搭社区)",
      qwen3: "Qwen3-235B (魔搭社区)",
    };
    return modelNames[modelKey] || modelKey;
  }

  // 添加提示：gmm-llama 仅用作工具
  console.log(
    "💡 注意: gmm-llama:latest 仅用作分子生成工具，不再作为主聊天模型"
  );

  // 显示Toast通知
  function showToast(message, type = "success") {
    // 创建toast元素
    const toast = document.createElement("div");
    toast.className = "model-switch-toast";

    const bgColor =
      type === "success" ? "#10b981" : type === "error" ? "#ef4444" : "#f59e0b";
    const icon = type === "success" ? "✓" : type === "error" ? "✗" : "ℹ";

    toast.style.cssText = `
      position: fixed;
      top: 80px;
      right: 30px;
      background: ${bgColor};
      color: white;
      padding: 16px 24px;
      border-radius: 12px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
      font-size: 15px;
      font-weight: 500;
      z-index: 10000;
      animation: slideInRight 0.3s ease-out;
      display: flex;
      align-items: center;
      gap: 10px;
    `;

    toast.innerHTML = `
      <span style="font-size: 20px;">${icon}</span>
      <span>${message}</span>
    `;

    // 添加动画样式
    if (!document.getElementById("toast-animation-style")) {
      const style = document.createElement("style");
      style.id = "toast-animation-style";
      style.textContent = `
        @keyframes slideInRight {
          from {
            transform: translateX(400px);
            opacity: 0;
          }
          to {
            transform: translateX(0);
            opacity: 1;
          }
        }
        @keyframes slideOutRight {
          from {
            transform: translateX(0);
            opacity: 1;
          }
          to {
            transform: translateX(400px);
            opacity: 0;
          }
        }
      `;
      document.head.appendChild(style);
    }

    document.body.appendChild(toast);

    // 3秒后自动消失
    setTimeout(() => {
      toast.style.animation = "slideOutRight 0.3s ease-out";
      setTimeout(() => {
        if (toast.parentNode) {
          toast.parentNode.removeChild(toast);
        }
      }, 300);
    }, 3000);
  }

  // 添加系统消息（仅在聊天模式下）
  function addSystemMessage(message, isError = false) {
    // 只在聊天模式下显示系统消息
    if (!chatMode || !elements.chatContainer) {
      return;
    }

    const msgDiv = document.createElement("div");
    msgDiv.className = "chat-message system-message";
    msgDiv.style.cssText = `
      margin: 15px 0;
      padding: 12px 18px;
      background: ${isError ? "#fee2e2" : "#f0f9ff"};
      border-left: 4px solid ${isError ? "#dc2626" : "#0284c7"};
      border-radius: 8px;
      font-size: 14px;
      color: ${isError ? "#991b1b" : "#1e40af"};
      text-align: center;
    `;
    msgDiv.textContent = `ℹ️ ${message}`;

    elements.chatContainer.appendChild(msgDiv);
    elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
  }

  // 修复后的消息发送函数
  function sendMessage() {
    const message = elements.input.value.trim();

    console.log("📤 准备发送消息:", {
      message: message,
      messageLength: message.length,
      wsExists: !!ws,
      wsReadyState: ws ? ws.readyState : "null",
      isConnected: isConnected,
    });

    if (!message) {
      HomeChatRenderer.showNotification("请输入消息内容", "warning");
      return;
    }

    // 更严格的连接状态检查
    if (!ws) {
      console.log("❌ WebSocket对象不存在，尝试重连");
      HomeChatRenderer.showNotification("连接不存在，正在重新连接...", "error");
      connectWebSocket();
      return;
    }

    if (ws.readyState !== WebSocket.OPEN) {
      console.log("❌ WebSocket未就绪:", {
        readyState: ws.readyState,
        states: {
          CONNECTING: WebSocket.CONNECTING,
          OPEN: WebSocket.OPEN,
          CLOSING: WebSocket.CLOSING,
          CLOSED: WebSocket.CLOSED,
        },
      });

      HomeChatRenderer.showNotification("连接未就绪，正在重新连接...", "error");

      if (ws.readyState === WebSocket.CLOSED) {
        connectWebSocket();
      }
      return;
    }

    try {
      // 切换到聊天模式
      if (!chatMode) {
        enterChatMode();
      }

      // 添加用户消息到界面
      addUserMessage(message);
      resetAgentTaskPanel();

      // 构建发送数据 - 确保格式正确，包含高级配置
      const advancedConfig = HomeAdvancedOptions.getConfig();
      const payload = {
        type: "chat",
        message: message,
        enable_rag: ragEnabled,
        enable_tools: toolsEnabled,
        rag_count: advancedConfig.ragCount, // RAG检索数量
        temperature: advancedConfig.temperature, // 生成温度
        mol_count: advancedConfig.molCount, // 分子生成数量
        timestamp: Date.now(),
        client_id: "web_client",
      };

      const payloadStr = JSON.stringify(payload);

      console.log("📤 发送消息到WebSocket:", {
        payload: payload,
        payloadString: payloadStr,
        wsReadyState: ws.readyState,
        payloadSize: payloadStr.length,
      });

      // 发送到服务器
      ws.send(payloadStr);

      // 清空输入框
      elements.input.value = "";

      // 显示正在输入状态
      showTypingIndicator();

      console.log("✅ 消息发送成功");
    } catch (error) {
      console.error("❌ 发送消息时出错:", {
        error: error.message,
        stack: error.stack,
        wsReadyState: ws ? ws.readyState : "null",
      });

      showErrorMessage(`发送失败: ${error.message}`);
      removeTypingIndicator();

      // 如果是网络错误，尝试重连
      if (
        error.name === "NetworkError" ||
        error.message.includes("WebSocket")
      ) {
        console.log("🔄 检测到网络错误，尝试重连");
        connectWebSocket();
      }
    }
  }

  // 添加连接诊断函数
  function diagnoseWebSocket() {
    console.log("=== WebSocket 诊断信息 ===");
    console.log("时间戳:", new Date().toISOString());
    console.log("页面URL:", window.location.href);
    console.log("WebSocket URL:", `ws://${window.location.host}/ws`);
    console.log("重连尝试次数:", reconnectAttempts);
    console.log("isConnected标志:", isConnected);
    console.log("chatMode:", chatMode);

    if (ws) {
      console.log("WebSocket对象存在");
      console.log("WebSocket详细状态:", {
        readyState: ws.readyState,
        url: ws.url,
        protocol: ws.protocol,
        extensions: ws.extensions,
        bufferedAmount: ws.bufferedAmount,
      });

      console.log("WebSocket状态说明:", {
        CONNECTING: `${WebSocket.CONNECTING} (连接中)`,
        OPEN: `${WebSocket.OPEN} (已连接)`,
        CLOSING: `${WebSocket.CLOSING} (正在关闭)`,
        CLOSED: `${WebSocket.CLOSED} (已关闭)`,
        current: `${ws.readyState} (当前状态)`,
      });

      // 测试发送消息
      if (ws.readyState === WebSocket.OPEN) {
        try {
          sendTestMessage();
          console.log("✅ 诊断测试消息发送成功");
        } catch (error) {
          console.error("❌ 诊断测试消息发送失败:", error);
        }
      }
    } else {
      console.log("❌ WebSocket对象不存在");
    }

    console.log("DOM元素状态:", {
      input: !!elements.input,
      sendBtn: !!elements.sendBtn,
      chatContainer: !!elements.chatContainer,
      connectionStatus: !!elements.connectionStatus,
    });

    console.log("=== 诊断结束 ===");
  }

  // 进入聊天模式
  function enterChatMode() {
    chatMode = true;
    HomeState.chatMode = true;
    console.log("🎯 进入聊天模式");

    // 隐藏欢迎内容
    const mass = document.querySelector(".mass");
    const iconList = document.querySelector(".icon_list");
    if (mass) mass.style.display = "none";
    if (iconList) iconList.style.display = "none";

    // 显示聊天容器（清除 inline display 让 CSS 的 flex:1 生效）
    if (elements.chatContainer) {
      elements.chatContainer.style.display = "";
    }

    // 添加聊天模式类（body 锁定视口，.right 控制子元素显示）
    document.body.classList.add("chat-active");
    document.querySelector(".index_con .right").classList.add("chat-mode");
  }

  // 获取当前时间
  function getCurrentTime() {
    const now = new Date();
    const hours = now.getHours().toString().padStart(2, "0");
    const minutes = now.getMinutes().toString().padStart(2, "0");
    return `${hours}:${minutes}`;
  }

  // 添加用户消息 - 美化版
  function addUserMessage(content) {
    const messageWrapper = document.createElement("div");
    messageWrapper.className = "message-wrapper user-wrapper";
    messageWrapper.style.cssText = `
            display: flex;
            justify-content: flex-end;
            margin: 25px 0;
            animation: slideInRight 0.4s ease-out;
        `;

    const messageContainer = document.createElement("div");
    messageContainer.style.cssText = `
            display: flex;
            align-items: flex-start;
            max-width: 70%;
            gap: 12px;
        `;

    // 消息内容 (胶囊气泡设计，移除时间，使用主题色)
    const messageBox = document.createElement("div");
    messageBox.className = "message-box user-message";
    messageBox.style.cssText = `
            position: relative;
            padding: 12px 24px;
            background: var(--theme-primary, #60a5fa);
            color: #1e293b;
            border-radius: 30px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
            word-wrap: break-word;
            font-size: 15px;
            line-height: 1.5;
            animation: messagePopIn 0.3s ease-out;
            border: 1px solid var(--theme-primary-border, transparent);
        `;

    const messageContent = document.createElement("div");
    messageContent.textContent = content;

    messageBox.appendChild(messageContent);

    // 用户头像 (颜色和主题联动)
    const avatar = document.createElement("div");
    avatar.style.cssText = `
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--theme-primary, #60a5fa);
            display: flex;
            align-items: center;
            justify-content: center;
            color: #1e293b;
            font-weight: bold;
            font-size: 18px;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.08);
            flex-shrink: 0;
            border: 1px solid var(--theme-primary-border, transparent);
        `;
    avatar.textContent = "我";

    messageContainer.appendChild(messageBox);
    messageContainer.appendChild(avatar);
    messageWrapper.appendChild(messageContainer);
    elements.chatContainer.appendChild(messageWrapper);

    // 滚动到底部
    HomeChatRenderer.scrollToBottom();

    // 保存到历史
    currentMessages.push({ role: "user", content: content });
    HomeState.currentMessages = currentMessages.slice();
    updateToolbarControlStates();
  }

  // 添加助手消息 - 美化版
  function addAssistantMessage(content) {
    removeTypingIndicator();

    const messageWrapper = document.createElement("div");
    messageWrapper.className = "message-wrapper assistant-wrapper";
    messageWrapper.style.cssText = `
            display: flex;
            justify-content: flex-start;
            margin: 25px 0;
            animation: slideInLeft 0.4s ease-out;
        `;

    const messageContainer = document.createElement("div");
    messageContainer.style.cssText = `
            display: flex;
            align-items: flex-start;
            max-width: 70%;
            gap: 12px;
        `;

    // AI头像
    const avatar = document.createElement("div");
    avatar.style.cssText = `
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            font-size: 16px;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.15);
            flex-shrink: 0;
        `;
    avatar.innerHTML = "🤖";

    // 消息内容
    const messageBox = document.createElement("div");
    messageBox.className = "message-box assistant-message";
    messageBox.style.cssText = `
            position: relative;
            padding: 16px 20px;
            background: white;
            color: #2d3748;
            border-radius: 20px 20px 20px 4px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
            word-wrap: break-word;
            font-size: 15px;
            line-height: 1.6;
            animation: messagePopIn 0.3s ease-out;
            border: 1px solid rgba(0, 0, 0, 0.05);
        `;

    // 添加时间戳
    const timeStamp = document.createElement("div");
    timeStamp.style.cssText = `
            font-size: 11px;
            color: #718096;
            margin-bottom: 8px;
        `;
    timeStamp.textContent = HomeChatRenderer.getCurrentTime();

    const messageContent = document.createElement("div");
      messageContent.innerHTML = HomeFormatters.formatContent(content);

    messageBox.appendChild(timeStamp);
    messageBox.appendChild(messageContent);

    messageContainer.appendChild(avatar);
    messageContainer.appendChild(messageBox);
    messageWrapper.appendChild(messageContainer);
    elements.chatContainer.appendChild(messageWrapper);

    // 滚动到底部
    HomeChatRenderer.scrollToBottom();

    // 保存到历史
    currentMessages.push({ role: "assistant", content: content });
    HomeState.currentMessages = currentMessages.slice();
    updateToolbarControlStates();
  }

  // 追加到最后一条消息（用于流式输出）
  function appendToLastMessage(content) {
    removeTypingIndicator();

    let lastMessage = elements.chatContainer.querySelector(
      ".assistant-wrapper:last-child .message-box"
    );

    if (!lastMessage || lastMessage.classList.contains("complete")) {
      // 创建新消息
      const messageWrapper = document.createElement("div");
      messageWrapper.className = "message-wrapper assistant-wrapper";
      messageWrapper.style.cssText = `
                display: flex;
                justify-content: flex-start;
                margin: 25px 0;
            `;

      const messageContainer = document.createElement("div");
      messageContainer.style.cssText = `
                display: flex;
                align-items: flex-start;
                max-width: 70%;
                gap: 12px;
            `;

      // AI头像
      const avatar = document.createElement("div");
      avatar.style.cssText = `
                width: 40px;
                height: 40px;
                border-radius: 50%;
                background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                font-size: 16px;
                box-shadow: 0 3px 10px rgba(0, 0, 0, 0.15);
                flex-shrink: 0;
            `;
      avatar.innerHTML = "🤖";

      // 消息内容
      lastMessage = document.createElement("div");
      lastMessage.className = "message-box assistant-message streaming";
      lastMessage.style.cssText = `
                position: relative;
                padding: 16px 20px;
                background: white;
                color: #2d3748;
                border-radius: 20px 20px 20px 4px;
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
                word-wrap: break-word;
                font-size: 15px;
                line-height: 1.6;
                border: 1px solid rgba(0, 0, 0, 0.05);
            `;

      const timeStamp = document.createElement("div");
      timeStamp.style.cssText = `
                font-size: 11px;
                color: #718096;
                margin-bottom: 8px;
            `;
    timeStamp.textContent = HomeChatRenderer.getCurrentTime();

      const messageContent = document.createElement("div");
      messageContent.className = "message-content";

      lastMessage.appendChild(timeStamp);
      lastMessage.appendChild(messageContent);

      messageContainer.appendChild(avatar);
      messageContainer.appendChild(lastMessage);
      messageWrapper.appendChild(messageContainer);
      elements.chatContainer.appendChild(messageWrapper);
    }

    // 追加内容
    const messageContent =
      lastMessage.querySelector(".message-content") ||
      lastMessage.querySelector("div:last-child");
    const currentContent = lastMessage.getAttribute("data-content") || "";
    const newContent = currentContent + content;
    lastMessage.setAttribute("data-content", newContent);
      messageContent.innerHTML = HomeFormatters.formatContent(newContent);

    // 添加光标闪烁效果
    if (!messageContent.querySelector(".cursor")) {
      const cursor = document.createElement("span");
      cursor.className = "cursor";
      cursor.style.cssText = `
                display: inline-block;
                width: 2px;
                height: 18px;
                background: #4a5568;
                margin-left: 2px;
                animation: blink 1s infinite;
            `;
      messageContent.appendChild(cursor);
    }

    // 滚动到底部
    HomeChatRenderer.scrollToBottom();
  }

  // 完成最后一条消息
  function completeLastMessage() {
    const lastMessage = elements.chatContainer.querySelector(
      ".assistant-wrapper:last-child .message-box"
    );
    if (lastMessage) {
      lastMessage.classList.remove("streaming");
      lastMessage.classList.add("complete");

      // 移除光标
      const cursor = lastMessage.querySelector(".cursor");
      if (cursor) {
        cursor.remove();
      }

      // 保存完整内容到历史
      const content =
        lastMessage.getAttribute("data-content") || lastMessage.textContent;
    currentMessages.push({ role: "assistant", content: content });
    HomeState.currentMessages = currentMessages.slice();
    updateToolbarControlStates();

      // ✨ 检测并渲染SMILES分子结构
      detectAndRenderMolecules(lastMessage, content);
    }
  }

  // 显示状态消息 - 美化版
  function showStatusMessage(message) {
    const statusDiv = document.createElement("div");
    statusDiv.className = "status-message";
    statusDiv.style.cssText = `
            margin: 15px auto;
            padding: 10px 20px;
            background: linear-gradient(135deg, #667eea20 0%, #764ba220 100%);
            color: #5a67d8;
            border-radius: 30px;
            text-align: center;
            font-size: 13px;
            max-width: 400px;
            animation: fadeIn 0.3s ease-out;
            border: 1px solid rgba(102, 126, 234, 0.2);
            backdrop-filter: blur(10px);
        `;

    statusDiv.innerHTML = `<span style="margin-right: 8px;">✨</span>${message}`;
    elements.chatContainer.appendChild(statusDiv);

    // 3秒后自动移除
    setTimeout(() => {
      statusDiv.style.opacity = "0";
      setTimeout(() => statusDiv.remove(), 300);
    }, 3000);

    HomeChatRenderer.scrollToBottom();
  }

  // 显示工具状态 - 美化版
  function showToolStatus(message) {
    const toolDiv = document.createElement("div");
    toolDiv.className = "tool-status";
    toolDiv.style.cssText = `
            margin: 15px auto;
            padding: 12px 24px;
            background: linear-gradient(135deg, #f6d36520 0%, #fca40420 100%);
            color: #d97706;
            border-radius: 30px;
            text-align: center;
            font-size: 14px;
            max-width: 500px;
            animation: pulse 2s infinite;
            border: 1px solid rgba(251, 191, 36, 0.3);
            backdrop-filter: blur(10px);
        `;

    toolDiv.innerHTML = `🔧 ${message}`;
    elements.chatContainer.appendChild(toolDiv);

    HomeChatRenderer.scrollToBottom();
  }

  function createAgentTaskPanel() {
    if (!elements.chatContainer) return null;

    let panel = elements.chatContainer.querySelector(".agent-task-panel");
    if (panel) {
      elements.agentTaskPanel = panel;
      return panel;
    }

    panel = document.createElement("div");
    panel.className = "agent-task-panel";
    panel.innerHTML = `
      <div class="agent-task-header">
        <div>
          <div class="agent-task-kicker">AGENT WORKFLOW</div>
          <div class="agent-task-title">智能任务执行</div>
        </div>
        <div class="agent-task-progress">准备中</div>
      </div>
      <div class="agent-task-list"></div>
    `;
    elements.chatContainer.appendChild(panel);
    elements.agentTaskPanel = panel;
    return panel;
  }

  function resetAgentTaskPanel() {
    const panel = createAgentTaskPanel();
    if (!panel) return;

    panel.classList.add("is-active");
    const progress = panel.querySelector(".agent-task-progress");
    const list = panel.querySelector(".agent-task-list");
    if (progress) progress.textContent = "执行中";
    if (list) list.innerHTML = "";
  }

  function handleAgentEvent(event) {
    const panel = createAgentTaskPanel();
    if (!panel) return;

    panel.classList.add("is-active");
    const progress = panel.querySelector(".agent-task-progress");
    const list = panel.querySelector(".agent-task-list");
    if (!list) return;

    const eventType = event.type || "agent_event";
    const toolName = event.tool_name || event.tool || "";
    const message = event.message || getAgentEventLabel(eventType, toolName);
    const item = document.createElement("div");
    item.className = `agent-task-item ${getAgentEventClass(eventType)}`;
    item.innerHTML = `
      <span class="agent-task-dot"></span>
      <div class="agent-task-copy">
        <strong>${escapeHtml(getAgentEventLabel(eventType, toolName))}</strong>
        <span>${escapeHtml(message)}</span>
      </div>
    `;
    list.appendChild(item);

    if (progress) {
      const percent =
        typeof event.progress === "number"
          ? Math.round(Math.max(0, Math.min(1, event.progress)) * 100)
          : null;
      progress.textContent =
        eventType.includes("completed") || eventType === "task_completed"
          ? "已完成"
          : percent !== null
          ? `${percent}%`
          : "执行中";
    }

    HomeChatRenderer.scrollToBottom();
  }

  function getAgentEventLabel(type, toolName) {
    const toolText = toolName ? formatToolName(toolName) : "";
    const labels = {
      planning_started: "任务规划",
      planning_completed: "规划完成",
      task_started: "任务开始",
      task_completed: "任务完成",
      task_failed: "任务失败",
      tool_started: toolText ? `调用 ${toolText}` : "工具调用",
      tool_completed: toolText ? `${toolText} 完成` : "工具完成",
      tool_failed: toolText ? `${toolText} 失败` : "工具失败",
      validation_warning: "结果校验提醒",
    };
    return labels[type] || "Agent 事件";
  }

  function getAgentEventClass(type) {
    if (type && type.includes("failed")) return "is-error";
    if (type === "validation_warning") return "is-warning";
    if (type && type.includes("completed")) return "is-complete";
    return "is-running";
  }

  function formatToolName(toolName) {
    const names = {
      property_calculator: "属性计算",
      admet_predictor: "ADMET预测",
      activity_predictor: "活性预测",
      reverse_target_predictor: "反向寻靶",
      target_database_search: "靶点库检索",
      llm_molecular_generator: "分子生成",
      molecular_docking: "分子对接",
    };
    return names[toolName] || toolName;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // 清除工具状态显示
  function clearToolStatus() {
    const toolStatusElements = document.querySelectorAll(".tool-status");
    toolStatusElements.forEach((element) => {
      element.style.opacity = "0";
      setTimeout(() => {
        if (element.parentNode) {
          element.parentNode.removeChild(element);
        }
      }, 300);
    });
  }

  // 显示错误消息 - 美化版
  function showErrorMessage(message) {
    removeTypingIndicator();

    const errorDiv = document.createElement("div");
    errorDiv.className = "error-message";
    errorDiv.style.cssText = `
            margin: 20px auto;
            padding: 16px 24px;
            background: linear-gradient(135deg, #fc8181 0%, #f56565 100%);
            color: white;
            border-radius: 16px;
            max-width: 70%;
            animation: shake 0.5s ease-out;
            box-shadow: 0 5px 15px rgba(239, 68, 68, 0.3);
        `;

    errorDiv.innerHTML = `
            <div style="display: flex; align-items: center; gap: 12px;">
                <span style="font-size: 20px;">⚠️</span>
                <span style="font-size: 15px;">${message}</span>
            </div>
        `;

    elements.chatContainer.appendChild(errorDiv);
    HomeChatRenderer.scrollToBottom();
  }

  // 显示输入指示器 - 美化版
  function showTypingIndicator() {
    removeTypingIndicator();

    const typingWrapper = document.createElement("div");
    typingWrapper.className = "typing-indicator-wrapper";
    typingWrapper.style.cssText = `
            display: flex;
            justify-content: flex-start;
            margin: 20px 0;
            animation: fadeIn 0.3s ease-out;
        `;

    const typingContainer = document.createElement("div");
    typingContainer.style.cssText = `
            display: flex;
            align-items: center;
            gap: 12px;
        `;

    // AI头像
    const avatar = document.createElement("div");
    avatar.style.cssText = `
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            box-shadow: 0 3px 10px rgba(0, 0, 0, 0.15);
        `;
    avatar.innerHTML = "🤖";

    const typingDiv = document.createElement("div");
    typingDiv.className = "typing-indicator";
    typingDiv.style.cssText = `
            padding: 16px 24px;
            background: white;
            border-radius: 20px;
            box-shadow: 0 5px 15px rgba(0, 0, 0, 0.08);
            border: 1px solid rgba(0, 0, 0, 0.05);
        `;

    typingDiv.innerHTML = `
            <div style="display: flex; gap: 6px; align-items: center;">
                <div style="width: 10px; height: 10px; background: #a0aec0; border-radius: 50%; animation: typing 1.4s infinite;"></div>
                <div style="width: 10px; height: 10px; background: #a0aec0; border-radius: 50%; animation: typing 1.4s infinite 0.2s;"></div>
                <div style="width: 10px; height: 10px; background: #a0aec0; border-radius: 50%; animation: typing 1.4s infinite 0.4s;"></div>
            </div>
        `;

    typingContainer.appendChild(avatar);
    typingContainer.appendChild(typingDiv);
    typingWrapper.appendChild(typingContainer);
    elements.chatContainer.appendChild(typingWrapper);
    HomeChatRenderer.scrollToBottom();
  }

  // 移除输入指示器
  function removeTypingIndicator() {
    const typingDiv = elements.chatContainer.querySelector(
      ".typing-indicator-wrapper"
    );
    if (typingDiv) {
      typingDiv.remove();
    }
  }

  // 切换RAG状态
  function toggleRAG() {
    ragEnabled = !ragEnabled;
    updateToggleStates();
    HomeChatRenderer.showNotification(
      `RAG检索已${ragEnabled ? "启用" : "禁用"}`,
      "info"
    );
    console.log("🔄 RAG状态切换:", ragEnabled);
  }

  // 切换工具状态
  function toggleTools() {
    toolsEnabled = !toolsEnabled;
    updateToggleStates();
    HomeChatRenderer.showNotification(
      `智能工具已${toolsEnabled ? "启用" : "禁用"}`,
      "info"
    );
    console.log("🔄 工具状态切换:", toolsEnabled);
  }

  // 更新开关状态显示
  function updateToggleStates() {
    if (elements.ragToggle) {
      elements.ragToggle.style.backgroundColor = ragEnabled
        ? "#667eea"
        : "#cbd5e0";
    }

    if (elements.toolsToggle) {
      elements.toolsToggle.style.backgroundColor = toolsEnabled
        ? "#667eea"
        : "#cbd5e0";
    }
  }

  // 快速操作处理
  function handleQuickAction(event) {
    const item = event.currentTarget;
    const action = item.dataset.action || "";

    if (action === "knowledge-graph") {
      event.preventDefault();
      window.open("./cadd_interactive_radial.html", "_blank");
      return;
    }

    if (action === "admet") {
      return;
    }

    const quickMessages = {
      "analyze-ethanol": "请分析乙醇(CCO)的分子性质和ADMET特征",
      "generate-molecule": "请生成符合Lipinski五规则的类药分子，分子量小于500，LogP小于5",
    };

    const message = quickMessages[action];
    if (message) {
      elements.input.value = message;
      sendMessage();
    }
  }
  function displayRAGInfo(molecules, infoMessage) {
    console.log("📊 显示RAG信息:", molecules);

    if (!molecules || molecules.length === 0) {
      return;
    }

    // 创建分子信息卡片容器（作为消息气泡内部的内容）
    const ragInfoContainer = document.createElement("div");
    ragInfoContainer.className = "rag-info-container";
    ragInfoContainer.style.cssText = `
      width: 100%;
      min-width: 0;
      overflow: hidden;
      padding: 20px;
      background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);
      border-radius: 12px;
      border-left: 5px solid #0284c7;
      box-shadow: 0 4px 12px rgba(2, 132, 199, 0.15);
      animation: slideInUp 0.3s ease-out;
    `;

    // 标题和信息
    const header = document.createElement("div");
    header.style.cssText = `
      display: flex;
      align-items: center;
      margin-bottom: 20px;
      gap: 12px;
    `;

    const icon = document.createElement("div");
    icon.innerHTML = "🧬";
    icon.style.cssText = `
      font-size: 24px;
      width: 40px;
      height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(2, 132, 199, 0.1);
      border-radius: 50%;
    `;

    const headerText = document.createElement("div");
    headerText.innerHTML = `
      <div style="font-size: 18px; font-weight: bold; color: #0c4a6e; margin-bottom: 4px;">
        检索到的相关分子数据
      </div>
      <div style="font-size: 14px; color: #075985;">
        ${infoMessage || `共找到 ${molecules.length} 个相关分子`}
      </div>
    `;

    header.appendChild(icon);
    header.appendChild(headerText);
    ragInfoContainer.appendChild(header);

    // 分子卡片容器 - 使用与反向寻靶一致的网格布局（方案A+C）
    const moleculeCount = molecules.length;

    // 横向滑动布局参数（统一固定比例，避免随数量突变）
    const minCardWidth = "260px";
    const imageHeight = "180px";
    const gap = "20px";
    const itemsPerPage = moleculeCount; // 既然是横滑，无需分页，全部渲染

    // 分页逻辑（横排不再需要底部分页组件，但在原逻辑中保留变量防错）
    let currentPage = 0;
    const totalPages = 1;
    const needsPagination = false;

    const moleculesContainer = document.createElement("div");
    moleculesContainer.className = "rag-molecules-carousel custom-scrollbar";
    // 添加 mask-image 以实现右侧渐变消失效果
    moleculesContainer.style.cssText = `
      display: flex;
      flex-wrap: nowrap;
      overflow-x: auto;
      gap: ${gap};
      padding-bottom: 12px;
      padding-right: 40px; /* 留出右侧足够的透气空间 */
      scroll-behavior: smooth;
      -webkit-overflow-scrolling: touch;
      -webkit-mask-image: linear-gradient(to right, black 90%, transparent 100%);
      mask-image: linear-gradient(to right, black 90%, transparent 100%);
    `;

    // 渲染分子函数
    function renderRAGMolecules(page) {
      moleculesContainer.innerHTML = "";

      const startIdx = page * itemsPerPage;
      const endIdx = Math.min(startIdx + itemsPerPage, moleculeCount);
      const pageMolecules = molecules.slice(startIdx, endIdx);

      pageMolecules.forEach((mol, pageIndex) => {
        const globalIndex = startIdx + pageIndex;
        const smiles = mol.smiles || mol.SMILES || "";
        const similarity = mol.similarity
          ? (parseFloat(mol.similarity) * 100).toFixed(1)
          : "";
        const properties = mol.properties || {};

        // 创建与反向寻靶一致的卡片样式
        const molCard = document.createElement("div");
        molCard.className = "molecule-card";
        molCard.style.cssText = `
        flex: 0 0 ${minCardWidth};
        width: ${minCardWidth};
        background: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 0;
        overflow: hidden;
        transition: all 0.2s;
        display: flex;
        flex-direction: column;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
      `;

        // 鼠标悬停效果
        molCard.addEventListener("mouseenter", () => {
          molCard.style.transform = "translateY(-3px)";
          molCard.style.boxShadow = "0 8px 25px rgba(79, 70, 229, 0.15)";
          molCard.style.borderColor = "#4f46e5";
        });

        molCard.addEventListener("mouseleave", () => {
          molCard.style.transform = "translateY(0)";
          molCard.style.boxShadow = "0 1px 2px rgba(0,0,0,0.05)";
          molCard.style.borderColor = "#e2e8f0";
        });

        // 卡片头部 - 序号和相似度
        const cardHeader = document.createElement("div");
        cardHeader.style.cssText = `
          padding: 12px 16px;
          border-bottom: 1px solid #f1f5f9;
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: #f8fafc;
        `;
        cardHeader.innerHTML = `
          <div style="font-weight: 700; color: #4f46e5; font-size: 16px;">#${
            globalIndex + 1
          }</div>
          ${
            similarity
              ? `<div style="background: #dcfce7; color: #166534; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 20px;">${similarity}% 相似</div>`
              : ""
          }
        `;

        // 分子图片区域 - 使用后端API生成图片（动态高度）
        const imageSection = document.createElement("div");
        imageSection.style.cssText = `
          height: ${imageHeight};
          display: flex;
          justify-content: center;
          align-items: center;
          background: #fff;
          padding: 10px;
          border-bottom: 1px solid #f1f5f9;
        `;

        const molImage = document.createElement("img");
        molImage.src = `/api/utils/smiles_to_image?smiles=${encodeURIComponent(
          smiles
        )}&width=360&height=300`;
        molImage.alt = "分子结构";
        molImage.style.cssText = `max-width: 100%; max-height: 100%; object-fit: contain;`;
        molImage.loading = "lazy";
        molImage.onerror = function () {
          this.style.display = "none";
          imageSection.innerHTML = `
            <div style="color: #94a3b8; font-size: 12px; text-align: center;">
              <div style="font-size: 24px; margin-bottom: 8px;">⚠️</div>
              <div>无法加载分子结构</div>
            </div>
          `;
        };
        imageSection.appendChild(molImage);

        // 信息区域
        const infoSection = document.createElement("div");
        infoSection.style.cssText = `padding: 16px; flex: 1; display: flex; flex-direction: column; gap: 10px;`;

        // 显示分子属性 - 优化为紧凑的网格布局
        const propertyLabels = {
          qed: "QED",
          logp: "LogP",
          molwt: "分子量",
          hbd: "HBD",
          hba: "HBA",
          tpsa: "TPSA",
          sas: "SAS",
          numrotbonds: "可旋转键",
        };

        const allProperties = ["qed", "logp", "molwt", "hbd", "hba", "tpsa"];
        
        const propertiesGrid = document.createElement("div");
        propertiesGrid.style.cssText = `
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 6px 10px;
        `;

        allProperties.forEach((prop) => {
          const possibleKeys = [
            prop,
            prop.toLowerCase(),
            prop.toUpperCase(),
            prop.replace("numrotbonds", "NumRotBonds"),
          ];
          let foundValue = null;

          for (const key of possibleKeys) {
            if (properties[key] !== undefined && properties[key] !== null) {
              foundValue = properties[key];
              break;
            }
          }

          if (foundValue !== null) {
            const propValue = parseFloat(foundValue);
            
            // 根据属性类型设置颜色
            let valueColor = "#334155";
            let bgColor = "#f8fafc";
            if (prop === "qed") {
              valueColor = propValue >= 0.7 ? "#059669" : propValue >= 0.5 ? "#d97706" : "#dc2626";
              bgColor = propValue >= 0.7 ? "#ecfdf5" : propValue >= 0.5 ? "#fffbeb" : "#fef2f2";
            } else if (prop === "logp") {
              valueColor = propValue >= -0.4 && propValue <= 5.6 ? "#059669" : "#dc2626";
              bgColor = propValue >= -0.4 && propValue <= 5.6 ? "#ecfdf5" : "#fef2f2";
            } else if (prop === "molwt") {
              valueColor = propValue <= 500 ? "#059669" : propValue <= 600 ? "#d97706" : "#dc2626";
              bgColor = propValue <= 500 ? "#ecfdf5" : propValue <= 600 ? "#fffbeb" : "#fef2f2";
            }

            const propItem = document.createElement("div");
            propItem.style.cssText = `
              display: flex;
              justify-content: space-between;
              align-items: center;
              background: ${bgColor};
              padding: 6px 10px;
              border-radius: 6px;
              border: 1px solid rgba(0,0,0,0.03);
            `;

            propItem.innerHTML = `
              <span style="color: #64748b; font-size: 11px; font-weight: 500;">${propertyLabels[prop] || prop}</span>
              <span style="color: ${valueColor}; font-weight: 700; font-size: 12px; font-family: 'Inter', ui-sans-serif, system-ui;">${
                prop === "molwt" ? propValue.toFixed(1) : propValue.toFixed(2)
              }</span>
            `;
            propertiesGrid.appendChild(propItem);
          }
        });
        
        if (propertiesGrid.children.length > 0) {
            infoSection.appendChild(propertiesGrid);
        }

        // SMILES 折叠区域
        const smilesDetails = document.createElement("details");
        smilesDetails.style.cssText = `margin-top: 4px;`;
        smilesDetails.innerHTML = `
        <summary style="cursor: pointer; color: #64748b; font-size: 12px; user-select: none;">显示 SMILES</summary>
        <div style="margin-top: 6px; padding: 8px; background: #f8fafc; border-radius: 4px; font-family: monospace; font-size: 11px; color: #475569; word-break: break-all; border: 1px solid #e2e8f0;">
          ${smiles}
        </div>
      `;
        infoSection.appendChild(smilesDetails);

        // 操作按钮
        const actionsSection = document.createElement("div");
        actionsSection.style.cssText = `
        display: flex;
        gap: 8px;
        padding: 12px 16px;
        border-top: 1px solid #f1f5f9;
        background: #f8fafc;
      `;

        const copyBtn = document.createElement("button");
        copyBtn.style.cssText = `
        flex: 1;
        background: #4f46e5;
        color: white;
        border: none;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 12px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
      `;
        copyBtn.textContent = "复制 SMILES";
        copyBtn.onclick = () => copyToClipboard(smiles);

        const analyzeBtn = document.createElement("button");
        analyzeBtn.style.cssText = `
        flex: 1;
        background: #10b981;
        color: white;
        border: none;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 12px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
      `;
        analyzeBtn.textContent = "深入分析";
        analyzeBtn.onclick = () => analyzeMolecule(smiles);

        // 悬停效果
        [copyBtn, analyzeBtn].forEach((btn) => {
          btn.addEventListener("mouseenter", () => {
            btn.style.opacity = "0.9";
          });
          btn.addEventListener("mouseleave", () => {
            btn.style.opacity = "1";
          });
        });

        actionsSection.appendChild(copyBtn);
        actionsSection.appendChild(analyzeBtn);

        // 组装卡片
        molCard.appendChild(cardHeader);
        molCard.appendChild(imageSection);
        molCard.appendChild(infoSection);
        molCard.appendChild(actionsSection);
        moleculesContainer.appendChild(molCard);
      });
    }

    // 初始渲染第一页
    renderRAGMolecules(currentPage);
    ragInfoContainer.appendChild(moleculesContainer);

    // 添加分页控件（如果需要）
    if (needsPagination) {
      const paginationContainer = document.createElement("div");
      paginationContainer.style.cssText = `
        margin-top: 20px;
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 12px;
        padding: 16px;
        background: rgba(255, 255, 255, 0.6);
        border-radius: 8px;
        border: 1px solid rgba(2, 132, 199, 0.2);
      `;

      const pageInfo = document.createElement("div");
      pageInfo.className = "page-info";
      pageInfo.style.cssText = `
        font-size: 13px;
        color: #075985;
        font-weight: 600;
      `;

      const prevBtn = document.createElement("button");
      prevBtn.innerHTML = "← 上一页";
      prevBtn.style.cssText = `
        padding: 8px 16px;
        background: #fff;
        border: 1px solid #0284c7;
        border-radius: 6px;
        color: #0284c7;
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
      `;
      prevBtn.disabled = currentPage === 0;
      if (prevBtn.disabled) {
        prevBtn.style.opacity = "0.5";
        prevBtn.style.cursor = "not-allowed";
      }

      const nextBtn = document.createElement("button");
      nextBtn.innerHTML = "下一页 →";
      nextBtn.style.cssText = `
        padding: 8px 16px;
        background: #0284c7;
        border: 1px solid #0284c7;
        border-radius: 6px;
        color: white;
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
      `;

      // 更新页面信息
      function updateRAGPagination() {
        const startIdx = currentPage * itemsPerPage + 1;
        const endIdx = Math.min(
          (currentPage + 1) * itemsPerPage,
          moleculeCount
        );
        pageInfo.textContent = `显示 ${startIdx}-${endIdx} / 共 ${moleculeCount} 个分子`;

        prevBtn.disabled = currentPage === 0;
        nextBtn.disabled = currentPage === totalPages - 1;

        if (prevBtn.disabled) {
          prevBtn.style.opacity = "0.5";
          prevBtn.style.cursor = "not-allowed";
        } else {
          prevBtn.style.opacity = "1";
          prevBtn.style.cursor = "pointer";
        }

        if (nextBtn.disabled) {
          nextBtn.style.opacity = "0.5";
          nextBtn.style.cursor = "not-allowed";
          nextBtn.style.background = "#94a3b8";
          nextBtn.style.borderColor = "#94a3b8";
        } else {
          nextBtn.style.opacity = "1";
          nextBtn.style.cursor = "pointer";
          nextBtn.style.background = "#0284c7";
          nextBtn.style.borderColor = "#0284c7";
        }
      }

      prevBtn.onclick = () => {
        if (currentPage > 0) {
          currentPage--;
          renderRAGMolecules(currentPage);
          updateRAGPagination();
          ragInfoContainer.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        }
      };

      nextBtn.onclick = () => {
        if (currentPage < totalPages - 1) {
          currentPage++;
          renderRAGMolecules(currentPage);
          updateRAGPagination();
          ragInfoContainer.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        }
      };

      prevBtn.addEventListener("mouseenter", () => {
        if (!prevBtn.disabled) {
          prevBtn.style.background = "#e0f2fe";
        }
      });
      prevBtn.addEventListener("mouseleave", () => {
        if (!prevBtn.disabled) {
          prevBtn.style.background = "#fff";
        }
      });

      nextBtn.addEventListener("mouseenter", () => {
        if (!nextBtn.disabled) {
          nextBtn.style.background = "#0369a1";
        }
      });
      nextBtn.addEventListener("mouseleave", () => {
        if (!nextBtn.disabled) {
          nextBtn.style.background = "#0284c7";
        }
      });

      updateRAGPagination();

      paginationContainer.appendChild(prevBtn);
      paginationContainer.appendChild(pageInfo);
      paginationContainer.appendChild(nextBtn);
      ragInfoContainer.appendChild(paginationContainer);
    }

    // 将 RAG 容器包装成与 AI 消息气泡完全一致的布局结构（实现左侧对齐和统一宽度）
    const messageWrapper = document.createElement("div");
    messageWrapper.className = "message-wrapper assistant-wrapper";
    messageWrapper.style.cssText = `
      display: flex;
      justify-content: flex-start;
      margin: 25px 0 5px 0;
    `;

    const messageContainer = document.createElement("div");
    messageContainer.style.cssText = `
      display: flex;
      align-items: flex-start;
      max-width: 70%;
      gap: 12px;
      width: 100%;
    `;

    // 占位头像（不可见，仅用于和下方AI头像保持缩进对齐）
    const placeholderAvatar = document.createElement("div");
    placeholderAvatar.style.cssText = `
      width: 40px;
      height: 40px;
      flex-shrink: 0;
    `;

    messageContainer.appendChild(placeholderAvatar);
    messageContainer.appendChild(ragInfoContainer);
    messageWrapper.appendChild(messageContainer);

    // 添加到聊天容器
    elements.chatContainer.appendChild(messageWrapper);
    HomeChatRenderer.scrollToBottom();
  }

  // 渲染RAG检索到的分子结构（保留作为备用）
  function renderRAGMoleculeStructure(smiles, canvas, container, scale, index) {
    if (!window.SmilesDrawer) {
      console.error("❌ SmilesDrawer库未加载");
      container.innerHTML = `
        <div style="color: #94a3b8; font-size: 12px; text-align: center;">
          <div style="font-size: 20px; margin-bottom: 4px;">⚠️</div>
          <div>无法加载可视化库</div>
        </div>
      `;
      return;
    }

    setTimeout(() => {
      try {
        const drawerOptions = {
          width: 350 * scale,
          height: 200 * scale,
          bondThickness: 1.0 * scale,
          bondLength: 14 * scale,
          shortBondLength: 0.85,
          bondSpacing: 0.18 * 14 * scale,
          atomVisualization: "default",
          isomeric: true,
          debug: false,
          compactDrawing: false,
          terminalCarbons: false,
          explicitHydrogens: false,
          overlapSensitivity: 0.42,
          overlapResolutionIterations: 1,
          themes: {
            light: {
              C: "#222222",
              O: "#e11d48",
              N: "#2563eb",
              S: "#d97706",
              P: "#ea580c",
              F: "#059669",
              Cl: "#16a34a",
              Br: "#9a3412",
              I: "#7c3aed",
              BACKGROUND: "#ffffff",
            },
          },
        };

        const smilesDrawer = new SmilesDrawer.Drawer(drawerOptions);

        SmilesDrawer.parse(
          smiles,
          (tree) => {
            // 移除芳香性标记，使用 Kekulé 结构
            if (tree && tree.vertices) {
              tree.vertices.forEach((vertex) => {
                if (vertex && vertex.value) {
                  vertex.value.isAromatic = false;
                }
              });
            }
            if (tree && tree.edges) {
              tree.edges.forEach((edge) => {
                if (edge) {
                  edge.isAromatic = false;
                }
              });
            }

            smilesDrawer.draw(tree, canvas, "light", false);
            console.log(`✅ RAG分子 ${index + 1} 结构渲染成功`);
          },
          (err) => {
            console.error(`❌ RAG分子 ${index + 1} 渲染失败:`, err);
            container.innerHTML = `
              <div style="color: #f59e0b; font-size: 12px; text-align: center; padding: 20px;">
                <div style="font-size: 20px; margin-bottom: 4px;">⚠️</div>
                <div>无法渲染该分子结构</div>
              </div>
            `;
          }
        );
      } catch (error) {
        console.error(`❌ RAG分子 ${index + 1} 渲染异常:`, error);
        container.innerHTML = `
          <div style="color: #ef4444; font-size: 12px; text-align: center;">渲染错误</div>
        `;
      }
    }, index * 50); // 错开渲染时间
  }

  // 复制SMILES到剪贴板
  function copyToClipboard(text) {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        HomeChatRenderer.showNotification(
          `已复制SMILES: ${text.substring(0, 20)}...`,
          "success"
        );
      })
      .catch(() => {
        HomeChatRenderer.showNotification("复制失败", "error");
      });
  }

  // 分析分子
  function analyzeMolecule(smiles) {
    const analysisQuery = `请分析这个分子的属性: ${smiles}`;
    if (elements.input) {
      elements.input.value = analysisQuery;
      sendMessage();
    }
  }

  // 显示通知 - 美化版
  function showNotification(message, type = "info") {
    const notification = document.createElement("div");
    notification.className = `notification notification-${type}`;

    const colors = {
      info: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
      success: "linear-gradient(135deg, #0fb981 0%, #07c983 100%)",
      warning: "linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%)",
      error: "linear-gradient(135deg, #ef4444 0%, #f87171 100%)",
    };

    notification.style.cssText = `
            position: fixed;
            top: 80px;
            right: 20px;
            padding: 16px 24px;
            border-radius: 12px;
            color: white;
            font-size: 14px;
            font-weight: 500;
            z-index: 10000;
            animation: slideInRight 0.3s ease-out;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
            background: ${colors[type] || colors.info};
            display: flex;
            align-items: center;
            gap: 10px;
        `;

    const icons = {
      info: "ℹ️",
      success: "✅",
      warning: "⚠️",
      error: "❌",
    };

    notification.innerHTML = `<span>${icons[type]}</span><span>${message}</span>`;
    document.body.appendChild(notification);

    // 3秒后自动移除
    setTimeout(() => {
      notification.style.animation = "slideOutRight 0.3s ease-out";
      setTimeout(() => notification.remove(), 300);
    }, 3000);
  }

  // 更新连接状态
  function updateConnectionStatus(status) {
    if (!elements.connectionStatus) return;

    const statusConfig = {
      connecting: { text: "连接中...", bg: "#fbbf24", icon: "🟡" },
      connected: { text: "已连接", bg: "#10b981", icon: "🟢" },
      disconnected: { text: "已断开", bg: "#94a3b8", icon: "⚪" },
      error: { text: "连接失败", bg: "#ef4444", icon: "🔴" },
    };

    const config = statusConfig[status] || statusConfig.connecting;
    elements.connectionStatus.innerHTML = `${config.icon} ${config.text}`;
    elements.connectionStatus.style.backgroundColor = config.bg;
    elements.connectionStatus.style.color = "#fff";
    elements.connectionStatus.style.padding = "6px 12px";
    elements.connectionStatus.style.borderRadius = "20px";
    elements.connectionStatus.style.fontWeight = "500";
    elements.connectionStatus.style.transition = "all 0.3s ease";

    console.log(`🔗 连接状态更新: ${status} -> ${config.text}`);
  }

  // 滚动到底部
  function scrollToBottom() {
    if (elements.chatContainer) {
      setTimeout(() => {
        elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
      }, 100);
    }
  }

  // 添加增强样式
  function addEnhancedStyles() {
    const style = document.createElement("style");
    style.textContent = `
            @keyframes fadeIn {
                from { 
                    opacity: 0; 
                    transform: translateY(10px); 
                }
                to { 
                    opacity: 1; 
                    transform: translateY(0); 
                }
            }

            @keyframes slideInRight {
                from { 
                    opacity: 0; 
                    transform: translateX(30px); 
                }
                to { 
                    opacity: 1; 
                    transform: translateX(0); 
                }
            }

            @keyframes slideInLeft {
                from { 
                    opacity: 0; 
                    transform: translateX(-30px); 
                }
                to { 
                    opacity: 1; 
                    transform: translateX(0); 
                }
            }

            @keyframes slideOutRight {
                from { 
                    opacity: 1; 
                    transform: translateX(0); 
                }
                to { 
                    opacity: 0; 
                    transform: translateX(30px); 
                }
            }

            @keyframes messagePopIn {
                0% { 
                    transform: scale(0.8); 
                    opacity: 0; 
                }
                50% { 
                    transform: scale(1.05); 
                }
                100% { 
                    transform: scale(1); 
                    opacity: 1; 
                }
            }

            @keyframes typing {
                0%, 60%, 100% { 
                    transform: translateY(0); 
                }
                30% { 
                    transform: translateY(-15px); 
                }
            }

            @keyframes blink {
                0%, 50%, 100% { opacity: 1; }
                25%, 75% { opacity: 0; }
            }

            @keyframes pulse {
                0%, 100% { 
                    opacity: 1; 
                    transform: scale(1); 
                }
                50% { 
                    opacity: 0.8; 
                    transform: scale(1.05); 
                }
            }

            @keyframes shake {
                0%, 100% { transform: translateX(0); }
                10%, 30%, 50%, 70%, 90% { transform: translateX(-5px); }
                20%, 40%, 60%, 80% { transform: translateX(5px); }
            }

            @keyframes slideInUp {
                from {
                    opacity: 0;
                    transform: translateY(30px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }

            .chat-messages {
                scrollbar-width: thin;
                scrollbar-color: #cbd5e0 #f7fafc;
            }

            .chat-messages::-webkit-scrollbar {
                width: 8px;
            }

            .chat-messages::-webkit-scrollbar-track {
                background: #f7fafc;
                border-radius: 10px;
            }

            .chat-messages::-webkit-scrollbar-thumb {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 10px;
            }

            .chat-messages::-webkit-scrollbar-thumb:hover {
                background: linear-gradient(135deg, #5a67d8 0%, #6b46a0 100%);
            }

            .message-wrapper {
                transition: all 0.3s ease;
            }

            .message-wrapper:hover .message-box {
                transform: translateY(-2px);
                box-shadow: 0 8px 25px rgba(0, 0, 0, 0.15);
            }

            .chat-mode .mass,
            .chat-mode .icon_list {
                display: none !important;
            }

            .agent-task-panel {
                width: min(780px, calc(100% - 48px));
                margin: 16px auto 8px;
                padding: 16px;
                border-radius: 18px;
                border: 1px solid rgba(148, 163, 184, 0.24);
                background: rgba(255, 255, 255, 0.92);
                box-shadow: 0 16px 40px rgba(15, 23, 42, 0.08);
                display: none;
                animation: fadeIn 0.25s ease-out;
            }

            .agent-task-panel.is-active {
                display: block;
            }

            .agent-task-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 16px;
                padding-bottom: 12px;
                border-bottom: 1px solid rgba(226, 232, 240, 0.9);
            }

            .agent-task-kicker {
                font-size: 11px;
                color: #64748b;
                letter-spacing: 0.12em;
                font-weight: 800;
            }

            .agent-task-title {
                margin-top: 3px;
                font-size: 16px;
                color: #0f172a;
                font-weight: 800;
            }

            .agent-task-progress {
                min-width: 64px;
                padding: 7px 12px;
                border-radius: 999px;
                background: #eef2ff;
                color: #4338ca;
                text-align: center;
                font-size: 12px;
                font-weight: 800;
            }

            .agent-task-list {
                display: grid;
                gap: 8px;
                margin-top: 12px;
            }

            .agent-task-item {
                display: grid;
                grid-template-columns: 12px 1fr;
                align-items: start;
                gap: 10px;
                padding: 10px 12px;
                border-radius: 12px;
                background: #f8fafc;
                border: 1px solid rgba(226, 232, 240, 0.82);
            }

            .agent-task-dot {
                width: 8px;
                height: 8px;
                margin-top: 6px;
                border-radius: 50%;
                background: #818cf8;
                box-shadow: 0 0 0 4px rgba(129, 140, 248, 0.14);
            }

            .agent-task-copy {
                display: grid;
                gap: 2px;
            }

            .agent-task-copy strong {
                color: #1e293b;
                font-size: 13px;
                line-height: 1.35;
            }

            .agent-task-copy span {
                color: #64748b;
                font-size: 12px;
                line-height: 1.45;
            }

            .agent-task-item.is-complete .agent-task-dot {
                background: #14b8a6;
                box-shadow: 0 0 0 4px rgba(20, 184, 166, 0.12);
            }

            .agent-task-item.is-warning .agent-task-dot {
                background: #f59e0b;
                box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.12);
            }

            .agent-task-item.is-error .agent-task-dot {
                background: #ef4444;
                box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.12);
            }
        `;
    document.head.appendChild(style);
  }

  // 格式化合成路线数据
  function formatSynthesisRoute(content) {
    // 提取路线信息 - 修复正则表达式
    const routeMatches = content.match(
      /\*\*路线 \d+\*\*[\s\S]*?(?=\*\*路线 \d+\*\*|✅|$)/g
    );

    if (routeMatches && routeMatches.length > 0) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #667eea20 0%, #764ba220 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">🔬</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">逆合成分析结果</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">IBM RXN for Chemistry</p>
            </div>
          </div>
      `;

      routeMatches.forEach((route, index) => {
        // 提取置信度 - 修复正则表达式以匹配百分比格式
        const confidenceMatch = route.match(/总体置信度:\s*(\d+\.?\d*%)/);
        const confidence = confidenceMatch ? confidenceMatch[1] : "未知%";

        // 提取步骤
        const stepMatches = route.match(
          /步骤 \d+:\s*`([^`]+)`\s*→\s*`([^`]+)`/g
        );

        visualContent += `
          <div style="background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;">
              <h4 style="margin: 0; color: #2d3748; font-size: 18px;">🛤️ 路线 ${
                index + 1
              }</h4>
              <div style="background: linear-gradient(135deg, #48bb78 0%, #38a169 100%); color: white; padding: 6px 12px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                置信度: ${confidence}
              </div>
            </div>
        `;

        if (stepMatches) {
          stepMatches.forEach((step, stepIndex) => {
            const stepMatch = step.match(
              /步骤 \d+:\s*`([^`]+)`\s*→\s*`([^`]+)`/
            );
            if (stepMatch) {
              const reactants = stepMatch[1];
              const products = stepMatch[2];

              visualContent += `
                <div style="display: flex; align-items: center; margin: 12px 0; padding: 12px; background: #f8fafc; border-radius: 8px;">
                  <div style="background: #e2e8f0; padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #4a5568; margin-right: 12px; flex: 1;">
                    ${reactants}
                  </div>
                  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 6px 12px; border-radius: 20px; font-size: 20px; margin: 0 12px;">→</div>
                  <div style="background: #e6fffa; padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #234e52; flex: 1;">
                    ${products}
                  </div>
                </div>
              `;
            }
          });
        } else {
          // 如果没有匹配到步骤，显示提示信息
          visualContent += `
            <div style="padding: 12px; background: #fdf2e9; border-radius: 8px; color: #744210; text-align: center;">
              暂无详细步骤信息
            </div>
          `;
        }

        visualContent += `</div>`;
      });

      visualContent += `
          <div style="margin-top: 20px; padding: 16px; background: rgba(102, 126, 234, 0.1); border-radius: 8px; border-left: 4px solid #667eea;">
            <p style="margin: 0; font-size: 14px; color: #4a5568;">
              💡 <strong>说明：</strong>合成路线按总体置信度排序，考虑了反应的可行性和文献报道的先例。
            </p>
          </div>
        </div>
      `;

      return visualContent;
    }

    // 如果没有匹配到路线格式，返回基础格式化
    return content.replace(/\n/g, "<br>");
  }

  // 格式化反应预测结果
  function formatReactionPrediction(content) {
    if (content.includes("预测的可能产物")) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #fbb6ce20 0%, #f687b320 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #ec4899 0%, #be185d 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">🧪</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">反应预测结果</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">IBM RXN for Chemistry</p>
            </div>
          </div>
      `;

      // 提取反应物
      const reactantMatch = content.match(/反应物:\s*`([^`]+)`/);
      if (reactantMatch) {
        visualContent += `
          <div style="background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h4 style="margin: 0 0 12px 0; color: #2d3748;">⚗️ 反应物</h4>
            <div style="background: #f0f9ff; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 16px; color: #0c4a6e; border: 2px solid #7dd3fc;">
              ${reactantMatch[1]}
            </div>
          </div>
        `;
      }

      // 提取产物
      const productMatches = content.match(
        /\d+\.\s*`([^`]+)`\s*\(置信度:\s*(\d+\.?\d*)%\)/g
      );
      if (productMatches) {
        visualContent += `
          <div style="background: white; border-radius: 12px; padding: 20px; margin: 16px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h4 style="margin: 0 0 16px 0; color: #2d3748;">📊 预测产物</h4>
        `;

        productMatches.forEach((product, index) => {
          const productMatch = product.match(
            /\d+\.\s*`([^`]+)`\s*\(置信度:\s*(\d+\.?\d*)%\)/
          );
          if (productMatch) {
            const productSMILES = productMatch[1];
            const confidence = productMatch[2];
            const confidenceNum = parseFloat(confidence);

            let confidenceColor = "#ef4444";
            if (confidenceNum >= 80) confidenceColor = "#22c55e";
            else if (confidenceNum >= 60) confidenceColor = "#f59e0b";

            visualContent += `
              <div style="display: flex; align-items: center; margin: 12px 0; padding: 16px; background: #f8fafc; border-radius: 8px; border-left: 4px solid ${confidenceColor};">
                <div style="margin-right: 16px; font-size: 18px; font-weight: bold; color: #4a5568; min-width: 40px;">
                  ${index + 1}.
                </div>
                <div style="flex: 1; margin-right: 16px;">
                  <div style="background: #e0f2fe; padding: 10px; border-radius: 6px; font-family: monospace; font-size: 14px; color: #0c4a6e;">
                    ${productSMILES}
                  </div>
                </div>
                <div style="background: ${confidenceColor}; color: white; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold; min-width: 80px; text-align: center;">
                  ${confidence}%
                </div>
              </div>
            `;
          }
        });

        visualContent += `</div>`;
      }

      visualContent += `
          <div style="margin-top: 20px; padding: 16px; background: rgba(236, 72, 153, 0.1); border-radius: 8px; border-left: 4px solid #ec4899;">
            <p style="margin: 0; font-size: 14px; color: #4a5568;">
              💡 <strong>说明：</strong>预测结果按置信度排序，置信度越高表示反应越可能发生。
            </p>
          </div>
        </div>
      `;

      return visualContent;
    }

    return content.replace(/\n/g, "<br>");
  }

  // 格式化文献搜索结果
  function formatLiteratureResults(content) {
    if (content.includes("相关文献") || content.includes("相关专利")) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #a7f3d020 0%, #6ee7b720 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #10b981 0%, #059669 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">📚</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">文献数据搜索结果</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">IBM RXN for Chemistry数据库</p>
            </div>
          </div>
      `;

      // 简单地格式化内容，保持原有的结构但增加样式
      const formattedContent = content
        .replace(
          /\*\*(.*?)\*\*/g,
          '<strong style="color: #2d3748;">$1</strong>'
        )
        .replace(/📄/g, '<span style="font-size: 20px;">📄</span>')
        .replace(/📋/g, '<span style="font-size: 20px;">📋</span>')
        .replace(/🔍/g, '<span style="font-size: 20px;">🔍</span>')
        .replace(/📊/g, '<span style="font-size: 20px;">📊</span>')
        .replace(/💡/g, '<span style="font-size: 20px;">💡</span>')
        .replace(/\n/g, "<br>");

      visualContent += `
        <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          ${formattedContent}
        </div>
      `;

      visualContent += `</div>`;
      return visualContent;
    }

    return content.replace(/\n/g, "<br>");
  }

  // 格式化ADMET预测结果
  function formatADMETResults(content) {
    if (
      content.includes("增强ADMET属性预测结果") ||
      content.includes("**理化性质:**")
    ) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #ffecd120 0%, #fed7aa20 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">💊</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">ADMET属性分析</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">药代动力学与毒理学预测</p>
            </div>
          </div>
      `;

      // 格式化内容并添加样式
      let formattedContent = content
        .replace(/💊 增强ADMET属性预测结果/g, "")
        .replace(
          /\*\*(.*?)\*\*/g,
          '<div style="background: #f7fafc; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #4299e1;"><strong style="color: #1a365d; font-size: 16px;">$1</strong></div>'
        )
        .replace(
          /• (.*?): (.*?)$/gm,
          '<div style="margin: 8px 0; padding: 10px; background: white; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);"><span style="color: #4a5568; font-weight: 500;">$1:</span> <span style="color: #2d3748;">$2</span></div>'
        )
        .replace(
          /🔽|🔄|⚙️|🚪|⚠️|📈/g,
          '<span style="font-size: 18px;">$&</span>'
        )
        .replace(
          /SMILES: `([^`]+)`/g,
          '<div style="background: #edf2f7; padding: 10px; border-radius: 6px; margin: 12px 0; font-family: monospace; color: #e53e3e; font-weight: 500;">SMILES: $1</div>'
        );

      visualContent += `
        <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          ${formattedContent}
        </div>
      `;

      visualContent += `</div>`;
      return visualContent;
    }
    return content.replace(/\n/g, "<br>");
  }

  // 格式化类药评估结果
  function formatDrugLikenessResults(content) {
    if (
      content.includes("类药性评估结果") ||
      content.includes("**综合评估:**")
    ) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #e0e7ff20 0%, #c7d2fe20 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">🎯</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">类药性评估</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">药物相似性与成药性分析</p>
            </div>
          </div>
      `;

      let formattedContent = content
        .replace(/💊 类药性评估结果/g, "")
        .replace(
          /\*\*(.*?)\*\*/g,
          '<div style="background: #f8fafc; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #8b5cf6;"><strong style="color: #1a202c; font-size: 16px;">$1</strong></div>'
        )
        .replace(
          /• (.*?): (.*?)$/gm,
          '<div style="margin: 8px 0; padding: 10px; background: white; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);"><span style="color: #4a5568; font-weight: 500;">$1:</span> <span style="color: #2d3748;">$2</span></div>'
        )
        .replace(
          /✓/g,
          '<span style="color: #10b981; font-size: 16px;">✓</span>'
        )
        .replace(
          /✗/g,
          '<span style="color: #ef4444; font-size: 16px;">✗</span>'
        )
        .replace(/🎯|📏|🔬|📊/g, '<span style="font-size: 18px;">$&</span>')
        .replace(
          /SMILES: `([^`]+)`/g,
          '<div style="background: #edf2f7; padding: 10px; border-radius: 6px; margin: 12px 0; font-family: monospace; color: #e53e3e; font-weight: 500;">SMILES: $1</div>'
        );

      visualContent += `
        <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          ${formattedContent}
        </div>
      `;

      visualContent += `</div>`;
      return visualContent;
    }
    return content.replace(/\n/g, "<br>");
  }

  // 格式化基础分子属性结果
  function formatMolecularProperties(content) {
    if (content.includes("基础分子属性计算结果")) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #ecfdf520 0%, #d1fae520 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #10b981 0%, #059669 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">🧬</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">分子属性计算</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">基础理化性质分析</p>
            </div>
          </div>
      `;

      let formattedContent = content
        .replace(/🧬 基础分子属性计算结果/g, "")
        .replace(
          /📊 关键属性:/g,
          '<div style="background: #f0fdfa; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #10b981;"><strong style="color: #134e4a; font-size: 16px;">📊 关键属性</strong></div>'
        )
        .replace(
          /• (.*?): (.*?)$/gm,
          '<div style="margin: 6px 0; padding: 8px 12px; background: white; border-radius: 6px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);"><span style="color: #374151; font-weight: 500;">$1:</span> <span style="color: #1f2937;">$2</span></div>'
        )
        .replace(
          /SMILES: `([^`]+)`/g,
          '<div style="background: #edf2f7; padding: 10px; border-radius: 6px; margin: 12px 0; font-family: monospace; color: #e53e3e; font-weight: 500;">SMILES: $1</div>'
        );

      visualContent += `
        <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          ${formattedContent}
        </div>
      `;

      visualContent += `</div>`;
      return visualContent;
    }
    return content.replace(/\n/g, "<br>");
  }

  // 格式化ReAct推理结果
  function formatReActResults(content) {
    if (
      content.includes("**推理过程:**") ||
      content.includes("**使用工具:**")
    ) {
      let visualContent = `
        <div style="background: linear-gradient(135deg, #fef3c720 0%, #fde68a20 100%); border-radius: 16px; padding: 24px; margin: 16px 0;">
          <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 24px;">🤔</div>
            <div>
              <h3 style="margin: 0; color: #2d3748; font-size: 20px;">智能推理分析</h3>
              <p style="margin: 4px 0 0 0; color: #718096; font-size: 14px;">ReAct推理框架结果</p>
            </div>
          </div>
      `;

      let formattedContent = content
        .replace(
          /\*\*(推理过程|使用工具|分析结果):\*\*/g,
          '<div style="background: #fffbeb; margin: 16px 0; padding: 12px; border-radius: 8px; border-left: 4px solid #f59e0b;"><strong style="color: #92400e; font-size: 16px;">🔧 $1</strong></div>'
        )
        .replace(
          /   \d+\. (.+)/g,
          '<div style="margin: 8px 0 8px 20px; padding: 10px; background: white; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-left: 3px solid #fbbf24;"><span style="color: #1f2937;">$1</span></div>'
        )
        .replace(
          /      → 使用了 (.+?) 工具/g,
          '<div style="margin: 4px 0 4px 40px; padding: 6px 10px; background: #f3f4f6; border-radius: 4px; font-size: 13px; color: #6b7280;">🔧 使用了 <strong style="color: #374151;">$1</strong> 工具</div>'
        )
        .replace(/🤔|🔧|📋/g, '<span style="font-size: 18px;">$&</span>');

      visualContent += `
        <div style="background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          ${formattedContent}
        </div>
      `;

      visualContent += `</div>`;
      return visualContent;
    }
    return content.replace(/\n/g, "<br>");
  }

  // 获取分子属性（用于旧版本的分子卡片）
  async function fetchMoleculeProperties(smiles, container) {
    try {
      // 调用后端API获取分子属性
      const response = await fetch("/api/molecule/properties", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ smiles: smiles }),
      });

      if (!response.ok) {
        throw new Error("API请求失败");
      }

      const data = await response.json();

      if (data.success && data.properties) {
        displayMoleculeProperties(data.properties, container);
      } else {
        const errorMsg = data.error || "未知错误";
        container.innerHTML = `
          <div style="text-align: center; color: #f59e0b; padding: 12px;">
            <div style="font-size: 18px; margin-bottom: 4px;">⚠️</div>
            <div style="font-size: 11px; color: #92400e;">该分子结构无法计算属性</div>
            <div style="font-size: 10px; color: #b45309; margin-top: 4px;">可能是无效的SMILES结构</div>
          </div>
        `;
      }
    } catch (error) {
      console.error("获取分子属性失败:", error);
      container.innerHTML = `
        <div style="text-align: center; color: #94a3b8; padding: 12px;">
          <div style="font-size: 18px; margin-bottom: 4px;">⚠️</div>
          <div style="font-size: 11px;">无法获取属性数据</div>
        </div>
      `;
    }
  }

  // 获取工具生成分子的属性（使用与反向寻靶一致的样式）
  async function fetchMoleculePropertiesForToolMolecule(smiles, container) {
    try {
      const response = await fetch("/api/molecule/properties", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ smiles: smiles }),
      });

      if (!response.ok) {
        throw new Error("API请求失败");
      }

      const data = await response.json();

      if (data.success && data.properties) {
        displayToolMoleculeProperties(data.properties, container);
      } else {
        container.innerHTML = `
          <div style="text-align: center; color: #f59e0b; padding: 12px;">
            <div style="font-size: 18px; margin-bottom: 4px;">⚠️</div>
            <div style="font-size: 11px; color: #92400e;">无法计算属性</div>
          </div>
        `;
      }
    } catch (error) {
      console.error("获取分子属性失败:", error);
      container.innerHTML = `
        <div style="text-align: center; color: #94a3b8; padding: 12px;">
          <div style="font-size: 18px; margin-bottom: 4px;">⚠️</div>
          <div style="font-size: 11px;">无法获取属性数据</div>
        </div>
      `;
    }
  }

  // 显示工具生成分子的属性（使用与反向寻靶一致的网格布局）
  function displayToolMoleculeProperties(properties, container) {
    const basicProps = properties.basic || {};

    // 属性映射
    const propertyLabels = {
      molecular_weight: "分子量",
      logp: "LogP",
      hbd: "HBD",
      hba: "HBA",
      tpsa: "TPSA",
      rotatable_bonds: "可旋转键",
    };

    const allProperties = [
      "molecular_weight",
      "logp",
      "hbd",
      "hba",
      "tpsa",
      "rotatable_bonds",
    ];

    let html = `<div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px 10px;">`;
    let hasProps = false;

    allProperties.forEach((prop) => {
      const value = basicProps[prop];

      if (value !== undefined && value !== null) {
        hasProps = true;
        const propValue = parseFloat(value);

        // 根据属性类型设置颜色
        let valueColor = "#334155";
        let bgColor = "#f8fafc";
        let displayValue = propValue;

        if (prop === "molecular_weight") {
          valueColor = propValue <= 500 ? "#059669" : propValue <= 600 ? "#d97706" : "#dc2626";
          bgColor = propValue <= 500 ? "#ecfdf5" : propValue <= 600 ? "#fffbeb" : "#fef2f2";
          displayValue = propValue.toFixed(1);
        } else if (prop === "logp") {
          valueColor = propValue >= -0.4 && propValue <= 5.6 ? "#059669" : "#dc2626";
          bgColor = propValue >= -0.4 && propValue <= 5.6 ? "#ecfdf5" : "#fef2f2";
          displayValue = propValue.toFixed(2);
        } else if (prop === "hbd") {
          valueColor = propValue <= 5 ? "#059669" : "#dc2626";
          displayValue = Math.round(propValue);
        } else if (prop === "hba") {
          valueColor = propValue <= 10 ? "#059669" : "#dc2626";
          displayValue = Math.round(propValue);
        } else if (prop === "tpsa") {
          valueColor = propValue <= 140 ? "#059669" : "#d97706";
          displayValue = propValue.toFixed(1);
        } else if (prop === "rotatable_bonds") {
          displayValue = Math.round(propValue);
        }

        html += `
          <div style="display: flex; justify-content: space-between; align-items: center; background: ${bgColor}; padding: 6px 10px; border-radius: 6px; border: 1px solid rgba(0,0,0,0.03);">
            <span style="color: #64748b; font-size: 11px; font-weight: 500;">${propertyLabels[prop] || prop}</span>
            <span style="color: ${valueColor}; font-weight: 700; font-size: 12px; font-family: 'Inter', ui-sans-serif, system-ui;">${displayValue}</span>
          </div>
        `;
      }
    });

    html += `</div>`;

    if (hasProps) {
      container.innerHTML = html;
    } else {
      container.innerHTML = `
        <div style="text-align: center; color: #94a3b8; padding: 12px;">
          <div style="font-size: 11px;">暂无属性数据</div>
        </div>
      `;
    }
  }

  // 显示分子属性
  function displayMoleculeProperties(properties, container) {
    const basicProps = properties.basic || {};
    const admetProps = properties.admet || {};

    let html = `
      <div style="margin-bottom: 12px;">
        <div style="font-weight: 600; color: #1e293b; margin-bottom: 8px; font-size: 13px; display: flex; align-items: center; gap: 6px;">
          <span>📊</span> 基础属性
        </div>
        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px;">
    `;

    // 基础属性
    const basicItems = [
      {
        label: "分子量",
        value: basicProps.molecular_weight,
        unit: "g/mol",
        key: "mw",
      },
      { label: "LogP", value: basicProps.logp, unit: "", key: "logp" },
      { label: "HBD", value: basicProps.hbd, unit: "", key: "hbd" },
      { label: "HBA", value: basicProps.hba, unit: "", key: "hba" },
      { label: "TPSA", value: basicProps.tpsa, unit: "Ų", key: "tpsa" },
      {
        label: "Rotatable",
        value: basicProps.rotatable_bonds,
        unit: "",
        key: "rot",
      },
    ];

    basicItems.forEach((item) => {
      if (item.value !== undefined && item.value !== null) {
        const displayValue =
          typeof item.value === "number" ? item.value.toFixed(2) : item.value;
        const color = getPropertyColor(item.key, item.value);
        html += `
          <div style="background: #f8fafc; padding: 6px 8px; border-radius: 6px; border: 1px solid #e2e8f0;">
            <div style="font-size: 10px; color: #64748b; margin-bottom: 2px;">${item.label}</div>
            <div style="font-weight: 600; color: ${color}; font-size: 12px;">${displayValue}${item.unit}</div>
          </div>
        `;
      }
    });

    html += `
        </div>
      </div>
    `;

    // ADMET属性
    if (admetProps && Object.keys(admetProps).length > 0) {
      html += `
        <div>
          <div style="font-weight: 600; color: #1e293b; margin-bottom: 8px; font-size: 13px; display: flex; align-items: center; gap: 6px;">
            <span>💊</span> ADMET属性
          </div>
          <div style="display: grid; grid-template-columns: 1fr; gap: 6px;">
      `;

      const admetItems = [
        { label: "BBB渗透", value: admetProps.bbb_penetration, key: "bbb" },
        { label: "CYP抑制", value: admetProps.cyp_inhibition, key: "cyp" },
        { label: "肝毒性", value: admetProps.hepatotoxicity, key: "hepato" },
        { label: "溶解度", value: admetProps.solubility, key: "sol" },
      ];

      admetItems.forEach((item) => {
        if (item.value !== undefined && item.value !== null) {
          const icon = getADMETIcon(item.value);
          const color = getADMETColor(item.value);
          html += `
            <div style="background: #f8fafc; padding: 6px 8px; border-radius: 6px; border: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center;">
              <span style="font-size: 11px; color: #475569;">${item.label}</span>
              <span style="font-size: 11px; font-weight: 600; color: ${color};">${icon} ${item.value}</span>
            </div>
          `;
        }
      });

      html += `
          </div>
        </div>
      `;
    }

    container.innerHTML = html;
  }

  // 获取属性值的颜色
  function getPropertyColor(key, value) {
    if (typeof value !== "number") return "#374151";

    switch (key) {
      case "mw":
        return value <= 500 ? "#059669" : value <= 600 ? "#d97706" : "#dc2626";
      case "logp":
        return value >= -0.4 && value <= 5.6 ? "#059669" : "#dc2626";
      case "hbd":
        return value <= 5 ? "#059669" : "#dc2626";
      case "hba":
        return value <= 10 ? "#059669" : "#dc2626";
      case "tpsa":
        return value <= 140 ? "#059669" : "#d97706";
      default:
        return "#374151";
    }
  }

  // 获取ADMET属性的图标
  function getADMETIcon(value) {
    if (typeof value === "string") {
      const lower = value.toLowerCase();
      if (
        lower.includes("high") ||
        lower.includes("good") ||
        lower.includes("yes") ||
        lower.includes("高")
      )
        return "✓";
      if (
        lower.includes("low") ||
        lower.includes("poor") ||
        lower.includes("no") ||
        lower.includes("低")
      )
        return "✗";
    }
    return "•";
  }

  // 获取ADMET属性的颜色
  function getADMETColor(value) {
    if (typeof value === "string") {
      const lower = value.toLowerCase();
      if (
        lower.includes("high") ||
        lower.includes("good") ||
        lower.includes("yes")
      )
        return "#059669";
      if (
        lower.includes("low") ||
        lower.includes("poor") ||
        lower.includes("no")
      )
        return "#dc2626";
      if (lower.includes("moderate") || lower.includes("medium"))
        return "#d97706";
    }
    return "#64748b";
  }

  // 🧬 检测并渲染SMILES分子结构 (2D可视化)
  function detectAndRenderMolecules(messageElement, content) {
    console.log("🔍 开始检测SMILES分子结构...");
    console.log("📄 消息内容长度:", content.length);
    console.log("📝 内容预览:", content.substring(0, 300));

    if (!window.SmilesDrawer) {
      console.error("❌ SmilesDrawer库未加载，无法渲染分子结构");
      console.log(
        '💡 请检查 <script src="https://unpkg.com/smiles-drawer@2.0.1/dist/smiles-drawer.min.js"></script> 是否正确加载'
      );
      return;
    }
    console.log("✅ SmilesDrawer库已加载");

    // SMILES检测模式：匹配典型的SMILES结构
    // 1. 在反引号中: `CCO`, `CC(=O)O` 等
    // 2. 在SMILES:标签后: SMILES: CCO
    // 3. 单独一行的复杂结构
    const smilesPatterns = [
      /`([A-Za-z][A-Za-z0-9@+\-\[\]\(\)=#\.\\\/:]{4,})`/g, // 反引号包裹
      /SMILES[:\s]+([A-Za-z][A-Za-z0-9@+\-\[\]\(\)=#\.\\\/:]{4,})/gi, // SMILES:标签
      /\b([A-Z][A-Za-z0-9@+\-\[\]\(\)=#]{8,})\b/g, // 复杂结构(较长)
    ];

    const detectedSmiles = new Set();

    // 提取所有SMILES
    smilesPatterns.forEach((pattern, idx) => {
      console.log(`🔎 使用模式 ${idx + 1} 检测...`);
      let match;
      while ((match = pattern.exec(content)) !== null) {
        const smiles = match[1] || match[0];
        console.log(`  发现候选SMILES: "${smiles}"`);

        // 增强的SMILES验证逻辑
        // 1. 基本长度检查
        if (smiles.length < 4 || smiles.length > 200) {
          console.log(`  ❌ 长度不符: ${smiles.length}`);
          continue;
        }

        // 2. 必须包含化学元素符号
        if (!/[CNOSPFClBrI]/.test(smiles)) {
          console.log(`  ❌ 缺少化学元素`);
          continue;
        }

        // 3. 排除常见的非SMILES字符串
        const excludePatterns = [
          /SMILES/i,
          /http/i,
          /scaffold/i,
          /canonical/i,
          /NumRotBonds/i,
          /^[A-Z][a-z]+$/, // 单个单词
          /\s{2,}/, // 多个空格
          /^[A-Z_]+$/, // 全大写变量名
          /\.\.\./, // 包含省略号(通常是被截断的SMILES)
        ];

        if (excludePatterns.some((pattern) => pattern.test(smiles))) {
          console.log(`  ❌ 匹配排除模式`);
          continue;
        }

        // 4. 检查括号匹配
        const openParens = (smiles.match(/\(/g) || []).length;
        const closeParens = (smiles.match(/\)/g) || []).length;
        if (openParens !== closeParens) {
          console.log(`  ❌ 括号不匹配: (${openParens}) vs )${closeParens})`);
          continue;
        }

        // 5. 检查方括号匹配
        const openBrackets = (smiles.match(/\[/g) || []).length;
        const closeBrackets = (smiles.match(/\]/g) || []).length;
        if (openBrackets !== closeBrackets) {
          console.log(`  ❌ 方括号不匹配`);
          continue;
        }

        // 6. 必须以字母或数字开头
        if (!/^[A-Za-z0-9]/.test(smiles)) {
          console.log(`  ❌ 开头字符无效`);
          continue;
        }

        detectedSmiles.add(smiles.trim());
        console.log(`  ✅ 验证通过: "${smiles}"`);
      }
    });

    if (detectedSmiles.size === 0) {
      console.log("📭 未检测到有效的SMILES分子结构");
      return;
    }

    console.log(
      `🎯 成功检测到 ${detectedSmiles.size} 个分子结构:`,
      Array.from(detectedSmiles)
    );

    // 创建分子结构容器
    const moleculeContainer = document.createElement("div");
    moleculeContainer.className = "molecule-visualization-container";
    moleculeContainer.style.cssText = `
      margin: 20px 0;
      padding: 20px;
      background: #ffffff;
      border-radius: 12px;
      border: 1px solid #e2e8f0;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
      box-sizing: border-box;
      animation: slideInUp 0.4s ease-out;
      max-width: 100%;
      overflow: hidden;
    `;

    const title = document.createElement("div");
    title.style.cssText = `
      font-size: 18px;
      font-weight: 700;
      color: #1e293b;
      margin-bottom: 24px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding-bottom: 16px;
      border-bottom: 1px solid #f1f5f9;
    `;
    title.innerHTML = `
      <div style="width: 32px; height: 32px; background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center; color: white; font-size: 18px; box-shadow: 0 4px 6px -1px rgba(59, 130, 246, 0.3);">🧬</div>
      <div style="flex: 1;">
        <div style="line-height: 1.2;">分子结构可视化</div>
        <div style="font-size: 12px; color: #64748b; font-weight: normal; margin-top: 2px;">检测到 ${detectedSmiles.size} 个分子结构</div>
      </div>
    `;
    moleculeContainer.appendChild(title);

    // 创建分子网格 - 根据分子数量动态调整（方案A+C）
    const moleculeCount = detectedSmiles.size;
    const moleculesArray = Array.from(detectedSmiles);

    // 横向滑动布局参数（统一固定比例，避免随数量突变）
    const minCardWidth = "260px";
    const imageHeight = "180px";
    const gap = "20px";
    const itemsPerPage = moleculeCount; // 无需分页

    // 分页逻辑（保留变量防错）
    let currentPage = 0;
    const totalPages = 1;
    const needsPagination = false;

    // 创建滑动容器
    const grid = document.createElement("div");
    grid.className = "molecules-carousel custom-scrollbar";
    grid.style.cssText = `
      display: flex;
      flex-wrap: nowrap;
      overflow-x: auto;
      gap: ${gap};
      width: 100%;
      padding-bottom: 12px;
      padding-right: 40px; /* 右侧渐变空间 */
      scroll-behavior: smooth;
      -webkit-overflow-scrolling: touch;
      -webkit-mask-image: linear-gradient(to right, black 90%, transparent 100%);
      mask-image: linear-gradient(to right, black 90%, transparent 100%);
    `;

    // 渲染分子函数
    function renderMolecules(page) {
      grid.innerHTML = ""; // 清空网格

      const startIdx = page * itemsPerPage;
      const endIdx = Math.min(startIdx + itemsPerPage, moleculeCount);
      const pageMolecules = moleculesArray.slice(startIdx, endIdx);

      pageMolecules.forEach((smiles, pageIndex) => {
        const globalIndex = startIdx + pageIndex;
        const molCard = document.createElement("div");
        molCard.className = "molecule-card";
        molCard.style.cssText = `
        flex: 0 0 ${minCardWidth};
        width: ${minCardWidth};
        background: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 0;
        overflow: hidden;
        transition: all 0.2s;
        display: flex;
        flex-direction: column;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
      `;

        // 鼠标悬停效果
        molCard.addEventListener("mouseenter", () => {
          molCard.style.transform = "translateY(-3px)";
          molCard.style.boxShadow = "0 8px 25px rgba(79, 70, 229, 0.15)";
          molCard.style.borderColor = "#4f46e5";
        });

        molCard.addEventListener("mouseleave", () => {
          molCard.style.transform = "translateY(0)";
          molCard.style.boxShadow = "0 1px 2px rgba(0,0,0,0.05)";
          molCard.style.borderColor = "#e2e8f0";
        });

        // 卡片头部 - 序号和标签
        const cardHeader = document.createElement("div");
        cardHeader.style.cssText = `
          padding: 12px 16px;
          border-bottom: 1px solid #f1f5f9;
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: #f8fafc;
        `;
        cardHeader.innerHTML = `
          <div style="font-weight: 700; color: #4f46e5; font-size: 16px;">#${
            globalIndex + 1
          }</div>
          <div style="background: #e0e7ff; color: #4338ca; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 20px;">工具生成</div>
        `;
        molCard.appendChild(cardHeader);

        // 分子图片区域 - 使用后端API生成图片（动态高度）
        const imageSection = document.createElement("div");
        imageSection.style.cssText = `
          height: ${imageHeight};
          display: flex;
          justify-content: center;
          align-items: center;
          background: #fff;
          padding: 10px;
          border-bottom: 1px solid #f1f5f9;
        `;

        const molImage = document.createElement("img");
        molImage.src = `/api/utils/smiles_to_image?smiles=${encodeURIComponent(
          smiles
        )}&width=360&height=300`;
        molImage.alt = "分子结构";
        molImage.style.cssText = `max-width: 100%; max-height: 100%; object-fit: contain;`;
        molImage.loading = "lazy";
        molImage.onerror = function () {
          this.style.display = "none";
          imageSection.innerHTML = `
            <div style="color: #94a3b8; font-size: 12px; text-align: center;">
              <div style="font-size: 24px; margin-bottom: 8px;">⚠️</div>
              <div>无法加载分子结构</div>
            </div>
          `;
        };
        imageSection.appendChild(molImage);
        molCard.appendChild(imageSection);

        // 信息区域
        const infoSection = document.createElement("div");
        infoSection.style.cssText = `padding: 16px; flex: 1; display: flex; flex-direction: column; gap: 10px;`;

        // 分子属性展示区域（占位）
        const propertiesContainer = document.createElement("div");
        propertiesContainer.className = "molecule-properties";
        propertiesContainer.style.cssText = `
        width: 100%;
        font-size: 12px;
      `;
        propertiesContainer.innerHTML = `
        <div style="text-align: center; color: #94a3b8; padding: 12px;">
          <div style="font-size: 18px; margin-bottom: 4px;">⏳</div>
          <div style="font-size: 11px;">正在计算分子属性...</div>
        </div>
      `;
        infoSection.appendChild(propertiesContainer);

        // 异步获取分子属性
        fetchMoleculePropertiesForToolMolecule(smiles, propertiesContainer);

        // SMILES 折叠区域
        const smilesDetails = document.createElement("details");
        smilesDetails.style.cssText = `margin-top: 4px;`;
        smilesDetails.innerHTML = `
        <summary style="cursor: pointer; color: #64748b; font-size: 12px; user-select: none;">显示 SMILES</summary>
        <div style="margin-top: 6px; padding: 8px; background: #f8fafc; border-radius: 4px; font-family: monospace; font-size: 11px; color: #475569; word-break: break-all; border: 1px solid #e2e8f0;">
          ${smiles}
        </div>
      `;
        infoSection.appendChild(smilesDetails);

        molCard.appendChild(infoSection);

        // 操作按钮区域
        const actionsSection = document.createElement("div");
        actionsSection.style.cssText = `
        display: flex;
        gap: 8px;
        padding: 12px 16px;
        border-top: 1px solid #f1f5f9;
        background: #f8fafc;
      `;

        const copyBtn = document.createElement("button");
        copyBtn.style.cssText = `
        flex: 1;
        background: #4f46e5;
        color: white;
        border: none;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 12px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
      `;
        copyBtn.innerHTML = `<span style="font-size: 13px;">📋</span> 复制`;
        copyBtn.onclick = () => {
          navigator.clipboard.writeText(smiles).then(() => {
            copyBtn.innerHTML = `<span style="font-size: 13px;">✅</span> 已复制`;
            copyBtn.style.background = "#10b981";
            setTimeout(() => {
              copyBtn.innerHTML = `<span style="font-size: 13px;">📋</span> 复制`;
              copyBtn.style.background = "#4f46e5";
            }, 2000);
          });
        };
        copyBtn.addEventListener("mouseenter", () => {
          if (!copyBtn.textContent.includes("已复制")) {
            copyBtn.style.background = "#4338ca";
          }
        });
        copyBtn.addEventListener("mouseleave", () => {
          if (!copyBtn.textContent.includes("已复制")) {
            copyBtn.style.background = "#4f46e5";
          }
        });
        actionsSection.appendChild(copyBtn);

        molCard.appendChild(actionsSection);
        grid.appendChild(molCard);
      });
    }

    // 初始渲染第一页
    renderMolecules(currentPage);
    moleculeContainer.appendChild(grid);

    // 添加分页控件（如果需要）
    if (needsPagination) {
      const paginationContainer = document.createElement("div");
      paginationContainer.style.cssText = `
        margin-top: 20px;
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 12px;
        padding: 16px;
        background: #f8fafc;
        border-radius: 8px;
        border: 1px solid #e2e8f0;
      `;

      const pageInfo = document.createElement("div");
      pageInfo.className = "page-info";
      pageInfo.style.cssText = `
        font-size: 13px;
        color: #64748b;
        font-weight: 500;
      `;

      const prevBtn = document.createElement("button");
      prevBtn.innerHTML = "← 上一页";
      prevBtn.style.cssText = `
        padding: 8px 16px;
        background: #fff;
        border: 1px solid #e2e8f0;
        border-radius: 6px;
        color: #475569;
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
      `;
      prevBtn.disabled = currentPage === 0;
      if (prevBtn.disabled) {
        prevBtn.style.opacity = "0.5";
        prevBtn.style.cursor = "not-allowed";
      }

      const nextBtn = document.createElement("button");
      nextBtn.innerHTML = "下一页 →";
      nextBtn.style.cssText = `
        padding: 8px 16px;
        background: #4f46e5;
        border: 1px solid #4f46e5;
        border-radius: 6px;
        color: white;
        font-size: 13px;
        cursor: pointer;
        transition: all 0.2s;
        font-weight: 500;
      `;

      // 更新页面信息
      function updatePagination() {
        const startIdx = currentPage * itemsPerPage + 1;
        const endIdx = Math.min(
          (currentPage + 1) * itemsPerPage,
          moleculeCount
        );
        pageInfo.textContent = `显示 ${startIdx}-${endIdx} / 共 ${moleculeCount} 个分子`;

        prevBtn.disabled = currentPage === 0;
        nextBtn.disabled = currentPage === totalPages - 1;

        if (prevBtn.disabled) {
          prevBtn.style.opacity = "0.5";
          prevBtn.style.cursor = "not-allowed";
          prevBtn.style.background = "#fff";
        } else {
          prevBtn.style.opacity = "1";
          prevBtn.style.cursor = "pointer";
        }

        if (nextBtn.disabled) {
          nextBtn.style.opacity = "0.5";
          nextBtn.style.cursor = "not-allowed";
          nextBtn.style.background = "#94a3b8";
          nextBtn.style.borderColor = "#94a3b8";
        } else {
          nextBtn.style.opacity = "1";
          nextBtn.style.cursor = "pointer";
          nextBtn.style.background = "#4f46e5";
          nextBtn.style.borderColor = "#4f46e5";
        }
      }

      prevBtn.onclick = () => {
        if (currentPage > 0) {
          currentPage--;
          renderMolecules(currentPage);
          updatePagination();
          // 滚动到分子容器顶部
          moleculeContainer.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        }
      };

      nextBtn.onclick = () => {
        if (currentPage < totalPages - 1) {
          currentPage++;
          renderMolecules(currentPage);
          updatePagination();
          // 滚动到分子容器顶部
          moleculeContainer.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        }
      };

      prevBtn.addEventListener("mouseenter", () => {
        if (!prevBtn.disabled) {
          prevBtn.style.background = "#f1f5f9";
          prevBtn.style.borderColor = "#cbd5e1";
        }
      });
      prevBtn.addEventListener("mouseleave", () => {
        if (!prevBtn.disabled) {
          prevBtn.style.background = "#fff";
          prevBtn.style.borderColor = "#e2e8f0";
        }
      });

      nextBtn.addEventListener("mouseenter", () => {
        if (!nextBtn.disabled) {
          nextBtn.style.background = "#4338ca";
        }
      });
      nextBtn.addEventListener("mouseleave", () => {
        if (!nextBtn.disabled) {
          nextBtn.style.background = "#4f46e5";
        }
      });

      updatePagination();

      paginationContainer.appendChild(prevBtn);
      paginationContainer.appendChild(pageInfo);
      paginationContainer.appendChild(nextBtn);
      moleculeContainer.appendChild(paginationContainer);
    }

    // 将分子容器添加到消息元素之后
    const messageWrapper = messageElement.closest(".assistant-wrapper");
    if (messageWrapper) {
      // 在消息框内部添加，保持与消息内容的宽度一致
      const messageBox = messageWrapper.querySelector(".message-box");
      if (messageBox) {
        messageBox.appendChild(moleculeContainer);
      }
    }
  }

  window.HomeMain = {
    init: init,
  };

  // 页面加载完成后初始化
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();


