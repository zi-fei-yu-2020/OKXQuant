import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const css=readFileSync(new URL('../src/style.css',import.meta.url),'utf8')
const ledger=readFileSync(new URL('../src/components/TradesLedger.vue',import.meta.url),'utf8')
const appTable=readFileSync(new URL('../src/components/ui/AppTable.vue',import.meta.url),'utf8')
const lab=readFileSync(new URL('../src/components/SelfEvolutionLab.vue',import.meta.url),'utf8')
test('single-column terminal grids have zero minimum tracks, not implicit min-content columns',()=>{
 assert.match(css,/\.terminal-overview,\s*\.terminal-overview__left,\s*\.terminal-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
 assert.match(css,/\.terminal-grid\s*>\s*\*\s*\{[^}]*min-width:\s*0/)
 assert.match(css,/\.terminal-overview--dual\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1\.45fr\)\s+minmax\(0,\s*1fr\)/)
})
test('trade history remains a complete keyboard-scrollable table, with an explicit phone hint',()=>{
 assert.match(ledger,/<AppTable\s+label="交易记录明细"[^>]*aria-describedby="trade-ledger-scroll-hint"/)
 assert.match(ledger,/id="trade-ledger-scroll-hint"/)
 assert.equal((ledger.match(/<th\s/g)||[]).length,10)
 assert.match(appTable,/role="region"/)
 assert.match(appTable,/tabindex="0"/)
 assert.match(css,/\.table-scroll-container:focus-visible\s*\{[^}]*outline-offset:\s*-3px/)
})
test('long strategy names and reasons wrap instead of determining the page width',()=>{
 assert.match(ledger,/trade-ledger space-y-3\.5 min-w-0 max-w-full/)
 for(const cls of ['strategy','reason'])assert.match(ledger,new RegExp(`\\.trade-ledger__${cls}[^}]+max-width:[^}]+white-space: normal;[^}]+overflow-wrap: anywhere`))
 assert.match(css,/\.terminal-grid\s*\{\s*overflow-wrap:\s*anywhere/)
})
test('lab headings can wrap instead of squeezing model identifiers offscreen',()=>{
 assert.match(lab,/\.evolution-toolbar\s*\{[^}]*flex-wrap: wrap/)
 assert.match(lab,/\.evolution-toolbar__model\s*\{[^}]*min-width: 0;[^}]*overflow-wrap: anywhere/)
})
