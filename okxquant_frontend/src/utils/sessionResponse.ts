/** Already reported globally (or belongs to an obsolete session). Never show it again in page feedback. */
export class HandledSessionError extends Error {
  readonly silent = true
  constructor() { super('') }
}

export const SESSION_EXPIRED_MESSAGE = '管理员会话已失效，请重新登录'

export function checkSessionResponse(
  status: number,
  requestToken: string,
  currentToken: string,
  expire: (token: string) => void,
) {
  // A delayed response must never log out a newly authenticated account.
  if (requestToken !== currentToken) throw new HandledSessionError()
  if (status === 401) {
    expire(requestToken)
    throw new HandledSessionError()
  }
  // 403 is authorization failure, not evidence that the session expired.
}
