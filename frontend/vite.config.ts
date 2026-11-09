/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend so the browser talks to a single
// origin (refresh-token cookie stays httpOnly and same-site).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: { manualChunks: { react: ["react", "react-dom", "react-router-dom"], charts: ["recharts"] } },
      },
    },
    server: { port: 5173, proxy: { "/api": { target: backend, changeOrigin: true } } },
    preview: { port: 4173, proxy: { "/api": { target: backend, changeOrigin: true } } },
    test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"], globals: true, css: false },
  };
});
