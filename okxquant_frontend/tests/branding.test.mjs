import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'

const read = path => readFileSync(new URL('../' + path, import.meta.url), 'utf8')
const packageInfo = JSON.parse(read('package.json'))
const branding = {}
new Function('require', 'exports', ts.transpileModule(read('src/config/branding.ts'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
}).outputText)(name => {
  assert.equal(name, '../../package.json')
  return packageInfo
}, branding)

async function render(path) {
  const { descriptor } = parse(read('src/' + path))
  const script = compileScript(descriptor, { id: path, inlineTemplate: true })
  const compiled = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const exports = {}
  const icon = { setup: () => () => Vue.h('svg', { 'aria-hidden': 'true' }) }
  new Function('require', 'exports', compiled)(name => {
    if (name === 'vue') return Vue
    if (name === 'vue-router') return { useRoute: () => ({path: '/admin/overview', meta: {}}), useRouter: () => ({push: () => {}}) }
    if (name === 'lucide-vue-next') return new Proxy({}, {get: () => icon})
    if (name.endsWith('/branding')) return branding
    if (name.endsWith('/navigation')) return {adminPages: [{id:'overview',label:'总览',description:''}]}
    if (name.endsWith('/auth')) return {useAuthStore: () => ({user:{username:'admin'},isSuperadmin:true})}
    if (name.endsWith('/useTheme')) return {useTheme: () => ({theme:Vue.ref('light'),toggleTheme: () => {}})}
    if (name.endsWith('.vue')) return {__esModule:true,default:{setup:(_, {slots})=>()=>Vue.h('div',slots.default?.())}}
    throw new Error('Unmocked dependency: ' + name)
  }, exports)
  const app = Vue.createSSRApp(exports.default)
  app.component('RouterLink',{props:['to'], setup:(props,{slots})=>()=>Vue.h('a',{href:props.to},slots.default?.())})
  app.component('RouterView',{setup:()=>()=>null})
  return renderToString(app)
}

test('workspace version comes from package.json, not a legacy layout number', async () => {
  assert.equal(branding.APP_VERSION, 'v' + packageInfo.version)
  const html = await render('views/AdminLayout.vue')
  assert.ok(html.includes('>'+branding.APP_VERSION+'</span>'))
  assert.doesNotMatch(html, /workspace-version[^>]*>7\.3/)
  assert.match(html, /工作台/)
})
test('administrator sidebar uses the actual shared favicon instead of an activity glyph', async () => {
  const html = await render('views/AdminLayout.vue')
  assert.ok(html.includes('<img src="'+branding.APP_LOGO_SRC+'"'))
  assert.match(html, /class="brand-mark__image"/)
})
test('terminal and login header use the same logo resource as administrator workspace', async () => {
  for (const file of ['components/HeaderBar.vue','views/admin/LoginPage.vue']) {
    assert.ok((await render(file)).includes('<img src="'+branding.APP_LOGO_SRC+'"'), file)
  }
  assert.match(read('public/favicon.svg'), /<svg/)
})
