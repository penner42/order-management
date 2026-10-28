const test = require('node:test')
const assert = require('node:assert/strict')
const { readFileSync } = require('node:fs')
const { dirname, join } = require('node:path')
const { fileURLToPath, pathToFileURL } = require('node:url')
const vm = require('node:vm')

test('signing uses the newly built release even when older ZIPs exist', async () => {
  const script = join(__dirname, 'sign-chrome.mjs')
  const root = dirname(__dirname)
  const version = '9.8.7'
  const currentZip = join(root, 'dist', `order-manager-${version}.zip`)
  const archives = new Map([[join(root, 'dist', 'old-release.zip'), 'old source']])
  let extractedSource, signedSource, cleaned = false
  const source = readFileSync(script, 'utf8')
    .replace(/\r\n/g, '\n')
    .replace(/^#!.*\n/, '')
    .replace(/^import .*$/gm, '')
    .replaceAll('import.meta.url', 'moduleUrl')
  await vm.runInNewContext(`(async () => { ${source} })()`, {
    moduleUrl: pathToFileURL(script).href, dirname, join, fileURLToPath,
    tmpdir: () => '/tmp',
    console: { log() {}, error() {} },
    process: { exit(code) { throw new Error(`Unexpected exit: ${code}`) } },
    existsSync: path => path.endsWith('chrome.pem') || archives.has(path),
    readFileSync: () => JSON.stringify({ version, key: 'public-test-key' }),
    mkdirSync() {},
    mkdtempSync: () => '/tmp/signing-fixture',
    rmSync: () => { cleaned = true },
    execFileSync(command, args) {
      if (command === 'npx') {
        const filename = args[args.indexOf('--filename') + 1]
        archives.set(join(root, 'dist', filename), 'current source')
      } else if (command === 'unzip') {
        extractedSource = archives.get(args[1])
      } else {
        throw new Error(`Unexpected command: ${command}`)
      }
    },
    crx3: async (paths, options) => {
      assert.equal(paths[0], '/tmp/signing-fixture')
      assert.equal(options.crxPath, join(root, 'dist', `order-manager-${version}.crx`))
      signedSource = extractedSource
    },
  })
  assert.equal(archives.get(currentZip), 'current source')
  assert.equal(signedSource, 'current source')
  assert.equal(cleaned, true)
})
