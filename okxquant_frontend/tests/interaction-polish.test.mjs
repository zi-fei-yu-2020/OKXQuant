import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {adminPages,pageTitle,publicPages} from '../src/config/navigation.ts'
import {readableLog} from '../src/utils/logText.ts'
const read=p=>readFileSync(new URL('../src/'+p,import.meta.url),'utf8')
test('document title is exactly the visible navigation/page title',()=>{
 for(const item of adminPages)assert.equal(pageTitle('/admin/'+item.id),item.label)
 for(const [path,title] of Object.entries(publicPages))assert.equal(pageTitle(path),title)
 assert.ok(read('router/index.ts').includes('document.title = pageTitle(to.path)'))
})
test('old corrupted logs are identified instead of invented or silently erased',()=>{
 assert.equal(readableLog('DOGE'+'?'.repeat(10)),'DOGE〔历史日志编码缺失〕')
 assert.equal(readableLog('为什么?'),'为什么?')
 assert.ok(read('components/LedgerLogs.vue').includes('readableLog(log)'))
})
test('all research disclosures are styled as explicit actions',()=>{
 assert.ok(read('components/EvolutionReviewPanel.vue').includes('action-disclosure'))
 const audit=read('components/DecisionAuditPanel.vue')
 for(const token of ['audit-row__expand','when-closed','when-open','audit-row__summary:focus-visible'])assert.ok(audit.includes(token))
 assert.ok(read('style.css').includes('.action-disclosure > summary::after'))
})
test('initial, transient and prolonged refresh delays use only the status badge',()=>{
 const view=read('views/DashboardView.vue')
 assert.ok(view.includes('data-monitor-connection'))
 assert.ok(view.includes("'数据更新延迟' : '数据已更新'"))
 for(const text of ['账户数据尚未就绪','connection-notice','后台重连中','showConnectionNotice'])assert.ok(!view.includes(text))
 assert.ok(!read('stores/dashboard.ts').includes('useToast'))
 assert.ok(read('stores/dashboard.ts').includes('void fetchDashboard(true)'))
})

test('refresh request is bounded and cannot permanently stall single-flight polling',()=>{
 const source=read('stores/dashboard.ts')
 assert.ok(source.includes('controller.abort(), 8000'))
 assert.ok(source.includes('clearTimeout(timeout)'))
})
