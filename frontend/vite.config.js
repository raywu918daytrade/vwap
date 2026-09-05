import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.TRADING_BACKEND_URL || process.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 8001,
    strictPort: false,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/chart": { target: backend, changeOrigin: true },
      "/quote": { target: backend, changeOrigin: true },
      "/health": { target: backend, changeOrigin: true },
      "/settings": { target: backend, changeOrigin: true },
      "/stream": { target: backend, changeOrigin: true },
      "/vwap_chg": { target: backend, changeOrigin: true },
      "/vwap_activity": { target: backend, changeOrigin: true },
      "/vwap_breakout": { target: backend, changeOrigin: true },
      "/vwap_sr_replay": { target: backend, changeOrigin: true },
      "/vwap_sr_catchup": { target: backend, changeOrigin: true },
      "/vwap_macd_div": { target: backend, changeOrigin: true },
      "/vwap_obv_div": { target: backend, changeOrigin: true },
      "/sr_vwap_cross": { target: backend, changeOrigin: true },
    },
  },
});
