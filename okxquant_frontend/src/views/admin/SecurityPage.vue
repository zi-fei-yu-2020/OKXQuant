<script setup lang="ts">
import AppField from '../../components/ui/AppField.vue'
import AppCard from '../../components/ui/AppCard.vue'
import AppButton from '../../components/ui/AppButton.vue'
import InstrumentSupportNotice from '../../components/InstrumentSupportNotice.vue'
import type { InstrumentSupportSummary } from '../../types/dashboard'
import LoadingState from '../../components/ui/LoadingState.vue'

import AppDialog from '../../components/ui/AppDialog.vue'

import { useFeedback } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { Wallet, Save, KeyRound, RefreshCw, Layers, Trash2 } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const config = ref<any>(null)
const loading = ref(true)
const bannerMsg = useFeedback()

// ---- capital ----
const newCapital = ref<string>('')
const capitalConfirm = ref<string>('')
const savingCapital = ref(false)

// ---- instruments ----
const instruments = ref<any[]>([])
const instLimits = ref<any>({ minimum: 1, maximum: 6 })
const newInstId = ref('')
const appliedEnvironment = ref<'demo' | 'live'>('demo')
const supportSummary = ref<InstrumentSupportSummary | null>(null)
const supportLoading = ref(false)
let supportGeneration = 0
async function refreshInstrumentSupport() {
  const generation = ++supportGeneration
  const environment = appliedEnvironment.value
  supportLoading.value = true
  try {
    const summary = await api<InstrumentSupportSummary>(`/api/v1/admin/instruments/support?environment=${environment}`)
    if (generation !== supportGeneration || environment !== appliedEnvironment.value) return
    supportSummary.value = summary
    instruments.value = instruments.value.map(item => ({ ...item, environment_support: summary.items[item.instId] }))
  } catch (e: any) {
    if (e?.silent) return
    if (generation === supportGeneration) bannerMsg.value = { text: `合约支持状态核验失败：${e.message}`, type: 'warn' }
  } finally { if (generation === supportGeneration) supportLoading.value = false }
}


// ---- positions & close ----
const snapshot = ref<any>(null)
const snapshotState = ref('')
const manualClose = ref(false)
const closePassword = ref('')
const closeModal = ref<{ show: boolean; pos: any } | null>(null)
const closePhraseInput = ref('')
const closing = ref(false)

async function loadAll() {
  loading.value = true
  try {
    const [cfg, inst] = await Promise.all([
      api('/api/v1/admin/config'),
      api('/api/v1/admin/instruments'),
    ])
    config.value = cfg
    newCapital.value = String(cfg.editable?.initial_capital ?? '')
    manualClose.value = !!cfg.editable?.manual_close_enabled
    instruments.value = inst.instruments || []
    instLimits.value = inst.limits || instLimits.value
    appliedEnvironment.value = inst.environment || cfg.editable.okx_environment
    supportSummary.value = inst.support_summary || null
    void refreshInstrumentSupport()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

async function saveCapital() {
  if (!auth.isSuperadmin) {
    bannerMsg.value = { text: '仅超级管理员可修改初始本金', type: 'err' }
    return
  }
  if (capitalConfirm.value.trim().toUpperCase() !== 'UPDATE CAPITAL') {
    bannerMsg.value = { text: '确认短语必须精确为：UPDATE CAPITAL', type: 'err' }
    return
  }
  savingCapital.value = true
  try {
    const res = await api('/api/v1/admin/account-baseline', {
      method: 'PUT',
      body: JSON.stringify({
        initial_capital: parseFloat(newCapital.value),
        confirmation: capitalConfirm.value,
      }),
    })
    bannerMsg.value = {
      text: res.effect || `初始本金已调整为 ${res.initial_capital} USDT`,
      type: 'ok',
    }
    capitalConfirm.value = ''
    await loadAll()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `更新失败：${e.message}`, type: 'err' }
  } finally {
    savingCapital.value = false
  }
}

async function addInstrument() {
  const instId = newInstId.value.trim().toUpperCase()
  if (!/^[A-Z0-9]{2,15}-USDT-SWAP$/.test(instId)) {
    bannerMsg.value = { text: '格式示例：XRP-USDT-SWAP（仅 USDT 永续）', type: 'err' }
    return
  }
  try {
    const res = await api('/api/v1/admin/instruments', {
      method: 'POST',
      body: JSON.stringify({ inst_id: instId }),
    })
    bannerMsg.value = {
      text: res.message || `${instId} 已成功加入交易池并实时同步全网大屏与因果雷达`,
      type: 'ok',
    }
    newInstId.value = ''
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
    void refreshInstrumentSupport()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `添加失败：${e.message}`, type: 'err' }
  }
}

