import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^react\/jsx-runtime$/, replacement: resolve(__dirname, 'src/dashboard-jsx-runtime.ts') },
      { find: /^react\/jsx-dev-runtime$/, replacement: resolve(__dirname, 'src/dashboard-jsx-runtime.ts') },
      { find: /^react$/, replacement: resolve(__dirname, 'src/dashboard-react-shim.ts') },
    ],
  },
  build: {
    lib: {
      entry: resolve(__dirname, 'src/dashboard.tsx'),
      name: 'TheraSkillExpertDashboard',
      formats: ['iife'],
      fileName: () => 'index.js',
      cssFileName: 'style',
    },
    outDir: resolve(__dirname, '../../../plugins/theraskill-expert/dashboard/dist'),
    emptyOutDir: true,
  },
})
