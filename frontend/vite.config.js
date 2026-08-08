import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // FastAPI runs on 8080 (not 8000 — that's commonly vLLM's own port)
      "/api": "http://localhost:8080",
    },
  },
});
