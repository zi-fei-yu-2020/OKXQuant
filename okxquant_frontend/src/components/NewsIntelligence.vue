<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import NewsConnectionStatus from './NewsConnectionStatus.vue'
import { newsIsFresh } from '../utils/newsStatus'

import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Newspaper, Flame, ExternalLink, ShieldAlert } from 'lucide-vue-next'

const store = useDashboardStore()
const intel = computed<any>(() => store.data?.news_intelligence || {})
const newsItems = computed<any[]>(() => intel.value.latest_news || [])
const coinsSentiment = computed<[string, any][]>(() =>
  Object.entries(intel.value.coins_sentiment || {}),
)
const macro = computed<string>(() => intel.value.macro_sentiment || '--')
const breakerActive = computed<boolean>(() => !!intel.value.circuit_breaker?.active)

function safeNewsLink(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined } catch { return undefined }
}

function labelClass(label: string) {
  if (label === 'bullish')
    return 'color: var(--color-up); background-color: var(--color-up-bg); border-color: var(--color-up-border);'
  if (label === 'bearish')
    return 'color: var(--color-down); background-color: var(--color-down-bg); border-color: var(--color-down-border);'
  if (label === 'mixed')
    return 'color: var(--color-warn); background-color: var(--color-warn-bg); border-color: var(--color-warn-border);'
  return 'color: var(--text-muted); background-color: var(--bg-badge); border-color: var(--border-subtle);'
}

function labelCn(label: string) {
  return (
    { bullish: '偏多', bearish: '偏空', mixed: '多空交织', neutral: '中性', unknown: '未知' }[label] ||
    label ||
    '中性'
  )
}

function importanceClass(imp: string) {
  if (imp === 'high' || imp === 'critical') return 'color: var(--color-down);'
  if (imp === 'medium') return 'color: var(--color-warn);'
  return 'color: var(--text-faint);'
}

function importanceCn(imp: string) {
  return { critical: '重大', high: '高', medium: '中', low: '低' }[imp] || imp || '低'
}
</script>

<template>
  <div class="space-y-3.5">
    <!-- Header Banner -->
    <AppCard
      class="rounded-xl border p-4 sm:p-5 flex flex-wrap items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div class="flex items-center space-x-3">
        <div
          class="w-9 h-9 rounded-lg flex items-center justify-center border shrink-0"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-medium);
            color: var(--text-main);
          "
        >
          <Newspaper class="w-4 h-4" />
        </div>
        <div>
          <h2
            class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide"
            style="color: var(--text-main)"
          >
            全网加密重大舆情与流动性情报
          </h2>
          <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted)">
            聚合扫描主流财经与链上异动 · 更新于 {{ intel.updated_at || '--' }} (UTC+8)
          </p>
        </div>
      </div>

      <div class="flex items-center space-x-2">
        <span
          class="px-2.5 py-1 rounded-lg border text-xs font-mono font-bold"
          :style="{
            backgroundColor: breakerActive ? 'var(--color-down-bg)' : newsIsFresh(intel) ? 'var(--color-up-bg)' : 'var(--color-warn-bg)',
            borderColor: breakerActive ? 'var(--color-down-border)' : newsIsFresh(intel) ? 'var(--color-up-border)' : 'var(--color-warn-border)',
            color: breakerActive ? 'var(--color-down)' : newsIsFresh(intel) ? 'var(--color-up)' : 'var(--color-warn)',
          }"
        >
          <ShieldAlert class="w-3 h-3 inline mr-1" />
          {{ breakerActive ? '黑天鹅熔断激活' : newsIsFresh(intel) ? '资讯监测可用' : '资讯监测待恢复' }}
        </span>

        <span
          class="px-2.5 py-1 rounded-lg border text-xs font-mono"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-muted);
          "
        >
          宏观情绪: <strong style="color: var(--text-main)">{{ macro }}</strong>
        </span>
      </div>
    </AppCard>

    <NewsConnectionStatus :status="intel" />
    <!-- Coin Sentiment Chips -->
    <div
      v-if="coinsSentiment.length"
      class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2.5"
    >
      <AppCard
        v-for="[ccy, s] in coinsSentiment"
        :key="ccy"
        class="rounded-xl border p-3 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs font-black font-mono" style="color: var(--text-main)">{{
            ccy
          }}</span>
          <span
            class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border"
            :style="labelClass(s.label)"
          >
            {{ labelCn(s.label) }}
          </span>
        </div>
        <div class="flex items-center justify-between text-[11px] font-mono">
          <span style="color: var(--color-up)"
            >多 {{ s.bullish_ratio || s.bullish_pct || '--' }}</span
          >
          <span style="color: var(--color-down)"
            >空 {{ s.bearish_ratio || s.bearish_pct || '--' }}</span
          >
        </div>
        <div
          class="flex items-center justify-between text-[10px] font-mono mt-1 pt-1 border-t"
          style="border-color: var(--border-subtle)"
        >
          <span style="color: var(--text-faint)"
            >提及 {{ s.mentions == null ? '--' : s.mentions.toLocaleString() }}</span
          >
          <span v-if="s.long_short_ratio" class="font-bold text-blue-400"
            >比率 {{ s.long_short_ratio }}</span
          >
        </div>
      </AppCard>
    </div>

    <!-- News List -->
    <div
      v-if="newsItems.length === 0"
      class="py-16 text-center border border-dashed rounded-xl"
      style="
        background-color: var(--bg-card-subtle);
        border-color: var(--border-subtle);
        color: var(--text-muted);
      "
    >
      <p class="text-xs font-mono font-medium">
        暂无可展示资讯。可能是连接未就绪、缓存过期或本次查询没有结果，不能据此判断市场平稳。
      </p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <AppCard
        v-for="item in newsItems"
        :key="item.id"
        class="rounded-xl border p-4 transition-all shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div class="flex items-start justify-between gap-2 mb-2">
          <div class="flex items-start space-x-1.5 min-w-0">
            <Flame class="w-4 h-4 shrink-0 mt-0.5" :style="importanceClass(item.importance)" />
            <span
              class="font-bold text-xs sm:text-sm leading-snug font-sans"
              style="color: var(--text-main)"
            >
              {{ item.title }}
            </span>
          </div>
          <span class="text-[10px] font-mono shrink-0" style="color: var(--text-faint)">
            {{ item.time }}
          </span>
        </div>

        <p class="text-xs leading-relaxed font-sans line-clamp-3" style="color: var(--text-muted)">
          {{ item.summary }}
        </p>

        <div
          class="mt-3 pt-2.5 border-t flex items-center justify-between text-[11px] font-mono"
          style="border-color: var(--border-subtle); color: var(--text-muted)"
        >
          <span
            >热度:
            <strong :style="importanceClass(item.importance)">{{
              importanceCn(item.importance)
            }}</strong></span
          >
          <span class="flex items-center space-x-2">
            <span
              >标的:
              <strong style="color: var(--text-main)">{{
                (item.coins || []).join(', ') || 'ALL'
              }}</strong></span
            >
            <a
              v-if="item.url"
              :href="safeNewsLink(item.url)"
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center hover:underline"
              style="color: var(--color-brand)"
            >
              原文<ExternalLink class="w-3 h-3 ml-0.5" />
            </a>
          </span>
        </div>
      </AppCard>
    </div>
  </div>
</template>
