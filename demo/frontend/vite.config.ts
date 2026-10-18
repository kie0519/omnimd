import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 服务形态单一：构建产物由后端托管（start.sh），无 dev server 配置
export default defineConfig({
  plugins: [react()],
});
