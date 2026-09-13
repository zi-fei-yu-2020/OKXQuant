import { useToast } from './useFeedback'
export function useClipboard() {
  const toast = useToast()
  async function copyText(text: string) {
    if (!text) {
      toast.info('暂无可复制的内容')
      return false
    }
    try {
      await navigator.clipboard.writeText(text)
      toast.success('已复制到剪贴板')
      return true
    } catch {
      toast.error('无法访问剪贴板，请手动选择文本复制，或检查浏览器权限。')
      return false
    }
  }
  return { copyText }
}
