<script setup lang="ts">
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { useApi } from '../composables/useApi'
import AppCard from './ui/AppCard.vue'
import AppButton from './ui/AppButton.vue'
import AppField from './ui/AppField.vue'

const { api } = useApi()
const state = ref<any>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
const form = reactive({ official_enabled: true, fmp_enabled: false, calendar_timezone: '', api_key: '', clear_api_key: false })
const abort = new AbortController()
const sources: Record<string,string> = { treasury: '美国财政部 · 2Y / 10Y', bea: 'BEA · GDP / PCE 日程', bls: 'BLS · CPI / 就业日程', fmp_indices: 'FMP · 美股指数 / DXY', fmp_calendar: 'FMP · 经济日历与发布值' }
const states: Record<string,string> = { available: '可用', ok: '已采集', partial: '部分可用', disabled: '已关闭', not_configured: '未配置 / 未启用', unavailable: '尚无数据', error: '采集失败', stale: '缓存已过期', cached_after_error: '上次缓存仍有效 · 刷新失败', stale_observation: '观测日期已过期', stale_or_market_closed: '旧报价 / 可能休市', outdated_schedule: '日程覆盖已过期' }
const errors: Record<string,string> = { access_denied: '来源拒绝访问（403）', authentication_failed: 'Key 鉴权失败', subscription_required: '订阅权限不足', rate_limited: '供应商限流', timeout: '连接超时', transport_error: '网络不可用', unverified_calendar_timezone_or_dates: '日历时区或时间未核实', provider_error_payload: '供应商返回错误文档', no_usable_index_quotes: '未取得有效指数报价', index_identity_not_verified: '指数身份未核实' }
const label = (value: string) => states[value] || value || '尚无数据'
const at = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? new Date(value*1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '—'
function assign(next: any) {
  state.value = next
  Object.assign(form, { ...next.configuration, api_key: '', clear_api_key: false })
}
async function run(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await fn() } catch (e: any) { if (!abort.signal.aborted && !e?.silent) error.value = e?.message || '宏观数据操作失败' }
  finally { busy.value = false }
}
async function load() { await run(async () => { assign(await api('/api/v1/admin/macro-data', { signal: abort.signal })) }) }
async function save() {
  await run(async () => {
    const body = { official_enabled: form.official_enabled, fmp_enabled: form.fmp_enabled, calendar_timezone: form.calendar_timezone,
      clear_api_key: form.clear_api_key, expected_revision: state.value.configuration.revision,
      ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}) }
    assign(await api('/api/v1/admin/macro-data', { method: 'PUT', body: JSON.stringify(body), signal: abort.signal }))
    notice.value = '配置已保存。等待定时采集，或检查到期数据；不会触发 AI 请求或交易。'
  })
}
async function refresh() {
  await run(async () => {
    const next = await api('/api/v1/admin/macro-data/refresh', { method: 'POST', signal: abort.signal })
    state.value = { ...next, configuration: state.value.configuration } // Keep the revision of the edited form; a probe cannot authorize a stale overwrite.
    notice.value = '检查完成；仍遵守缓存及限流退避，不会反复消耗接口额度。下方以各来源状态为准。'
  })
}
onMounted(load)
onBeforeUnmount(() => { abort.abort(); form.api_key = '' })
</script>

