<script setup lang="ts">
// Chart-only adaptation of upstream TacticalChart.vue (6cc663f, MIT).
// No private chart internals, synthetic candles, trading commands or strategy writes.
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import type { Chart, DeepPartial, KLineData, Styles } from 'klinecharts'
import { ChartCandlestick, RefreshCw, SlidersHorizontal, RotateCcw, Plus, Minus } from 'lucide-vue-next'
import AppCard from './ui/AppCard.vue'
import AppBadge from './ui/AppBadge.vue'
import AppButton from './ui/AppButton.vue'
import AppDialog from './ui/AppDialog.vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { CHART_PERIODS, CHART_INDICATORS, chartInstruments, parseCandleSnapshot, chartVwap, candleCountdown, chartPriceLines } from '../utils/marketChart'
import type { ChartBar, ChartPeriod, CandleSnapshot } from '../utils/marketChart'

const props = defineProps<{ active: boolean }>()
const store = useDashboardStore(), { theme } = useTheme()
const instruments = computed(() => chartInstruments(store.positions, store.pendingOrders, store.data?.factors))
const instrument = ref('BTC-USDT-SWAP'), period = ref<ChartPeriod>('1H')
const bars = shallowRef<ChartBar[]>([]), canvas = ref<HTMLDivElement | null>(null)
const delayed = ref(true), hasGaps = ref(false), manualBusy = ref(false), chartReady = ref(false)
const indicatorDialog = ref(false), showLines = ref(true)
const enabled = ref<string[]>(['VWAP', 'VOL'])
const width = ref(800), precision = ref(2), clock = ref(Date.now()), loadedCount = ref(0)
const serverAt = ref(0), receivedAt = ref(0)
const last = computed(() => bars.value.at(-1))
const chartHeight = computed(() => (width.value < 600 ? 290 : 350) + enabled.value.filter(id => CHART_INDICATORS.find(i => i.id === id)?.main === false).length * 100 + 30)
const countdown = computed(() => candleCountdown(last.value?.timestamp, period.value, serverAt.value + clock.value - receivedAt.value))
const lines = computed(() => !showLines.value || store.isStale ? [] : chartPriceLines(instrument.value, store.positions, store.pendingOrders))
const labels = computed(() => enabled.value.join(' · ') || '纯 K 线')
function formatPrice(value: number | undefined) { return value == null ? '--' : value.toFixed(precision.value) }
function token(name: string, fallback: string) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback }
function styles(): DeepPartial<Styles> {
  const text = token('--text-muted', '#586579'), border = token('--border-subtle', '#e4e7ee')
  const bg = token('--bg-card', '#ffffff'), up = token('--color-up', '#067647'), down = token('--color-down', '#c43249')
  return {
    grid: { horizontal: { color: border, style: 'dashed' }, vertical: { color: border, style: 'dashed' } },
    candle: {
      type: 'candle_solid',
      bar: { upColor: up, downColor: down, noChangeColor: text, upBorderColor: up, downBorderColor: down, noChangeBorderColor: text, upWickColor: up, downWickColor: down, noChangeWickColor: text },
      priceMark: { high: { show: false }, low: { show: false }, last: { upColor: up, downColor: down, noChangeColor: text } },
      tooltip: { showRule: 'follow_cross', showType: 'rect', title: { color: text }, legend: { color: text }, rect: { color: bg, borderColor: border, borderSize: 1 } },
    },
    indicator: { ohlc: { upColor: up, downColor: down, noChangeColor: text }, bars: [{ upColor: up, downColor: down, noChangeColor: text }],
      lines: [token('--color-brand', '#4658d7'), token('--color-warn', '#b54708'), up, down].map(color => ({ color, size: 1 })),
      tooltip: { showRule: 'follow_cross', title: { color: text }, legend: { color: text } } },
    xAxis: { axisLine: { color: border }, tickLine: { color: border }, tickText: { color: text, size: 11 } },
    yAxis: { axisLine: { color: border }, tickLine: { color: border }, tickText: { color: text, size: 11 } },
    separator: { color: border, activeBackgroundColor: token('--bg-card-hover', bg) },
    crosshair: { horizontal: { line: { color: text }, text: { color: token('--text-main', '#172033'), backgroundColor: bg, borderColor: border } },
      vertical: { line: { color: text }, text: { color: token('--text-main', '#172033'), backgroundColor: bg, borderColor: border } } },
  }
}

