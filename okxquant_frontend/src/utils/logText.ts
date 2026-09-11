/** Legacy question marks have already lost their bytes; never invent the text. */
export function readableLog(line: unknown) {
  return String(line ?? '').replace(/[?]{3,}/g, '〔历史日志编码缺失〕')
}
