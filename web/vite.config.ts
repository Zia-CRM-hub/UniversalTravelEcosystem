import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, forward API calls to the local backend so the app can use same-origin URLs like production.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, allowedHosts: [".trycloudflare.com"], proxy: { "/v1": "http://localhost:8000", "/health": "http://localhost:8000" } },
});
