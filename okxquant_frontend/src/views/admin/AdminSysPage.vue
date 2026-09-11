<script setup lang="ts">
import AppField from '../../components/ui/AppField.vue'
import AppCard from '../../components/ui/AppCard.vue'

import AppDialog from '../../components/ui/AppDialog.vue'

import { useFeedback } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { UserCog, KeyRound, Plus, Lock, Unlock, ShieldCheck } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const users = ref<any[]>([])
const currentUserId = ref<number>(0)
const loading = ref(true)
const bannerMsg = useFeedback()

// Password form
const pwdUserId = ref<number>(0)
const currentPassword = ref('')
const newPassword = ref('')
const changingPwd = ref(false)

// Create form
const createVisible = ref(false)
const newUsername = ref('')
const newRole = ref('admin')
const newPasswordForCreate = ref('')

async function load() {
  if (!auth.isSuperadmin) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const res = await api('/api/v1/admin/users')
    users.value = res.users || []
    currentUserId.value = res.current_user_id
    pwdUserId.value = res.current_user_id
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: e.message, type: 'err' }
  } finally {
    loading.value = false
  }
}

async function changePassword() {
  if (newPassword.value.length < 12) {
    bannerMsg.value = { text: '新密码至少需要 12 位字符', type: 'err' }
    return
  }
  changingPwd.value = true
  try {
    await api(`/api/v1/admin/users/${pwdUserId.value}/password`, {
      method: 'PUT',
      body: JSON.stringify({
        current_password: currentPassword.value,
        new_password: newPassword.value,
      }),
    })
    bannerMsg.value = { text: '✅ 密码已修改，其他设备的会话已全部失效', type: 'ok' }
    currentPassword.value = ''
    newPassword.value = ''
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `修改失败：${e.message}`, type: 'err' }
  } finally {
    changingPwd.value = false
  }
}

async function createUser() {
  if (newUsername.value.length < 3 || newPasswordForCreate.value.length < 12) {
    bannerMsg.value = { text: '账号至少 3 位，密码至少 12 位', type: 'err' }
    return
  }
  try {
    await api('/api/v1/admin/users', {
      method: 'POST',
      body: JSON.stringify({
        username: newUsername.value,
        password: newPasswordForCreate.value,
        role: newRole.value,
      }),
    })
    bannerMsg.value = { text: `已创建管理员 ${newUsername.value}`, type: 'ok' }
    createVisible.value = false
    newUsername.value = ''
    newPasswordForCreate.value = ''
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `创建失败：${e.message}`, type: 'err' }
  }
}

async function toggleEnabled(u: any) {
  try {
    await api(`/api/v1/admin/users/${u.id}/enabled`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !u.enabled }),
    })
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: e.message, type: 'err' }
  }
}

async function unlockUser(u: any) {
  const phrase = await prompt(`解锁 ${u.username} 需输入确认短语：UNLOCK ADMIN ${u.id}`)
  if (!phrase) return
  try {
    await api(`/api/v1/admin/users/${u.id}/unlock`, {
      method: 'POST',
      body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }),
    })
    bannerMsg.value = { text: `${u.username} 已解锁`, type: 'ok' }
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: e.message, type: 'err' }
  }
}

onMounted(load)

const { prompt } = useDialogs()
</script>

