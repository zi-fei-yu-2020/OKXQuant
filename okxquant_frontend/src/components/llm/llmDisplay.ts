/** Shared, presentation-only helpers for the model-service admin views. */

const PROVIDER_GLYPHS: Record<string, { glyph: string; tone: string }> = {
  openai: { glyph: '❖', tone: 'var(--color-up)' },
  siliconflow: { glyph: '⚡', tone: 'var(--color-purple)' },
  gemini: { glyph: '✦', tone: 'var(--color-blue)' },
  openrouter: { glyph: '◈', tone: 'var(--color-brand)' },
  deepseek: { glyph: '◐', tone: 'var(--color-blue)' },
  claude: { glyph: '✳', tone: 'var(--color-warn)' },
  grok: { glyph: 'Ø', tone: 'var(--text-muted)' },
  volcengine: { glyph: '▲', tone: 'var(--color-blue)' },
  dashscope: { glyph: '◇', tone: 'var(--color-warn)' },
  zhipu: { glyph: '◆', tone: 'var(--color-purple)' },
}

export function providerGlyph(id: string | undefined) {
  return PROVIDER_GLYPHS[String(id || '')] || { glyph: '❖', tone: 'var(--color-brand)' }
}

export const CAPABILITIES = [
  { id: 'chat', label: '聊天', tone: 'purple' },
  { id: 'vision', label: '图像理解', tone: 'pink' },
  { id: 'tools', label: '工具调用', tone: 'blue' },
  { id: 'reasoning', label: '链式思考', tone: 'warn' },
] as const

export function toneStyle(tone: string) {
  return {
    backgroundColor: `var(--color-${tone}-bg)`,
    borderColor: `var(--color-${tone}-border)`,
    color: `var(--color-${tone})`,
  }
}

export function resultStyle(ok: boolean) {
  return toneStyle(ok ? 'up' : 'down')
}
