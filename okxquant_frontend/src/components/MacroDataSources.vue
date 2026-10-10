<script setup lang="ts">
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { useApi } from '../composables/useApi'
import AppCard from './ui/AppCard.vue'
import AppButton from './ui/AppButton.vue'

const { api } = useApi()
const state = ref<any>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const form = reactive({ official_enabled: true })
const abort = new AbortController()
const sources: Record<string,string> = { treasury: '美国财政部 · 2Y / 10Y', bea: 'BEA · GDP / PCE 日程', bls: 'BLS · CPI / 就业日程' }
const states: Record<string,string> = { available: '可用', ok: '已采集', disabled: '已关闭', unavailable: '尚无数据', error: '采集失败', stale: '缓存已过期', cached_after_error: '上次缓存仍有效 · 刷新失败', stale_observation: '观测日期已过期', outdated_schedule: '日程覆盖已过期' }
const errors: Record<string,string> = { access_denied: '来源拒绝访问（403）', rate_limited: '来源限流', timeout: '连接超时', transport_error: '网络不可用', invalid_calendar: '日历格式无效', no_verified_event_times: '无可核实的日程时间' }
const label = (value: string) => states[value] || value || '尚无数据'
const at = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? new Date(value*1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '—'
function assign(next: any) {
  state.value = next
  form.official_enabled = next.configuration.official_enabled
}
async function run(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await fn() } catch (e: any) { if (!abort.signal.aborted && !e?.silent) error.value = e?.message || '官方数据操作失败' }
  finally { busy.value = false }
}
async function load() { await run(async () => { assign(await api('/api/v1/admin/macro-data', { signal: abort.signal })) }) }
async function save() {
  await run(async () => {
    const body = { official_enabled: form.official_enabled, expected_revision: state.value.configuration.revision }
    assign(await api('/api/v1/admin/macro-data', { method: 'PUT', body: JSON.stringify(body), signal: abort.signal }))
    notice.value = '配置已保存；仅影响官方数据采集，不会触发 AI 请求或交易。'
  })
}
async function refresh() {
  await run(async () => {
    const next = await api('/api/v1/admin/macro-data/refresh', { method: 'POST', signal: abort.signal })
    state.value = { ...next, configuration: state.value.configuration }
    notice.value = '检查完成，仍遵守缓存及失败退避；以各来源状态为准。'
  })
}
onMounted(load)
onBeforeUnmount(() => abort.abort())
</script>

<template>
  <AppCard title="官方宏观数据" description="无需 API Key 的财政部收益率与 BEA/BLS 发布日程；为15分钟 AI 提供可追溯背景。" data-macro-sources>
    <div class="space-y-5" :aria-busy="busy">
      <p v-if="error" role="alert" class="text-sm" style="color:var(--color-danger)">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm" style="color:var(--text-secondary)">{{ notice }}</p>
      <div v-if="!state" class="flex items-center gap-3 text-sm" style="color:var(--text-muted)"><span>{{ busy ? '正在读取官方数据状态…' : '接入状态暂不可用' }}</span><AppButton v-if="!busy" @click="load">重试</AppButton></div>
      <template v-else>
        <div class="overflow-x-auto rounded-lg border" style="border-color:var(--border-subtle)">
          <table class="w-full text-left text-xs" aria-label="官方宏观数据源状态">
            <thead style="background:var(--bg-card-subtle);color:var(--text-muted)"><tr><th class="px-3 py-2">数据源</th><th class="px-3 py-2">可用性</th><th class="px-3 py-2">上次成功采集 · 北京时间</th></tr></thead>
            <tbody><tr v-for="(name, id) in sources" :key="id" class="border-t" style="border-color:var(--border-subtle)"><td class="px-3 py-3 font-medium whitespace-nowrap">{{ name }}</td><td class="px-3 py-3">{{ label(state.snapshot.sources[id]?.status) }}<p v-if="state.snapshot.sources[id]?.error" class="mt-1" style="color:var(--text-muted)">{{ errors[state.snapshot.sources[id].error] || state.snapshot.sources[id].error }}</p></td><td class="px-3 py-3 font-mono whitespace-nowrap">{{ at(state.snapshot.sources[id]?.last_success_at) }}</td></tr></tbody>
          </table>
        </div>
        <form class="space-y-3" @submit.prevent="save">
          <label class="flex items-center gap-2 text-sm"><input v-model="form.official_enabled" :disabled="busy" type="checkbox">启用官方公开收益率与发布日历</label>
          <p class="text-xs" style="color:var(--text-muted)">默认每小时采集；失败会退避，不改变开仓或持仓保护规则。</p>
          <div class="flex flex-wrap gap-2"><AppButton type="submit" variant="primary" :loading="busy">保存配置</AppButton><AppButton :disabled="busy" @click="refresh">检查到期数据</AppButton></div>
        </form>
        <div v-if="state.snapshot.rates?.observation_date" class="border-t pt-4" style="border-color:var(--border-subtle)">
          <p class="text-sm font-medium">美国财政部日频收益率 <span class="font-normal text-xs" style="color:var(--text-muted)">观测日 {{ state.snapshot.rates.observation_date }} · {{ state.snapshot.rates.usable ? '日频参考' : '已过期，不进入数值证据' }}</span></p>
          <p class="font-mono text-sm mt-2">2Y {{ number(state.snapshot.rates.us2y_pct) }}% · 10Y {{ number(state.snapshot.rates.us10y_pct) }}% · 利差 {{ number(state.snapshot.rates.spread_10y_2y_bp) }} bp</p>
          <p class="text-xs mt-1" style="color:var(--text-muted)">不是美联储政策利率，也不是盘中债券报价。</p>
        </div>
        <div v-if="state.snapshot.events?.length" class="space-y-2 border-t pt-4" style="border-color:var(--border-subtle)">
          <p class="text-sm font-medium">官方发布日程 <span class="text-xs font-normal" style="color:var(--text-muted)">高相关事件优先 · 北京时间 · 非完整日历</span></p>
          <div v-for="event in state.snapshot.events.slice(0, 10)" :key="event.id" class="grid grid-cols-1 md:grid-cols-[10rem_1fr] gap-1 text-xs"><span class="font-mono" style="color:var(--text-muted)">{{ at(event.scheduled_at) }}</span><span>{{ event.event }} <span style="color:var(--text-muted)">[{{ event.source }}] · 仅发布日程</span></span></div>
        </div>
        <p class="text-xs leading-relaxed" style="color:var(--text-muted)">美股、美元指数以及经济数据预期值和实际值尚未接入。过期数据不进入当前数值证据；官方日历覆盖有限，缺失不等于没有事件风险。</p>
      </template>
    </div>
  </AppCard>
</template>
