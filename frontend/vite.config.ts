import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 方案甲：后端在 Linux 虚拟机；Windows 只跑前端。按实际 VM IP 修改。
const API_TARGET = process.env.VITE_API_PROXY || "http://192.168.88.136:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
});
