import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
const proxy = {target:'http://127.0.0.1:8879',changeOrigin:true,rewrite:(path:string)=>path.replace(/^\/api\/plugins\/theraskill-expert/,'')}
export default defineConfig({plugins:[react()],server:{port:5175,proxy:{'/api':proxy}},preview:{proxy:{'/api':proxy}}})
