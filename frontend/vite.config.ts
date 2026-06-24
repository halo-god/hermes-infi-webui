import { fileURLToPath, URL } from "node:url";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  // Load env vars (VITE_API_PROXY_TARGET overrides the dev proxy target).
  // Default matches Docker Compose / Make targets which serve API on :8000.
  // Bare-metal deployments running uvicorn on a different port can set
  // VITE_API_PROXY_TARGET=http://localhost:8001 in frontend/.env.local
  // without patching this file.
  // Use globalThis to avoid requiring @types/node just for process.cwd().
  const g = globalThis as { process?: { cwd: () => string } };
  const cwd = g.process?.cwd() ?? ".";
  const env = loadEnv(mode, cwd, "");
  const proxyTarget = env.VITE_API_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            "vendor-vue": ["vue", "vue-router", "pinia", "axios"],
            "vendor-naive": ["naive-ui"],
            "vendor-mermaid": ["mermaid"],
            "vendor-katex": ["katex", "@vscode/markdown-it-katex"],
            "vendor-hljs": ["highlight.js"],
            "vendor-virtual": ["@tanstack/vue-virtual"],
          },
        },
      },
      chunkSizeWarningLimit: 600,
    },
    server: {
      host: true,
      port: 5173,
      proxy: {
        // In dev, proxy API calls (incl. WebSocket) to the FastAPI backend.
        // Target is configurable via VITE_API_PROXY_TARGET env var.
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      host: true,
      port: 5173,
      allowedHosts: ["hermes.infiled.com"],
    },
  };
});
