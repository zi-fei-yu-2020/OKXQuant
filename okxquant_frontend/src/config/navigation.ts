import {
  LayoutDashboard,
  Radio,
  FileText,
  Sparkles,
  ShieldCheck,
  Users,
  Cpu,
  Package,
  Wallet,
  Workflow,
  Bell,
  HardDrive,
  ScrollText,
  UserCog,
  Info,
  Plug,
  Brain,
  Newspaper,
  Receipt,
} from 'lucide-vue-next'

export interface AdminNavItem {
  id: string
  label: string
  icon: any
  description: string
  advanced?: boolean
  superadminOnly?: boolean
}

export interface AdminNavSection {
  id: string
  label: string
  icon: any
  description?: string
  defaultPath: string
  items: AdminNavItem[]
}

export const adminNavigation: AdminNavSection[] = [
  {
    id: 'overview-section',
    label: '运行概览',
    icon: LayoutDashboard,
    description: '集中查看服务状态、轻量运行配置、配置进度与最近交易决策。',
    defaultPath: '/admin/overview',
    items: [
      {
        id: 'overview',
        label: '运行概览',
        icon: LayoutDashboard,
        description: '集中查看服务状态、配置进度与最近的交易决策。',
      },
    ],
  },
  {
    id: 'accounts-trading-section',
    label: '账户与交易',
    icon: Wallet,
    description: '管理账户凭证与连接，以及标的池、基准与持仓。',
    defaultPath: '/admin/accounts',
    items: [
      {
        id: 'accounts',
        label: '账户中心',
        icon: Wallet,
        description: '隔离交易与资讯连接，核验能力并安全绑定、解绑和更换账户。',
        superadminOnly: true,
      },
      {
        id: 'security',
        label: '交易标的与持仓',
        icon: Wallet,
        description: '管理标的池、盈亏基准及受保护平仓；账户配置统一在账户中心。',
      },
    ],
  },
  {
    id: 'strategy-risk-section',
    label: '策略与风控',
    icon: Sparkles,
    description: '策略提示词、多模型委员会、策略复盘与风控拦截门禁。',
    defaultPath: '/admin/promptlib',
    items: [
      {
        id: 'promptlib',
        label: '提示词工作室',
        icon: FileText,
        description: '编辑、版本管理和预览策略提示词，保留核心安全约束。',
      },
      {
        id: 'council',
        label: '模型委员会',
        icon: Users,
        description: '配置交易员席位、讨论规则与 CIO 最终裁决。',
      },
      {
        id: 'evolution',
        label: '策略自进化',
        icon: Sparkles,
        description: '管理复盘计划、长期记忆和经验审查。',
      },
      {
        id: 'interceptors',
        label: '风控拦截器',
        icon: ShieldCheck,
        description: '检查规则、调整执行顺序并使用沙盒验证风险门禁。',
        advanced: true,
      },
    ],
  },
  {
    id: 'models-section',
    label: '模型服务',
    icon: Cpu,
    description: '管理模型连接、供应商配置与协议兼容性。',
    defaultPath: '/admin/llm',
    items: [
      {
        id: 'llm',
        label: '模型与供应商',
        icon: Cpu,
        description: '管理模型连接、协议兼容性和主模型选择。',
      },
    ],
  },
  {
    id: 'runtime-logs-section',
    label: '运行与日志',
    icon: Radio,
    description: '查看决策日志、任务网关、运行单元与系统插件。',
    defaultPath: '/admin/decisions',
    items: [
      {
        id: 'decisions',
        label: '决策日志',
        icon: Radio,
        description: '查看 AI 决策记录、宏观判断与执行日志。',
      },
      {
        id: 'gateway',
        label: '任务网关',
        icon: Workflow,
        description: '查看计划任务、投递记录与重试状态。',
      },
      {
        id: 'agents',
        label: '运行单元',
        icon: Package,
        description: '查看 Worker 状态、任务心跳与模型调用遥测。',
      },
      {
        id: 'plugins',
        label: '系统插件',
        icon: Plug,
        description: '查看系统内置插件及其运行状态。',
      },
    ],
  },
  {
    id: 'notify-backup-section',
    label: '通知与备份',
    icon: Bell,
    description: '管理通知渠道与消息发送，以及本地与远程备份。',
    defaultPath: '/admin/notify',
    items: [
      {
        id: 'notify',
        label: '消息通知',
        icon: Bell,
        description: '连接通知渠道并配置报告时间，测试发送需要确认。',
      },
      {
        id: 'backup',
        label: '备份与恢复',
        icon: HardDrive,
        description: '管理本地及远程备份、保留策略与灾难恢复。',
      },
    ],
  },
  {
    id: 'system-settings-section',
    label: '系统设置',
    icon: UserCog,
    description: '操作审计追溯、管理员安全管理与版本信息。',
    defaultPath: '/admin/audit',
    items: [
      {
        id: 'audit',
        label: '操作审计',
        icon: ScrollText,
        description: '追溯配置变更和关键安全操作。',
      },
      {
        id: 'adminsys',
        label: '成员与安全',
        icon: UserCog,
        description: '管理管理员权限、账户状态和密码。',
      },
      {
        id: 'about',
        label: '版本与更新',
        icon: Info,
        description: '核对版本、部署信息与更新方式。',
      },
    ],
  },
]

