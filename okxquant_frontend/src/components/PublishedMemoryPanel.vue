<script setup lang="ts">
import { computed } from 'vue'
import AppCard from './ui/AppCard.vue'
import AppBadge from './ui/AppBadge.vue'
import type { MemoryPublication } from '../utils/memory'
const props = defineProps<{ publication?: MemoryPublication }>()
const enabledRules = computed(() => (props.publication?.rules || []).filter(rule => rule.enabled === true))
const legacy = computed(() => props.publication?.format === 'legacy_snapshot' || props.publication?.status === 'legacy_unmanaged')
const ruleCount = computed(() => props.publication && Array.isArray(props.publication.rules) ? enabledRules.value.length : null)
</script>
<template>
  <AppCard class="research-panel min-w-0" data-published-memory>
    <header class="research-header">
      <h3>运行记忆</h3>
      <AppBadge :tone="publication?.active_version ? 'brand' : 'neutral'">{{ publication?.active_version ? `版本 v${publication.active_version}` : publication?.status === 'legacy_unmanaged' ? '尚未纳管' : '版本待核验' }}</AppBadge>
    </header>
    <p v-if="publication?.status === 'unavailable'" role="alert" class="research-notice">{{ publication.message || '运行记忆不可用，已阻止回退到其他来源。' }}</p>
    <p v-else-if="!publication" class="research-note">尚未取得运行记忆状态，不推断已启用规则数量。</p>
    <template v-else>
      <div class="research-memory-summary">
        <span class="research-memory-count"><strong>{{ ruleCount ?? '—' }}</strong> 条已审核启用规则</span>
        <AppBadge tone="neutral">{{ legacy ? '历史兼容输入' : publication.active_version ? '已发布内容' : '来源待核验' }}</AppBadge>
      </div>
      <p class="research-date">内容最近变更 <time>{{ publication.effective_updated_at || '尚无记录' }}</time></p>
      <p v-if="legacy" class="research-note">当前保留历史兼容文本，不代表旧心法已通过审核或启用。</p>
      <p v-if="ruleCount === 0" class="research-note">暂无审核启用规则；基础策略与执行层风控仍独立运行。</p>

      <details v-if="enabledRules.length" class="action-disclosure research-disclosure" data-memory-rules>
        <summary><span>已启用规则</span><span class="research-summary-meta">{{ enabledRules.length }} 条</span></summary>
        <ul class="research-disclosure-body research-text-list"><li v-for="rule in enabledRules" :key="rule.id"><p>{{ rule.text }}</p><code class="research-id">{{ rule.id }}</code></li></ul>
      </details>
      <div class="research-resources">
        <details v-if="publication.prompt_text" class="action-disclosure text-xs min-w-0" data-memory-disclosure="effective">
          <summary><span class="research-summary-title">当前模型输入<span>实际读取的完整记忆文本</span></span></summary>
          <div class="research-disclosure-body"><p class="research-note">核对实际输入原文，与当前版本和内容指纹对应。</p><pre tabindex="0" aria-label="当前模型完整记忆原文" class="research-original">{{ publication.prompt_text }}</pre></div>
        </details>
        <details v-if="publication.legacy_context" class="action-disclosure text-xs min-w-0" data-memory-disclosure="legacy">
          <summary><span class="research-summary-title">历史兼容上下文<span>不是新审批规则</span></span></summary>
          <div class="research-disclosure-body"><p class="research-note">点击展开历史兼容内容不会启用或修改规则。纳管本身不改变模型实际读取的文字，也不会自动加载旧的“黄金基准”。</p><pre tabindex="0" aria-label="历史兼容上下文原文" class="research-original">{{ publication.legacy_context }}</pre></div>
        </details>
        <details class="action-disclosure research-disclosure" data-memory-metadata>
          <summary><span>版本与来源</span><span class="research-summary-meta">时间 / 内容指纹</span></summary>
          <div class="research-disclosure-body">
            <dl class="research-metadata">
              <div><dt>版本发布 / 纳管时间</dt><dd>{{ publication.published_at || '—' }}</dd></div>
              <div><dt>有效内容变更时间</dt><dd>{{ publication.effective_updated_at || '—' }}</dd></div>
              <div><dt>已审核启用规则</dt><dd>{{ ruleCount ?? '—' }} 条</dd></div>
              <div v-if="publication.prompt_hash"><dt>完整内容指纹</dt><dd><code class="research-id">{{ publication.prompt_hash }}</code></dd></div>
            </dl>
            <p class="research-note">纳管时间、内容变更时间与复盘报告时间各自独立，发布版本不等于产生新经验。这里的启用规则不会替代基础策略和硬风控。</p>
          </div>
        </details>
      </div>
    </template>
  </AppCard>
</template>
<style src="./research-panels.css"></style>
