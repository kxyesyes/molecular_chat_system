"use strict";

window.HomeState = {
  ws: null,
  isConnected: false,
  chatMode: false,
  ragEnabled: false,
  toolsEnabled: true,
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
