<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { mediaTicket } from "@/api/client";

const termContainer = ref<HTMLElement | null>(null);
const connected = ref(false);
const connecting = ref(false);

let terminal: Terminal | null = null;
let fitAddon: FitAddon | null = null;
let ws: WebSocket | null = null;
let resizeObserver: ResizeObserver | null = null;

async function connect() {
  if (ws) return;
  connecting.value = true;

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ticket = await mediaTicket.ensure();
  ws = new WebSocket(`${proto}//${location.host}/api/v1/terminal/ws?ticket=${encodeURIComponent(ticket)}`);

  ws.onopen = () => {
    connected.value = true;
    connecting.value = false;
    terminal?.writeln("\x1b[32m✓ Connected to Hermes Terminal\x1b[0m");
  };

  ws.onmessage = (e) => {
    terminal?.write(e.data);
  };

  ws.onclose = (e) => {
    connected.value = false;
    connecting.value = false;
    if (e.code !== 1000) {
      terminal?.writeln(`\r\n\x1b[31m[Disconnected: ${e.reason || "connection closed"}]\x1b[0m`);
    }
    ws = null;
  };

  ws.onerror = () => {
    connected.value = false;
    connecting.value = false;
    terminal?.writeln("\r\n\x1b[31m[Connection error]\x1b[0m");
    ws = null;
  };
}

function disconnect() {
  ws?.close(1000, "user disconnect");
  ws = null;
}

onMounted(() => {
  if (!termContainer.value) return;

  terminal = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: '"JetBrains Mono", "Menlo", "Consolas", monospace',
    theme: {
      background: "#1a1a1a",
      foreground: "#e0e0e0",
      cursor: "#b8852a",
      selectionBackground: "rgba(184, 133, 42, 0.3)",
    },
    rows: 24,
    cols: 100,
  });

  fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  terminal.open(termContainer.value);
  fitAddon.fit();

  // Send input to WebSocket
  terminal.onData((data) => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(data);
    }
  });

  // Auto-resize
  resizeObserver = new ResizeObserver(() => {
    fitAddon?.fit();
  });
  resizeObserver.observe(termContainer.value);

  // Connect
  connect();
});

onBeforeUnmount(() => {
  disconnect();
  resizeObserver?.disconnect();
  terminal?.dispose();
});
</script>

<template>
  <div class="terminal-page">
    <div class="terminal-head">
      <span class="terminal-status" :class="{ online: connected, pending: connecting }">
        {{ connecting ? "连接中..." : connected ? "已连接" : "未连接" }}
      </span>
      <button v-if="!connected && !connecting" class="terminal-btn" @click="connect">连接</button>
      <button v-if="connected" class="terminal-btn" @click="disconnect">断开</button>
    </div>
    <div ref="termContainer" class="terminal-body" />
  </div>
</template>

<style scoped>
.terminal-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #1a1a1a;
}
.terminal-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: #222;
  border-bottom: 1px solid #333;
  font-size: 12px;
}
.terminal-status {
  color: #888;
}
.terminal-status.online {
  color: #5a9a48;
}
.terminal-status.pending {
  color: #d4a04a;
}
.terminal-btn {
  background: #333;
  color: #ccc;
  border: 1px solid #444;
  border-radius: 4px;
  padding: 2px 10px;
  font-size: 11px;
  cursor: pointer;
}
.terminal-btn:hover {
  background: #444;
}
.terminal-body {
  flex: 1;
  padding: 4px;
  overflow: hidden;
}
.terminal-body :deep(.xterm) {
  height: 100%;
}
</style>
