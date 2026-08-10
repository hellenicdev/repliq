import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `base` matches the GitHub Pages repository path (https://<user>.github.io/repliq/).
export default defineConfig({
  plugins: [react()],
  base: '/repliq/',
})