async function removeInstrument(item: any) {
  if (item.protected) {
    bannerMsg.value = { text: 'BTC 为保底标的，不可删除', type: 'err' }
    return
  }
  if (item.has_tracker) {
    bannerMsg.value = { text: `${item.name} 存在持仓追踪器，禁止移除`, type: 'err' }
    return
  }
  const phrase = await prompt(`删除交易池标的 ${item.instId}\n输入确认短语：REMOVE ${item.instId}`)
  if (!phrase) return
  try {
    const res = await api(`/api/v1/admin/instruments/${encodeURIComponent(item.instId)}`, {
      method: 'DELETE',
      body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }),
    })
    bannerMsg.value = {
      text: res.message || `${item.instId} 已从交易池移除并实时同步全网大屏与因果雷达`,
      type: 'ok',
    }
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
    void refreshInstrumentSupport()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `删除失败：${e.message}`, type: 'err' }
  }
}

async function loadPositions() {
  snapshotState.value = '正在从 OKX 读取当前持仓与挂单…'
  try {
    const [d,cfg] = await Promise.all([api('/api/v1/admin/okx/account-snapshot'),api('/api/v1/admin/config')])
    manualClose.value=!!cfg.editable?.manual_close_enabled
    snapshot.value = d
    snapshotState.value = ''
  } catch (e: any) {
    if (e?.silent) return
    snapshotState.value = e.message
    snapshot.value = null
  }
}

function openClose(pos: any) {
  if (!manualClose.value) {
    bannerMsg.value = { text: '请先在账户中心启用后台手动平仓并保存开关', type: 'err' }
    return
  }
  closePhraseInput.value = ''
  closeModal.value = { show: true, pos }
}

async function confirmClose() {
  const pos = closeModal.value?.pos
  if (!pos) return
  if (!closePassword.value) {
    bannerMsg.value = { text: '请输入当前管理员密码', type: 'err' }
    return
  }
  if (!pos.close_token || !pos.close_confirmation) {
    bannerMsg.value = { text: '平仓令牌缺失，请刷新当前持仓', type: 'err' }
    return
  }
  if (closePhraseInput.value.trim().toUpperCase() !== pos.close_confirmation) {
    bannerMsg.value = { text: `确认短语必须精确为：${pos.close_confirmation}`, type: 'err' }
    return
  }
  closing.value = true
  try {
    const d = await api('/api/v1/admin/positions/close', {
      method: 'POST',
      body: JSON.stringify({
        close_token: pos.close_token,
        admin_password: closePassword.value,
        confirmation: closePhraseInput.value.trim().toUpperCase(),
      }),
    })
    bannerMsg.value = { text: `✅ 已确认平仓：${d.instId} ${d.closed_size}`, type: 'ok' }
    closeModal.value = null
    closePassword.value = ''
    await loadPositions()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `平仓失败：${e.message}`, type: 'err' }
  } finally {
    closing.value = false
  }
}

onMounted(loadAll)

const { prompt } = useDialogs()
</script>