let library: typeof import('klinecharts') | null = null
let chart: Chart | null = null, stream: ((bar: KLineData) => void) | null = null
let generation = 0, mounted = false, disposed = false, lineSignature = ''
let request: { controller: AbortController; promise: Promise<void> } | null = null
let pollTimer: ReturnType<typeof setInterval> | undefined, clockTimer: ReturnType<typeof setInterval> | undefined
let observer: ResizeObserver | undefined, frame: number | undefined
function clearChart() {
  if (chart && canvas.value && library) library.dispose(canvas.value)
  chart = null; stream = null; lineSignature = ''; chartReady.value = false; loadedCount.value = 0
}
function syncIndicators() {
  if (!chart) return
  for (const item of CHART_INDICATORS) {
    const id = `okxquant-${item.id}`, existing = chart.getIndicators({ id })
    if (enabled.value.includes(item.id) && !existing.length) {
      chart.createIndicator({ id, name: item.id, ...(item.main ? { paneId: 'candle_pane' } : {}), ...(item.params.length ? { calcParams: item.params } : {}), ...(item.main ? { precision: precision.value } : {}) }, item.main)
      if (!item.main) {
        const pane = chart.getIndicators({ id })[0]?.paneId
        if (pane) chart.setPaneOptions({ id: pane, height: 100, minHeight: 65 })
      }
    } else if (!enabled.value.includes(item.id) && existing.length) chart.removeIndicator({ id })
  }
  void nextTick(() => chart?.resize())
}
function updateLines(force = false) {
  if (!chart || !bars.value.length) return
  const signature = JSON.stringify(lines.value)
  if (!force && signature === lineSignature) return
  chart.removeOverlay({ groupId: 'okxquant-readonly' })
  for (const line of lines.value) {
    const color = token(`--color-${line.tone}`, '#586579')
    chart.createOverlay({ name: 'priceLine', id: `okxquant-line-${line.id}`, groupId: 'okxquant-readonly', paneId: 'candle_pane', lock: true,
      needDefaultPointFigure: false, points: [{ value: line.price }], extendData: line.label,
      styles: { line: { style: 'dashed', dashedValue: [4, 4], color, size: 1 }, text: { color, backgroundColor: token('--bg-card', '#ffffff'), size: 11 } } })
  }
  lineSignature = signature
}
function initializeChart(snapshot: CandleSnapshot) {
  if (!library || !canvas.value) return
  clearChart()
  // Avoid leaking an instance on window. All updates use public SDK callbacks.
  chart = library.init(canvas.value, { locale: 'zh-CN', timezone: 'Asia/Shanghai', styles: styles() })
  if (!chart) throw new Error('Chart initialization failed')
  const owner = chart
  chart.setSymbol({ ticker: instrument.value, pricePrecision: snapshot.precision, volumePrecision: 2 })
  chart.setPeriod(CHART_PERIODS.find(p => p.id === period.value)!.period)
  chart.setDataLoader({
    getBars: ({ type, callback }) => callback(type === 'init' ? bars.value : [], false),
    subscribeBar: ({ callback }) => { if (chart === owner) stream = callback },
    unsubscribeBar: () => { if (chart === owner) stream = null },
  })
  chart.setOffsetRightDistance(24)
  chart.setBarSpace(Math.max(4, Math.min(12, (canvas.value.clientWidth - 80) / Math.min(150, bars.value.length))))
  syncIndicators(); updateLines(true)
  chart.scrollToRealTime(); chartReady.value = true; loadedCount.value = chart.getDataList().length
}
async function refresh() {
  if (!mounted || disposed || !props.active || document.visibilityState === 'hidden') return
  if (request) return request.promise
  const epoch = generation, inst = instrument.value, selectedPeriod = period.value
  const controller = new AbortController()
  let timeout: ReturnType<typeof setTimeout>
  const expired = new Promise<never>((_, reject) => {
    timeout = setTimeout(() => { controller.abort(); reject(new Error('Chart read timed out')) }, 8000)
  })
  const pending = { controller, promise: Promise.resolve() }
  pending.promise = Promise.resolve().then(async () => {
    try {
      if (epoch !== generation || disposed || !props.active) return
      const [response, sdk] = await Promise.race([Promise.all([
        fetch(`/api/v1/market/${encodeURIComponent(inst)}/candles?bar=${selectedPeriod}&limit=150`, { signal: controller.signal, cache: 'no-store' }),
        library ? Promise.resolve(library) : import('klinecharts'),
      ]), expired])
      if (!response.ok) throw new Error(`Candle HTTP ${response.status}`)
      const snapshot = parseCandleSnapshot(await Promise.race([response.json(), expired]), inst, selectedPeriod)
      if (epoch !== generation || disposed || !props.active) return
      if (!library) {
        library = sdk
        library.registerIndicator({ name: 'VWAP', shortName: 'VWAP', series: 'price', figures: [{ key: 'vwap', title: 'VWAP: ', type: 'line' }], calc: chartVwap })
      }
      const tail = chart?.getDataList().at(-1)?.timestamp
      if (tail !== undefined && snapshot.bars.at(-1)!.timestamp < tail) throw new Error('Candle clock regressed')
      bars.value = snapshot.bars; precision.value = snapshot.precision
      serverAt.value = snapshot.asOf; receivedAt.value = Date.now(); clock.value = receivedAt.value
      delayed.value = snapshot.stale; hasGaps.value = snapshot.hasGaps
      if (!chart || !stream || tail === undefined || tail < snapshot.bars[0]!.timestamp) initializeChart(snapshot)
      else {
        // Finalize the previous candle before adding the new one at a boundary.
        // Never reset the viewport on normal polling, zooming or hovering.
        for (const bar of snapshot.bars) if (bar.timestamp >= tail) stream(bar)
        loadedCount.value = chart.getDataList().length
        updateLines()
      }
    } catch {
      if (epoch === generation && !disposed && props.active) delayed.value = true
    } finally {
      clearTimeout(timeout!)
      if (request === pending) request = null
    }
  })
  request = pending
  return pending.promise
}
function suspend() {
  generation += 1
  request?.controller.abort(); request = null
  if (pollTimer) clearInterval(pollTimer)
  if (clockTimer) clearInterval(clockTimer)
  pollTimer = undefined; clockTimer = undefined
}
function resume() {
  if (!mounted || disposed || !props.active || document.visibilityState === 'hidden') return
  suspend(); void refresh()
  pollTimer = setInterval(() => { void refresh() }, 3000)
  clockTimer = setInterval(() => { clock.value = Date.now() }, 1000)
  void nextTick(() => chart?.resize())
}
function zoom(scale: number) { chart?.zoomAtCoordinate(scale) }
function resetView() { chart?.scrollToRealTime() }
function visibilityChanged() { if (document.visibilityState === 'hidden') suspend(); else resume() }
async function manualRefresh() { manualBusy.value = true; try { await refresh() } finally { manualBusy.value = false } }
function toggleIndicator(id: string) { enabled.value = enabled.value.includes(id) ? enabled.value.filter(value => value !== id) : [...enabled.value, id] }
watch(instruments, ids => { if (!ids.includes(instrument.value)) instrument.value = ids[0]! })
watch([instrument, period], () => { suspend(); clearChart(); bars.value = []; delayed.value = true; hasGaps.value = false; resume() })
watch(() => props.active, value => { if (value) resume(); else suspend() })
watch(theme, () => { chart?.setStyles(styles()); updateLines(true) }, { flush: 'post' })
watch(enabled, syncIndicators)
watch(lines, () => updateLines())
onMounted(() => {
  mounted = true
  document.addEventListener('visibilitychange', visibilityChanged)
  if (canvas.value) {
    observer = new ResizeObserver(entries => {
      const measured = entries[0]?.contentRect.width || 0
      if (measured <= 0) return
      width.value = Math.round(measured)
      if (frame !== undefined) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => { if (props.active) chart?.resize() })
    })
    observer.observe(canvas.value)
  }
  resume()
})
onUnmounted(() => {
  disposed = true; suspend(); observer?.disconnect()
  if (frame !== undefined) cancelAnimationFrame(frame)
  document.removeEventListener('visibilitychange', visibilityChanged)
  clearChart()
})
</script>

