import { pageTitle } from '../config/navigation'
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'dashboard-overview',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'overview' },
  },
  {
    path: '/decisions',
    name: 'dashboard-decisions',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'decisions' },
  },
  {
    path: '/market-intelligence',
    name: 'dashboard-market-intelligence',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'market-intelligence' },
  },
  {
    path: '/reviews',
    name: 'dashboard-reviews',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'reviews' },
  },
  {
    path: '/trades',
    name: 'dashboard-trades',
    component: () => import('../views/DashboardView.vue'),
    meta: { isPublic: true, tab: 'trades' },
  },
  {
    path: '/trading',
    redirect: (to) => ({ path: '/', query: to.query, hash: to.hash }),
  },
  {
    path: '/factors',
    redirect: (to) => ({ path: '/decisions', query: to.query, hash: to.hash }),
  },
  {
    path: '/news',
    redirect: (to) => ({ path: '/market-intelligence', query: to.query, hash: to.hash }),
  },
  {
    path: '/lab',
    redirect: (to) => ({ path: '/reviews', query: to.query, hash: to.hash }),
  },
  {
    path: '/history',
    redirect: (to) => ({ path: '/trades', query: to.query, hash: to.hash }),
  },
  {
    path: '/docs',
    name: 'docs',
    component: () => import('../views/DocsView.vue'),
    meta: { isPublic: true },
  },
  {
    path: '/doc',
    redirect: '/docs',
  },
  {
    path: '/admin',
    component: () => import('../views/AdminLayout.vue'),
    meta: { requiresAuth: true, isPublic: false },
    children: [
      { path: '', redirect: '/admin/overview' },
      { path: 'overview', name: 'admin-overview', component: () => import('../views/admin/OverviewPage.vue') },
      { path: 'accounts', name: 'admin-accounts', component: () => import('../views/admin/AccountsPage.vue') },
      { path: 'security', name: 'admin-security', component: () => import('../views/admin/SecurityPage.vue') },
      { path: 'symbols', redirect: '/admin/security' },
      { path: 'manual-trade', redirect: '/admin/security' },
      { path: 'backups', redirect: '/admin/backup' },
      { path: 'council', name: 'admin-council', component: () => import('../views/admin/CouncilPage.vue') },
      { path: 'llm', name: 'admin-llm', component: () => import('../views/admin/LlmPage.vue') },
      { path: 'notify', name: 'admin-notify', component: () => import('../views/admin/NotifyPage.vue') },
      { path: 'about', name: 'admin-about', component: () => import('../views/admin/AboutPage.vue') },
      { path: 'decisions', name: 'admin-decisions', component: () => import('../views/admin/DecisionsPage.vue') },
      { path: 'gateway', name: 'admin-gateway', component: () => import('../views/admin/GatewayPage.vue') },
      { path: 'promptlib', name: 'admin-promptlib', component: () => import('../views/admin/PromptStudioPage.vue') },
      { path: 'evolution', name: 'admin-evolution', component: () => import('../views/admin/EvolutionPage.vue') },
      { path: 'interceptors', name: 'admin-interceptors', component: () => import('../views/admin/InterceptorsPage.vue') },
      { path: 'agents', name: 'admin-agents', component: () => import('../views/admin/AgentsPage.vue') },
      { path: 'backup', name: 'admin-backup', component: () => import('../views/admin/BackupPage.vue') },
      { path: 'plugins', name: 'admin-plugins', component: () => import('../views/admin/PluginsPage.vue') },
      { path: 'audit', name: 'admin-audit', component: () => import('../views/admin/AuditPage.vue') },
      { path: 'adminsys', name: 'admin-adminsys', component: () => import('../views/admin/AdminSysPage.vue') },
    ],
  },
  {
    path: '/admin/login',
    name: 'admin-login',
    component: () => import('../views/admin/LoginPage.vue'),
    meta: { isPublic: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth) {
    const verified = await auth.restoreSession()
    if (!verified || !auth.isAuthenticated) {
      const isSafeLocal =
        typeof to.fullPath === 'string' &&
        to.fullPath.startsWith('/') &&
        !to.fullPath.startsWith('//') &&
        to.fullPath !== '/admin/overview'
      return isSafeLocal ? { name: 'admin-login', query: { next: to.fullPath } } : { name: 'admin-login' }
    }
  }
  // Redirect logged-in users away from login page
  if (to.name === 'admin-login') {
    const verified = await auth.restoreSession()
    if (verified && auth.isAuthenticated) {
      const rawNext = to.query?.next
      const isSafeLocal = typeof rawNext === 'string' && rawNext.startsWith('/') && !rawNext.startsWith('//')
      return isSafeLocal ? { path: rawNext } : { name: 'admin-overview' }
    }
  }
})

router.afterEach((to) => {
  document.title = pageTitle(to.path)
  const isNoIndex = to.path.startsWith('/admin')

  // Ensure search engines do not index administrative routes
  let robotsMeta = document.querySelector('meta[name="robots"]') as HTMLMetaElement | null
  if (isNoIndex) {
    if (!robotsMeta) {
      robotsMeta = document.createElement('meta')
      robotsMeta.name = 'robots'
      document.head.appendChild(robotsMeta)
    }
    robotsMeta.content = 'noindex, nofollow, noarchive'
  } else if (robotsMeta) {
    robotsMeta.content = 'index, follow, max-image-preview:large'
  }
})

export default router
