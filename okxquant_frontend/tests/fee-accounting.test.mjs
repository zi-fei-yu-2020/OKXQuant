import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as accounting from '../src/utils/feeAccounting.ts'
import { isSettlementPending } from '../src/utils/tradeSettlement.ts'
import { tradeDuration } from '../src/utils/tradeDuration.ts'
import { observedNumber } from '../src/utils/observationDisplay.ts'

const verified = { status: 'closed', fee_allocation: 'verified_from_archived_fills', fee_reconciliation: {status:'verified'}, open_fee:-.00786281,close_fee:-.00785893 }
test('fee allocation needs explicit proven status and finite amounts', () => {
  assert.equal(accounting.feeAccounting(verified).verified,true)
  for (const change of [{status:'closed_pending'}, {fee_allocation:'unknown'}, {open_fee:null}, {close_fee:NaN}, {fee_reconciliation:{status:'unverified'}}]) {
    const result=accounting.feeAccounting({...verified,...change})
    assert.equal(result.verified,false);assert.equal(result.opening,null);assert.equal(result.closing,null)
  }
})
test('zero and rebates are displayed without false charges or loss of precision', () => {
  assert.equal(accounting.feeText(0),'0 USDT')
  assert.equal(accounting.feeText(-.00786281),'-0.00786281 USDT')
  assert.equal(accounting.feeText(.0001),'+0.0001 USDT')
  for (const value of [null,undefined,'',NaN,Infinity,true]) assert.equal(accounting.feeText(value),'--')
})
test('explicit null does not become fallback gross pnl and actual zero is retained', () => {
  assert.equal(accounting.ledgerValue({net_pnl:null,pnl:12,gross_pnl:15},['net_pnl','pnl']),null)
  assert.equal(accounting.ledgerValue({net_pnl:0,pnl:12},['net_pnl','pnl']),0)
  assert.equal(accounting.ledgerValue({pnl:12},['net_pnl','pnl']),12)
  assert.equal(accounting.ledgerNumberText(null,2,' U'),'--')
  assert.equal(accounting.ledgerNumberColor(null),'var(--text-muted)')
})
const source=readFileSync(new URL('../src/components/TradesLedger.vue',import.meta.url),'utf8')
const {descriptor}=parse(source)
const script=compileScript(descriptor,{id:'fee-ledger-test',inlineTemplate:true})
const compiled=ts.transpileModule(script.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const Box={setup:(_,{slots})=>()=>Vue.h('div',slots.default?.())}
const Empty={render:()=>null}
async function render(row){
  const exports={}
  const require=name=>{
    if(name==='vue')return Vue
    if(name==='lucide-vue-next')return {Receipt:Empty,Search:Empty}
    if(name.includes('useDashboard')||name.includes('stores/dashboard'))return {useDashboardStore:()=>({data:{trades:[{id:'test',inst:'BTC',side:'long',open_px:100,close_px:101,...row}]}})}
    if(name.includes('observationDisplay'))return {observedNumber}
    if(name.includes('feeAccounting'))return accounting
    if(name.includes('tradeSettlement'))return {isSettlementPending}
    if(name.includes('tradeDuration'))return {tradeDuration}
    if(name.endsWith('.vue'))return {__esModule:true,default:Box}
    throw Error(name)
  }
  new Function('require','exports',compiled)(require,exports)
  return renderToString(Vue.createSSRApp(exports.default))
}
test('actual ledger SFC exposes verified opening and closing fee amounts', async()=>{
  const html=await render({...verified,net_pnl:0,roi_pct:0})
  assert.ok(html.includes('费用明细'))
  assert.ok(!html.includes('-0.00786281 USDT'))
  assert.ok(source.includes('showFees(t)'))
  assert.ok(html.includes('+0.00 U'))
  assert.ok(source.includes('v-model:open="feeDialogOpen"'))
})
test('unverified and pending entries never fabricate fee allocation or financial zero', async()=>{
  const html=await render({...verified,fee_allocation:'unknown',net_pnl:null,pnl:10,roi_pct:null})
  assert.ok(html.includes('费用明细'))
  assert.ok(!html.includes('-0.00786281 USDT'))
  assert.ok(!html.includes('+10.00 U'));assert.ok(!html.includes('+0.00 U'))
  const pending=await render({...verified,status:'closed_pending',net_pnl:10})
  assert.ok(pending.includes('结算同步中'))
  assert.ok(!pending.includes('data-fee-reconciliation'))
})
test('fee actions occupy a dedicated centered column inside the scroll region', async()=>{
  const html=await render({...verified,net_pnl:1})
  const headers=html.match(/<th\b[^>]*>[\s\S]*?<\/th>/g)
  const cells=html.match(/<td\b[^>]*>[\s\S]*?<\/td>/g)
  assert.equal(headers.length,10);assert.equal(cells.length,10)
  assert.ok(headers[9].includes('text-center'))
  assert.ok(cells[9].includes('trade-ledger__fee-button'))
  assert.ok(!cells[8].includes('trade-ledger__fee-button'))
  assert.ok(cells[9].includes('text-center'))
  assert.ok(source.includes('variant="ghost"'))
  assert.ok(source.includes('min-height: 2.75rem'))
  assert.ok(source.includes('aria-haspopup="dialog"'))
  assert.ok(source.includes('<AppTable'))
})

test('margin and price rendering distinguish zero from invalid observations',async()=>{
 const zero=await render({...verified,margin:0,open_px:0})
 assert.ok(zero.includes('0.00 U'));assert.ok(zero.includes('0.000000'))
 for(const value of [true,Infinity,'bad']){
  const html=await render({...verified,margin:value,open_px:value,net_pnl:null,roi_pct:null})
  assert.ok(!html.includes('Infinity'));assert.ok(!html.includes('1.000000'));assert.ok(!html.includes('0.00 U'))
 }
})
