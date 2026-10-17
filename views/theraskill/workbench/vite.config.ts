import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const proxy = (target: string, prefix: string) => ({ target, changeOrigin: true, rewrite: (path: string) => path.replace(new RegExp(`^/api/${prefix}`), '') })

export default defineConfig({
  plugins: [react()],
  server: { port: 5176, proxy: {
    '/api/resources': { target: 'http://127.0.0.1:5186', changeOrigin: true },
    '/api/support': proxy('http://127.0.0.1:8878', 'support'),
    '/api/simulation': proxy('http://127.0.0.1:8876', 'simulation'),
    '/api/expert': proxy('http://127.0.0.1:8879', 'expert'),
    '/api/contribution': proxy('http://127.0.0.1:8881', 'contribution'),
    '/api/extraction': proxy('http://127.0.0.1:8891', 'extraction'),
    '/api/transcript': proxy('http://127.0.0.1:8892', 'transcript'),
  }},
  preview: { port: 5176 },
})
