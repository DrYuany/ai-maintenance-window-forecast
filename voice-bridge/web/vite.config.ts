import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [react(), VitePWA({ registerType: 'autoUpdate', manifest: { name: '随声译', short_name: '随声译', description: '中文口述，英文翻译与播报', theme_color: '#f6f8f7', background_color: '#f6f8f7', display: 'standalone', start_url: '/', icons: [{ src: '/icons/app.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any maskable' }] } })],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
});
