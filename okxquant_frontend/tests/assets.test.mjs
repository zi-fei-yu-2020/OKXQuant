import assert from 'node:assert/strict'
import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { prepareAssets } from '../scripts/prepare-assets.mjs'

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'okxquant-assets-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const okxquant_frontend = join(root, 'okxquant_frontend')
  const source = join(root, 'docs/images')
  const target = join(okxquant_frontend, 'public/images')
  mkdirSync(source, { recursive: true })
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(join(source, 'test.png'), Buffer.from([137, 80, 78, 71, 0, 255]))
  return { root, okxquant_frontend, source, target }
}

for (const state of ['absent', 'windows-link-file', 'directory', 'symlink']) {
  test(`prepares real asset copies from ${state}`, (t) => {
    const { okxquant_frontend, source, target } = fixture(t)
    if (state === 'windows-link-file') writeFileSync(target, '../../docs/images')
    if (state === 'directory') {
      mkdirSync(target)
      writeFileSync(join(target, 'stale.png'), 'stale')
    }
    if (state === 'symlink') {
      try {
        symlinkSync('../../docs/images', target, 'dir')
      } catch (error) {
        if (error.code === 'EPERM' || error.code === 'EACCES') {
          t.skip('Creating Windows symlinks requires developer mode or privileges')
          return
        }
        throw error
      }
    }
    prepareAssets(okxquant_frontend)
    assert.equal(lstatSync(target).isSymbolicLink(), false)
    assert.deepEqual(readdirSync(target), ['test.png'])
    assert.deepEqual(readFileSync(join(target, 'test.png')), readFileSync(join(source, 'test.png')))
    prepareAssets(okxquant_frontend)
    assert.ok(existsSync(join(source, 'test.png')), 'source must survive repeated preparation')
    unlinkSync(join(source, 'test.png'))
    writeFileSync(join(source, 'updated.png'), 'updated')
    prepareAssets(okxquant_frontend)
    assert.deepEqual(readdirSync(target), ['updated.png'], 'removed source assets must not linger')
  })
}

test('missing source fails clearly before changing existing output', (t) => {
  const { okxquant_frontend, source, target } = fixture(t)
  writeFileSync(target, 'keep')
  rmSync(source, { recursive: true })
  assert.throws(() => prepareAssets(okxquant_frontend), /Documentation images not found:.*docs[\\/]images/)
  assert.equal(readFileSync(target, 'utf8'), 'keep')
})

test('CLI resolves the repository/Docker sibling layout independently of cwd', (t) => {
  const { root, okxquant_frontend, target } = fixture(t)
  const script = join(okxquant_frontend, 'scripts/prepare-assets.mjs')
  mkdirSync(dirname(script))
  cpSync(fileURLToPath(new URL('../scripts/prepare-assets.mjs', import.meta.url)), script)
  const result = spawnSync(process.execPath, [script], { cwd: root, encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  assert.ok(existsSync(join(target, 'test.png')))
})

test('all six DocsView image references are copied byte-for-byte from the only source', (t) => {
  const { okxquant_frontend, source, target } = fixture(t)
  cpSync(fileURLToPath(new URL('../../docs/images', import.meta.url)), source, { recursive: true })
  prepareAssets(okxquant_frontend)
  const view = readFileSync(new URL('../src/views/DocsView.vue', import.meta.url), 'utf8')
  const names = [...new Set([...view.matchAll(/src="\/images\/([^"]+)"/g)].map((match) => match[1]))]
  assert.equal(names.length, 6)
  for (const name of names) {
    assert.deepEqual(readFileSync(join(target, name)), readFileSync(join(source, name)), name)
  }
})

test('dev and build prepare assets without install-time hooks or dependencies', () => {
  const { scripts } = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  assert.equal(scripts.predev, 'npm run assets:prepare')
  assert.equal(scripts.prebuild, 'npm run assets:prepare')
  assert.equal(scripts['assets:prepare'], 'node scripts/prepare-assets.mjs')
  assert.equal(scripts.postinstall, undefined)
  assert.equal(scripts.prepare, undefined)
})
