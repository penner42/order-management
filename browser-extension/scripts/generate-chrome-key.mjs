#!/usr/bin/env node
/**
 * Generate (or reuse) a Chrome extension signing key and embed the public key
 * in manifest.json so the extension ID stays stable across installs.
 */
import { execSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const extRoot = join(__dirname, '..')
const keysDir = join(extRoot, '.keys')
const keyPath = join(keysDir, 'chrome.pem')
const manifestPath = join(extRoot, 'manifest.json')

function publicKeyBase64(pemPath) {
  return execSync(`openssl rsa -in "${pemPath}" -pubout -outform DER | openssl base64 -A`, {
    encoding: 'utf8',
  }).trim()
}

function generateKey() {
  mkdirSync(keysDir, { recursive: true })
  execSync(`openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out "${keyPath}"`, {
    stdio: 'inherit',
  })
  console.log(`Created signing key: ${keyPath}`)
}

function updateManifest(publicKey) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  if (manifest.key === publicKey) {
    console.log('manifest.json already has the matching public key.')
    return
  }
  if (manifest.key && manifest.key !== publicKey) {
    console.warn('Warning: manifest.json key differs from .keys/chrome.pem — updating to match the key file.')
  }
  manifest.key = publicKey
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n')
  console.log('Updated manifest.json with Chrome public key.')
}

if (!existsSync(keyPath)) {
  generateKey()
} else {
  console.log(`Using existing key: ${keyPath}`)
}

updateManifest(publicKeyBase64(keyPath))
console.log('Done. Run `npm run sign:chrome` to build a signed .crx.')
