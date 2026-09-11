// Reuse equal sections of a full snapshot; no fields or history are omitted.
// Callers never mutate previous snapshots. Stable references preserve quiet UI updates.
export function shareSnapshot<T>(previous: T, incoming: T, depth = 0): T {
  if (Object.is(previous, incoming)) return previous
  if (depth > 40 || !previous || !incoming || typeof previous !== 'object' || typeof incoming !== 'object') return incoming
  if (Array.isArray(previous) !== Array.isArray(incoming)) return incoming
  if (Array.isArray(previous) && Array.isArray(incoming)) {
    let equal = previous.length === incoming.length
    const result = incoming.map((item, index) => {
      const shared = shareSnapshot(previous[index], item, depth + 1)
      if (shared !== previous[index]) equal = false
      return shared
    })
    return (equal ? previous : result) as T
  }
  const old = previous as Record<string, unknown>, next = incoming as Record<string, unknown>
  const keys = Object.keys(next)
  let equal = Object.keys(old).length === keys.length
  const result: Record<string, unknown> = {}
  for (const key of keys) {
    const shared = shareSnapshot(old[key], next[key], depth + 1)
    if (!Object.hasOwn(old, key) || shared !== old[key]) equal = false
    Object.defineProperty(result, key, { value: shared, enumerable: true, configurable: true, writable: true })
  }
  return (equal ? previous : result) as T
}
