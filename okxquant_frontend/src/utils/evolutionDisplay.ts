/** Display-only grouping. A model's own "verified" label is not system verification. */
export function insightGroups(insights: string[] = []) {
  const definitions = [
    { id: 'observations', label: '报告观察', match: '已验证事实' },
    { id: 'hypotheses', label: '待验证假设', match: '待验证假设' },
    { id: 'gaps', label: '数据缺口', match: '数理快照不可观测' },
    { id: 'other', label: '其他复盘记录', match: '' },
  ]
  const groups = definitions.map(group => ({ ...group, items: [] as Array<{ index: number; original: string; text: string }> }))
  insights.forEach((original, index) => {
    const tag = original.match(/^\s*【([^】]+)】/)
    const group = groups.find(group => group.match && group.match === tag?.[1]) || groups[3]!
    group.items.push({ index, original, text: tag ? original.slice(tag[0].length).trim() : original })
  })
  return groups.filter(group => group.items.length)
}

export function insightExcerpt(text: string, limit = 96): string {
  const chars = Array.from(text)
  return chars.length > limit ? chars.slice(0, limit).join('') + '…' : text
}

export function reviewStatusLabel(status?: string): string {
  return ({success:'复盘已完成', no_new_evidence:'暂无新增证据', failed:'最近尝试失败', timeout:'最近尝试超时',
    running:'复盘运行中', not_run:'尚无复盘记录', other_scope_report:'报告账户待核验'} as Record<string,string>)[status || 'not_run'] || '状态待核验'
}

export function changeProposalLabel(value?: string | null): string {
  return ({NO_CHANGE:'无需变更', ADD:'新增建议', REVISE:'修订建议', UPDATE:'更新建议', DEACTIVATE:'停用建议'} as Record<string,string>)[value || ''] || value || '—'
}
