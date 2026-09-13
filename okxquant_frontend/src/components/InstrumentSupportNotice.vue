<script setup lang="ts">
import { computed } from 'vue'
import AppBadge from './ui/AppBadge.vue'
import type { InstrumentSupport } from '../types/dashboard'
import { canOpen, supportTone } from '../utils/instrumentSupport'
const props = withDefaults(defineProps<{ support?: InstrumentSupport; compact?: boolean }>(), { compact: false })
const allowed = computed(() => canOpen(props.support))
const checked = computed(() => props.support?.checked_at ? new Date(props.support.checked_at * 1000).toLocaleString() : '')
</script>
<template>
  <div class="instrument-support-notice" :class="{ 'instrument-support-notice--blocked': !allowed && support?.status !== 'refreshing' }" :data-support-status="support?.status || 'unknown'">
    <AppBadge :tone="supportTone(support)" dot>{{ support?.label || '支持状态待确认' }}</AppBadge>
    <p v-if="!allowed && support?.status !== 'refreshing'" class="instrument-support-notice__message">{{ compact ? (support?.status === 'unknown' || !support ? '核验前暂停新开仓 · 公共行情仅供观察' : '仅供公共行情观察 · 不参与当前环境开仓或加仓') : (support?.message || '正在确认当前交易环境的合约支持情况，请稍后重试。') }}</p>
    <p v-if="!compact && checked" class="instrument-support-notice__time">目录核验：{{ checked }}</p>
  </div>
</template>
<style scoped>
.instrument-support-notice { display: grid; gap: .375rem; min-width: 0; white-space: normal; }
.instrument-support-notice--blocked { border: 1px solid var(--color-warn-border); background: var(--color-warn-bg); padding: .625rem .75rem; border-radius: .625rem; }
.instrument-support-notice__message { font-size: .75rem; line-height: 1.65; color: var(--text-main); overflow-wrap: anywhere; }
.instrument-support-notice__time { font-size: .75rem; line-height: 1.5; color: var(--text-muted); }
</style>
