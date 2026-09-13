import test from 'node:test'
import assert from 'node:assert/strict'
import { createSingleFlight } from '../src/utils/singleFlight.ts'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

test('simultaneous reads share one pending operation', async () => {
  const read = createSingleFlight()
  const wait = deferred()
  let calls = 0
  const work = () => { calls++; return wait.promise }
  const first = read('same-session:same-path', work)
  const second = read('same-session:same-path', work)
  assert.equal(first, second)
  await Promise.resolve()
  assert.equal(calls, 1)
  wait.resolve('ok')
  assert.deepEqual(await Promise.all([first, second]), ['ok', 'ok'])
})

test('different session or request keys never share data', async () => {
  const read = createSingleFlight()
  assert.deepEqual(await Promise.all([
    read('session-a:path', async () => 'a'),
    read('session-b:path', async () => 'b'),
  ]), ['a', 'b'])
})

test('completed results are not cached and failures can be retried', async () => {
  const read = createSingleFlight()
  await assert.rejects(read('key', async () => { throw new Error('network') }), /network/)
  assert.equal(await read('key', async () => 'fresh'), 'fresh')
  assert.equal(await read('key', async () => 'newer'), 'newer')
})
