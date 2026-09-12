import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本地开发时把 /api 代理到 FastAPI；生产环境由 nginx 反代
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
