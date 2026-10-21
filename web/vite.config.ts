import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(({ command }) => {
  const production = command === "build";
  let server;
  if (!production) {
    const configuredTarget = process.env.CRAFT_BACKEND_URL;
    let target: URL;
    try { target = new URL(configuredTarget ?? ""); } catch { throw new Error("CRAFT_BACKEND_URL must configure one loopback HTTP backend."); }
    if (!["http:", "https:"].includes(target.protocol) || !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) || target.username || target.password || target.pathname !== "/" || target.search || target.hash) throw new Error("CRAFT_BACKEND_URL must configure one loopback HTTP backend.");
    const proxy = { target: target.origin, changeOrigin: false };
    server = { proxy: { "/api": proxy, "/health": proxy, "/mcp": proxy } };
  }
  return {
    base: production ? "./" : "/",
    envDir: production ? false : undefined,
    publicDir: production ? "release-public" : "public",
    build: { outDir: "dist", sourcemap: false },
    plugins: [react(), tailwindcss()],
    server,
    resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  };
});
