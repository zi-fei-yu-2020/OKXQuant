// Coalesce only active work; completed results are never cached.
export function createSingleFlight<T>() {
  const pending = new Map<string, Promise<T>>()
  return (key: string, work: () => Promise<T>): Promise<T> => {
    const existing = pending.get(key)
    if (existing) return existing
    const promise = Promise.resolve().then(work).finally(() => {
      if (pending.get(key) === promise) pending.delete(key)
    })
    pending.set(key, promise)
    return promise
  }
}
