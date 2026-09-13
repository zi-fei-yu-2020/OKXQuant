<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import { newsIsFresh, newsStatusText, newsTime } from '../utils/newsStatus'
import { friendlyConnectionError } from '../utils/accountConnections'
defineProps<{ status?: any }>()
const names: Record<string,string>={latest:'最新新闻',important:'重要新闻',sentiment:'币种情绪'}
</script>
<template>
 <AppCard class="min-w-0 p-3 sm:p-4 space-y-2" data-news-connection-status>
  <p class="text-sm font-medium" :style="{color:newsIsFresh(status)?'var(--color-up)':'var(--color-warn)'}">{{ newsStatusText(status) }}</p>
  <p class="text-xs leading-relaxed" style="color:var(--text-muted)">来源：OKX 官方资讯 · 与交易账户环境独立<br>全部分区最近成功：{{ newsTime(status?.last_success_at) }} · 最近尝试：{{ newsTime(status?.last_attempt_at) }}</p>
  <div v-if="status?.sections" class="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs" style="color:var(--text-muted)">
   <div v-for="(section,key) in status.sections" :key="key" class="min-w-0 break-words" style="overflow-wrap:anywhere"><p>{{ names[String(key)] || key }}：{{ section.status==='fresh'?'读取成功':section.status==='stale'?'读取失败，显示旧缓存':'不可用' }}</p><p>最近成功 {{ newsTime(section.last_success_at) }}</p><p v-if="section.error">{{ friendlyConnectionError(section.error) }}</p></div>
  </div>
  <p v-if="!newsIsFresh(status)" class="text-xs" style="color:var(--text-muted)">旧缓存仅供历史查看；缺少或过期的资讯不会被当作当前市场平稳的证据。</p>
 </AppCard>
</template>
