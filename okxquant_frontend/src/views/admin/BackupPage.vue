<script setup lang="ts">
import AppField from '../../components/ui/AppField.vue'
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'

import { useFeedback, useToast } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
import { backupConfiguration, backupLatest } from '../../utils/backupDisplay'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  HardDrive,
  PlugZap,
  Save,
  PlayCircle,
  Archive,
  Download,
  Upload,
  RotateCcw,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const loading = ref(true)
const busy = ref<'test' | 'save' | 'run' | 'restore' | 'upload' | ''>('')
const bannerMsg = useFeedback()

const simple = ref<any>(null)
const configurationDisplay = computed(() => backupConfiguration(simple.value))
const latestDisplay = computed(() => backupLatest(simple.value?.latest))
const targetTypes = ref<any[]>([])
const status = ref<any>(null)
const uploadFileInput = ref<HTMLInputElement | null>(null)

const enabled = ref(false)
const scheduleTime = ref('02:00')
const destination = ref('local')
const retention = ref(3)
const endpoint = ref('')
const bucket = ref('')
const credentials = ref<Record<string, string>>({})

const remoteDest = computed(() =>
  ['s3', 'oss', 'webdav', 'baidu_oauth'].includes(destination.value),
)
const needsBucket = computed(() => destination.value === 's3' || destination.value === 'oss')
const credentialFields = computed(() => {
  if (['s3', 'oss'].includes(destination.value)) return ['access_key_id', 'secret_access_key']
  if (['webdav', 'aliyundrive', 'quark'].includes(destination.value))
    return ['username', 'password']
  if (destination.value === 'baidu_oauth') return ['app_key', 'app_secret', 'refresh_token']
  return []
})

