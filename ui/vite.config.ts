import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/ui/',
  plugins: [react()],
  server: {
    https: {
      // Dev mode: use self-signed certs from ~/.surf-ssh
      // In production, the Python daemon serves the built assets
    },
    proxy: {
      '/api': {
        target: 'https://localhost:8443',
        secure: false,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
