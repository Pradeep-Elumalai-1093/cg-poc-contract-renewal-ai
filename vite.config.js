import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // forwards browser calls to our local Express proxy on :3001,
      // which is the one that actually holds the API key.
      "/api": "http://localhost:3001",
    },
  },
});
