"use strict";

window.HomeChatRenderer = (function () {
  function getElements() {
    return HomeState.elements || {};
  }

  function ensureAnimationStyle() {
    if (document.getElementById("home-toast-animation-style")) {
      return;
    }

    const style = document.createElement("style");
    style.id = "home-toast-animation-style";
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

  function getCurrentTime() {
    const now = new Date();
    const hours = now.getHours().toString().padStart(2, "0");
    const minutes = now.getMinutes().toString().padStart(2, "0");
    return `${hours}:${minutes}`;
  }

  function showToast(message, type) {
    const toast = document.createElement("div");
    const normalizedType = type || "success";
    const bgColor =
      normalizedType === "success"
        ? "#10b981"
        : normalizedType === "error"
          ? "#ef4444"
          : "#f59e0b";
    const icon =
      normalizedType === "success"
        ? "✓"
        : normalizedType === "error"
          ? "!"
          : "i";

    ensureAnimationStyle();

    toast.className = "model-switch-toast";
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

    document.body.appendChild(toast);

    setTimeout(function () {
      toast.style.animation = "slideOutRight 0.3s ease-out";
      setTimeout(function () {
        if (toast.parentNode) {
          toast.parentNode.removeChild(toast);
        }
      }, 300);
    }, 3000);
  }

  function showNotification(message, type) {
    const notification = document.createElement("div");
    const normalizedType = type || "info";
    const colors = {
      info: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
      success: "linear-gradient(135deg, #10b981 0%, #07c983 100%)",
      warning: "linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%)",
      error: "linear-gradient(135deg, #ef4444 0%, #f87171 100%)",
    };
    const icons = {
      info: "i",
      success: "✓",
      warning: "!",
      error: "×",
    };

    ensureAnimationStyle();

    notification.className = `notification notification-${normalizedType}`;
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
      background: ${colors[normalizedType] || colors.info};
      display: flex;
      align-items: center;
      gap: 10px;
    `;

    notification.innerHTML = `<span>${icons[normalizedType]}</span><span>${message}</span>`;
    document.body.appendChild(notification);

    setTimeout(function () {
      notification.style.animation = "slideOutRight 0.3s ease-out";
      setTimeout(function () {
        notification.remove();
      }, 300);
    }, 3000);
  }

  function updateConnectionStatus(status) {
    const elements = getElements();
    if (!elements.connectionStatus) return;

    const statusConfig = {
      connecting: { text: "连接中...", bg: "#fbbf24", icon: "●" },
      connected: { text: "已连接", bg: "#10b981", icon: "●" },
      disconnected: { text: "已断开", bg: "#94a3b8", icon: "●" },
      error: { text: "连接失败", bg: "#ef4444", icon: "●" },
    };

    const config = statusConfig[status] || statusConfig.connecting;
    elements.connectionStatus.innerHTML = `${config.icon} ${config.text}`;
    elements.connectionStatus.style.backgroundColor = config.bg;
    elements.connectionStatus.style.color = "#fff";
    elements.connectionStatus.style.padding = "6px 12px";
    elements.connectionStatus.style.borderRadius = "20px";
    elements.connectionStatus.style.fontWeight = "500";
    elements.connectionStatus.style.transition = "all 0.3s ease";
  }

  function scrollToBottom() {
    const elements = getElements();
    if (!elements.chatContainer) return;

    setTimeout(function () {
      elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
    }, 100);
  }

  function addSystemMessage(message, isError) {
    const elements = getElements();
    if (!HomeState.chatMode || !elements.chatContainer) {
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
    msgDiv.textContent = `提示 ${message}`;

    elements.chatContainer.appendChild(msgDiv);
    elements.chatContainer.scrollTop = elements.chatContainer.scrollHeight;
  }

  return {
    addSystemMessage: addSystemMessage,
    getCurrentTime: getCurrentTime,
    scrollToBottom: scrollToBottom,
    showNotification: showNotification,
    showToast: showToast,
    updateConnectionStatus: updateConnectionStatus,
  };
})();
