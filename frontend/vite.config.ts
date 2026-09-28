import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The control center always talks to the API through a RELATIVE /api path:
// the browser never needs to know where the API lives, and the Vite dev server
// proxies it (see server.proxy). That also keeps the app embeddable behind the
// preview host.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: true, // preview host is dynamic (*.e2b.app)
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ready': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/metrics': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  preview: { host: '0.0.0.0', port: 5173, allowedHosts: true },
  build: { outDir: 'dist', sourcemap: false },
});
