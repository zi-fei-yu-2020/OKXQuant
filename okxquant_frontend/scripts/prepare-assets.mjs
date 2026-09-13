import { cpSync, lstatSync, mkdirSync, rmSync, statSync, unlinkSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptPath = fileURLToPath(import.meta.url)

// docs/images is the only source; public/images is disposable build output.
// Resolve from the script, not cwd, for Windows, WSL and /app/okxquant_frontend in Docker.
export function prepareAssets(okxquant_frontendDir = resolve(dirname(scriptPath), '..')) {
  const source = resolve(okxquant_frontendDir, '../docs/images')
  const target = resolve(okxquant_frontendDir, 'public/images')
  if (!statSync(source, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error(`Documentation images not found: ${source}. Include docs/images in the checkout or build context.`)
  }

  // Unlink legacy Git symlinks without traversing into the source. A Windows
  // checkout may instead leave a plain file containing ../../docs/images.
  const existing = lstatSync(target, { throwIfNoEntry: false })
  if (existing?.isSymbolicLink() || existing?.isFile()) {
    unlinkSync(target)
  } else if (existing) {
    rmSync(target, { recursive: true })
  }
  mkdirSync(dirname(target), { recursive: true })
  cpSync(source, target, { recursive: true, dereference: true })
}

if (process.argv[1] && resolve(process.argv[1]) === scriptPath) {
  prepareAssets()
}
