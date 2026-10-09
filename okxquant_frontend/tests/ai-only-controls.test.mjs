import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { compileTemplate } from '@vue/compiler-sfc'
import ts from 'typescript'
function fragment(file,marker){
 const source=readFileSync(new URL('../src/'+file,import.meta.url),'utf8')
 const at=source.indexOf(marker);assert.ok(at>=0)
 const start=source.lastIndexOf('<AppCard',at);const end=source.indexOf('</AppCard>',at)+10
 return source.slice(start,end)
}
async function render(source,state){
 const compiled=compileTemplate({source,filename:'ai-only-test.vue',id:'ai-only-test'})
 assert.deepEqual(compiled.errors,[])
 const js=ts.transpileModule(compiled.code,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={};new Function('require','exports',js)(name=>{if(name==='vue')return Vue;throw Error(name)},exports)
 const app=Vue.createSSRApp({setup:()=>state,render:exports.render})
 app.component('AppCard',{setup:(_,{slots})=>()=>Vue.h('section',slots.default?.())})
 return renderToString(app)
}
test('retired engine has no re-authorization controls even with stale live flags',async()=>{
 const source=fragment('views/admin/PromptStudioPage.vue','自动开仓来源')
 const html=await render(source,{auth:{isSuperadmin:true},engineLoading:false,engineError:'',engineStatus:{status:'retired',environment:'live',enabled:true,authorized:true},engineStatusText:'已移除',actionBusy:false,loadEngineStatus(){},authorizeLiveEngine(){}})
 assert.match(html,/15 分钟 AI 主脑/);assert.match(html,/退役分钟仓位不再加仓/)
 assert.doesNotMatch(html,/确认实盘授权|撤销实盘授权/)
})
test('history receipt describes validated references without inventing weights or quote feeds',async()=>{
 const source=fragment('components/AiBrainHistory.vue','data-market-context-receipt')
 const html=await render(source,{item:{market_context_receipt:{provided_article_count:6,cited_article_count:0,sentiment_cited_by:[]}}})
 assert.match(html,/已提供 6 条新闻/);assert.match(html,/明确引用 0 条/)
 assert.match(html,/未明确引用不等于没有阅读/);assert.match(html,/尚未接入/)
 const legacy=await render(source,{item:{}})
 assert.doesNotMatch(legacy,/已提供|明确引用/)
})
