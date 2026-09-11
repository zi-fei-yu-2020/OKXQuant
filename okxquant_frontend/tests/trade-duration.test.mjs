import test from 'node:test'
import assert from 'node:assert/strict'
import {durationText,tradeDuration} from '../src/utils/tradeDuration.ts'

test('exchange millisecond duration remains visible below one minute',()=>{
 assert.equal(durationText(24.425),'24.4秒');assert.equal(durationText(45.349),'45.3秒')
 assert.equal(durationText(59.99),'59.9秒');assert.equal(durationText(.01),'不足1秒')
 assert.equal(durationText(60),'1分钟');assert.equal(durationText(3721),'1时2分1秒')
})
test('old zero-minute closed snapshots can use explicit Beijing timestamps',()=>{
 const row={status:'closed',duration:'0分钟',open_time:'2026-09-09 18:15:41',close_time:'2026-09-09 18:16:06'}
 assert.equal(tradeDuration(row),'25秒')
 assert.equal(tradeDuration({...row,duration_seconds:24.425}),'24.4秒')
 assert.equal(tradeDuration({...row,status:'holding',duration:'3分钟'}),'3分钟')
})
test('missing or negative durations never become a fabricated holding time',()=>{
 for(const value of [undefined,null,true,-1,NaN,Infinity])assert.equal(durationText(value),'--')
 assert.equal(tradeDuration({}),'--')
 assert.equal(tradeDuration({status:'closed',open_time:'2026-09-09 18:16:06',close_time:'2026-09-09 18:15:41'}),'--')
})
