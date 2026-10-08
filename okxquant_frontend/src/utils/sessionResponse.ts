/** Already reported globally (or belongs to an obsolete session). Never show it again in page feedback. */
export class HandledSessionError extends Error {
  readonly silent = true
  constructor() {
    super('')
  }
}

export const SESSION_TOKEN_KEY = 'okxquant.admin.session.id'
export const SESSION_USER_KEY = 'okxquant.admin.session.user'
export const SESSION_EXPIRED_MESSAGE = '管理员会话已失效，请重新登录'

let sessionExpireCallback: ((token: string) => void) | null = null

export function registerSessionExpireCallback(cb: ((token: string) => void) | null) {
  sessionExpireCallback = cb
}

export function getSessionToken(): string {
  try {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem(SESSION_TOKEN_KEY) || ''
    }
  } catch {}
  return ''
}

export function buildAuthHeaders(customToken?: string): Record<string, string> {
  const token = customToken !== undefined ? customToken : getSessionToken()
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (token) {
    headers['X-OKXQuant-Session'] = token
  }
  return headers
}

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

export function handleSessionResponse(
  status: number,
  requestToken: string,
  onCurrentExpired?: () => void,
) {
  const currentToken = getSessionToken()
  checkSessionResponse(status, requestToken, currentToken, (expired) => {
    try {
      if (typeof localStorage !== 'undefined') {
        if (localStorage.getItem(SESSION_TOKEN_KEY) === expired) {
          localStorage.removeItem(SESSION_TOKEN_KEY)
          localStorage.removeItem(SESSION_USER_KEY)
        }
      }
    } catch {}
    if (sessionExpireCallback) {
      try {
        sessionExpireCallback(expired)
      } catch {}
    }
    if (onCurrentExpired) {
      onCurrentExpired()
    }
  })
}
