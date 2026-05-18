import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

/**
 * Proxy `/api/*` to the two services that scripts/dev-ports.sh forwards
 * onto localhost. Stripping `/api` keeps the backend routes unchanged.
 *
 *   /api/buffer/*    -> ingestor      (localhost:8000)
 *   /api/ask         -> coordinator   (localhost:8001)
 *   /api/findings/*  -> coordinator   (localhost:8001)
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/buffer': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      '/api/ask': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      '/api/findings': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
});
