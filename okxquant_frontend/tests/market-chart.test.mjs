import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { CHART_PERIODS, CHART_INDICATORS, chartInstruments, parseCandleSnapshot, chartVwap, candleCountdown, chartPriceLines } from '../src/utils/marketChart.ts'

const start=Date.parse('2026-09-09T00:00:00+08:00')
const candle=(changes={})=>({ts:start,open:.08981,high:.09,low:.089,close:.08982,vol:2,turnover:17.5,confirmed:false,...changes})
const payload=(changes={})=>({instId:'DOGE-USDT-SWAP',bar:'1H',candles:[candle()],price_precision:5,as_of_ms:start+1000,stale:false,has_gaps:false,...changes})

test('dynamic instrument pool prioritizes holdings and includes new monitored symbols',()=>{
 assert.deepEqual(chartInstruments([{instId:'SUI-USDT-SWAP'}],[{instId:'ETH-USDT-SWAP'}],[{instId:'BTC-USDT-SWAP'},{instId:'SUI-USDT-SWAP'},{instId:'WLD-USDT-SWAP'}]),['SUI-USDT-SWAP','ETH-USDT-SWAP','BTC-USDT-SWAP','WLD-USDT-SWAP'])
 assert.deepEqual(chartInstruments([null,{instId:'https://example.org'},{instId:'BTC-USDT'}]),['BTC-USDT-SWAP'])
})
test('chart parses complete OHLCV while preserving actual turnover and price precision',()=>{
 const result=parseCandleSnapshot(payload(),'DOGE-USDT-SWAP','1H')
 assert.equal(result.bars[0].close,.08982);assert.equal(result.precision,5)
 assert.equal(result.bars[0].turnover,17.5);assert.equal(result.bars[0].confirmed,false)
 assert.deepEqual(CHART_PERIODS.map(x=>x.id),['15m','1H','4H','1D'])
})
test('wrong instrument, wrong period and invalid OHLC never become chart data',()=>{
 for(const changes of [{instId:'BTC-USDT-SWAP'},{bar:'4H'},{candles:[]},{candles:[candle({close:null})]},{candles:[candle({high:.088})]},{candles:[candle({vol:-1})]},{candles:[candle({turnover:undefined})]},{candles:[candle({confirmed:1})]},{candles:[candle(),candle()]},{price_precision:20},{stale:'false'}]){
  assert.throws(()=>parseCandleSnapshot(payload(changes),'DOGE-USDT-SWAP','1H'))
 }
})
test('zero volume is known but VWAP is not invented from a price without trades',()=>{
 const result=parseCandleSnapshot(payload({candles:[candle({vol:0,turnover:0})]}),'DOGE-USDT-SWAP','1H')
 assert.equal(result.bars[0].volume,0);assert.equal(chartVwap(result.bars)[0].vwap,null)
})
test('VWAP uses full UTC session dates rather than day-of-month alone',()=>{
 const rows=[{timestamp:Date.parse('2026-09-09T12:00:00Z'),open:100,high:100,low:100,close:100,volume:2},
  {timestamp:Date.parse('2026-09-09T13:00:00Z'),open:120,high:120,low:120,close:120,volume:2},
  {timestamp:Date.parse('2026-10-09T12:00:00Z'),open:80,high:80,low:80,close:80,volume:1}]
 assert.deepEqual(chartVwap(rows).map(v=>v.vwap),[100,110,80])
})
test('daily and four-hour countdowns retain hours and follow exchange candle start time',()=>{
 assert.equal(candleCountdown(start,'1D',start+1000),'23:59:59')
 assert.equal(candleCountdown(start,'4H',start+1000),'03:59:59')
 assert.equal(candleCountdown(start,'15m',start+1000),'14:59')
 assert.equal(candleCountdown(start,'1H',start+3600000),'等待新K线')
 assert.equal(candleCountdown(undefined,'1H',start),'--')
})
test('reference lines never infer ATR stops or claim stale protection is verified',()=>{
 const p={instId:'BTC-USDT-SWAP',side:'long',avgPx:100,displayStop:95,takeProfitPx:110}
 assert.deepEqual(chartPriceLines(p.instId,[p],[]).map(v=>v.id),['cost-0'])
 assert.equal(chartPriceLines(p.instId,[{...p,cloud_oco_verified:true,protectionStatus:'fully_protected'}],[]).length,3)
 assert.equal(chartPriceLines(p.instId,[{...p,avgPx:0}],[]).length,0)
 assert.equal(chartPriceLines('ETH-USDT-SWAP',[p],[]).length,0)
 assert.equal(chartPriceLines(p.instId,[],[{instId:p.instId,state:'canceled',px:105}]).length,0)
 assert.equal(chartPriceLines(p.instId,[],[{instId:p.instId,state:'live',px:'105'}])[0].price,105)
})
test('chart uses public SDK updates and keeps its data request independent of the monitor',()=>{
 const source=readFileSync(new URL('../src/components/MarketCandles.vue',import.meta.url),'utf8')
 for(const required of ['subscribeBar','unsubscribeBar','stream(bar)','lock: true','ResizeObserver','document.visibilityState','epoch !== generation','controller.abort()','chart?.setStyles','setInterval(() => { void refresh() }, 3000)'])assert.ok(source.includes(required),required)
 for(const forbidden of ['_chartStore','_addData','window.__klineChart','POST','close-position','amend-order'])assert.ok(!source.includes(forbidden),forbidden)
 assert.equal(CHART_INDICATORS.length,12)
 const store=readFileSync(new URL('../src/stores/dashboard.ts',import.meta.url),'utf8')
 assert.ok(store.includes('/api/all?_t='));assert.ok(store.includes('startPolling(intervalMs = 3000)'))
})
test('only one chart dependency is introduced and it stays lazy-loaded',()=>{
 const pkg=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8'))
 assert.equal(pkg.dependencies.klinecharts,'10.0.3');assert.equal(pkg.dependencies['lightweight-charts'],undefined)
 const component=readFileSync(new URL('../src/components/MarketCandles.vue',import.meta.url),'utf8')
 assert.ok(component.includes("import('klinecharts')"))
 const view=readFileSync(new URL('../src/views/DashboardView.vue',import.meta.url),'utf8')
 assert.ok(view.includes('<TopHudRibbon /><MarketCandles'))
 assert.ok(view.includes('<TacticalDesk />'))
})
