export function isSettlementPending(trade: { status?: string; settlement_status?: string }): boolean {
  return trade.status === 'closed_pending' || trade.settlement_status === 'pending'
}
