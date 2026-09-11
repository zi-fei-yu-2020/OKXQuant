<script setup lang="ts">
import AppCard from '../../components/ui/AppCard.vue'

import { useFeedback, useToast } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  Users,
  Shield,
  Zap,
  Cpu,
  Save,
  RotateCcw,
  Play,
  Plus,
  Trash2,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ToggleLeft,
  ToggleRight,
  Sliders,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const bannerMsg = useFeedback()

const councilConfig = ref<any>({
  enabled: false,
  consensus_mode: 'weighted',
  timeout_seconds: 60.0,
  roles: {},
})

const availableSuites = ref<any[]>([])
const availableModels = ref<any[]>([])
const expandedRole = ref<string>('trader_trend')
const testResult = ref<any>(null)
const expandedReasoning = ref<Record<string, boolean>>({})

const consensusModes = [
  {
    id: 'strict',
    name: '一票否决制',
    tag: '稳健首选',
    desc: '任一交易员提出严重假突破、逆势或流动性陷阱警告，强制观望。',
  },
  {
    id: 'weighted',
    name: '加权共识制',
    tag: '对冲基金标准',
    desc: '根据交易员历史绩效权重综合裁决，两组以上提案共振时优先立项。',
  },
  {
    id: 'aggressive',
    name: '动能进取制',
    tag: '突破进攻',
    desc: '微积分动能一阶速度与加速度爆发时，允许由动能交易员主导小仓试探。',
  },
]

const roleIcons: Record<string, any> = {
  trader_trend: Shield,
  trader_momentum: Zap,
  trader_quant: Cpu,
  cio: Users,
  custom: Sliders,
}

const roleColors: Record<string, string> = {
  trader_trend: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
  trader_momentum: 'text-amber-400 border-amber-500/30 bg-amber-500/10',
  trader_quant: 'text-cyan-400 border-cyan-500/30 bg-cyan-500/10',
  cio: 'text-purple-400 border-purple-500/30 bg-purple-500/10',
  custom: 'text-blue-400 border-blue-500/30 bg-blue-500/10',
}

