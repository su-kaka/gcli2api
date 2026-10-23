import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/auth": {
        target: "http://localhost:7861",
        changeOrigin: true,
      },
      "/creds": {
        target: "http://localhost:7861",
        changeOrigin: true,
      },
      "/config": {
        target: "http://localhost:7861",
        changeOrigin: true,
      },
      "/logs": {
        target: "http://localhost:7861",
        changeOrigin: true,
        ws: true,
      },
      "/version": {
        target: "http://localhost:7861",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
});
