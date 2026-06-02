import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-vue": ["vue", "vue-router", "pinia", "axios"],
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
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