<template>
  <AppCard title="跨资产与经济日历" description="为15分钟 AI 提供可追溯的宏观背景；不改变开仓风控规则。" data-macro-sources>
    <div class="space-y-5" :aria-busy="busy">
      <p v-if="error" role="alert" class="text-sm" style="color:var(--color-danger)">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm" style="color:var(--text-secondary)">{{ notice }}</p>
      <div v-if="!state" class="flex items-center gap-3 text-sm" style="color:var(--text-muted)">
        <span>{{ busy ? '正在读取接入状态…' : '接入状态暂不可用' }}</span><AppButton v-if="!busy" @click="load">重试</AppButton>
      </div>
      <template v-else>
        <div class="overflow-x-auto rounded-lg border" style="border-color:var(--border-subtle)">
          <table class="w-full text-left text-xs" aria-label="宏观数据源状态">
            <thead style="background:var(--bg-card-subtle);color:var(--text-muted)"><tr><th class="px-3 py-2">数据源</th><th class="px-3 py-2">可用性</th><th class="px-3 py-2">上次成功采集 · 北京时间</th></tr></thead>
            <tbody><tr v-for="(name, id) in sources" :key="id" class="border-t" style="border-color:var(--border-subtle)">
              <td class="px-3 py-3 font-medium whitespace-nowrap">{{ name }}</td>
              <td class="px-3 py-3"><span>{{ label(state.snapshot.sources[id]?.status) }}</span><p v-if="state.snapshot.sources[id]?.error" class="mt-1" style="color:var(--text-muted)">{{ errors[state.snapshot.sources[id].error] || state.snapshot.sources[id].error }}</p></td>
              <td class="px-3 py-3 font-mono whitespace-nowrap">{{ at(state.snapshot.sources[id]?.last_success_at) }}</td>
            </tr></tbody>
          </table>
        </div>
        <form class="space-y-3" @submit.prevent="save">
          <fieldset :disabled="busy" class="space-y-3">
            <legend class="text-sm font-semibold mb-2">接入配置</legend>
            <label class="flex items-center gap-2 text-sm"><input v-model="form.official_enabled" type="checkbox">启用官方公开收益率与发布日历（无需 Key）</label>
            <label class="flex items-start gap-2 text-sm"><input v-model="form.fmp_enabled" type="checkbox" class="mt-1">启用 FMP；我已确认订阅额度及数据用于模型分析、审计展示的权限</label>
            <div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
              <AppField label="FMP API Key" :hint="state.configuration.api_key_configured ? '已配置。留空保留；不会返回或显示原 Key。' : '未配置。请输入自己的 Key，不会自动购买订阅。'" v-slot="field">
                <input :id="field.id" v-model="form.api_key" type="password" autocomplete="new-password" spellcheck="false" class="ui-input w-full" placeholder="留空不修改" :disabled="form.clear_api_key">
              </AppField>
              <AppField label="FMP 无时区日历的时间基准" hint="仅在向供应商核实后选择；留空时拒绝猜测无时区时间。自带时区的响应不受影响。" v-slot="field">
                <select :id="field.id" v-model="form.calendar_timezone" class="ui-input w-full"><option value="">未核实</option><option value="UTC">UTC（已向供应商确认）</option><option value="America/New_York">America/New_York（含夏令时）</option></select>
              </AppField>
            </div>
            <label class="flex items-center gap-2 text-xs" style="color:var(--text-muted)"><input v-model="form.clear_api_key" type="checkbox" @change="form.api_key=''">删除加密存储中的 FMP Key，并关闭 FMP（部署环境另设的 Key 不会被删除）</label>
            <p class="text-xs leading-relaxed" style="color:var(--text-muted)">指数与供应商日历默认每15分钟采集，官方数据每小时采集。全天运行约需385次 FMP 请求/日（未计重试或额外检查），不能假定免费套餐够用。DXY 必须通过指数目录核验；不以 ETF 或其他美元指数替代。</p>
          </fieldset>
          <div class="flex flex-wrap gap-2"><AppButton type="submit" variant="primary" :loading="busy">保存配置</AppButton><AppButton :disabled="busy" @click="refresh">检查到期数据</AppButton></div>
        </form>
        <div v-if="state.snapshot.rates?.observation_date" class="border-t pt-4" style="border-color:var(--border-subtle)">
          <p class="text-sm font-medium">美国财政部日频收益率 <span class="font-normal text-xs" style="color:var(--text-muted)">观测日 {{ state.snapshot.rates.observation_date }} · {{ state.snapshot.rates.usable ? '日频参考' : '已过期，不进入数值证据' }}</span></p>
          <p class="font-mono text-sm mt-2">2Y {{ number(state.snapshot.rates.us2y_pct) }}% · 10Y {{ number(state.snapshot.rates.us10y_pct) }}% · 利差 {{ number(state.snapshot.rates.spread_10y_2y_bp) }} bp</p>
          <p class="text-xs mt-1" style="color:var(--text-muted)">不是美联储政策利率，也不是盘中债券报价。</p>
        </div>
        <div v-if="Object.keys(state.snapshot.quotes || {}).length" class="space-y-2">
          <div v-for="(quote, id) in state.snapshot.quotes" :key="id" class="flex flex-wrap justify-between gap-2 text-xs">
            <span class="font-mono">{{ quote.symbol }} {{ number(quote.price) }} · {{ number(quote.change_pct) }}%</span>
            <span style="color:var(--text-muted)">{{ at(quote.as_of) }} · {{ quote.usable ? '近期供应商报价 · 未承诺实时' : '旧报价 / 可能休市 · 不作为当前数值证据' }}</span>
          </div>
        </div>
        <div v-if="state.snapshot.events?.length" class="space-y-2 border-t pt-4" style="border-color:var(--border-subtle)">
          <p class="text-sm font-medium">近期发布日程 <span class="text-xs font-normal" style="color:var(--text-muted)">高相关事件优先 · 北京时间 · 非完整日历</span></p>
          <div v-for="event in state.snapshot.events.slice(0, 10)" :key="event.id" class="grid grid-cols-1 md:grid-cols-[10rem_1fr] gap-1 text-xs">
            <span class="font-mono" style="color:var(--text-muted)">{{ at(event.scheduled_at) }}</span><span>{{ event.event }} <span style="color:var(--text-muted)">[{{ event.source }}] · 实际 {{ number(event.actual) }} / 预期 {{ number(event.estimate) }} / 前值 {{ number(event.previous) }} {{ event.unit || '（单位未提供）' }}</span></span>
          </div>
        </div>
        <p class="text-xs leading-relaxed" style="color:var(--text-muted)">采集成功不等于行情实时。旧数据保留来源与时间，但不进入当前数值证据；日历缺失不等于没有事件风险。日程不含预期或发布值时显示“—”，不会补零。</p>
      </template>
    </div>
  </AppCard>
</template>
