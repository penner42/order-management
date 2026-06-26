#!/usr/bin/env node
/**
 * Build a clean extension zip, then pack and sign it as a Chrome .crx.
 */
import crx3 from 'crx3'
import { execSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const extRoot = join(__dirname, '..')
const keysDir = join(extRoot, '.keys')
const keyPath = join(keysDir, 'chrome.pem')
const distDir = join(extRoot, 'dist')

const manifest = JSON.parse(readFileSync(join(extRoot, 'manifest.json'), 'utf8'))
const version = manifest.version || '0.0.0'

if (!existsSync(keyPath)) {
  console.error('No Chrome signing key found. Run: npm run generate-key')
  process.exit(1)
}

if (!manifest.key) {
  console.error('manifest.json is missing the "key" field. Run: npm run generate-key')
  process.exit(1)
}

mkdirSync(distDir, { recursive: true })

console.log('Building clean extension zip…')
execSync('npx web-ext build --source-dir=. --artifacts-dir=./dist --overwrite-dest', {
  cwd: extRoot,
  stdio: 'inherit',
})

const zipFile = readdirSync(distDir).find((name) => name.endsWith('.zip'))
if (!zipFile) {
  console.error('web-ext build did not produce a zip in dist/')
  process.exit(1)
}

const zipPath = join(distDir, zipFile)
const crxPath = join(distDir, `order-manager-${version}.crx`)
const stagingDir = mkdtempSync(join(tmpdir(), 'order-manager-ext-'))

try {
  execSync(`unzip -q "${zipPath}" -d "${stagingDir}"`)
  await crx3([stagingDir], { keyPath, crxPath })
} finally {
  rmSync(stagingDir, { recursive: true, force: true })
}

console.log(`Signed Chrome extension: ${crxPath}`)
console.log('Install: chrome://extensions → Developer mode → drag the .crx onto the page.')