export const adminPages: AdminNavItem[] = adminNavigation.flatMap((group) => group.items)

export type FrontTabId = 'overview' | 'decisions' | 'market-intelligence' | 'reviews' | 'trades'

export interface FrontNavTab {
  id: FrontTabId
  path: string
  label: string
  description: string
  icon: any
  legacyPaths: string[]
}

export const frontTabs: FrontNavTab[] = [
  {
    id: 'overview',
    path: '/',
    label: '交易概览',
    description: '账户、持仓与市场信号，在一个视图中保持同步。',
    icon: LayoutDashboard,
    legacyPaths: ['/trading'],
  },
  {
    id: 'decisions',
    path: '/decisions',
    label: 'AI 决策',
    description: '回溯模型判断与投委会讨论，了解每一轮策略的依据。',
    icon: Brain,
    legacyPaths: ['/factors'],
  },
  {
    id: 'market-intelligence',
    path: '/market-intelligence',
    label: '市场情报',
    description: '关注影响市场的新闻、情绪与资金动向。',
    icon: Newspaper,
    legacyPaths: ['/news'],
  },
  {
    id: 'reviews',
    path: '/reviews',
    label: '策略复盘',
    description: '查看交易复盘与长期记忆，追踪策略的持续演进。',
    icon: Sparkles,
    legacyPaths: ['/lab'],
  },
  {
    id: 'trades',
    path: '/trades',
    label: '交易记录',
    description: '查阅订单生命周期、历史成交与执行日志。',
    icon: Receipt,
    legacyPaths: ['/history'],
  },
]

export const publicPages: Record<string, string> = {
  '/': '交易概览',
  '/trading': '交易概览',
  '/decisions': 'AI 决策',
  '/factors': 'AI 决策',
  '/market-intelligence': '市场情报',
  '/news': '市场情报',
  '/reviews': '策略复盘',
  '/lab': '策略复盘',
  '/trades': '交易记录',
  '/history': '交易记录',
  '/docs': '使用文档',
}

export function findFrontTab(pathOrTab: string): FrontNavTab | undefined {
  return frontTabs.find(
    (t) => t.id === pathOrTab || t.path === pathOrTab || t.legacyPaths.includes(pathOrTab)
  )
}

export function pageTitle(path: string): string {
  if (path === '/admin/login') return '登录控制台'
  if (path.startsWith('/admin/')) {
    const found = adminPages.find((p) => '/admin/' + p.id === path)
    return found?.label || '控制台'
  }
  return publicPages[path] || (path.startsWith('/docs') ? '使用文档' : 'OKXQuant')
}