<template>
  <AppCard class="market-chart min-w-0 overflow-hidden" data-market-chart>
    <header class="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b" style="border-color:var(--border-subtle)">
      <div class="flex items-center gap-2 min-w-0"><ChartCandlestick class="size-4 shrink-0" style="color:var(--color-brand)" /><h2 class="text-sm font-semibold">行情 K 线</h2><span class="text-xs" style="color:var(--text-muted)">OKX 公共行情 · 只读</span></div>
      <AppBadge data-chart-status :tone="delayed ? 'warning' : 'success'" dot>{{ delayed ? '数据更新延迟' : '数据已更新' }}</AppBadge>
    </header>
    <div class="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div class="flex flex-wrap items-center gap-3 min-w-0">
        <label class="sr-only" for="market-chart-instrument">K线标的</label>
        <select id="market-chart-instrument" v-model="instrument" class="ui-input market-chart__select" aria-label="K线标的">
          <option v-for="id in instruments" :key="id" :value="id">{{ id.replace('-USDT-SWAP', '') }} / USDT 永续</option>
        </select>
        <div class="min-w-0"><div class="text-base font-semibold num-tabular" data-chart-price>{{ formatPrice(last?.close) }}</div><div class="text-[11px]" style="color:var(--text-muted)">本根剩余 {{ countdown }} · {{ last ? last.confirmed ? '已收盘' : '未收盘' : '--' }}</div></div>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex items-center gap-1 rounded-lg border p-0.5" role="group" aria-label="K线周期" style="border-color:var(--border-subtle);background:var(--bg-card-subtle)">
          <AppButton v-for="item in CHART_PERIODS" :key="item.id" size="sm" class="market-chart__period" :variant="period === item.id ? 'primary' : 'ghost'" :aria-pressed="period === item.id" @click="period = item.id as ChartPeriod">{{ item.label }}</AppButton>
        </div>
        <AppButton size="sm" class="market-chart__control" @click="indicatorDialog = true"><SlidersHorizontal class="size-4" aria-hidden="true" />指标</AppButton>
        <AppButton variant="ghost" size="sm" class="market-chart__control" :loading="manualBusy" aria-label="刷新K线" @click="manualRefresh"><RefreshCw v-if="!manualBusy" class="size-4" /></AppButton>
      </div>
    </div>
    <div class="flex flex-wrap items-center justify-between gap-2 px-4 pb-2 text-[11px]" style="color:var(--text-muted)">
      <span>{{ labels }}<span v-if="hasGaps"> · 行情存在缺口</span></span>
      <div class="flex items-center gap-1">
        <AppButton variant="ghost" size="sm" class="market-chart__control" aria-label="放大K线" @click="zoom(1.2)"><Plus class="size-3.5" /></AppButton>
        <AppButton variant="ghost" size="sm" class="market-chart__control" aria-label="缩小K线" @click="zoom(0.8)"><Minus class="size-3.5" /></AppButton>
        <AppButton variant="ghost" size="sm" class="market-chart__control" aria-label="K线回到最新" @click="resetView"><RotateCcw class="size-3.5" /></AppButton>
      </div>
    </div>
    <div class="relative min-w-0 overflow-hidden" :style="{ height: chartHeight + 'px' }">
      <div ref="canvas" class="h-full w-full min-w-0" role="img" :aria-label="instrument + ' ' + period + ' K线与技术指标'" :data-chart-ready="chartReady" :data-chart-bars="loadedCount" :data-chart-symbol="instrument" :data-chart-period="period" />
      <div v-if="!chartReady" class="absolute inset-0 flex items-center justify-center px-5 text-center text-sm pointer-events-none" style="color:var(--text-muted)">暂未取得可用 K 线，自动重试中</div>
    </div>
    <footer class="px-4 py-3 border-t space-y-2 text-[11px]" style="border-color:var(--border-subtle);color:var(--text-muted)">
      <div v-if="lines.length" class="flex flex-wrap gap-x-4 gap-y-1"><span v-for="line in lines" :key="line.id" class="num-tabular">{{ line.label }} {{ formatPrice(line.price) }}</span></div>
      <div class="flex flex-wrap justify-between gap-2"><span>已载入 {{ loadedCount || '--' }} 根 · 3秒刷新 · 北京时间 · 成交量单位：张</span><span>滚轮 / 双指缩放，拖动查看；不支持在图表下单</span></div>
    </footer>
    <AppDialog v-model:open="indicatorDialog" title="K线指标设置" description="只改变图表显示，不修改策略、仓位、止盈止损或任何订单。">
      <div class="space-y-4">
        <fieldset v-for="main in [true, false]" :key="String(main)" class="space-y-2"><legend class="text-sm font-semibold">{{ main ? '主图叠加' : '独立副图' }}</legend>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2"><label v-for="item in CHART_INDICATORS.filter(i => i.main === main)" :key="item.id" class="flex items-center gap-2 min-h-11 rounded-lg border px-3 text-sm cursor-pointer" style="border-color:var(--border-subtle)"><input type="checkbox" :checked="enabled.includes(item.id)" @change="toggleIndicator(item.id)">{{ item.label }}</label></div>
        </fieldset>
        <label class="flex items-center gap-2 min-h-11 text-sm"><input v-model="showLines" type="checkbox">显示已有持仓 / 挂单参考线（不可拖动）</label>
        <p class="text-xs leading-relaxed" style="color:var(--text-muted)">指标仅使用已载入的 K 线计算，不是交易建议。VWAP 使用 UTC 日内典型价加权，窗口起始日可能不完整；没有成交量时不补造均价。账户快照延迟时隐藏参考线。</p>
      </div>
      <template #footer><AppButton @click="indicatorDialog = false">完成</AppButton></template>
    </AppDialog>
  </AppCard>
</template>

<style scoped>
.market-chart { color: var(--text-main); background: var(--bg-card); }
.market-chart__select { min-height: 44px; max-width: 100%; width: 11rem; }
.market-chart__period { min-width: 44px; min-height: 44px; padding-inline: 9px; }
.market-chart__control { min-width: 44px; min-height: 44px; }
</style>
