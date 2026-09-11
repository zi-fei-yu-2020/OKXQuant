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
} from 'lucide-vue-next'
export const adminNavigation = [
  {
    label: '工作空间',
    items: [
      {
        id: 'overview',
        label: '运行概览',
        icon: LayoutDashboard,
        description: '集中查看服务状态、配置进度与最近的交易决策。',
      },
      {
        id: 'decisions',
        label: '决策日志',
        icon: Radio,
        description: '查看 AI 决策记录、宏观判断与执行日志。',
      },
    ],
  },
  {
    label: '策略与模型',
    items: [
      {
        id: 'llm',
        label: '模型与供应商',
        icon: Cpu,
        description: '管理模型连接、协议兼容性和主模型选择。',
      },
      {
        id: 'council',
        label: '模型委员会',
        icon: Users,
        description: '配置交易员席位、讨论规则与 CIO 最终裁决。',
      },
      {
        id: 'promptlib',
        label: '提示词工作室',
        icon: FileText,
        description: '编辑、版本管理和预览策略提示词，保留核心安全约束。',
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
      },
    ],
  },
  {
    label: '连接与运行',
    items: [
      {
        id: 'accounts',
        label: '账户中心',
        icon: Wallet,
        description: '隔离交易与资讯连接，核验能力并安全绑定、解绑和更换账户。',
      },
      {
        id: 'security',
        label: '交易标的与持仓',
        icon: Wallet,
        description: '管理标的池、盈亏基准及受保护平仓；账户配置统一在账户中心。',
      },
      {
        id: 'gateway',
        label: '任务网关',
        icon: Workflow,
        description: '查看计划任务、投递记录与重试状态。',
      },
      {
        id: 'notify',
        label: '消息通知',
        icon: Bell,
        description: '连接通知渠道并配置报告时间，测试发送需要确认。',
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
    label: '系统设置',
    items: [
      {
        id: 'backup',
        label: '备份与恢复',
        icon: HardDrive,
        description: '管理本地及远程备份、保留策略与灾难恢复。',
      },
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
export const adminPages = adminNavigation.flatMap((group) => group.items)

export const publicPages: Record<string, string> = {
  '/': '交易概览', '/trading': '交易概览', '/factors': 'AI 决策',
  '/news': '市场情报', '/lab': '策略复盘', '/history': '交易记录', '/docs': '使用文档',
}
export function pageTitle(path: string) {
  if (path === '/admin/login') return '登录控制台'
  if (path.startsWith('/admin/')) return adminPages.find(p=>'/admin/'+p.id===path)?.label || '控制台'
  return publicPages[path] || (path.startsWith('/docs/') ? '使用文档' : 'OKXQuant Quantum')
}
