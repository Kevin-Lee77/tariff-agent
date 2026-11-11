import { defineConfig } from "vite";
export default defineConfig({
  server: {
    proxy: {
      "/v1": "http://127.0.0.1:18000",
      "/auth": "http://127.0.0.1:18000",
      "/health": "http://127.0.0.1:18000",
    },
  },
});
