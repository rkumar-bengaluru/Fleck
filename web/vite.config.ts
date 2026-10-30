import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // changeOrigin stays false: the api's origin guard needs the browser's own Origin (http://localhost:5173)
  server: { port: 5173, strictPort: true, proxy: { "/api": process.env.FLECK_API_PROXY ?? "http://localhost:8000" } },
  test: { environment: "jsdom", include: ["tests/**/*.test.tsx"] },
});
