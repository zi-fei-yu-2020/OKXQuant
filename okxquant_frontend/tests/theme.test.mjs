import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const css = readFileSync(new URL('../src/style.css', import.meta.url), 'utf8')
test('connection status text keeps the calibrated semantic contrast', () => {
  const page = readFileSync(new URL('../src/views/admin/LlmPage.vue', import.meta.url), 'utf8')
  const status = page.match(/<span\s+class="([^"]*)"\s*>状态:/)
  assert.ok(status, 'model connection status label exists')
  assert.doesNotMatch(status[1], /opacity-/)
})

const definitions = {
  light: css.match(/:root\s*\{([\s\S]*?)\}/)?.[1],
  dark: css.match(/:root\[data-theme=['"]dark['"]\]\s*\{([\s\S]*?)\}/)?.[1],
}
function luminance(hex) {
  let value = hex.replace('#', '')
  if (value.length === 3) value = [...value].map((char) => char + char).join('')
  const rgb = value.match(/../g).map((value) => parseInt(value, 16) / 255)
  const linear = rgb.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  )
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
}
function ratio(a, b) {
  const values = [luminance(a), luminance(b)].sort((a, b) => b - a)
  return (values[0] + 0.05) / (values[1] + 0.05)
}
for (const [theme, source] of Object.entries(definitions)) {
  test(`${theme} body, muted and supporting text maintain readable contrast`, () => {
    assert.ok(source, `${theme} tokens are missing`)
    const tokens = Object.fromEntries(
      [...source.matchAll(/(--[\w-]+):\s*(#[\da-fA-F]{3,6})\s*;/g)].map((match) => [
        match[1],
        match[2],
      ]),
    )
    for (const name of ['--text-main', '--text-muted', '--text-faint']) {
      for (const surface of ['--bg-card', '--bg-card-subtle', '--bg-badge', '--bg-app']) {
        assert.ok(
          ratio(tokens[name], tokens[surface]) >= 4.5,
          `${theme} ${name} on ${surface} contrast is too low`,
        )
      }
    }
  })
  test(`${theme} brand and financial status labels retain contrast on their semantic surfaces`, () => {
    const tokens = Object.fromEntries(
      [...source.matchAll(/(--[\w-]+):\s*(#[\da-fA-F]{3,6})\s*;/g)].map((match) => [
        match[1],
        match[2],
      ]),
    )
    for (const name of ['brand', 'up', 'down', 'warn', 'purple', 'pink', 'blue']) {
      assert.ok(
        ratio(tokens[`--color-${name}`], tokens[`--color-${name}-bg`]) >= 4.5,
        `${theme} ${name} contrast is too low`,
      )
    }
  })
}

test('HTML body cannot override the selected theme with fixed dark colors', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  const bodyTag = html.match(/<body\b[^>]*>/i)?.[0]
  assert.ok(bodyTag)
  assert.doesNotMatch(bodyTag, /(?:bg|text)-(?:\[#|white|black|slate-|gray-|zinc-)/)
})

test('model status and capability colors must not use dark-only pastel literals', () => {
  const modelPage = readFileSync(new URL('../src/views/admin/LlmPage.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(modelPage, /#(?:818cf8|f472b6|60a5fa|10b981|f87171|f59e0b)/i)
})
