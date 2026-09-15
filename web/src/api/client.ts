export class ApiError extends Error {
  readonly status: number
  // Messages keyed by field id, e.g. { eula: "must be accepted" }.
  readonly fieldErrors: Record<string, string>

  constructor(status: number, message: string, fieldErrors: Record<string, string> = {}) {
    super(message)
    this.status = status
    this.fieldErrors = fieldErrors
  }
}

interface PydanticError {
  loc: (string | number)[]
  msg: string
}

function parseError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail

  // Our own template validation: { detail: { errors: { field: message } } }
  if (detail && typeof detail === 'object' && 'errors' in detail) {
    return new ApiError(status, 'Please fix the highlighted fields', detail.errors as Record<string, string>)
  }
  // FastAPI request validation: { detail: [{ loc: ["body", "name"], msg }] }
  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {}
    for (const err of detail as PydanticError[]) {
      fieldErrors[String(err.loc.at(-1))] = err.msg
    }
    return new ApiError(status, 'Please fix the highlighted fields', fieldErrors)
  }
  return new ApiError(status, typeof detail === 'string' ? detail : `Request failed (${status})`)
}

// The panel refuses state changes without this header: other sites can't add it (CSRF).
export const CSRF_HEADERS = { 'X-Requested-With': 'possum' }

/** Fired when the session is gone (logged out elsewhere, expired): the app shows the login page. */
export const UNAUTHORIZED_EVENT = 'possum:unauthorized'

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...CSRF_HEADERS, ...init?.headers },
  })
  if (resp.status === 204) {
    return undefined as T
  }
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    if (resp.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    throw parseError(resp.status, body)
  }
  return body as T
}
