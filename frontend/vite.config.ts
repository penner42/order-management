import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import type { IncomingMessage } from 'node:http'

const apiProxyTarget = process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000'
const backendOrigin = new URL(apiProxyTarget).origin

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: true,
    allowedHosts: true,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyRes', (proxyRes: IncomingMessage, req: IncomingMessage) => {
            const status = proxyRes.statusCode ?? 0
            if (status < 300 || status >= 400) return
            const location = proxyRes.headers['location']
            if (!location || !location.startsWith(backendOrigin)) return
            const host = req.headers['host']
            if (!host) return
            const proto = (req.headers['x-forwarded-proto'] as string) ?? 'http'
            const publicOrigin = `${proto}://${host}`
            proxyRes.headers['location'] = location.replace(backendOrigin, publicOrigin)
          })
        },
      },
    },
  },
})
