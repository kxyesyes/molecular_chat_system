"use strict";

// Low-level WebSocket lifecycle only. Reconnect policy and application state
// remain in home/main.js so this module cannot invent task outcomes.
window.HomeConnection = {
  create({
    url,
    timeoutMs = 10000,
    socketFactory = window.WebSocket,
    onOpen,
    onMessage,
    onError,
    onClose,
    onCreateError,
    onTimeout,
  }) {
    let socket;
    let timeout;
    try {
      socket = new socketFactory(url);
      timeout = setTimeout(() => {
        if (socket.readyState !== socketFactory.CONNECTING &&
            socket.readyState !== window.WebSocket.CONNECTING) return;
        onTimeout?.(socket);
        socket.close();
      }, timeoutMs);

      socket.onopen = () => {
        clearTimeout(timeout);
        onOpen?.(socket);
      };
      socket.onmessage = event => onMessage?.(event, socket);
      socket.onerror = event => {
        clearTimeout(timeout);
        onError?.(event, socket);
      };
      socket.onclose = event => {
        clearTimeout(timeout);
        onClose?.(event, socket);
      };
      return socket;
    } catch (error) {
      if (timeout) clearTimeout(timeout);
      onCreateError?.(error);
      return null;
    }
  },
};
