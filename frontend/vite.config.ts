import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import ui from '@nuxt/ui/vite'
import tailwindcss from '@tailwindcss/vite'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tailwindcss(),
    vue(),
    ui({
      ui: {
        colors: {
          primary: 'green',
          neutral: 'neutral',
        },
        pageHero: {
          slots: {
            container: 'py-12 sm:py-12 lg:py-12',
            title: 'mx-auto max-w-xl text-pretty text-3xl sm:text-4xl lg:text-5xl',
            description: 'mt-2 text-md mx-auto max-w-2xl text-pretty sm:text-md text-muted',
          },
        },
      },
    }),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5000',
        changeOrigin: true,
      },
    },
  },
})