async function load() {
  loading.value = true
  try {
    const results = await Promise.allSettled([
      api('/api/v1/admin/backups/simple'),
      api('/api/v1/admin/backup-target-types'),
      api('/api/v1/admin/backups'),
    ])
    const [simpleResult, targetsResult, statusResult] = results
    if (simpleResult.status === 'fulfilled') {
      const s: any = simpleResult.value
      simple.value = s
      enabled.value = s.enabled
      destination.value = s.destination
      scheduleTime.value = s.schedule_time || '02:00'
      retention.value = s.retention || 3
      endpoint.value = s.target?.endpoint || ''
      bucket.value = s.target?.bucket || ''
    }
    if (targetsResult.status === 'fulfilled') targetTypes.value = (targetsResult.value as any).target_types || []
    if (statusResult.status === 'fulfilled') status.value = statusResult.value
    const firstFailure = results.find((item) => item.status === 'rejected') as PromiseRejectedResult | undefined
    if (firstFailure) throw firstFailure.reason
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function payload() {
  return {
    enabled: enabled.value,
    schedule_time: scheduleTime.value,
    destination: destination.value,
    retention: Number(retention.value) || 3,
    endpoint: endpoint.value.trim(),
    bucket: bucket.value.trim(),
    credentials: credentials.value,
  }
}

async function testConnection() {
  busy.value = 'test'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/backups/simple/test', {
      method: 'POST',
      body: JSON.stringify(payload()),
    })
    bannerMsg.value = { text: `✅ ${res.detail}`, type: 'ok' }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `测试失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function save() {
  busy.value = 'save'
  bannerMsg.value = null
  try {
    await api('/api/v1/admin/backups/simple', { method: 'PUT', body: JSON.stringify(payload()) })
    bannerMsg.value = {
      text: '✅ 灾备配置已保存，每天北京时间 ' + scheduleTime.value + ' 自动执行',
      type: 'ok',
    }
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `保存失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function runNow() {
  const phrase = await prompt(
    '立即执行完整灾备（打包并按已启用目标上传）需输入确认短语：BACKUP OKXQuant',
  )
  if (!phrase) return
  busy.value = 'run'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/backups/run', {
      method: 'POST',
      body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }),
    })
    bannerMsg.value = {
      text: `✅ 灾备执行完成（${(res.output || '').length} 字符输出已记录）`,
      type: 'ok',
    }
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `灾备失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function downloadArchive(archiveName: string) {
  try {
    const clean = archiveName.split('/').pop() || archiveName
    const url = `/api/v1/admin/backups/download/${encodeURIComponent(clean)}`
    const checkedToken = auth.token
    const resp = await fetch(url, {
      headers: {
        ...(checkedToken ? { 'X-OKXQuant-Session': checkedToken } : {}),
      },
    })
    auth.checkResponse(resp, checkedToken)
    if (!resp.ok) {
      throw new Error(`下载失败 HTTP ${resp.status}`)
    }
    const blob = await resp.blob()
    auth.checkResponse(resp, checkedToken)
    const blobUrl = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = clean
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(blobUrl)
    bannerMsg.value = { text: `✅ 归档文件 ${clean} 已成功触发下载`, type: 'ok' }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `下载失败：${e.message}`, type: 'err' }
  }
}

function triggerUpload() {
  if (uploadFileInput.value) {
    uploadFileInput.value.click()
  }
}

async function onFileSelected(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  busy.value = 'upload'
  bannerMsg.value = null
  try {
    const formData = new FormData()
    formData.append('file', file)
    const checkedToken = auth.token
    const resp = await fetch('/api/v1/admin/backups/upload', {
      method: 'POST',
      headers: {
        ...(checkedToken ? { 'X-OKXQuant-Session': checkedToken } : {}),
      },
      body: formData,
    })
    auth.checkResponse(resp, checkedToken)
    const res = await resp.json()
    auth.checkResponse(resp, checkedToken)
    if (!resp.ok) {
      throw new Error(res.detail || `上传失败 HTTP ${resp.status}`)
    }
    bannerMsg.value = { text: `✅ 备份包 ${file.name} 上传成功！`, type: 'ok' }
    await load()
  } catch (err: any) {
    if (err?.silent) return
    bannerMsg.value = { text: `上传备份失败：${err.message}`, type: 'err' }
  } finally {
    busy.value = ''
    if (target) target.value = ''
  }
}

async function restoreArchive(archiveName: string) {
  const clean = archiveName.split('/').pop() || archiveName
  const phrase = await prompt(
    `警告：仅恢复可信备份包中允许的源码与运行数据，不恢复认证及密钥。请先停止 gateway、trader 及自动拉起机制，否则返回 409；系统不会自动停机或修改云端保护订单。\n如确认恢复归档【${clean}】，请输入确认短语：RESTORE OKXQuant`,
  )
  if (!phrase) return
  if (phrase.trim().toUpperCase() !== 'RESTORE OKXQuant') {
    toast.success('确认短语不正确，已取消恢复！')
    return
  }
  busy.value = 'restore'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/backups/restore', {
      method: 'POST',
      body: JSON.stringify({
        archive_name: clean,
        confirmation: 'RESTORE OKXQuant',
      }),
    })
    bannerMsg.value = {
      text: `✅ 备份 ${clean} 恢复成功！共恢复 ${res.restored_count} 个文件，跳过 ${res.skipped_count ?? 0} 个不允许恢复的成员。请先离线检查数据，再由管理员启动服务。`,
      type: 'ok',
    }
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `恢复失败：${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

function fmtBytes(n: number) {
  if (!n) return '--'
  return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.round(n / 1024) + ' KB'
}
function fmtTime(ts: number) {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

onMounted(load)

const { prompt } = useDialogs()

const toast = useToast()
</script>

<template>
  <div class="space-y-4">
    <LoadingState v-if="loading" />

    <template v-else-if="simple">
      <!-- Simple Config -->
      <AppCard
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors space-y-4"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div class="flex items-center justify-between mb-2">
          <div class="flex items-center space-x-2">
            <HardDrive class="w-4 h-4" style="color: var(--color-brand)" />
            <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">自动灾备</h2>
          </div>
          <label class="flex items-center space-x-2 text-sm font-sans cursor-pointer">
            <input
              v-model="enabled"
              type="checkbox"
              class="accent-blue-500 w-4 h-4"
              :disabled="!auth.isSuperadmin"
            />
            <span :class="enabled ? 'text-emerald-500 font-bold' : 'text-[var(--text-muted)]'">{{
              enabled ? '每日自动灾备已启用' : '已停用'
            }}</span>
          </label>
        </div>

        <div
          v-if="simple.legacy_bypy"
          class="p-2.5 rounded-lg border text-xs font-sans"
          style="
            background-color: var(--color-warn-bg);
            border-color: var(--color-warn-border);
            color: var(--color-warn);
          "
        >
          ⚠ {{ simple.migration_note }}
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                  >1. 备份内容</span
                ></template
              ><template #default="{ id: fieldId }"
                ><select
                  :id="fieldId"
                  disabled
                  class="w-full rounded-lg px-3 py-2 text-sm font-sans opacity-70 border"
                  style="
                    background-color: var(--bg-input);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  "
                >
                  <option>OKXQuant 系统、策略、配置与运行数据</option>
                </select></template
              ></AppField
            >
          </div>
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                  >2. 保存位置</span
                ></template
              ><template #default="{ id: fieldId }"
                ><select
                  :id="fieldId"
                  v-model="destination"
                  :disabled="!auth.isSuperadmin"
                  class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border cursor-pointer"
                  style="
                    background-color: var(--bg-input);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  "
                >
                  <option value="local">本地滚动归档</option>
                  <option value="s3">S3 兼容存储</option>
                  <option value="oss">阿里云 OSS</option>
                  <option value="webdav">WebDAV / OpenList</option>
                  <option value="baidu_oauth">百度网盘（官方 OAuth）</option>
                </select></template
              ></AppField
            >
          </div>
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                  >3. 每天执行时间（北京时间）</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="scheduleTime"
                  type="time"
                  :disabled="!auth.isSuperadmin"
                  class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border"
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
                  >4. 保留最近几份{{ destination === 'local' ? '（本地）' : '' }}</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="retention"
                  type="number"
                  min="1"
                  max="365"
                  :disabled="!auth.isSuperadmin"
                  class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border num-tabular"
                  style="
                    background-color: var(--bg-input);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>
        </div>

        <!-- Remote Credentials -->
        <div
          v-if="remoteDest"
          class="mt-4 p-3.5 rounded-lg border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
        >
          <div class="text-xs font-sans mb-2" style="color: var(--text-muted)">
            连接信息（保存进本机加密密文库，不回显明文）
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div v-if="destination !== 'baidu_oauth'">
              <AppField class="w-full min-w-0"
                ><template #label
                  ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                    >Endpoint</span
                  ></template
                ><template #default="{ id: fieldId }"
                  ><input
                    :id="fieldId"
                    v-model="endpoint"
                    :disabled="!auth.isSuperadmin"
                    placeholder="https://s3.us-west-004.backblazeb2.com"
                    class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border"
                    style="
                      background-color: var(--bg-input);
                      border-color: var(--border-subtle);
                      color: var(--text-main);
                    " /></template
              ></AppField>
            </div>
            <div v-if="needsBucket">
              <AppField class="w-full min-w-0"
                ><template #label
                  ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                    >Bucket</span
                  ></template
                ><template #default="{ id: fieldId }"
                  ><input
                    :id="fieldId"
                    v-model="bucket"
                    :disabled="!auth.isSuperadmin"
                    class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border"
                    style="
                      background-color: var(--bg-input);
                      border-color: var(--border-subtle);
                      color: var(--text-main);
                    " /></template
              ></AppField>
            </div>
            <div v-for="f in credentialFields" :key="f">
              <AppField class="w-full min-w-0"
                ><template #label
                  ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)">{{
                    f
                  }}</span></template
                ><template #default="{ id: fieldId }"
                  ><input
                    :id="fieldId"
                    v-model="credentials[f]"
                    type="password"
                    :disabled="!auth.isSuperadmin"
                    :placeholder="simple.configured ? '留空保持现有值' : ''"
                    class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border"
                    style="
                      background-color: var(--bg-input);
                      border-color: var(--border-subtle);
                      color: var(--text-main);
                    " /></template
              ></AppField>
            </div>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-2 mt-4">
          <template v-if="auth.isSuperadmin">
            <button
              @click="testConnection"
              :disabled="busy !== ''"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-sm font-sans cursor-pointer disabled:opacity-40 transition-all shadow-xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              <PlugZap class="w-3.5 h-3.5" /><span>{{
                busy === 'test' ? '测试中...' : '测试连接'
              }}</span>
            </button>
            <button
              @click="save"
              :disabled="busy !== ''"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
              style="background-color: var(--text-main); color: var(--bg-card)"
            >
              <Save class="w-3.5 h-3.5" /><span>{{
                busy === 'save' ? '保存中...' : '保存灾备'
              }}</span>
            </button>
            <button
              @click="runNow"
              :disabled="busy !== ''"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
              style="
                background-color: var(--color-down-bg);
                border-color: var(--color-down-border);
                color: var(--color-down);
              "
            >
              <PlayCircle class="w-3.5 h-3.5" /><span>{{
                busy === 'run' ? '执行中（最长10分钟）...' : '立即备份'
              }}</span>
            </button>

            <!-- Hidden file input for upload -->
            <input
              ref="uploadFileInput"
              type="file"
              accept=".tar.gz,.tgz"
              class="hidden"
              @change="onFileSelected"
            />
            <button
              @click="triggerUpload"
              :disabled="busy !== ''"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--color-brand);
              "
            >
              <Upload class="w-3.5 h-3.5" /><span>{{
                busy === 'upload' ? '正在上传...' : '上传备份包'
              }}</span>
            </button>
          </template>
          <span v-else class="text-xs font-sans" style="color: var(--text-faint)"
            >只读视图 · 修改需超级管理员登录</span
          >
          <span
            class="ml-auto text-xs font-sans font-bold"
            :class="configurationDisplay.className"
            >{{ configurationDisplay.label }}</span
          >
        </div>
        <p class="text-xs font-sans" style="color: var(--text-muted)">
          已保存目标：{{ configurationDisplay.note }}
        </p>
      </AppCard>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Latest -->
        <AppCard
          class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <h2 class="text-sm font-bold font-sans uppercase mb-3" style="color: var(--text-main)">
            最近一次灾备
          </h2>
          <div v-if="simple.latest" class="space-y-1.5 text-sm font-sans">
            <div
              class="flex justify-between border rounded-lg px-3 py-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span style="color: var(--text-muted)">开始时间（北京时间）</span>
              <span style="color: var(--text-main)">{{ latestDisplay.startedAt }}</span>
            </div>
            <div
              class="flex justify-between border rounded-lg px-3 py-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span style="color: var(--text-muted)">完成时间（北京时间）</span>
              <span style="color: var(--text-main)">{{ latestDisplay.finishedAt }}</span>
            </div>
            <div
              class="flex justify-between border rounded-lg px-3 py-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span style="color: var(--text-muted)">状态</span
              ><span class="font-bold" :class="latestDisplay.className">{{ latestDisplay.label }}</span>
            </div>
          </div>
          <div v-else class="py-6 text-center text-sm font-sans" style="color: var(--text-faint)">
            尚无匹配的灾备清单记录
          </div>
          <div class="text-xs font-sans mt-3 leading-relaxed" style="color: var(--text-faint)">
            {{ status?.schedule }}
          </div>
        </AppCard>

        <!-- Local archives -->
        <AppCard
          class="rounded-xl border overflow-hidden shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="px-4 py-3 border-b flex items-center justify-between"
            style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
          >
            <div class="flex items-center space-x-2">
              <Archive class="w-4 h-4 text-cyan-400" />
              <h2
                class="text-sm font-black font-sans uppercase tracking-wide"
                style="color: var(--text-main)"
              >
                备份归档清单 ({{ status?.local_archives?.length ?? 0 }})
              </h2>
            </div>
            <span class="text-xs font-sans" style="color: var(--text-faint)"
              >支持直接下载与一键恢复</span
            >
          </div>
          <div class="table-scroll-container">
            <table
              v-if="status?.local_archives?.length"
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
                  <th class="py-2.5 px-4">归档文件</th>
                  <th class="py-2.5 px-3 text-right">大小</th>
                  <th class="py-2.5 px-4 text-right">创建时间</th>
                  <th class="py-2.5 px-4 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="a in status.local_archives.slice(0, 10)"
                  :key="a.name"
                  class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                  style="border-color: var(--border-subtle)"
                >
                  <td
                    class="py-2.5 px-4 font-sans font-medium truncate max-w-[200px]"
                    style="color: var(--text-main)"
                    :title="a.name"
                  >
                    {{ a.name }}
                  </td>
                  <td class="py-2.5 px-3 text-right num-tabular" style="color: var(--text-muted)">
                    {{ fmtBytes(a.bytes) }}
                  </td>
                  <td class="py-2.5 px-4 text-right num-tabular" style="color: var(--text-faint)">
                    {{ fmtTime(a.mtime) }}
                  </td>
                  <td class="py-2.5 px-4 text-center">
                    <div class="flex items-center justify-center space-x-2">
                      <button
                        @click="downloadArchive(a.name)"
                        class="p-1 rounded hover:bg-[var(--bg-badge)] text-[var(--color-brand)] transition-colors cursor-pointer"
                        title="下载归档到本地"
                      >
                        <Download class="w-3.5 h-3.5" />
                      </button>
                      <button
                        v-if="auth.isSuperadmin"
                        @click="restoreArchive(a.name)"
                        :disabled="busy === 'restore'"
                        class="p-1 rounded hover:bg-[var(--bg-badge)] text-amber-500 transition-colors cursor-pointer"
                        title="恢复此备份到系统"
                      >
                        <RotateCcw class="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="py-8 text-center text-sm font-sans" style="color: var(--text-muted)">
              暂无本地待清归档，可点击「立即备份」生成完整镜像包或「上传备份包」
            </div>
          </div>
        </AppCard>
      </div>
    </template>
  </div>
</template>