<template>
  <div class="space-y-4">
    <!-- Change Password -->
    <AppCard
      class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        class="flex items-center space-x-2 mb-4 pb-3 border-b"
        style="border-color: var(--border-subtle)"
      >
        <KeyRound class="w-4 h-4 text-amber-500" />
        <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">修改密码</h2>
        <span class="text-xs font-sans ml-2" style="color: var(--text-faint)"
          >当前账号：{{ auth.user?.username }}（修改后需重新登录）</span
        >
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <AppField class="w-full min-w-0"
            ><template #label
              ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
                >当前密码</span
              ></template
            ><template #default="{ id: fieldId }"
              ><input
                :id="fieldId"
                v-model="currentPassword"
                type="password"
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
                >新密码 (≥12 位)</span
              ></template
            ><template #default="{ id: fieldId }"
              ><input
                :id="fieldId"
                v-model="newPassword"
                type="password"
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
            @click="changePassword"
            :disabled="changingPwd"
            class="w-full flex items-center justify-center space-x-1.5 px-4 py-2 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            <ShieldCheck class="w-3.5 h-3.5" /><span>{{
              changingPwd ? '修改中...' : '确认修改'
            }}</span>
          </button>
        </div>
      </div>
      <p class="mt-2 text-xs font-sans" style="color: var(--text-faint)">
        超级管理员可在下方用户列表为其他账号重置密码（无需旧密码）。
      </p>
    </AppCard>

    <!-- Users List -->
    <AppCard
      class="rounded-xl border overflow-hidden shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        class="px-4 py-3 border-b flex items-center justify-between"
        style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
      >
        <div class="flex items-center space-x-2">
          <UserCog class="w-4 h-4 text-blue-400" />
          <h2
            class="text-sm font-black font-sans uppercase tracking-wide"
            style="color: var(--text-main)"
          >
            管理员账号与权限
          </h2>
        </div>
        <button
          v-if="auth.isSuperadmin"
          @click="createVisible = true"
          class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-sm font-sans font-bold transition-all cursor-pointer shadow-xs"
          style="background-color: var(--text-main); color: var(--bg-card)"
        >
          <Plus class="w-3.5 h-3.5" />
          <span>新建管理员</span>
        </button>
      </div>

      <div
        v-if="!auth.isSuperadmin"
        class="py-8 text-center text-sm font-sans border border-dashed rounded-lg m-4"
        style="color: var(--text-muted); border-color: var(--border-subtle)"
      >
        仅超级管理员可查看与管理团队账号。
      </div>

      <div v-else class="table-scroll-container">
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
              <th class="py-2.5 px-4">UID</th>
              <th class="py-2.5 px-3">账号名</th>
              <th class="py-2.5 px-3">授权角色</th>
              <th class="py-2.5 px-3">账号状态</th>
              <th class="py-2.5 px-3">最近登录时间</th>
              <th class="py-2.5 px-4 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="u in users"
              :key="u.id"
              class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
              style="border-color: var(--border-subtle)"
            >
              <td class="py-2.5 px-4 num-tabular" style="color: var(--text-faint)">{{ u.id }}</td>
              <td class="py-2.5 px-3 font-bold" style="color: var(--text-main)">
                {{ u.username }}
                <span
                  v-if="u.id === currentUserId"
                  class="px-1 py-0.2 rounded text-xs font-bold border ml-1"
                  style="
                    background-color: var(--color-brand-bg);
                    border-color: var(--color-brand-border);
                    color: var(--color-brand);
                  "
                  >(当前会话)</span
                >
              </td>
              <td class="py-2.5 px-3">
                <span
                  class="px-2 py-0.5 rounded text-xs font-bold border"
                  :style="
                    u.role === 'superadmin'
                      ? {
                          backgroundColor: 'var(--color-brand-bg)',
                          borderColor: 'var(--color-brand-border)',
                          color: 'var(--color-brand)',
                        }
                      : {
                          backgroundColor: 'var(--bg-badge)',
                          borderColor: 'var(--border-subtle)',
                          color: 'var(--text-muted)',
                        }
                  "
                >
                  {{ u.role === 'superadmin' ? '超级管理员' : '普通管理员' }}
                </span>
              </td>
              <td
                class="py-2.5 px-3 font-bold"
                :class="
                  u.enabled
                    ? u.locked_until
                      ? 'text-amber-500'
                      : 'text-emerald-500'
                    : 'text-rose-500'
                "
              >
                {{
                  !u.enabled
                    ? '已停用'
                    : u.locked_until && new Date(u.locked_until) > new Date()
                      ? '已锁定'
                      : '正常启用'
                }}
              </td>
              <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint)">
                {{ u.last_login_at || '从未登录' }}
              </td>
              <td class="py-2.5 px-4 text-right whitespace-nowrap space-x-1.5">
                <button
                  v-if="u.id !== currentUserId"
                  @click="toggleEnabled(u)"
                  class="px-2.5 py-1 rounded-md border text-xs font-sans transition-all cursor-pointer shadow-xs"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-medium);
                    color: var(--text-main);
                  "
                >
                  <component :is="u.enabled ? Lock : Unlock" class="w-3 h-3 inline" />
                  {{ u.enabled ? '停用' : '启用' }}
                </button>
                <button
                  v-if="u.locked_until"
                  @click="unlockUser(u)"
                  class="px-2.5 py-1 rounded-md border text-xs font-sans cursor-pointer transition-colors"
                  style="
                    background-color: var(--color-warn-bg);
                    border-color: var(--color-warn-border);
                    color: var(--color-warn);
                  "
                >
                  解锁
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </AppCard>

    <!-- Create Modal -->
    <AppDialog
      v-if="createVisible"
      :open="!!createVisible"
      title="新增管理员"
      size="md"
      @update:open="
        (open) => {
          if (!open) {
            createVisible = false
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <h3 class="text-sm font-bold mb-4 font-sans" style="color: var(--text-main)">新建管理员</h3>
        <AppField class="w-full min-w-0"
          ><template #label
            ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
              >账号 (3-32 位)</span
            ></template
          ><template #default="{ id: fieldId }"
            ><input
              :id="fieldId"
              v-model="newUsername"
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
              >角色</span
            ></template
          ><template #default="{ id: fieldId }"
            ><select
              :id="fieldId"
              v-model="newRole"
              class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border mb-3 cursor-pointer"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            >
              <option value="admin">管理员（日常运维）</option>
              <option value="superadmin">超级管理员（全部权限）</option>
            </select></template
          ></AppField
        >
        <AppField class="w-full min-w-0"
          ><template #label
            ><span class="block text-xs mb-1 font-sans" style="color: var(--text-muted)"
              >初始密码 (≥12 位)</span
            ></template
          ><template #default="{ id: fieldId }"
            ><input
              :id="fieldId"
              v-model="newPasswordForCreate"
              type="password"
              class="w-full rounded-lg px-3 py-2 text-sm font-sans outline-none border mb-4"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              " /></template
        ></AppField>
        <div class="flex justify-end space-x-2">
          <button
            @click="createVisible = false"
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
            @click="createUser"
            class="px-3 py-2 rounded-lg text-sm font-sans font-bold cursor-pointer transition-all shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            创建
          </button>
        </div>
      </div></AppDialog
    >
  </div>
</template>
