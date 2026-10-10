import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

const read = (path) => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')

test('public inspection logs are a direct always-visible card, not a disclosure', () => {
  const { descriptor, errors } = parse(read('views/DashboardView.vue'))
  assert.deepEqual(errors, [])
  const logs = []
  function visit(node, parents = []) {
    if (node.tag === 'LedgerLogs') logs.push(parents)
    for (const child of node.children || []) visit(child, [...parents, node.tag])
    for (const branch of node.branches || []) visit(branch, parents)
  }
  visit(descriptor.template.ast)
  assert.equal(logs.length, 1)
  assert.ok(logs.every(parents => !parents.includes('details')))
  assert.match(read('components/LedgerLogs.vue'), /data-inspection-logs/)
  assert.doesNotMatch(read('views/DashboardView.vue'), /auth\.isAuthenticated/)
})

test('shared buttons retain semantic variants, native type and loading/disabled access contract', () => {
  const button = read('components/ui/AppButton.vue')
  for (const variant of ['primary', 'secondary', 'ghost', 'danger']) assert.ok(button.includes(variant))
  assert.match(button, /:type="type"/)
  assert.match(button, /:disabled="disabled \|\| loading"/)
  assert.match(button, /:aria-busy="loading \|\| undefined"/)
  assert.match(button, /<slot/)
  assert.match(read('components/ui/AppDialog.vue'), /<dialog/)
})


test('visual cleanup retains the existing light/dark brand and financial palette', () => {
  const css = read('style.css')
  const light = css.match(/:root\s*\{([\s\S]*?)\}/)?.[1] || ''
  const dark = css.match(/:root\[data-theme=['"]dark['"]\]\s*\{([\s\S]*?)\}/)?.[1] || ''
  for (const [source, values] of [[light, {'color-brand':'#4658d7','color-up':'#067647','color-down':'#c43249'}], [dark, {'color-brand':'#a2adff','color-up':'#64d5a1','color-down':'#ff9aad'}]]) {
    for (const [name, value] of Object.entries(values)) assert.ok(new RegExp(`--${name}:\\s*${value}\\s*;`, 'i').test(source), name+' palette changed')
  }
})


test('shared icon styles retain positive toggle and danger hover semantics', () => {
  const css = read('style.css')
  assert.match(css, /\.ui-icon-button--active\s*\{[^}]*color: var\(--color-up\)/)
  assert.match(css, /\.ui-icon-button--active:hover:not\(:disabled\)\s*\{[^}]*color: var\(--color-up\)/)
  assert.match(css, /\.ui-icon-button--danger:hover:not\(:disabled\)\s*\{[^}]*color: var\(--color-down\)/)
  for (const page of ['CouncilPage','InterceptorsPage','PromptStudioPage']) {
    const source = read('views/admin/'+page+'.vue')
    assert.match(source, /ui-icon-button--active/)
    assert.match(source, /:aria-pressed=/)
  }
  const genericCards = css.match(/\.admin-content :where\(\.rounded-xl\.border, \.rounded-lg\.border\)\s*\{([^}]*)\}/)?.[1] || ''
  assert.doesNotMatch(genericCards, /background|border-color/)
})

test('module editor title and actions can wrap within a narrow panel', () => {
  const source = read('views/admin/PromptStudioPage.vue')
  assert.match(source, /data-prompt-module/)
  for (const name of ['header','title','actions']) assert.match(source, new RegExp(`\\.prompt-module__${name}\\s*\\{[^}]*flex-wrap: wrap`))
})

test('filled primary/danger buttons use theme tokens with AA contrast for white text', () => {
  const css = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
  const light = css.match(/:root\s*\{([\s\S]*?)\}/)[1]
  const dark = css.match(/:root\[data-theme=['"]dark['"]\]\s*\{([\s\S]*?)\}/)[1]
  const channel = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  const lum = (hex) => { const [r, g, b] = hex.slice(1).match(/../g).map((x) => channel(parseInt(x, 16))); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
  for (const block of [light, dark]) for (const name of ['button-primary', 'button-danger']) {
    const hex = block.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, 'i'))?.[1]
    assert.ok(hex, name)
    assert.ok(1.05 / (lum(hex) + 0.05) >= 4.5, `${name} ${hex} white-text contrast`)
  }
  assert.match(css, /\.ui-button--primary\s*\{[^}]*background:\s*var\(--button-primary\)/)
  assert.doesNotMatch(css, /\.ui-button--(?:primary|danger)\s*\{[^}]*background:\s*#/)
})
