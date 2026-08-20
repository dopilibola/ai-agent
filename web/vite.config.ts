import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only proxy: forward /ai-sales-admin/api to the FastAPI admin API, stripping the
// /ai-sales-admin prefix (the API itself serves plain /api — in production nginx does
// the same strip). Same-origin (localhost:5173) keeps the session cookie
// first-party with no CORS dance.
const API_TARGET = process.env.ADMIN_API_TARGET || "http://127.0.0.1:58210";

// The panel is public at akmaljon.com/ai-sales-admin/ behind nginx, which strips the
// prefix before proxying to the API process — the backend never sees /ai-sales-admin.
export default defineConfig({
  base: "/ai-sales-admin/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/ai-sales-admin/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/ai-sales-admin/, ""),
      },
    },
  },
});
