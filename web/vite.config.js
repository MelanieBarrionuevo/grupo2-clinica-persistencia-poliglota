import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// En desarrollo (npm run dev) se hace proxy de /api hacia la API local.
// En Docker, nginx cumple el mismo papel (ver nginx.conf): el navegador solo habla con la web.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
