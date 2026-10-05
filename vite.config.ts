import { readFileSync } from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

type Session = { token: string };

function proxyHeaders() {
  const path = process.env.HEALTH_BEE_SESSION_FILE;
  if (!path) return { origin: "http://127.0.0.1:8000" };
  const session = JSON.parse(readFileSync(path, "utf8")) as Session;
  if (!session.token) throw new Error("Local API session is missing a token.");
  return { origin: "http://127.0.0.1:8000", authorization: `Bearer ${session.token}` };
}

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, headers: proxyHeaders() } },
  },
  preview: { host: "127.0.0.1", port: 4173 },
});
