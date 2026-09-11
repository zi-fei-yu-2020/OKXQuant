import type { ScenarioShadowStatus } from '../utils/scenarioShadow'
import type { CapitalPoolStatus } from '../utils/capitalPool'
import type { WaitAuditState, DecisionCycle } from '../utils/waitAudit'
import type { MacroAnalysis } from '../utils/macroAnalysis'
// OKX raw numeric strings and locally aggregated numeric values coexist.
export type NumericValue = string | number
export type TradeAction = 'BUY_LONG' | 'SELL_SHORT' | 'WAIT'

export interface AccountSummary {
  total_eq: number
  avail_eq: number
  cash_bal?: number
  upl?: number
  pos_upl_total?: number
  margin_usage_pct?: number
  margin_ratio?: number
  risk_level?: string
  currency?: string
  initial_capital?: number | null
  baseline_configured?: boolean
  cum_net_pnl?: number | null
  cum_realized_pnl?: number
  cum_roi_pct?: number | null
  cum_total_fees?: number
}

export interface PositionItem {
  instId: string
  name: string
  side: 'long' | 'short'
  pos: NumericValue
  lever: NumericValue
  margin?: NumericValue
  margin_source?: string
  marginSource?: string
  markPx?: NumericValue
  margin_usdt?: number | null
  roi_pct?: number
  roi?: NumericValue // legacy persisted snapshots
  protectionStatus?: 'fully_protected' | 'partially_protected' | 'unprotected' | 'unknown_stale' | 'verification_stale'
  protectionCoveragePct?: number
  avgPx: NumericValue
  last?: NumericValue
  upl: NumericValue
  uplRatio: NumericValue
  displayStop?: number | null
  takeProfitPx?: number | null
  cloud_oco_verified?: boolean
  horizon?: 'scalp' | 'swing' | 'unknown'
  strategy_type?: string
}

export interface PendingOrderItem {
  inst?: string
  side_raw?: 'buy' | 'sell'
  time?: string
  ordId: string
  instId: string
  name: string
  side: 'buy' | 'sell'
  posSide: 'long' | 'short'
  px: string
  sz: string
  state: string
  cTime: string
  tpTriggerPx?: string
  slTriggerPx?: string
}

export interface InstrumentSupport {
  instId: string
  environment: 'demo' | 'live'
  status: 'supported' | 'unsupported' | 'unavailable' | 'unknown' | 'refreshing'
  can_open: boolean
  label: string
  message: string
  checked_at: number | null
}
export interface InstrumentSupportSummary {
  environment: 'demo' | 'live'
  status: 'verified' | 'unknown' | 'refreshing'
  checked_at: number | null
  items: Record<string, InstrumentSupport>
  supported_count: number
  observation_count: number
}
export interface InstrumentFactor {
  environment_support?: InstrumentSupport
  instId: string
  name: string
  type: string
  price: number
  chg24h: number
  high24h?: number
  low24h?: number
  vol24h?: number
  rsi: number
  macd_hist: number
  trend_direction?: string
  action?: TradeAction
  confidence?: number
  adx_1h?: NumericValue
  calculus?: {
    velocity_1h?: number
    accel_1h?: number
    jerk_1h?: number
    impulse_1h?: number
    energy_1h?: number
    action_area_1h?: number
    state_1h?: string
  }
  smart_money?: {
    weighted_long_pct?: number
    net_flow_usdt?: string
    top_win_rate?: string
  }
  decision_status?: string
  horizon?: 'scalp' | 'swing' | 'unknown'
  strategy_type?: string
  regime?: string
  wait_reason?: string
  decision?: {
    decision_status?: string
    action: TradeAction
    confidence: number
    leverage: number
    margin_usdt: number
    entry_price: number
    take_profit_price: number
    stop_loss_price: number
    risk_reward_ratio: string
    summary_reason: string
  }
  thought_process?: {
    market_structure?: string
    calculus_dynamics?: string
    math_prob_rationale?: string
    volume_and_oi?: string
    risk_reward_evaluation?: string
  }
  position?: PositionItem | null
}

export interface LLMRuntime {
  model: string
  provider_name: string
  reasoning_effort: string
  api_format: string
}

export interface ExecutionProfileSnapshot {
  profile_id?: string
  signature?: string
  execution?: {
    id?: string
    label?: string
    equity_cap_usdt?: number
    per_trade_equity_pct?: number
    single_asset_margin_usdt?: number
    total_margin_usdt?: number
    max_active_instruments?: number
    max_same_direction_positions?: number
    max_leverage?: number
    daily_drawdown_pct?: number
  }
  risk?: {
    daily_drawdown?: number
    correlated?: number
    correlated_cap?: number
    available?: number
  }
  error?: string
}

export interface HorizonStat {
  closed?: number
  wins?: number
  losses?: number
  net_pnl?: number
  fees?: number
  win_rate?: number
  avg_pnl?: number
  avg_hold_seconds?: number
}

export interface DashboardResponse {
  account_source_id?: string
  initializing?: boolean
  scenario_shadow?: ScenarioShadowStatus
  capital_pool?: CapitalPoolStatus
  entry_opportunities?: import('../utils/waitAudit').OpportunityShadow
  wait_audit?: WaitAuditState
  decision_cycle?: DecisionCycle
  wait_state?: { code?: string; detail?: string; next_trigger?: string }
  macro_analysis?: MacroAnalysis
  ledger_sync?: { status?: string; last_success?: number; pending_settlements?: number }
  instrument_support?: InstrumentSupportSummary
  timestamp: string
  is_stale?: boolean
  okx_environment?: 'demo' | 'live'
  data_health?: { status?: 'LIVE' | 'STALE' | 'PARTIAL' | 'OFFLINE'; partial?: boolean; errors?: string[] }
  account: AccountSummary
  positions_summary: {
    total_count: number
    long_count: number
    short_count: number
    items: PositionItem[]
  }
  pending_orders: PendingOrderItem[]
  factors: InstrumentFactor[]
  macro_assessment?: string
  llm_runtime?: LLMRuntime
  logs: string[]
  trades: any[]
  ai_brain_history?: AiBrainHistoryItem[]
  ai_last_prompt?: string
  today_stats?: any
  performance?: any
  news_intelligence?: Record<string, any>
  evolution_review?: import('../components/EvolutionReviewPanel.vue').EvolutionReview
  review?: any
  memory_publication?: import('../utils/memory').MemoryPublication
  ai_trading_memory_md?: string
  factor_library?: any
  execution_profile?: ExecutionProfileSnapshot
  horizon_stats?: Record<string, HorizonStat>
}

export interface CouncilMemberResult {
  role_id?: string
  role_name?: string
  model_used?: string
  status?: string
  content?: string
  duration_ms?: number
  weight?: number
}

export interface AiBrainHistoryItem {
  time: string
  macro_assessment?: string
  ai_last_prompt?: string
  council_transcript?: {
    council_mode?: boolean
    total_duration_ms?: number
    advisors?: Record<string, CouncilMemberResult>
    arbitrator?: CouncilMemberResult
  } | null
  position_management?: Array<{
    instId: string
    action: 'HOLD' | 'CLOSE_MARKET' | 'UPDATE_SL'
    reason?: string
    reasoning?: string
  }>
}
