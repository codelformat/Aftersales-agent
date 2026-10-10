import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig(({ mode }) => {
  const replay = mode === 'replay';
  return {
    plugins: [react()],
    base: replay ? '/Aftersales-agent/' : '/',
    define: {
      'import.meta.env.VITE_DATA_SOURCE': JSON.stringify(replay ? 'replay' : 'live'),
    },
    build: {
      outDir: replay ? 'dist-replay' : '../app/web/dist',
      emptyOutDir: true,
    },
    preview: { host: '127.0.0.1' },
    server: {
      proxy: {
        '/api': 'http://127.0.0.1:8000',
        '/chat': 'http://127.0.0.1:8000',
        '/tickets': 'http://127.0.0.1:8000',
        '/refunds': 'http://127.0.0.1:8000',
      },
    },
  };
});
