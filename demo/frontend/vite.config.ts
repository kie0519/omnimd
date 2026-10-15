import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 后端端口与 start.sh 的 PORT 覆盖约定一致（缺省 8300）
    proxy: { "/api": `http://127.0.0.1:${process.env.PORT ?? 8300}` },
  },
});
