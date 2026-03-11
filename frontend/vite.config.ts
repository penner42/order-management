import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import type { IncomingMessage, ServerResponse } from 'node:http'

const apiProxyTarget = process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000'
const backendOrigin = new URL(apiProxyTarget).origin
const trackingProxyTarget =
  process.env.PACKAGE_TRACKING_PROXY_TARGET ?? 'http://127.0.0.1:8080'

const extensionOriginRe = /^(chrome|moz)-extension:\/\//

function handleExtensionCors(req: IncomingMessage, res: ServerResponse): boolean {
  const origin = req.headers['origin'] as string | undefined
  if (!origin || !extensionOriginRe.test(origin)) return false
  res.setHeader('Access-Control-Allow-Origin', origin)
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS')
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization')
  res.setHeader('Access-Control-Allow-Credentials', 'true')
  if (req.method === 'OPTIONS') {
    res.writeHead(204)
    res.end()
    return true
  }
  return false
}

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    host: true,
    allowedHosts: true,
    cors: true,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (_proxyReq, req, res) => {
            if (handleExtensionCors(req, res as ServerResponse)) {
              (req as any).extensionCorsHandled = true
            }
          })
          proxy.on('proxyRes', (proxyRes: IncomingMessage, req: IncomingMessage) => {
            const origin = req.headers['origin'] as string | undefined
            if (origin && extensionOriginRe.test(origin)) {
              proxyRes.headers['access-control-allow-origin'] = origin
              proxyRes.headers['access-control-allow-credentials'] = 'true'
            }
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
      '/tracking': {
        target: trackingProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/tracking/, ''),
      },
    },
  },
})