<template>
  <div class="space-y-4 font-sans text-sm">
    <AppCard class="p-4 text-sm leading-relaxed"><router-link to="/admin/accounts" class="font-semibold" style="color:var(--color-brand)">前往统一账户中心</router-link><p style="color:var(--text-muted)">新增资讯授权、双环境绑定和安全换号请使用账户中心；此页只保留盈亏基准、标的管理和受保护的平仓操作；账户、凭据及手动平仓权限统一在账户中心配置。</p></AppCard>
    <!-- Header & Action Bar -->

    <LoadingState v-if="loading" />

    <template v-else-if="config">
      <!-- 2. initial capital -->
      <AppCard
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center space-x-2 mb-4 pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <Wallet class="w-4 h-4 text-emerald-500" />
          <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">
            1. 主页盈亏基准 · 初始本金
          </h2>
        </div>
        <div class="text-sm font-sans space-y-1.5 mb-4" style="color: var(--text-muted)">
          <p v-if="config.editable.baseline_configured === false" class="mb-2" role="status">
            尚未确认本金基准。下方为建议填写值，不会把模拟盘初始资金计为累计收益。
          </p>
          <div>
            {{ config.editable.baseline_configured === false ? '待确认本金' : '当前基准本金' }}:
            <strong class="text-emerald-500 text-sm num-tabular"
              >{{ config.editable.initial_capital }} USDT</strong
            >
          </div>
          <div>
            历史起算时间:
            <span style="color: var(--text-faint)">{{
              config.editable.initial_capital_reset_time
            }}</span
            >（修改本金不改变起算时间）
          </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                  >新初始本金 (USDT)</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="newCapital"
                  type="number"
                  step="0.01"
                  class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border num-tabular"
                  style="
                    background-color: var(--bg-input);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                  >确认短语 (UPDATE CAPITAL)</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="capitalConfirm"
                  placeholder="输入 UPDATE CAPITAL"
                  class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border"
                  style="
                    background-color: var(--bg-input);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>
          <div class="flex items-end">
            <button
              @click="saveCapital"
              :disabled="savingCapital"
              class="w-full flex items-center justify-center space-x-1.5 px-4 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-800 text-white text-sm font-sans font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs"
            >
              <Save class="w-3.5 h-3.5" /><span>{{
                savingCapital ? '更新中...' : '更新基准本金'
              }}</span>
            </button>
          </div>
        </div>
      </AppCard>

      <!-- 3. instruments -->
      <AppCard
        class="rounded-xl border overflow-hidden shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="px-4 py-3 border-b flex flex-wrap gap-3 items-center justify-between"
          style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
        >
          <div class="flex items-center space-x-2">
            <Layers class="w-4 h-4" style="color: var(--color-brand)" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              2. 交易标的池 ({{ instruments.length }}/{{ instLimits.maximum }})
            </h2>
          </div>
          <div class="flex gap-2">
            <input
              aria-label="新增交易标的"
              v-model="newInstId"
              placeholder="例如: XRP-USDT-SWAP"
              class="w-44 rounded-lg px-2.5 py-1.5 text-sm font-sans outline-none border transition-colors"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
              @keyup.enter="addInstrument"
            />
            <button
              @click="addInstrument"
              class="px-3 py-1.5 rounded-lg text-sm font-sans font-bold transition-all cursor-pointer shadow-xs"
              style="background-color: var(--text-main); color: var(--bg-card)"
            >
              添加标的
            </button>
          </div>
        </div>
        <div class="px-4 py-3 flex flex-wrap items-center justify-between gap-3 border-b" style="border-color: var(--border-subtle)">
          <div class="text-sm leading-relaxed" style="color: var(--text-muted)" aria-live="polite">
            <strong style="color: var(--text-main)">当前生效：{{ appliedEnvironment === 'demo' ? '模拟盘' : '实盘' }}</strong>
            <span v-if="supportLoading"> · 正在核验合约目录…</span>
            <span v-else-if="supportSummary"> · {{ supportSummary.supported_count }} 个已核验支持 · {{ supportSummary.observation_count }} 个仅观察/待确认</span>
            <p class="text-xs mt-1">能看到行情不代表当前环境支持交易。不支持或待确认的标的不参与新开仓/加仓，当前环境已有持仓管理不受此检查阻断。</p>
          </div>
          <AppButton :loading="supportLoading" title="刷新支持状态；60 秒内的合约目录结果可复用" @click="refreshInstrumentSupport">重新核验</AppButton>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-sm font-sans whitespace-nowrap">
            <thead>
              <tr
                class="border-b text-xs uppercase tracking-wider font-bold"
                style="
                  border-color: var(--border-subtle);
                  background-color: var(--bg-card-subtle);
                  color: var(--text-muted);
                "
              >
                <th class="py-2.5 px-4">合约代码</th>
                <th class="py-2.5 px-3">名称</th>
                <th class="py-2.5 px-3">类型</th>
                <th class="py-2.5 px-3">当前环境支持情况</th>
                <th class="py-2.5 px-3">风控状态</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in instruments"
                :key="item.instId"
                class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                style="border-color: var(--border-subtle)"
              >
                <td class="py-2.5 px-4 font-bold" style="color: var(--text-main)">
                  {{ item.instId }}
                </td>
                <td class="py-2.5 px-3" style="color: var(--text-muted)">{{ item.name }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint)">
                  {{ item.ctType || 'SWAP' }}
                </td>
                <td class="py-2.5 px-3 min-w-56 max-w-80 whitespace-normal"><InstrumentSupportNotice :support="item.environment_support" compact /></td>
                <td class="py-2.5 px-3">
                  <span
                    v-if="item.protected"
                    class="px-1.5 py-0.2 rounded text-xs font-bold border"
                    style="
                      background-color: var(--color-warn-bg);
                      border-color: var(--color-warn-border);
                      color: var(--color-warn);
                    "
                    >🔒 保底必选</span
                  >
                  <span
                    v-else-if="item.has_tracker"
                    class="px-1.5 py-0.2 rounded text-xs font-bold border"
                    style="
                      background-color: var(--color-brand-bg);
                      border-color: var(--color-brand-border);
                      color: var(--color-brand);
                    "
                    >持仓中</span
                  >
                  <span v-else class="text-xs" style="color: var(--text-faint)">可移除</span>
                </td>
                <td class="py-2.5 px-4 text-right">
                  <button
                    @click="removeInstrument(item)"
                    :disabled="item.protected || item.has_tracker"
                    class="p-1 rounded hover:opacity-80 text-rose-400 disabled:opacity-20 cursor-pointer transition-opacity"
                    title="从标的池移除"
                  >
                    <Trash2 class="w-3.5 h-3.5" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p
          class="px-4 py-2 border-t text-xs font-sans"
          style="border-color: var(--border-subtle); color: var(--text-faint)"
        >
          BTC 为系统保底标的不可删除；有在途追踪器的标的禁止移除；最多 {{ instLimits.maximum }} 个。
        </p>
      </AppCard>

      <!-- 4. positions & emergency close -->
      <AppCard
        class="rounded-xl border overflow-hidden shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="px-4 py-3 border-b flex items-center justify-between"
          style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
        >
          <div class="flex items-center space-x-2">
            <KeyRound class="w-4 h-4 text-rose-500" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              3. 当前持仓与应急平仓
            </h2>
          </div>
          <button
            @click="loadPositions"
            class="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border text-sm font-sans cursor-pointer transition-all shadow-xs"
            style="
              background-color: var(--bg-card);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <RefreshCw class="w-3.5 h-3.5" />
            <span>刷新持仓与挂单</span>
          </button>
        </div>
        <div v-if="snapshotState" class="px-4 pt-2 text-xs font-sans text-amber-500">
          {{ snapshotState }}
        </div>
        <div v-if="snapshot" class="px-4 pt-2 text-xs font-sans" style="color: var(--text-muted)">
          环境：<strong
            :class="snapshot.environment === 'live' ? 'text-rose-500' : 'text-emerald-500'"
            >{{ (snapshot.environment || '').toUpperCase() }}</strong
          >
          · 持仓 {{ snapshot.positions?.length ?? 0 }} · 当前挂单
          {{ snapshot.orders?.length ?? 0 }} ·
          {{ new Date(snapshot.captured_at_ms).toLocaleString() }}
        </div>
        <div class="overflow-x-auto mt-2">
          <table
            v-if="snapshot?.positions?.length"
            class="w-full text-left text-sm font-sans whitespace-nowrap"
          >
            <thead>
              <tr
                class="border-b text-xs uppercase tracking-wider font-bold"
                style="
                  border-color: var(--border-subtle);
                  background-color: var(--bg-card-subtle);
                  color: var(--text-muted);
                "
              >
                <th class="py-2.5 px-4">仓位标的</th>
                <th class="py-2.5 px-3">张数</th>
                <th class="py-2.5 px-3">模式</th>
                <th class="py-2.5 px-3">未实现盈亏</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="p in snapshot.positions"
                :key="p.instId + p.posSide"
                class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                style="border-color: var(--border-subtle)"
              >
                <td class="py-2.5 px-4">
                  <strong style="color: var(--text-main)">{{ p.instId }}</strong>
                  <span
                    class="ml-1.5 px-1.5 py-0.2 rounded text-xs font-bold border"
                    :style="
                      p.posSide === 'long'
                        ? {
                            backgroundColor: 'var(--color-up-bg)',
                            borderColor: 'var(--color-up-border)',
                            color: 'var(--color-up)',
                          }
                        : {
                            backgroundColor: 'var(--color-down-bg)',
                            borderColor: 'var(--color-down-border)',
                            color: 'var(--color-down)',
                          }
                    "
                  >
                    {{ (p.posSide || 'net').toUpperCase() }}
                  </span>
                </td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-muted)">
                  {{ p.pos || '0' }}
                </td>
                <td class="py-2.5 px-3 text-xs" style="color: var(--text-faint)">
                  {{ p.mgnMode || '--' }}
                </td>
                <td
                  class="py-2.5 px-3 font-bold num-tabular"
                  :class="Number(p.upl || 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'"
                >
                  {{ Number(p.upl || 0).toFixed(4) }}
                </td>
                <td class="py-2.5 px-4 text-right">
                  <button
                    @click="openClose(p)"
                    class="px-2.5 py-1 rounded-md text-xs font-sans font-bold border transition-all cursor-pointer shadow-xs"
                    style="
                      background-color: var(--color-down-bg);
                      border-color: var(--color-down-border);
                      color: var(--color-down);
                    "
                  >
                    快速平仓
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else-if="snapshot" class="py-6 text-center text-sm font-sans text-emerald-500">
            ✓ 当前环境 0 活跃持仓
          </div>
          <div v-else class="py-6 text-center text-sm font-sans" style="color: var(--text-faint)">
            点击"刷新持仓与挂单"从 OKX 读取最新实时状态
          </div>
        </div>
        <p
          class="px-4 py-2 border-t text-xs font-sans"
          style="border-color: var(--border-subtle); color: var(--text-faint)"
        >
          平仓流程：复核环境与仓位 → 撤销同标的冲突委托 → autoCxl 市价平仓 →
          轮询确认仓位归零。需先启用上方手动平仓开关。
        </p>
      </AppCard>
    </template>

    <!-- Close confirm modal -->
    <AppDialog
      v-if="closeModal?.show"
      :open="!!closeModal?.show"
      title="确认平仓"
      size="md"
      @update:open="
        (open) => {
          if (!open) {
            closeModal = null
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <h3 class="text-sm font-bold text-rose-500 mb-2 font-sans">快速安全平仓</h3>
        <p class="text-xs font-sans leading-relaxed mb-3" style="color: var(--text-muted)">
          将从 {{ (snapshot?.environment || 'demo').toUpperCase() }} 环境重新核对并平掉
          <strong style="color: var(--text-main)"
            >{{ closeModal.pos.instId }} {{ (closeModal.pos.posSide || 'net').toUpperCase() }}
            {{ Math.abs(Number(closeModal.pos.pos || 0)) }}</strong
          >。 令牌 90 秒有效且仅可使用一次。
        </p>
        <AppField class="w-full min-w-0"
          ><template #label
            ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
              >当前管理员密码</span
            ></template
          ><template #default="{ id: fieldId }"
            ><input
              :id="fieldId"
              v-model="closePassword"
              type="password"
              class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border mb-3"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              " /></template
        ></AppField>
        <AppField class="w-full min-w-0"
          ><template #label
            ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
              >确认短语：{{ closeModal.pos.close_confirmation }}</span
            ></template
          ><template #default="{ id: fieldId }"
            ><input
              :id="fieldId"
              v-model="closePhraseInput"
              :placeholder="closeModal.pos.close_confirmation"
              class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border mb-4"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              " /></template
        ></AppField>
        <div class="flex justify-end gap-2">
          <button
            @click="closeModal = null"
            class="px-3 py-2 rounded-lg border text-sm font-sans cursor-pointer transition-all shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            取消
          </button>
          <button
            @click="confirmClose"
            :disabled="closing"
            class="px-3 py-2 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs"
            style="
              background-color: var(--color-down-bg);
              border-color: var(--color-down-border);
              color: var(--color-down);
            "
          >
            {{ closing ? '执行中，等待成交确认…' : '确认平仓' }}
          </button>
        </div>
      </div></AppDialog
    >
  </div>
</template>