async function loadData() {
  loading.value = true
  try {
    const [cRes, mRes] = await Promise.all([
      api('/api/v1/admin/council/config'),
      api('/api/v1/admin/llm/models'),
    ])
    councilConfig.value = cRes
    availableSuites.value = cRes.available_suites || []
    availableModels.value = mRes.models || []
    const roleKeys = Object.keys(cRes.roles || {})
    if (roleKeys.length > 0 && !roleKeys.includes(expandedRole.value)) {
      expandedRole.value = roleKeys[0]
    }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `加载配置失败: ${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (!auth.isSuperadmin) {
    bannerMsg.value = { text: '仅超级管理员可修改投委会配置', type: 'err' }
    return
  }
  saving.value = true
  try {
    const res = await api('/api/v1/admin/council/config', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: councilConfig.value.enabled,
        consensus_mode: councilConfig.value.consensus_mode || 'weighted',
        timeout_seconds: Number(councilConfig.value.timeout_seconds) || 60.0,
        roles: councilConfig.value.roles,
      }),
    })
    councilConfig.value = res.config
    bannerMsg.value = {
      text: councilConfig.value.enabled
        ? `✅ 对冲基金投委会配置已保存并生效（${consensusModes.find((m) => m.id === councilConfig.value.consensus_mode)?.name}）`
        : '✅ 投委会配置已保存（当前为单模型直连决策）',
      type: 'ok',
    }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `保存失败: ${e.message}`, type: 'err' }
  } finally {
    saving.value = false
  }
}

async function applySuite(suiteId: string) {
  if (!auth.isSuperadmin) return
  if (!(await confirm('确定载入对冲基金标准投委会套件吗？将恢复标准交易员阵容。'))) return
  try {
    const res = await api('/api/v1/admin/council/apply-suite', {
      method: 'POST',
      body: JSON.stringify({ suite_id: suiteId }),
    })
    councilConfig.value = res.config
    bannerMsg.value = { text: '🎉 已载入标准投委会阵容！', type: 'ok' }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `载入失败: ${e.message}`, type: 'err' }
  }
}

function addNewCustomTrader() {
  if (!auth.isSuperadmin) return
  const roleId = `trader_${Date.now().toString(36)}`
  councilConfig.value.roles[roleId] = {
    id: roleId,
    name: '自定义交易员',
    role_title: 'Custom Trader',
    description: '自主定制策略风格的交易员席位',
    prompt:
      '【角色：自定义资深交易员】\n' +
      '你作为对冲基金交易台的一线交易员，请核验账户可用资金、现有持仓与挂单，并对 6 大标的输出你的实战作战提案：\n' +
      '1. 现有持仓与挂单：逐一给出 HOLD/CLOSE_MARKET 或 CANCEL/KEEP 建议。\n' +
      '2. 作战提案：对 6 大币种逐一给出明确方向、限价、止损、止盈与拟用保证金。\n' +
      '3. 指出同行方案中的致命风险漏洞（50字内/币种）。',
    weight: 0.3,
    enabled: true,
    reasoning_effort: 'medium',
    temperature: 0.2,
    is_arbitrator: false,
    model_id: '',
  }
  expandedRole.value = roleId
  bannerMsg.value = { text: '已添加自定义交易员席位，可直接编辑提示词与参数', type: 'ok' }
}

async function removeRole(roleId: string) {
  if (!auth.isSuperadmin) return
  const role = councilConfig.value.roles[roleId]
  if (role?.is_arbitrator || roleId === 'cio') {
    toast.warning('首席投资官 (CIO) 负责终审收口与发单，不可删除！')
    return
  }
  if (!(await confirm(`确定移除交易员【${role?.name || roleId}】席位吗？`))) return
  delete councilConfig.value.roles[roleId]
  bannerMsg.value = { text: '已移除席位，点击右上角「保存配置」后生效', type: 'warn' }
}

async function resetRole(roleId: string) {
  if (
    !(await confirm(
      `确定将【${councilConfig.value.roles[roleId]?.name || roleId}】恢复出厂提示词吗？`,
    ))
  )
    return
  try {
    const res = await api('/api/v1/admin/council/reset-role', {
      method: 'POST',
      body: JSON.stringify({ role_id: roleId }),
    })
    councilConfig.value = res.config
    bannerMsg.value = { text: '已重置为出厂标准模板', type: 'ok' }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `重置失败: ${e.message}`, type: 'err' }
  }
}

async function runDebateTest() {
  testing.value = true
  testResult.value = null
  expandedReasoning.value = {}
  bannerMsg.value = {
    text: '投委会正在全息审阅资金与行情并组织交易员辩论（预计 10~25 秒）...',
    type: 'warn',
  }
  try {
    const res = await api('/api/v1/admin/council/test', {
      method: 'POST',
      body: JSON.stringify({}),
    })
    if (res.status === 'ok') {
      testResult.value = res
      bannerMsg.value = {
        text: `✅ 投委会辩论与 CIO 终审完成！耗时 ${res.transcript?.total_duration_ms || 0}ms`,
        type: 'ok',
      }
    } else {
      bannerMsg.value = { text: `测试失败: ${res.error || '未知错误'}`, type: 'err' }
    }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `测试出错: ${e.message}`, type: 'err' }
  } finally {
    testing.value = false
  }
}

onMounted(loadData)

const { confirm } = useDialogs()

const toast = useToast()
</script>

<template>
  <div class="space-y-4">
    <!-- Notice Banner -->

    <!-- 1. Top Control Station: Switch, Consensus Mode & Actions -->
    <div
      class="rounded-2xl border p-4 sm:p-5 shadow-xs space-y-4"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <!-- Header row -->
      <div
        class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b"
        style="border-color: var(--border-subtle)"
      >
        <div class="flex items-center space-x-3">
          <div
            class="p-2.5 rounded-xl border"
            style="
              background-color: var(--color-brand-bg);
              border-color: var(--color-brand-border);
              color: var(--color-brand);
            "
          >
            <Users class="w-5 h-5" />
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">
                对冲基金投委会决策中枢 (Trading Desk Council)
              </h2>
              <span
                class="text-xs font-sans font-bold px-2 py-0.5 rounded border"
                :style="
                  councilConfig.enabled
                    ? {
                        backgroundColor: 'var(--color-brand-bg)',
                        color: 'var(--color-brand)',
                        borderColor: 'var(--color-brand-border)',
                      }
                    : {
                        backgroundColor: 'var(--bg-badge)',
                        color: 'var(--text-faint)',
                        borderColor: 'var(--border-subtle)',
                      }
                "
              >
                {{ councilConfig.enabled ? '● 投委会辩论模式' : '○ 单模型决策模式' }}
              </span>
            </div>
            <p class="text-sm font-sans mt-0.5" style="color: var(--text-muted)">
              Trader A/B/C 独立提出作战方案并互相找茬，由首席投资官 (CIO) 统筹可用资金并终审发单。
            </p>
          </div>
        </div>

        <!-- Master Switch & Action Buttons -->
        <div class="flex flex-wrap items-center gap-2 shrink-0">
          <!-- Toggle Button -->
          <button
            type="button"
            @click="auth.isSuperadmin && (councilConfig.enabled = !councilConfig.enabled)"
            class="flex items-center space-x-2 px-3 py-1.5 rounded-xl border cursor-pointer transition-colors text-sm font-sans font-bold"
            :style="
              councilConfig.enabled
                ? {
                    backgroundColor: 'var(--color-brand-bg)',
                    borderColor: 'var(--color-brand-border)',
                    color: 'var(--color-brand)',
                  }
                : {
                    backgroundColor: 'var(--bg-card-subtle)',
                    borderColor: 'var(--border-subtle)',
                    color: 'var(--text-muted)',
                  }
            "
            :disabled="!auth.isSuperadmin"
          >
            <ToggleRight v-if="councilConfig.enabled" class="w-4 h-4 text-emerald-400" />
            <ToggleLeft v-else class="w-4 h-4 text-[var(--text-muted)]" />
            <span>{{ councilConfig.enabled ? '机制已开启' : '机制已关闭' }}</span>
          </button>

          <!-- Save Button -->
          <button
            @click="saveConfig"
            :disabled="saving || !auth.isSuperadmin"
            class="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-sm font-sans font-bold cursor-pointer disabled:opacity-40 shadow-xs transition-all"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            <Save class="w-3.5 h-3.5" />
            <span>{{ saving ? '保存中...' : '保存配置' }}</span>
          </button>

          <!-- Test Button -->
          <button
            @click="runDebateTest"
            :disabled="testing"
            class="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl border text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <Play class="w-3.5 h-3.5" :class="{ 'animate-spin': testing }" />
            <span>{{ testing ? '现场辩论中...' : '现场辩论测试' }}</span>
          </button>
        </div>
      </div>

      <!-- Consensus Mode Selection Grid -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
        <div
          v-for="mode in consensusModes"
          :key="mode.id"
          @click="auth.isSuperadmin && (councilConfig.consensus_mode = mode.id)"
          class="p-3 rounded-xl border transition-all cursor-pointer shadow-xs"
          :style="
            councilConfig.consensus_mode === mode.id
              ? {
                  borderColor: 'var(--color-brand-border)',
                  backgroundColor: 'var(--color-brand-bg)',
                }
              : { borderColor: 'var(--border-subtle)', backgroundColor: 'var(--bg-card-subtle)' }
          "
        >
          <div class="flex items-center justify-between mb-1">
            <span class="text-sm font-bold font-sans" style="color: var(--text-main)">{{
              mode.name
            }}</span>
            <span
              class="text-xs px-1.5 py-0.2 rounded font-sans border font-bold"
              :style="
                councilConfig.consensus_mode === mode.id
                  ? {
                      backgroundColor: 'var(--bg-card)',
                      color: 'var(--text-main)',
                      borderColor: 'var(--border-medium)',
                    }
                  : {
                      backgroundColor: 'var(--bg-card)',
                      color: 'var(--text-muted)',
                      borderColor: 'var(--border-subtle)',
                    }
              "
            >
              {{ mode.tag }}
            </span>
          </div>
          <p class="text-xs font-sans leading-relaxed" style="color: var(--text-muted)">
            {{ mode.desc }}
          </p>
        </div>
      </div>

      <!-- Quick Timeout & Preset Bar -->
      <div
        class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t"
        style="border-color: var(--border-subtle)"
      >
        <div class="flex items-center space-x-2">
          <span class="text-xs font-sans font-bold" style="color: var(--text-muted)"
            >投委会超时保护:</span
          >
          <input aria-label="投委会总超时（秒）"
            v-model="councilConfig.timeout_seconds"
            type="number"
            min="10"
            max="180"
            step="5"
            class="w-16 rounded-lg px-2 py-1 text-sm font-sans outline-none border text-center"
            style="
              background-color: var(--bg-input);
              border-color: var(--border-subtle);
              color: var(--text-main);
            "
            :disabled="!auth.isSuperadmin"
          />
          <span class="text-xs font-sans text-[var(--text-muted)]"
            >秒 (超时自动降级为单模型决策)</span
          >
        </div>

        <div class="flex items-center space-x-2">
          <button
            @click="applySuite('hedge_fund_desk')"
            :disabled="!auth.isSuperadmin"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-sm font-sans cursor-pointer transition-all"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-subtle);
              color: var(--text-main);
            "
          >
            <RotateCcw class="w-3 h-3 text-purple-400" />
            <span>恢复对冲基金标准阵容</span>
          </button>
          <button
            @click="addNewCustomTrader"
            :disabled="!auth.isSuperadmin"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border border-dashed text-sm font-sans cursor-pointer transition-all"
            style="border-color: var(--color-brand); color: var(--color-brand)"
          >
            <Plus class="w-3 h-3" />
            <span>添加自定义交易员席位</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 2. Trader Seats & CIO Desk (Core Cards) -->
    <div class="space-y-3">
      <div
        v-for="(role, roleId) in councilConfig.roles"
        :key="roleId"
        class="rounded-2xl border p-4 sm:p-5 transition-all shadow-xs"
        :style="{
          backgroundColor: expandedRole === roleId ? 'var(--bg-card-subtle)' : 'var(--bg-card)',
          borderColor:
            expandedRole === roleId ? 'var(--color-brand-border)' : 'var(--border-subtle)',
          opacity: role.enabled === false ? '0.6' : '1',
        }"
      >
        <!-- Seat Row -->
        <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <!-- Left: Identity -->
          <div class="flex items-center space-x-3 min-w-0 flex-1">
            <span
              class="w-9 h-9 rounded-xl flex items-center justify-center font-bold text-sm border shrink-0"
              :class="roleColors[roleId] || roleColors['custom']"
            >
              <component :is="roleIcons[roleId] || roleIcons['custom']" class="w-4 h-4" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <input :aria-label="String(roleId) + ' 席位名称'"
                  v-model="role.name"
                  class="bg-transparent border-b border-dashed text-sm font-bold font-sans outline-none max-w-[240px]"
                  style="border-color: var(--border-medium); color: var(--text-main)"
                  :readonly="!auth.isSuperadmin"
                  placeholder="角色名称"
                />
                <span
                  class="rounded px-2 py-0.5 text-xs font-sans border"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-muted);
                  "
                >
                  {{ role.role_title || (role.is_arbitrator ? 'CIO / 终审' : 'Senior Trader') }}
                </span>
                <span
                  v-if="role.is_arbitrator || roleId === 'cio'"
                  class="text-xs font-sans font-bold px-1.5 py-0.2 rounded border shrink-0 text-purple-400 border-purple-500/30 bg-purple-500/10"
                >
                  ⚖️ 终审发单席位
                </span>
                <span
                  v-else
                  class="text-xs font-sans px-1.5 py-0.2 rounded border shrink-0"
                  :style="
                    role.enabled !== false
                      ? {
                          backgroundColor: 'var(--color-up-bg)',
                          color: 'var(--color-up)',
                          borderColor: 'var(--color-up-border)',
                        }
                      : {
                          backgroundColor: 'var(--bg-badge)',
                          color: 'var(--text-faint)',
                          borderColor: 'var(--border-subtle)',
                        }
                  "
                >
                  {{ role.enabled !== false ? '活跃参与' : '已静音' }}
                </span>
              </div>
              <p class="text-xs font-sans mt-0.5 truncate" style="color: var(--text-muted)">
                {{ role.description || '负责当前交易台的独立审查与实战方案提交' }}
              </p>
            </div>
          </div>

          <!-- Right: Model Binding, Weight & Controls -->
          <div class="flex flex-wrap items-center justify-end gap-2 shrink-0">
            <!-- Bound Model -->
            <div class="flex items-center space-x-1">
              <span class="text-xs font-sans text-[var(--text-muted)]">模型:</span>
              <select :aria-label="String(roleId) + ' 席位模型'"
                v-model="role.model_id"
                class="rounded-xl px-2 py-1 text-sm font-sans outline-none border cursor-pointer max-w-[150px]"
                style="
                  background-color: var(--bg-input);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
                :disabled="!auth.isSuperadmin"
              >
                <option value="">(继承全局主脑)</option>
                <option v-for="m in availableModels" :key="m.id" :value="m.id">
                  {{ m.name || m.id }}
                </option>
              </select>
            </div>

            <!-- Weight (For traders only) -->
            <div v-if="!role.is_arbitrator && roleId !== 'cio'" class="flex items-center space-x-1">
              <span class="text-xs font-sans text-[var(--text-muted)]">权重:</span>
              <input :aria-label="String(roleId) + ' 绩效权重'"
                v-model="role.weight"
                type="number"
                step="0.05"
                min="0.1"
                max="1.0"
                class="w-14 rounded-xl px-1.5 py-1 text-sm font-sans outline-none text-center border"
                style="
                  background-color: var(--bg-input);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
                :disabled="!auth.isSuperadmin"
              />
            </div>

            <!-- Enable / Mute Toggle -->
            <button
              v-if="!role.is_arbitrator && roleId !== 'cio'"
              @click="role.enabled = role.enabled === false ? true : false"
              :disabled="!auth.isSuperadmin"
              class="cursor-pointer p-1"
              :class="role.enabled !== false ? 'text-emerald-400' : 'text-[var(--text-muted)]'"
              :title="role.enabled !== false ? '静音此交易员' : '激活此交易员'"
            >
              <ToggleRight v-if="role.enabled !== false" class="w-5 h-5" />
              <ToggleLeft v-else class="w-5 h-5" />
            </button>

            <!-- Delete (Only for custom traders) -->
            <button
              v-if="
                !role.is_arbitrator &&
                roleId !== 'cio' &&
                !['trader_trend', 'trader_momentum', 'trader_quant'].includes(String(roleId))
              "
              @click="removeRole(String(roleId))"
              :disabled="!auth.isSuperadmin"
              class="p-1.5 rounded text-rose-400 hover:opacity-80 cursor-pointer"
              title="移除此席位"
            >
              <Trash2 class="w-3.5 h-3.5" />
            </button>

            <!-- Expand Accordion Button -->
            <button
              @click="expandedRole = expandedRole === roleId ? '' : String(roleId)"
              class="p-1.5 rounded cursor-pointer transition-colors"
              style="color: var(--text-muted)"
              title="展开/收起定制提示词"
            >
              <ChevronUp v-if="expandedRole === roleId" class="w-4 h-4" />
              <ChevronDown v-else class="w-4 h-4" />
            </button>
          </div>
        </div>

        <!-- Expanded Custom Prompt & Parameter Tuning -->
        <div
          v-if="expandedRole === roleId"
          class="mt-3 pt-3 border-t space-y-3"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex flex-wrap items-center justify-between gap-2 text-sm font-sans">
            <div class="flex items-center space-x-2">
              <span style="color: var(--text-muted)">采样温度 (Temperature):</span>
              <input :aria-label="String(roleId) + ' 采样温度'"
                v-model="role.temperature"
                type="number"
                step="0.05"
                min="0.0"
                max="1.0"
                class="w-16 rounded-lg px-2 py-0.5 text-sm outline-none text-center border"
                style="
                  background-color: var(--bg-input);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
                :disabled="!auth.isSuperadmin"
              />
              <span class="text-xs text-[var(--text-muted)]">(0.1~0.2 严格理性 / 0.3+ 进取)</span>
            </div>

            <!-- Quick Data Slots Inserter -->
            <div class="flex flex-wrap items-center gap-1 text-xs font-sans">
              <span class="text-[var(--text-muted)]">插入插槽:</span>
              <button
                v-for="slot in [
                  { k: 'macro_4h', label: '4H宏观' },
                  { k: 'calculus_1h', label: '微积分动能' },
                  { k: 'smart_money', label: '聪明钱' },
                  { k: 'orderbook_depth', label: '盘口深度' },
                  { k: 'sentiment', label: '情绪异动' },
                  { k: 'trading_memory', label: '长期心法' },
                ]"
                :key="slot.k"
                type="button"
                @click="
                  role.prompt = role.prompt
                    ? `${role.prompt.trim()}\n- 重点核验: {{${slot.k}}}`
                    : `{{${slot.k}}}`
                "
                class="px-2 py-0.5 rounded-md border cursor-pointer hover:border-purple-400 transition-colors"
                style="
                  background-color: var(--bg-card);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              >
                +&#123;&#123;{{ slot.k }}&#125;&#125;
              </button>

              <button
                @click="resetRole(String(roleId))"
                :disabled="!auth.isSuperadmin"
                class="ml-2 flex items-center space-x-1 text-xs text-purple-400 hover:underline cursor-pointer"
              >
                <RotateCcw class="w-3 h-3" />
                <span>恢复预设提示词</span>
              </button>
            </div>
          </div>

          <!-- Prompt Editor Textarea -->
          <textarea :aria-label="String(roleId) + ' 角色提示词'"
            v-model="role.prompt"
            rows="6"
            class="w-full rounded-xl p-3 text-sm font-sans outline-none border leading-relaxed resize-y select-text transition-colors"
            style="
              background-color: var(--bg-input);
              border-color: var(--border-subtle);
              color: var(--text-main);
            "
            :disabled="!auth.isSuperadmin"
            placeholder="编写该席位的实战职责、资金/持仓审查规范与作战提案指引..."
          ></textarea>
        </div>
      </div>
    </div>

    <!-- 3. Live Deliberation Docket & CIO Verdict (Only shown after test run) -->
    <div
      v-if="testResult"
      class="rounded-2xl border p-4 sm:p-5 space-y-4 shadow-lg"
      style="background-color: var(--bg-card); border-color: var(--color-brand-border)"
    >
      <div
        class="flex items-center justify-between pb-3 border-b"
        style="border-color: var(--border-subtle)"
      >
        <div class="flex flex-wrap items-center gap-2">
          <CheckCircle2 class="w-4 h-4 text-emerald-400" />
          <h3 class="text-sm font-bold font-sans" style="color: var(--text-main)">
            投委会现场辩论与 CIO 裁定实录
          </h3>
          <span
            class="text-xs font-sans px-2 py-0.5 rounded border border-purple-500/30 bg-purple-500/10 text-purple-400"
          >
            共识机制: {{ testResult.transcript?.consensus_mode }}
          </span>
          <span
            class="text-xs font-sans px-2 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
          >
            全流程耗时 {{ testResult.transcript?.total_duration_ms }}ms
          </span>
        </div>
        <button
          @click="testResult = null"
          class="text-sm font-sans cursor-pointer px-3 py-1 rounded-lg border"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-muted);
          "
        >
          收起
        </button>
      </div>

      <!-- Traders' Proposals Grid -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div
          v-for="(adv, key) in testResult.transcript?.advisors || {}"
          :key="key"
          class="rounded-xl border p-3.5 space-y-2 flex flex-col justify-between"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
        >
          <div class="space-y-1">
            <div class="flex items-center justify-between text-sm font-sans font-bold">
              <span style="color: var(--text-main)">{{ adv.role_name }}</span>
              <span class="text-xs text-purple-400 truncate max-w-[120px]">{{
                adv.model_used
              }}</span>
            </div>
            <div
              class="flex items-center justify-between text-xs font-sans text-[var(--text-muted)]"
            >
              <span>响应: {{ adv.latency_ms }}ms</span>
              <span v-if="adv.weight !== undefined">权重: {{ adv.weight }}</span>
            </div>
            <p
              class="text-sm font-sans whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto pr-1 select-text"
              style="color: var(--text-muted)"
            >
              {{ adv.content }}
            </p>
          </div>

          <div
            v-if="adv.reasoning"
            class="pt-2 border-t"
            style="border-color: var(--border-subtle)"
          >
            <button
              @click="expandedReasoning[String(key)] = !expandedReasoning[String(key)]"
              class="text-xs font-sans text-purple-400 cursor-pointer"
            >
              <span>{{
                expandedReasoning[String(key)] ? '收起思考链' : '展开思考链 (Reasoning)'
              }}</span>
            </button>
            <div
              v-if="expandedReasoning[String(key)]"
              class="mt-1.5 p-2 rounded text-xs font-sans whitespace-pre-wrap max-h-36 overflow-y-auto select-text border"
              style="
                background-color: var(--bg-card);
                border-color: var(--border-subtle);
                color: var(--text-muted);
              "
            >
              {{ adv.reasoning }}
            </div>
          </div>
        </div>
      </div>

      <!-- CIO Arbitrated Verdict & Order Dispatch -->
      <div
        class="rounded-xl border p-4 space-y-3"
        style="background-color: var(--bg-card-subtle); border-color: var(--color-brand-border)"
      >
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <span class="text-sm font-bold font-sans text-purple-400"
              >【首席投资官 (CIO) 终审批复】</span
            >
            <span class="text-xs font-sans text-[var(--text-muted)]"
              >{{ testResult.transcript?.arbitrator?.model_used }} · 审阅耗时
              {{ testResult.transcript?.arbitrator?.latency_ms }}ms</span
            >
          </div>
          <span
            class="text-xs font-sans font-bold px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-400"
          >
            方案采纳与点位落地
          </span>
        </div>

        <div
          class="text-sm font-sans font-bold leading-relaxed p-2.5 rounded border text-emerald-400"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          资金总括与决议: {{ testResult.brain_output?.macro_assessment }}
        </div>

        <!-- 6 Instruments Points Matrix -->
        <div v-if="testResult.brain_output?.decisions" class="space-y-1.5">
          <div class="text-sm font-bold font-sans" style="color: var(--text-main)">
            六大标的落盘点位矩阵:
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 font-sans text-sm">
            <AppCard
              v-for="(dec, sym) in testResult.brain_output?.decisions"
              :key="sym"
              class="p-3 rounded-xl border flex flex-col justify-between space-y-2"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="flex items-center justify-between">
                <span class="font-black text-sm" style="color: var(--text-main)">{{ sym }}</span>
                <span
                  class="px-2 py-0.5 rounded text-xs font-bold border"
                  :style="{
                    backgroundColor: dec.action?.includes('BUY')
                      ? 'var(--color-up-bg)'
                      : dec.action?.includes('SELL')
                        ? 'var(--color-down-bg)'
                        : 'var(--bg-badge)',
                    borderColor: dec.action?.includes('BUY')
                      ? 'var(--color-up-border)'
                      : dec.action?.includes('SELL')
                        ? 'var(--color-down-border)'
                        : 'var(--border-subtle)',
                    color: dec.action?.includes('BUY')
                      ? 'var(--color-up)'
                      : dec.action?.includes('SELL')
                        ? 'var(--color-down)'
                        : 'var(--text-muted)',
                  }"
                >
                  {{ dec.action || 'WAIT' }} ({{ dec.confidence || 0 }}%)
                </span>
              </div>

              <!-- Price & Risk Metrics -->
              <div
                v-if="dec.action !== 'WAIT'"
                class="grid grid-cols-3 gap-1.5 p-2 rounded-lg bg-black/20 text-xs text-center"
              >
                <div>
                  <div class="text-[var(--text-muted)]">入场限价</div>
                  <div class="font-bold text-[var(--text-main)] mt-0.5">
                    ${{ dec.limit_price || dec.entry_price || '--' }}
                  </div>
                </div>
                <div>
                  <div class="text-rose-400">2.0x止损</div>
                  <div class="font-bold text-rose-400 mt-0.5">${{ dec.stop_loss || '--' }}</div>
                </div>
                <div>
                  <div class="text-emerald-400">2.0R止盈</div>
                  <div class="font-bold text-emerald-400 mt-0.5">
                    ${{ dec.take_profit || '--' }}
                  </div>
                </div>
              </div>
              <div
                v-else
                class="p-2 rounded-lg bg-black/10 text-xs text-[var(--text-muted)] italic"
              >
                保持空仓防守，未达顺势回踩或微积分爆发要求。
              </div>

              <div class="text-xs text-[var(--text-muted)] line-clamp-3 leading-relaxed">
                {{ dec.reasoning || dec.reason || '遵从投委会综合裁定。' }}
              </div>
            </AppCard>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
