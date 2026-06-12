"use strict";

window.HomeConfig = {
  themes: ["low", "medium", "high"],
  storageKeys: {
    theme: "medchat-theme-level",
    advancedOptions: "medchatAdvancedOptions",
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
